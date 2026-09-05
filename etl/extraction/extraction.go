package extraction

import (
	"compress/gzip"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/jackc/pgx/v5"
	parquet "github.com/parquet-go/parquet-go"
	"github.com/wmespi/honest-healthcare/etl/core"
)

// mrfHTTPClient fetches MRFs. There is deliberately no Client.Timeout — a
// multi-GB body legitimately streams for hours — but the connection setup and
// the wait for response headers are bounded so a dead endpoint fails fast
// instead of hanging the queue (issue #52). A mid-body stall is still caught
// downstream by the byte-count reconciliation in validateStreamComplete.
var mrfHTTPClient = &http.Client{
	Transport: &http.Transport{
		Proxy:                 http.ProxyFromEnvironment,
		DialContext:           (&net.Dialer{Timeout: 30 * time.Second}).DialContext,
		TLSHandshakeTimeout:   30 * time.Second,
		ResponseHeaderTimeout: 2 * time.Minute,
		IdleConnTimeout:       90 * time.Second,
	},
}

const copyBatchSize = 1_000_000

// inflightSubdir holds parquet files still being written. They are renamed up one
// level (into the backend's glob path) only after a clean single-pass stream.
const inflightSubdir = ".inflight"

func upsertBillingCode(ctx context.Context, conn *pgx.Conn, row core.BillingCodeRow) {
	if conn == nil {
		return
	}
	if _, err := conn.Exec(ctx,
		`INSERT INTO billing_codes (billing_code_type, billing_code, name, description)
		 VALUES ($1, $2, $3, $4)
		 ON CONFLICT (billing_code) DO NOTHING`,
		row.BillingCodeType, row.BillingCode, row.Name, row.Description,
	); err != nil {
		log.Printf("⚠️ Failed to upsert billing code %s: %v", row.BillingCode, err)
	}
}

func markFailed(ctx context.Context, conn *pgx.Conn, fileID int, reason error, dryRun bool) {
	log.Printf("❌ File %d failed: %v", fileID, reason)
	if !dryRun && conn != nil {
		msg := ""
		if reason != nil {
			msg = reason.Error()
		}
		conn.Exec(ctx, "UPDATE index_files SET status = 'failed', failure_reason = $2 WHERE id = $1", fileID, msg)
	}
}

// markSkipped records a probe abort. `skipped` is deliberately not `failed`:
// nothing went wrong, the file simply prices nobody we serve, and `make db-reset
// WHAT=failed` must not put it back in the queue to be downloaded again.
func markSkipped(ctx context.Context, conn *pgx.Conn, fileID int, reason error, dryRun bool) {
	if !dryRun && conn != nil {
		msg := ""
		if reason != nil {
			msg = reason.Error()
		}
		if _, err := conn.Exec(ctx,
			"UPDATE index_files SET status = 'skipped', failure_reason = $2 WHERE id = $1", fileID, msg); err != nil {
			log.Printf("⚠️ Failed to mark file %d as skipped: %v", fileID, err)
		}
	}
}

// humanBytes renders a byte count for the abort log — the number that says how
// much of a multi-GB download the probe saved.
func humanBytes(n int64) string {
	switch {
	case n >= 1<<30:
		return fmt.Sprintf("%.2f GB", float64(n)/(1<<30))
	case n >= 1<<20:
		return fmt.Sprintf("%.1f MB", float64(n)/(1<<20))
	case n >= 1<<10:
		return fmt.Sprintf("%.1f KB", float64(n)/(1<<10))
	default:
		return fmt.Sprintf("%d B", n)
	}
}

func writeNPILookup(seenNPIs map[int64]string) {
	if len(seenNPIs) == 0 {
		return
	}
	if err := os.MkdirAll(filepath.Dir(core.NPILookupPath), os.ModePerm); err != nil {
		log.Printf("⚠️ Failed to create dir for npi_lookup: %v", err)
		return
	}
	f, err := os.Create(core.NPILookupPath)
	if err != nil {
		log.Printf("⚠️ Failed to create npi_lookup.parquet: %v", err)
		return
	}
	defer f.Close()
	w := parquet.NewGenericWriter[core.NPILookupRow](f, parquet.Compression(&parquet.Zstd))
	defer w.Close()

	rows := make([]core.NPILookupRow, 0, len(seenNPIs))
	for npi, tin := range seenNPIs {
		rows = append(rows, core.NPILookupRow{NPI: npi, TINValue: tin})
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].NPI < rows[j].NPI })

	if _, err := w.Write(rows); err != nil {
		log.Printf("⚠️ Failed to write npi_lookup.parquet: %v", err)
		return
	}
	log.Printf("✅ Wrote npi_lookup.parquet — %d unique NPIs", len(rows))
}

// openMRF returns a reader for the raw (still gzipped) MRF bytes, its size, and a
// cancel func that aborts the in-flight request (a no-op for a fixture). A
// fixturePath reads from disk (offline); otherwise it GETs url under a
// cancellable context so watchStall can kill a dead transfer.
func openMRF(ctx context.Context, url, fixturePath string) (io.ReadCloser, int64, context.CancelFunc, error) {
	if fixturePath != "" {
		f, err := os.Open(fixturePath)
		if err != nil {
			return nil, 0, nil, err
		}
		fi, _ := f.Stat()
		return f, fi.Size(), func() {}, nil
	}
	reqCtx, cancel := context.WithCancel(ctx)
	req, err := http.NewRequestWithContext(reqCtx, "GET", url, nil)
	if err != nil {
		cancel()
		return nil, 0, nil, err
	}
	resp, err := mrfHTTPClient.Do(req)
	if err != nil {
		cancel()
		return nil, 0, nil, err
	}
	if resp.StatusCode != 200 {
		resp.Body.Close()
		cancel()
		return nil, 0, nil, fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	return resp.Body, resp.ContentLength, cancel, nil
}

// stallTimeout is how long a download may deliver zero bytes before watchStall
// aborts it. A healthy transfer always moves; minutes of silence means the
// connection is dead (issue #52 — the "hung parse" failure mode).
const stallTimeout = 3 * time.Minute

// watchStall aborts a download that stops delivering bytes. Unlike a total
// timeout it tolerates a multi-hour transfer as long as it keeps moving: it
// samples the compressed-byte counter and, if it has not advanced for timeout,
// calls cancel — unblocking the body Read with an error. Call the returned stop
// func once the stream is done; stalled() reports whether the abort fired, so
// the caller can label the failure a (retryable) stall rather than corruption.
func watchStall(cancel context.CancelFunc, pr *core.ProgressReader, timeout time.Duration) (stop func(), stalled func() bool) {
	done := make(chan struct{})
	var fired atomic.Bool
	interval := timeout / 4
	if interval > 15*time.Second {
		interval = 15 * time.Second
	}
	go func() {
		t := time.NewTicker(interval)
		defer t.Stop()
		last := pr.ReadBytes.Load()
		lastMoved := time.Now()
		for {
			select {
			case <-done:
				return
			case now := <-t.C:
				switch n := pr.ReadBytes.Load(); {
				case n != last:
					last, lastMoved = n, now
				case now.Sub(lastMoved) >= timeout:
					log.Printf("  ⏱️  download stalled — no bytes for %s, aborting", timeout)
					fired.Store(true)
					cancel()
					return
				}
			}
		}
	}()
	var once sync.Once
	return func() { once.Do(func() { close(done) }) }, fired.Load
}

// validateStreamComplete runs after the JSON decoder has stopped. The decoder
// reads only as far as the document's closing brace, so it can miss a body that
// was cut off in the trailing bytes (or a gzip trailer that never arrived).
// Draining the rest forces gzip to verify its CRC-32 + ISIZE and forces the
// HTTP layer to surface a short body, then the compressed byte count is
// reconciled against Content-Length. Truncation-shaped failures use wording the
// `make db-reset WHAT=failed` filter treats as retryable; genuine corruption
// ("corrupt gzip") is kept failed. Issue #52.
func validateStreamComplete(gz *gzip.Reader, pr *core.ProgressReader, contentLength int64) error {
	_, drainErr := io.Copy(io.Discard, gz)
	closeErr := gz.Close()
	read := pr.ReadBytes.Load()
	switch {
	case contentLength > 0 && read < contentLength:
		return fmt.Errorf("short read: got %d of %d compressed bytes — download truncated", read, contentLength)
	case drainErr != nil && errors.Is(drainErr, io.ErrUnexpectedEOF):
		return fmt.Errorf("stream truncated after %d bytes — download incomplete", read)
	case drainErr != nil:
		return fmt.Errorf("corrupt gzip after %d bytes: %w", read, drainErr)
	case closeErr != nil:
		return fmt.Errorf("corrupt gzip trailer: %w", closeErr)
	}
	return nil
}

// parseOutcome is what a file amounted to, for the end-of-run guard in Run.
// A file that never finished (a failure before the probe even ran) is
// outcomeFailed.
type parseOutcome int

const (
	outcomeFailed parseOutcome = iota
	outcomeCompleted
	outcomeSkippedOverlap // probe: no provider group survived the GA NPI filter
	outcomeSkippedNetwork // probe: no provider_references network_name matched a target's network_patterns
)

// parseRates streams one MRF (by URL, or from fixturePath when set) into
// rates/providers/codes Parquet keyed by fileID, upserts billing codes, updates
// index_files status, and writes a coverage_log row describing what the file gave.
// The second return is what became of the file (Run aggregates it).
func parseRates(
	ctx context.Context,
	conn *pgx.Conn,
	fileID int,
	url, planName, fixturePath string,
	isFirstFile bool,
	seenBillingCodes map[string]bool,
	seenNPIs map[int64]string,
	seenTINs map[string]bool,
	gaNPIs map[int64]struct{},
	pb providerProbe,
	totalBillingCodesBefore int,
	dryRun bool,
) (*mrfResult, parseOutcome) {
	log.Printf("⚙️ Processing Rate File [id=%d]: %s", fileID, orFixture(url, fixturePath))

	if !dryRun && conn != nil {
		if _, err := conn.Exec(ctx, "UPDATE index_files SET status = 'processing' WHERE id = $1", fileID); err != nil {
			log.Printf("⚠️ Failed to mark file %d as processing: %v", fileID, err)
		}
	}

	body, contentLength, cancelDownload, err := openMRF(ctx, url, fixturePath)
	if err != nil {
		markFailed(ctx, conn, fileID, err, dryRun)
		return nil, outcomeFailed
	}
	defer body.Close()
	defer cancelDownload()

	pr := core.NewProgressReader(body, contentLength)

	// Kill a download that goes silent mid-transfer (a hung socket that never
	// resets) so the queue moves on instead of blocking on this file (issue #52).
	stalled := func() bool { return false }
	if fixturePath == "" {
		var stopStall func()
		stopStall, stalled = watchStall(cancelDownload, pr, stallTimeout)
		defer stopStall()
	}

	if contentLength > 0 && fixturePath == "" {
		gb := float64(contentLength) / 1e9
		log.Printf("  📦 %.2f GB compressed | ~%.1f GB uncompressed (est. ×12)", gb, gb*12)
		if !dryRun && conn != nil {
			conn.Exec(ctx, "UPDATE index_files SET file_size_bytes = $1 WHERE id = $2", contentLength, fileID)
		}
	}

	// Parquet writers (skipped in dry-run). Everything for this file is written
	// under a per-file scratch dir (…/anthem/.inflight/<id>/) and moved into place
	// only after a clean stream, so the backend — which globs prices/**/*.parquet,
	// group_sets/*.parquet, providers/*.parquet, codes/*.parquet — never sees a
	// half-written or zero-byte file. Price rows fan out into
	// prices/net=<slug>/<id>.parquet, one partition per network_name, so a
	// network-filtered query prunes to it; the deduped provider-group rosters go
	// to group_sets/<id>.parquet.
	name := fmt.Sprintf("%d.parquet", fileID)
	anthemDir := filepath.Dir(core.PricesOutputDir)
	scratchDir := filepath.Join(anthemDir, inflightSubdir, fmt.Sprintf("%d", fileID))

	var w mrfWriters
	var closers []io.Closer
	var fanout *priceFanout
	groupSetsScratch := filepath.Join(scratchDir, "group_sets", name)
	provScratch := filepath.Join(scratchDir, "providers", name)
	codesScratch := filepath.Join(scratchDir, "codes", name)
	committed := false
	if !dryRun {
		defer func() {
			if !committed {
				os.RemoveAll(scratchDir)
			}
		}()
		for _, d := range []string{
			filepath.Join(scratchDir, "prices"),
			filepath.Join(scratchDir, "group_sets"),
			filepath.Join(scratchDir, "providers"),
			filepath.Join(scratchDir, "codes"),
		} {
			if err := os.MkdirAll(d, os.ModePerm); err != nil {
				markFailed(ctx, conn, fileID, fmt.Errorf("create dir %s: %w", d, err), dryRun)
				return nil, outcomeFailed
			}
		}
		fanout = newPriceFanout(filepath.Join(scratchDir, "prices"), name)
		gsW, gsC, err := newParquetWriter[core.GroupSetMemberRow](groupSetsScratch)
		if err != nil {
			markFailed(ctx, conn, fileID, err, dryRun)
			return nil, outcomeFailed
		}
		provW, provC, err := newParquetWriter[core.ProviderRow](provScratch)
		if err != nil {
			markFailed(ctx, conn, fileID, err, dryRun)
			return nil, outcomeFailed
		}
		codesW, codesC, err := newParquetWriter[core.BillingCodeRow](codesScratch)
		if err != nil {
			markFailed(ctx, conn, fileID, err, dryRun)
			return nil, outcomeFailed
		}
		closers = []io.Closer{fanoutCloser{fanout}, gsW, gsC, provW, provC, codesW, codesC}
		w = mrfWriters{
			prices: func(rows []core.PriceRow) {
				if err := fanout.write(rows); err != nil {
					log.Printf("⚠️ write prices parquet: %v", err)
				}
			},
			groupSetMembers: func(rows []core.GroupSetMemberRow) {
				if _, err := gsW.Write(rows); err != nil {
					log.Printf("⚠️ write group_sets parquet: %v", err)
				}
				gsW.Flush()
			},
			providers: func(rows []core.ProviderRow) {
				if _, err := provW.Write(rows); err != nil {
					log.Printf("⚠️ write providers parquet: %v", err)
				}
				provW.Flush()
			},
			code: func(row core.BillingCodeRow) {
				upsertBillingCode(ctx, conn, row)
				codesW.Write([]core.BillingCodeRow{row})
			},
		}
	}

	gz, err := gzip.NewReader(pr)
	if err != nil {
		closeAll(closers)
		markFailed(ctx, conn, fileID, err, dryRun)
		return nil, outcomeFailed
	}
	defer gz.Close()

	log.Println("  🔄 Starting single-pass extract...")
	res, err := streamMRF(gz, planName, int64(fileID), isFirstFile, seenBillingCodes, seenNPIs, seenTINs, gaNPIs, pb, w, pr)

	// The probe rejected the file at the end of provider_references, before a
	// byte of in_network. Kill the transfer now — that is the whole saving — and
	// record the row as skipped rather than failed. No completeness check: the
	// body is deliberately incomplete, and nothing was written to promote.
	if errors.Is(err, errNoWantedProviders) {
		cancelDownload()
		read := pr.ReadBytes.Load()
		closeAll(closers)
		of := ""
		if contentLength > 0 {
			of = fmt.Sprintf(" of %s (%.1f%%)", humanBytes(contentLength), 100*float64(read)/float64(contentLength))
		}
		log.Printf("  ⛔ File %d skipped — %v", fileID, err)
		log.Printf("     aborted after reading %s%s", humanBytes(read), of)
		markSkipped(ctx, conn, fileID, err, dryRun)
		var pae *probeAbortError
		if errors.As(err, &pae) && pae.Signal == signalNetwork {
			return nil, outcomeSkippedNetwork
		}
		return nil, outcomeSkippedOverlap
	}

	// The document parsed — now confirm the whole compressed body arrived and the
	// gzip trailer checks out before anything is promoted (issue #52). Only for a
	// real download: a fixture is a local file and always complete.
	if err == nil && fixturePath == "" {
		err = validateStreamComplete(gz, pr, contentLength)
	}
	// A watchStall abort surfaces as a context-cancelled read somewhere in the
	// two steps above — relabel it so the reason reads as a (retryable) stall,
	// not "malformed MRF" / "corrupt gzip".
	if err != nil && stalled() {
		err = fmt.Errorf("download stalled — no data for %s, aborted", stallTimeout)
	}
	// Parquet writers must be closed (flushed) before we read the files back or
	// mark the row completed — close in LIFO order (writer before its file).
	closeAll(closers)
	if err != nil {
		markFailed(ctx, conn, fileID, err, dryRun)
		return nil, outcomeFailed
	}

	// Stream succeeded — move the scratch parquet into place.
	if !dryRun {
		if err := fanout.promote(core.PricesOutputDir); err != nil {
			markFailed(ctx, conn, fileID, err, dryRun)
			return nil, outcomeFailed
		}
		for _, mv := range [][2]string{
			{groupSetsScratch, filepath.Join(core.GroupSetsOutputDir, name)},
			{provScratch, filepath.Join(core.ProvidersOutputDir, name)},
			{codesScratch, filepath.Join(core.CodesOutputDir, name)},
		} {
			if err := os.MkdirAll(filepath.Dir(mv[1]), os.ModePerm); err != nil {
				markFailed(ctx, conn, fileID, err, dryRun)
				return nil, outcomeFailed
			}
			if err := os.Rename(mv[0], mv[1]); err != nil {
				markFailed(ctx, conn, fileID, fmt.Errorf("promote %s: %w", mv[1], err), dryRun)
				return nil, outcomeFailed
			}
		}
		os.RemoveAll(scratchDir)
		committed = true
	}

	if isFirstFile && !dryRun && len(res.SchemaExample) > 0 {
		os.MkdirAll(filepath.Dir(core.ExampleOutputPath), os.ModePerm)
		if out, err := os.Create(core.ExampleOutputPath); err == nil {
			enc := json.NewEncoder(out)
			enc.SetIndent("", "  ")
			enc.Encode(res.SchemaExample)
			out.Close()
			log.Println("    ✅ Wrote ERD snippet to mrf_example.json")
		}
	}

	if !dryRun && conn != nil {
		if _, err := conn.Exec(ctx, `
			UPDATE index_files
			SET status = 'completed',
			    completed_at = NOW(),
			    reporting_entity_name = COALESCE(NULLIF($2, ''), reporting_entity_name),
			    reporting_entity_type = COALESCE(NULLIF($3, ''), reporting_entity_type)
			WHERE id = $1`,
			fileID, res.ReportingEntityName, res.ReportingEntityType); err != nil {
			log.Printf("⚠️ Failed to mark file %d as completed: %v", fileID, err)
		}
		var parts []string
		if gaNPIs != nil {
			parts = append(parts, "ga-npi-filtered")
		}
		note := strings.Join(parts, ",")
		writeCoverageLog(ctx, conn, fileID, orFixture(url, fixturePath), contentLength,
			totalBillingCodesBefore, note, res)
	}

	if gaNPIs != nil && (res.PriceRowsDropped > 0 || res.ProviderRowsDropped > 0) {
		log.Printf("  🗺️  GA NPI filter dropped %d provider rows, %d price rows, %d groups — kept %d groups, %d / %d price rows",
			res.ProviderRowsDropped, res.PriceRowsDropped, res.GroupsDropped,
			res.GroupsKept, res.PriceRows, res.PriceRows+res.PriceRowsDropped)
	}
	log.Printf("  ✅ Completed. %d provider rows | %d price rows | %d group-set edges (%d sets) | %d new codes | %d new NPIs | networks=%v",
		res.ProviderRows, res.PriceRows, res.GroupSetMemberRows, res.GroupSets,
		res.NewBillingCodes, res.NewNPIs, sortedKeys(res.NetworkNames))
	return res, outcomeCompleted
}

func orFixture(url, fixturePath string) string {
	if fixturePath != "" {
		return "fixture:" + fixturePath
	}
	return url
}

func closeAll(closers []io.Closer) {
	for _, c := range closers {
		if c != nil {
			c.Close()
		}
	}
}

// newParquetWriter creates a ZSTD-compressed Parquet writer over a new file and
// returns the writer and the underlying file (close the writer first, then the file).
func newParquetWriter[T any](path string) (*parquet.GenericWriter[T], io.Closer, error) {
	f, err := os.Create(path)
	if err != nil {
		return nil, nil, fmt.Errorf("create %s: %w", path, err)
	}
	w := parquet.NewGenericWriter[T](f, parquet.Compression(&parquet.Zstd))
	return w, f, nil
}

// writeCoverageLog records one row summarizing what this file contributed. The
// index_files metadata (market_types etc.) is joined in from the row itself. A
// re-parse replaces the file's prior row — coverage_log.file_id is UNIQUE
// (migration 004) and this is an upsert on it, so the table stays
// one-row-per-file, which `make cov-report` keys on to spot distinct files that
// parsed to identical counts (issue #52).
func writeCoverageLog(ctx context.Context, conn *pgx.Conn, fileID int, location string, compressedBytes int64, totalCodesBefore int, note string, res *mrfResult) {
	if note == "" {
		note = "unfiltered"
	}
	if res.PriceRowsDropped > 0 || res.ProviderRowsDropped > 0 {
		note = fmt.Sprintf("%s; dropped %d price + %d provider rows, %d groups",
			note, res.PriceRowsDropped, res.ProviderRowsDropped, res.GroupsDropped)
	}
	_, err := conn.Exec(ctx, `
		INSERT INTO coverage_log (
			file_id, location, compressed_bytes,
			n_rate_rows, n_provider_rows,
			n_new_billing_codes, n_total_billing_codes_after,
			n_new_npis, n_new_tins,
			network_names, plan_states, hios_issuer_ids, market_types,
			distinct_settings, distinct_billing_classes, billing_code_types, notes
		)
		SELECT
			$1, $2, NULLIF($3, 0)::bigint,
			$4, $5, $6, $7, $8, $9,
			$10::text[], i.plan_states, i.hios_issuer_ids, i.market_types,
			$11::text[], $12::text[], $13::text[], $14
		FROM index_files i WHERE i.id = $1
		ON CONFLICT (file_id) DO UPDATE SET
			location                    = EXCLUDED.location,
			parsed_at                   = NOW(),
			compressed_bytes            = EXCLUDED.compressed_bytes,
			n_rate_rows                 = EXCLUDED.n_rate_rows,
			n_provider_rows             = EXCLUDED.n_provider_rows,
			n_new_billing_codes         = EXCLUDED.n_new_billing_codes,
			n_total_billing_codes_after = EXCLUDED.n_total_billing_codes_after,
			n_new_npis                  = EXCLUDED.n_new_npis,
			n_new_tins                  = EXCLUDED.n_new_tins,
			network_names               = EXCLUDED.network_names,
			plan_states                 = EXCLUDED.plan_states,
			hios_issuer_ids             = EXCLUDED.hios_issuer_ids,
			market_types                = EXCLUDED.market_types,
			distinct_settings           = EXCLUDED.distinct_settings,
			distinct_billing_classes    = EXCLUDED.distinct_billing_classes,
			billing_code_types          = EXCLUDED.billing_code_types,
			notes                       = EXCLUDED.notes`,
		fileID, location, compressedBytes,
		res.PriceRows, res.ProviderRows,
		res.NewBillingCodes, totalCodesBefore+res.NewBillingCodes,
		res.NewNPIs, res.NewTINs,
		sortedKeys(res.NetworkNames),
		sortedKeys(res.Settings), sortedKeys(res.BillingClasses), sortedKeys(res.BillingCodeTypes),
		note,
	)
	if err != nil {
		log.Printf("⚠️ Failed to write coverage_log for file %d: %v", fileID, err)
	}
}

// ── orchestration ───────────────────────────────────────────────────────────

// Options configure a parse run (the flags behind `etl parse`).
type Options struct {
	FileIDs []int // explicit index_files ids; empty = pull from the pending queue
	// Targets is the path to the target-plan list (etl/targets.yaml). The queue
	// is restricted to pending files the index links to one of those plans; ""
	// disables the restriction and takes every pending file.
	Targets string
	Limit   int    // cap files processed (0 = no cap)
	Fixture string // read a local *.json.gz instead of downloading (needs exactly one FileID)
	AllNPIs bool   // keep every NPI/rate (disable the GA NPPES filter)
	// MinGroups is the probe's provider-overlap threshold: a file whose
	// provider_references leave fewer than this many provider groups is
	// abandoned before in_network. 0 disables that signal (the probe's network
	// signal, from the targets' network_patterns, is independent of it).
	MinGroups int
	DryRun    bool // stream only, no writes
}

type pendingFile struct {
	ID       int
	Location string
	PlanName string
}

// pendingFiles resolves the queue for this run: either the explicit -file-ids,
// or the pending rows the master index links to a target plan. targets == nil
// means no target restriction (every pending row).
func pendingFiles(ctx context.Context, conn *pgx.Conn, opts Options, targets *TargetSet) ([]pendingFile, error) {
	const cols = `SELECT f.id, f.location, COALESCE(array_to_string(f.market_types, ' | '), '') FROM index_files f`
	var query string
	var args []any
	if len(opts.FileIDs) > 0 {
		// One-off runs bypass target selection entirely — that is the whole point
		// of -file-ids (re-parse a known file, parse a fixture, probe a shard).
		query = cols + ` WHERE f.id = ANY($1) ORDER BY f.id`
		args = []any{opts.FileIDs}
	} else {
		where := `f.status = 'pending'`
		nextArg := 1
		if expr, targetArgs, n := targets.PlanMatchSQL("p", nextArg); expr != "" {
			// EXISTS, not a join: a file serving 40k employer plans must still
			// appear once, and the semi-join stops at the first matching plan.
			where += ` AND EXISTS (SELECT 1 FROM index_file_plans p WHERE p.file_id = f.id AND (` + expr + `))`
			args = append(args, targetArgs...)
			nextArg = n
		}
		query = cols + ` WHERE ` + where + ` ORDER BY f.file_size_bytes ASC NULLS LAST, f.id`
		if opts.Limit > 0 {
			query += fmt.Sprintf(` LIMIT $%d`, nextArg)
			args = append(args, opts.Limit)
		}
	}
	rows, err := conn.Query(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var files []pendingFile
	for rows.Next() {
		var f pendingFile
		if err := rows.Scan(&f.ID, &f.Location, &f.PlanName); err != nil {
			log.Printf("⚠️ Failed to scan row: %v", err)
			continue
		}
		files = append(files, f)
	}
	return files, rows.Err()
}

// Run is Phase 2: stream pending MRF files into Parquet (+ a little Postgres).
func Run(ctx context.Context, conn *pgx.Conn, opts Options) error {
	log.Println("🚀 Starting Anthem Bronze Layer: Rate Parsing (Go)...")

	// Which plans are we pricing? Loaded even for a -file-ids run so a broken
	// targets.yaml fails loudly at the start rather than on the next queue run.
	targets, err := LoadTargets(opts.Targets)
	if err != nil {
		return err
	}
	switch {
	case targets == nil:
		log.Println("⚠️ no target-plan filter — taking every pending file")
	case len(opts.FileIDs) > 0:
		log.Printf("🎯 targets %s loaded — not used to select (-file-ids given), still used by the probe", targets.Path)
	default:
		log.Printf("🎯 target plans from %s: %s", targets.Path, strings.Join(targets.Names(), ", "))
	}

	files, err := pendingFiles(ctx, conn, opts, targets)
	if err != nil {
		return fmt.Errorf("query pending files: %w", err)
	}
	if len(files) == 0 {
		if targets != nil && len(opts.FileIDs) == 0 {
			log.Printf("✅ No pending files serve a plan in %s. Run 'etl discover' first, all target files are already completed, or the target list needs another plan.", targets.Path)
			return nil
		}
		log.Println("✅ No pending files found in index_files. Run 'etl discover' first, or all files are already completed.")
		return nil
	}
	log.Printf("Found %d pending file(s). Processing...", len(files))

	if opts.DryRun {
		log.Println("🔍 Dry-run mode — streaming only, no DB writes.")
	}
	if opts.Fixture != "" && len(files) != 1 {
		return fmt.Errorf("-fixture needs exactly one -file-ids target (got %d)", len(files))
	}

	seenBillingCodes := make(map[string]bool)
	seenNPIs := make(map[int64]string)
	seenTINs := make(map[string]bool)

	var gaNPIs map[int64]struct{}
	if opts.AllNPIs {
		log.Println("⚠️ -all-npis — keeping every NPI/rate (GA filter disabled)")
	} else {
		gaNPIs = loadGANPISet(core.GAProvidersPath)
		if gaNPIs == nil {
			log.Printf("ℹ️  no %s — keeping all NPIs (run 'etl nppes' first to enable the GA filter)", core.GAProvidersPath)
		}
	}

	// The probe (#98). Not a row filter — every network a surviving file carries
	// is written, and the build step selects. This only decides whether the rest
	// of a file is worth downloading at all, and it applies however the file was
	// chosen (a -file-ids re-parse included): the question "does this file price
	// anyone on a target plan?" doesn't depend on how it reached the queue.
	pb := providerProbe{minGroups: opts.MinGroups}
	if pb.minGroups < 0 {
		pb.minGroups = 0
	}
	if m := targets.NetworkMatcher(); m != nil {
		pb.networkMatch = m
		pb.networkSpec = strings.Join(targets.NetworkPatterns(), ", ")
	}
	switch {
	case !pb.active():
		log.Println("⚠️ provider probe off — every selected file streams to the end")
	case pb.networkMatch == nil:
		log.Printf("🛰️  probe: abort before in_network unless ≥%d provider groups survive the filter "+
			"(no network signal — no network_patterns in the target list)", pb.minGroups)
	default:
		log.Printf("🛰️  probe: abort before in_network unless ≥%d provider groups survive the filter "+
			"and a network_name matches {%s}", pb.minGroups, pb.networkSpec)
	}

	totalCodes := 0
	if !opts.DryRun {
		conn.QueryRow(ctx, "SELECT count(*) FROM billing_codes").Scan(&totalCodes)
	}

	outcomes := make(map[parseOutcome]int, 4)
	for i, f := range files {
		res, outcome := parseRates(ctx, conn, f.ID, f.Location, f.PlanName, opts.Fixture,
			i == 0, seenBillingCodes, seenNPIs, seenTINs, gaNPIs, pb, totalCodes, opts.DryRun)
		outcomes[outcome]++
		if res != nil {
			totalCodes += res.NewBillingCodes
		}
	}
	if !opts.DryRun {
		writeNPILookup(seenNPIs)
	}
	return guardAgainstSilentSkip(ctx, conn, opts, targets, outcomes)
}

// guardAgainstSilentSkip turns the probe's quietest failure mode — every file
// the index links to a target plan abandoned on the *network* signal, none
// completing — into a loud one. That is what a stale `network_patterns` in
// targets.yaml looks like: the one MRF that actually carries the plan's rates
// gets skipped because Anthem renamed the network, and without this the run
// exits 0 with nothing ingested.
//
// It fires only for a target-selected queue run with the network signal active
// (a -file-ids re-parse or a probe with no network_patterns can't hit this
// mode). If no file for any target plan has *ever* completed, the run fails; if
// some have, serving still has data, so it is a warning — but a renamed network
// still means today's rates are stale, so it is a loud one.
func guardAgainstSilentSkip(ctx context.Context, conn *pgx.Conn, opts Options, targets *TargetSet, outcomes map[parseOutcome]int) error {
	if opts.DryRun || len(opts.FileIDs) > 0 || conn == nil || targets == nil || targets.NetworkMatcher() == nil {
		return nil
	}
	netSkipped := outcomes[outcomeSkippedNetwork]
	if netSkipped == 0 || outcomes[outcomeCompleted] > 0 {
		return nil
	}

	completedEver := -1
	if expr, args, _ := targets.PlanMatchSQL("p", 1); expr != "" {
		q := `SELECT count(*) FROM index_files f
		      WHERE f.status = 'completed'
		        AND EXISTS (SELECT 1 FROM index_file_plans p WHERE p.file_id = f.id AND (` + expr + `))`
		if err := conn.QueryRow(ctx, q, args...).Scan(&completedEver); err != nil {
			log.Printf("⚠️ silent-skip guard: could not count completed target files: %v", err)
			completedEver = -1
		}
	}

	fatal, warn := silentSkipVerdict(netSkipped, outcomes[outcomeCompleted], completedEver)
	if !warn {
		return nil
	}

	log.Printf("╔══════════════════════════════════════════════════════════════════════")
	log.Printf("║ 🛑 PROBE SKIPPED EVERY TARGET FILE ON THE NETWORK SIGNAL")
	log.Printf("║ %d file(s) the index links to %s were abandoned because no", netSkipped, strings.Join(targets.Names(), ", "))
	log.Printf("║ provider_references network_name matched {%s}, and none completed.", strings.Join(targets.NetworkPatterns(), ", "))
	log.Printf("║ If a plan's rates now land under a network Anthem renamed, the")
	log.Printf("║ network_patterns in etl/targets.yaml is stale — check the abort")
	log.Printf("║ messages above for the labels the files actually carry.")
	log.Printf("╚══════════════════════════════════════════════════════════════════════")

	if fatal {
		return fmt.Errorf("probe skipped every target file on the network signal and no target file has ever completed — "+
			"etl/targets.yaml network_patterns is stale, or a target plan has no dedicated MRF (%d skipped)", netSkipped)
	}
	log.Printf("⚠️ %d target file(s) completed in an earlier run, so serving still has rates — "+
		"but if a network was renamed those rates are now stale.", completedEver)
	return nil
}

// silentSkipVerdict decides what an end-of-run probe tally means. It is fatal
// only when the network signal skipped files, nothing completed this run, and
// no target file has *ever* completed (completedEver == 0) — a first run that
// found nothing, which is a stale pattern or a plan with no dedicated MRF. If
// earlier runs did land data (completedEver > 0, or < 0 meaning the count
// failed), it is a warning: serving still has rates, but a renamed network
// means they are stale. Not warned at all when the signal never fired or
// something did complete.
func silentSkipVerdict(netSkipped, completedThisRun, completedEver int) (fatal, warn bool) {
	if netSkipped == 0 || completedThisRun > 0 {
		return false, false
	}
	return completedEver == 0, true
}
