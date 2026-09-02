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

// parseRates streams one MRF (by URL, or from fixturePath when set) into
// rates/providers/codes Parquet keyed by fileID, upserts billing codes, updates
// index_files status, and writes a coverage_log row describing what the file gave.
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
	networkAllow func(string) bool,
	totalBillingCodesBefore int,
	dryRun bool,
) *mrfResult {
	log.Printf("⚙️ Processing Rate File [id=%d]: %s", fileID, orFixture(url, fixturePath))

	if !dryRun && conn != nil {
		if _, err := conn.Exec(ctx, "UPDATE index_files SET status = 'processing' WHERE id = $1", fileID); err != nil {
			log.Printf("⚠️ Failed to mark file %d as processing: %v", fileID, err)
		}
	}

	body, contentLength, cancelDownload, err := openMRF(ctx, url, fixturePath)
	if err != nil {
		markFailed(ctx, conn, fileID, err, dryRun)
		return nil
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
				return nil
			}
		}
		fanout = newPriceFanout(filepath.Join(scratchDir, "prices"), name)
		gsW, gsC, err := newParquetWriter[core.GroupSetMemberRow](groupSetsScratch)
		if err != nil {
			markFailed(ctx, conn, fileID, err, dryRun)
			return nil
		}
		provW, provC, err := newParquetWriter[core.ProviderRow](provScratch)
		if err != nil {
			markFailed(ctx, conn, fileID, err, dryRun)
			return nil
		}
		codesW, codesC, err := newParquetWriter[core.BillingCodeRow](codesScratch)
		if err != nil {
			markFailed(ctx, conn, fileID, err, dryRun)
			return nil
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
		return nil
	}
	defer gz.Close()

	log.Println("  🔄 Starting single-pass extract...")
	res, err := streamMRF(gz, planName, int64(fileID), isFirstFile, seenBillingCodes, seenNPIs, seenTINs, gaNPIs, networkAllow, w, pr)
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
		return nil
	}

	// Stream succeeded — move the scratch parquet into place.
	if !dryRun {
		if err := fanout.promote(core.PricesOutputDir); err != nil {
			markFailed(ctx, conn, fileID, err, dryRun)
			return nil
		}
		for _, mv := range [][2]string{
			{groupSetsScratch, filepath.Join(core.GroupSetsOutputDir, name)},
			{provScratch, filepath.Join(core.ProvidersOutputDir, name)},
			{codesScratch, filepath.Join(core.CodesOutputDir, name)},
		} {
			if err := os.MkdirAll(filepath.Dir(mv[1]), os.ModePerm); err != nil {
				markFailed(ctx, conn, fileID, err, dryRun)
				return nil
			}
			if err := os.Rename(mv[0], mv[1]); err != nil {
				markFailed(ctx, conn, fileID, fmt.Errorf("promote %s: %w", mv[1], err), dryRun)
				return nil
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
		if networkAllow != nil {
			parts = append(parts, "ga-network-filtered")
		}
		if gaNPIs != nil {
			parts = append(parts, "ga-npi-filtered")
		}
		note := strings.Join(parts, ",")
		writeCoverageLog(ctx, conn, fileID, orFixture(url, fixturePath), contentLength,
			totalBillingCodesBefore, note, res)
	}

	if (gaNPIs != nil || networkAllow != nil) && (res.PriceRowsDropped > 0 || res.ProviderRowsDropped > 0) {
		log.Printf("  🗺️  GA filter dropped %d provider rows, %d price rows, %d groups (%d non-GA-network) — kept %d / %d price rows",
			res.ProviderRowsDropped, res.PriceRowsDropped, res.GroupsDropped, res.GroupsDroppedNetwork,
			res.PriceRows, res.PriceRows+res.PriceRowsDropped)
	}
	log.Printf("  ✅ Completed. %d provider rows | %d price rows | %d group-set edges (%d sets) | %d new codes | %d new NPIs | networks=%v",
		res.ProviderRows, res.PriceRows, res.GroupSetMemberRows, res.GroupSets,
		res.NewBillingCodes, res.NewNPIs, sortedKeys(res.NetworkNames))
	return res
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
// re-parse replaces the file's prior row (DELETE + INSERT in one statement) so
// coverage_log stays one-row-per-file — the `make cov-report` sanity check keys
// on that to spot distinct files that parsed to identical counts (issue #52).
func writeCoverageLog(ctx context.Context, conn *pgx.Conn, fileID int, location string, compressedBytes int64, totalCodesBefore int, note string, res *mrfResult) {
	if note == "" {
		note = "unfiltered"
	}
	if res.PriceRowsDropped > 0 || res.ProviderRowsDropped > 0 {
		note = fmt.Sprintf("%s; dropped %d price + %d provider rows, %d groups",
			note, res.PriceRowsDropped, res.ProviderRowsDropped, res.GroupsDropped)
	}
	_, err := conn.Exec(ctx, `
		WITH prior AS (DELETE FROM coverage_log WHERE file_id = $1)
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
		FROM index_files i WHERE i.id = $1`,
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
	FileIDs     []int  // explicit index_files ids; empty = pull from the pending queue
	Priority    bool   // order the queue GA / individual-market first
	Limit       int    // cap files processed (0 = no cap)
	Fixture     string // read a local *.json.gz instead of downloading (needs exactly one FileID)
	AllNPIs     bool   // keep every NPI/rate (disable the GA NPPES filter)
	Networks    string // network_name allowlist; "" = default "GA *"
	AllNetworks bool   // disable the network_name allowlist entirely
	DryRun      bool   // stream only, no writes
}

type pendingFile struct {
	ID       int
	Location string
	PlanName string
}

func pendingFiles(ctx context.Context, conn *pgx.Conn, opts Options) ([]pendingFile, error) {
	var query string
	var args []any
	if len(opts.FileIDs) > 0 {
		query = `SELECT id, location, COALESCE(array_to_string(market_types, ' | '), '') FROM index_files WHERE id = ANY($1) ORDER BY id`
		args = []any{opts.FileIDs}
	} else {
		order := `file_size_bytes ASC NULLS LAST, id`
		if opts.Priority {
			order = gaPriorityExpr + ` DESC, file_size_bytes ASC NULLS LAST, id`
		}
		query = `SELECT id, location, COALESCE(array_to_string(market_types, ' | '), '') FROM index_files WHERE status = 'pending' ORDER BY ` + order
		if opts.Limit > 0 {
			query += ` LIMIT $1`
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

	files, err := pendingFiles(ctx, conn, opts)
	if err != nil {
		return fmt.Errorf("query pending files: %w", err)
	}
	if len(files) == 0 {
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

	// Network allowlist. Default 'GA *' unless overridden. NOT applied to
	// anthem/GA_* plan-specific files unless the user set -networks (their
	// network_name labels vary wildly). See etl/parse.md.
	networksSpec := opts.Networks
	userSetNetworks := opts.Networks != ""
	if opts.AllNetworks {
		networksSpec = ""
		log.Println("⚠️ -all-networks — keeping every network (network_name allowlist disabled)")
	} else if networksSpec == "" {
		networksSpec = "GA *"
	}
	networkAllow := buildNetworkAllow(networksSpec)
	if networkAllow != nil {
		log.Printf("🗺️  network allowlist active — keeping only network_name in {%s} (skipped for anthem/GA_* files unless -networks is set)", networksSpec)
	}

	totalCodes := 0
	if !opts.DryRun {
		conn.QueryRow(ctx, "SELECT count(*) FROM billing_codes").Scan(&totalCodes)
	}

	for i, f := range files {
		fileNetworkAllow := networkAllow
		if !userSetNetworks && isGAPlanSpecific(f.Location) {
			fileNetworkAllow = nil // trust the GA_* filename; keep every network
		}
		res := parseRates(ctx, conn, f.ID, f.Location, f.PlanName, opts.Fixture,
			i == 0, seenBillingCodes, seenNPIs, seenTINs, gaNPIs, fileNetworkAllow, totalCodes, opts.DryRun)
		if res != nil {
			totalCodes += res.NewBillingCodes
		}
	}
	if !opts.DryRun {
		writeNPILookup(seenNPIs)
	}
	return nil
}
