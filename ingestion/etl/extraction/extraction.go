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
	"slices"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	parquet "github.com/parquet-go/parquet-go"
	"github.com/wmespi/honest-healthcare/ingestion/etl/core"
)

// mrfHTTPClient fetches MRFs. There is deliberately no Client.Timeout — a
// multi-GB body legitimately streams for hours — but the connection setup and
// the wait for response headers are bounded so a dead endpoint fails fast
// instead of hanging the run (issue #52). A mid-body stall is still caught
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

// openMRF returns a reader for the raw (still gzipped) MRF bytes, its size, when
// Anthem last updated the copy being read (its Last-Modified; zero for a fixture
// or a response without one), and a cancel func that aborts the in-flight
// request (a no-op for a fixture). A fixturePath reads from disk (offline);
// otherwise it GETs url under a cancellable context so watchStall can kill a
// dead transfer.
func openMRF(ctx context.Context, url, fixturePath string) (io.ReadCloser, int64, time.Time, context.CancelFunc, error) {
	if fixturePath != "" {
		f, err := os.Open(fixturePath)
		if err != nil {
			return nil, 0, time.Time{}, nil, err
		}
		fi, _ := f.Stat()
		return f, fi.Size(), time.Time{}, func() {}, nil
	}
	reqCtx, cancel := context.WithCancel(ctx)
	req, err := http.NewRequestWithContext(reqCtx, "GET", url, nil)
	if err != nil {
		cancel()
		return nil, 0, time.Time{}, nil, err
	}
	resp, err := mrfHTTPClient.Do(req)
	if err != nil {
		cancel()
		return nil, 0, time.Time{}, nil, err
	}
	if resp.StatusCode != 200 {
		resp.Body.Close()
		cancel()
		return nil, 0, time.Time{}, nil, fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	updated, _ := lastModifiedHeader(resp.Header)
	return resp.Body, resp.ContentLength, updated, cancel, nil
}

// lastModified asks Anthem when it last updated the file at url: a HEAD
// request's Last-Modified.
func lastModified(ctx context.Context, url string) (time.Time, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodHead, url, nil)
	if err != nil {
		return time.Time{}, err
	}
	resp, err := mrfHTTPClient.Do(req)
	if err != nil {
		return time.Time{}, err
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return time.Time{}, fmt.Errorf("HEAD: HTTP %d", resp.StatusCode)
	}
	return lastModifiedHeader(resp.Header)
}

// lastModifiedHeader reads a response's Last-Modified, and errors when it is
// missing or malformed.
func lastModifiedHeader(h http.Header) (time.Time, error) {
	v := h.Get("Last-Modified")
	if v == "" {
		return time.Time{}, errors.New("no Last-Modified header")
	}
	return http.ParseTime(v)
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
// reconciled against Content-Length. The wording tells a truncated download,
// worth retrying, from genuine corruption ("corrupt gzip") in the parse record's
// reason. Issue #52.
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

// parseRates streams one MRF (by its location, or from fixturePath when set)
// into rates/providers/codes Parquet keyed by its file ID, and writes its parse
// record. The second return is what became of the file (Run aggregates it).
func parseRates(
	ctx context.Context,
	f core.IndexFile,
	fixturePath string,
	isFirstFile bool,
	gaNPIs map[int64]struct{},
	pb providerProbe,
	dryRun bool,
) (*mrfResult, parseOutcome) {
	fileID := f.FileID
	log.Printf("⚙️ Processing Rate File %s: %s", f.Name, orFixture(f.Location, fixturePath))

	rec := parseRecord{FileID: fileID, Name: f.Name, Address: f.Address}
	finish := func(status string, reason error) {
		if dryRun {
			return
		}
		rec.Status = status
		if reason != nil {
			rec.Reason = reason.Error()
		}
		rec.FinishedAt = time.Now().UTC()
		if err := writeRecord(rec); err != nil {
			log.Printf("⚠️ Failed to write the parse record of %s: %v", f.Name, err)
		}
	}
	fail := func(err error) (*mrfResult, parseOutcome) {
		log.Printf("❌ %s failed: %v", f.Name, err)
		finish(statusFailed, err)
		return nil, outcomeFailed
	}

	body, contentLength, updated, cancelDownload, err := openMRF(ctx, f.Location, fixturePath)
	if err != nil {
		return fail(err)
	}
	defer body.Close()
	defer cancelDownload()
	if !updated.IsZero() {
		rec.AnthemUpdatedAt = &updated
	}

	pr := core.NewProgressReader(body, contentLength)

	// Kill a download that goes silent mid-transfer (a hung socket that never
	// resets) so the run moves on instead of blocking on this file (issue #52).
	stalled := func() bool { return false }
	if fixturePath == "" {
		var stopStall func()
		stopStall, stalled = watchStall(cancelDownload, pr, stallTimeout)
		defer stopStall()
	}

	if contentLength > 0 && fixturePath == "" {
		gb := float64(contentLength) / 1e9
		log.Printf("  📦 %.2f GB compressed | ~%.1f GB uncompressed (est. ×12)", gb, gb*12)
		rec.CompressedBytes = contentLength
	}

	// Parquet writers (skipped in dry-run). Everything for this file is written
	// under a per-file scratch dir (…/anthem/.inflight/<stem>/) and moved into place
	// only after a clean stream, so the build, which globs prices/**/*.parquet,
	// group_sets/*.parquet, providers/*.parquet and codes/*.parquet, never sees a
	// half-written or zero-byte file. Price rows fan out into
	// prices/net=<slug>/<stem>.parquet, one partition per network_name, so a
	// network-filtered query prunes to it; the deduped provider-group rosters go
	// to group_sets/<stem>.parquet. Every row carries the file's ID.
	name := f.Stem() + ".parquet"
	scratch := scratchDir(f)

	var w mrfWriters
	var closers []io.Closer
	var fanout *priceFanout
	groupSetsScratch := filepath.Join(scratch, "group_sets", name)
	provScratch := filepath.Join(scratch, "providers", name)
	codesScratch := filepath.Join(scratch, "codes", name)
	committed := false
	if !dryRun {
		defer func() {
			if !committed {
				os.RemoveAll(scratch)
			}
		}()
		for _, d := range []string{
			filepath.Join(scratch, "prices"),
			filepath.Join(scratch, "group_sets"),
			filepath.Join(scratch, "providers"),
			filepath.Join(scratch, "codes"),
		} {
			if err := os.MkdirAll(d, os.ModePerm); err != nil {
				return fail(fmt.Errorf("create dir %s: %w", d, err))
			}
		}
		fanout = newPriceFanout(filepath.Join(scratch, "prices"), name)
		gsW, gsC, err := newParquetWriter[core.GroupSetMemberRow](groupSetsScratch)
		if err != nil {
			return fail(err)
		}
		provW, provC, err := newParquetWriter[core.ProviderRow](provScratch)
		if err != nil {
			return fail(err)
		}
		codesW, codesC, err := newParquetWriter[core.BillingCodeRow](codesScratch)
		if err != nil {
			return fail(err)
		}
		closers = []io.Closer{fanoutCloser{fanout}, gsW, gsC, provW, provC, codesW, codesC}
		w = mrfWriters{
			prices: fanout.write,
			groupSetMembers: func(rows []core.GroupSetMemberRow) error {
				if _, err := gsW.Write(rows); err != nil {
					return err
				}
				return gsW.Flush()
			},
			providers: func(rows []core.ProviderRow) error {
				if _, err := provW.Write(rows); err != nil {
					return err
				}
				return provW.Flush()
			},
			code: func(row core.BillingCodeRow) error {
				_, err := codesW.Write([]core.BillingCodeRow{row})
				return err
			},
		}
	}

	gz, err := gzip.NewReader(pr)
	if err != nil {
		closeAll(closers)
		return fail(err)
	}
	defer gz.Close()

	log.Println("  🔄 Starting single-pass extract...")
	res, err := streamMRF(gz, fileID, isFirstFile, gaNPIs, pb, w, pr)

	// The probe rejected the file at the end of provider_references, before a
	// byte of in_network. Kill the transfer now — that is the whole saving — and
	// record the file as skipped rather than failed. No completeness check: the
	// body is deliberately incomplete, and nothing was written to promote.
	if errors.Is(err, errNoWantedProviders) {
		cancelDownload()
		read := pr.ReadBytes.Load()
		closeAll(closers)
		of := ""
		if contentLength > 0 {
			of = fmt.Sprintf(" of %s (%.1f%%)", humanBytes(contentLength), 100*float64(read)/float64(contentLength))
		}
		log.Printf("  ⛔ %s skipped - %v", f.Name, err)
		log.Printf("     aborted after reading %s%s", humanBytes(read), of)
		finish(statusSkipped, err)
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
	// Close the Parquet writers (each before its file) before anything is
	// promoted: a writer writes its last rows and its footer as it closes, so a
	// full disk shows up here or in the writes above, and the file fails.
	if cerr := closeAll(closers); err == nil && cerr != nil {
		err = fmt.Errorf("finish the Parquet files: %w", cerr)
	}
	if err != nil {
		return fail(err)
	}

	// Stream succeeded — move the scratch parquet into place.
	if !dryRun {
		if err := fanout.promote(core.PricesOutputDir); err != nil {
			return fail(err)
		}
		for _, mv := range [][2]string{
			{groupSetsScratch, filepath.Join(core.GroupSetsOutputDir, name)},
			{provScratch, filepath.Join(core.ProvidersOutputDir, name)},
			{codesScratch, filepath.Join(core.CodesOutputDir, name)},
		} {
			if err := os.MkdirAll(filepath.Dir(mv[1]), os.ModePerm); err != nil {
				return fail(err)
			}
			if err := os.Rename(mv[0], mv[1]); err != nil {
				return fail(fmt.Errorf("promote %s: %w", mv[1], err))
			}
		}
		os.RemoveAll(scratch)
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

	rec.PriceRows, rec.ProviderRows = int64(res.PriceRows), int64(res.ProviderRows)
	rec.PriceRowsDropped, rec.ProviderRowsDropped = int64(res.PriceRowsDropped), int64(res.ProviderRowsDropped)
	finish(statusParsed, nil)

	if gaNPIs != nil && (res.PriceRowsDropped > 0 || res.ProviderRowsDropped > 0) {
		log.Printf("  🗺️  GA NPI filter dropped %d provider rows, %d price rows, %d groups — kept %d groups, %d / %d price rows",
			res.ProviderRowsDropped, res.PriceRowsDropped, res.GroupsDropped,
			res.GroupsKept, res.PriceRows, res.PriceRows+res.PriceRowsDropped)
	}
	log.Printf("  ✅ Completed. %d provider rows | %d price rows | %d group-set edges (%d sets) | %d codes | networks=%v",
		res.ProviderRows, res.PriceRows, res.GroupSetMemberRows, res.GroupSets,
		res.BillingCodes, sortedKeys(res.NetworkNames))
	return res, outcomeCompleted
}

func orFixture(url, fixturePath string) string {
	if fixturePath != "" {
		return "fixture:" + fixturePath
	}
	return url
}

// closeAll closes each in order and returns the first error.
func closeAll(closers []io.Closer) error {
	var first error
	for _, c := range closers {
		if c == nil {
			continue
		}
		if err := c.Close(); err != nil && first == nil {
			first = err
		}
	}
	return first
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

// ── orchestration ───────────────────────────────────────────────────────────

// Options configure a parse run (the flags behind `etl parse`).
type Options struct {
	// Files are names of files in this month's index to parse whatever their
	// parse record says: a re-parse, a retry after a failure, or a file no target
	// names. Empty means the files the targets name, when they're due.
	Files []string
	// Targets is the path to the target-plan list (ingestion/etl/targets.yaml). "" needs
	// Files or Fixture, and leaves the probe without its network signal.
	Targets string
	Limit   int    // cap files processed (0 = no cap)
	Fixture string // parse a local *.json.gz instead of a file from the index
	AllNPIs bool   // keep every NPI/rate (disable the GA NPPES filter)
	// MinGroups is the probe's provider-overlap threshold: a file whose
	// provider_references leave fewer than this many provider groups is
	// abandoned before in_network. 0 disables that signal (the probe's network
	// signal, from the targets' network_patterns, is independent of it).
	MinGroups int
	DryRun    bool // stream only, no writes
}

// fixtureFile is the file a -fixture run parses. It isn't in the index, so its
// address is made from the fixture's name.
func fixtureFile(path string) core.IndexFile {
	name := filepath.Base(path)
	addr := "fixture://" + name
	return core.IndexFile{FileID: core.FileID(addr), Name: name, Address: addr}
}

// readIndex is this month's file list by name, as `make discover` wrote it.
func readIndex() (map[string][]core.IndexFile, error) {
	files, err := parquet.ReadFile[core.IndexFile](core.IndexFilesPath)
	if err != nil {
		return nil, fmt.Errorf("read %s (run `make discover`): %w", core.IndexFilesPath, err)
	}
	byName := map[string][]core.IndexFile{}
	for _, f := range files {
		byName[f.Name] = append(byName[f.Name], f)
	}
	return byName, nil
}

// readLinks is the plan IDs this month's index lists against each file.
func readLinks() (map[int64][]string, error) {
	links, err := parquet.ReadFile[core.PlanLink](core.IndexFilePlansPath)
	if err != nil {
		return nil, fmt.Errorf("read %s (run `make discover`): %w", core.IndexFilePlansPath, err)
	}
	plans := map[int64][]string{}
	for _, l := range links {
		plans[l.FileID] = append(plans[l.FileID], l.PlanID)
	}
	return plans, nil
}

// oneFile is the file a name matches in this month's index. It errors unless
// the name matches exactly one, so a mistyped name or a mirrored one can't
// stand for the wrong file.
func oneFile(byName map[string][]core.IndexFile, name string) (core.IndexFile, error) {
	switch matches := byName[name]; len(matches) {
	case 0:
		return core.IndexFile{}, fmt.Errorf("%s isn't in this month's index (%s): run `make discover`, or check the name", name, core.IndexFilesPath)
	case 1:
		return matches[0], nil
	default:
		addrs := make([]string, len(matches))
		for i, m := range matches {
			addrs[i] = m.Address
		}
		return core.IndexFile{}, fmt.Errorf("%s matches %d files in this month's index, %s; it has to match one", name, len(matches), strings.Join(addrs, ", "))
	}
}

// FindFile is the file name stands for in this month's index, for a tool that
// takes a file by name.
func FindFile(name string) (core.IndexFile, error) {
	byName, err := readIndex()
	if err != nil {
		return core.IndexFile{}, err
	}
	return oneFile(byName, name)
}

// pickNamed is the files a target names, in its order. It stops unless each
// name matches one file in this month's index and the index links that file to
// one of planIDs, the plans on the target's networks, so a file that prices
// another plan can't slip by.
func pickNamed(path string, t Target, byName map[string][]core.IndexFile, plansOf map[int64][]string, planIDs []string) ([]core.IndexFile, error) {
	var picked []core.IndexFile
	for _, name := range t.Files {
		f, err := oneFile(byName, name)
		if err != nil {
			return nil, fmt.Errorf("%s: target %q: %w", path, t.Name, err)
		}
		if !linksOneOf(plansOf[f.FileID], planIDs) {
			return nil, fmt.Errorf("%s: target %q names %s, but the index doesn't link it to a plan on %s, so it doesn't price the target's plans",
				path, t.Name, f.Name, strings.Join(t.ExchangeNetworks, ", "))
		}
		picked = append(picked, f)
	}
	return picked, nil
}

// linksOneOf says whether any of a file's plans is in planIDs, which is sorted.
func linksOneOf(plans, planIDs []string) bool {
	for _, id := range plans {
		if _, ok := slices.BinarySearch(planIDs, id); ok {
			return true
		}
	}
	return false
}

// selectFiles is the files this run reads: the fixture, the -files, or the files
// the targets name. For targets, it also returns named, every file they name
// whether due or not, for the end-of-run guard.
func selectFiles(ctx context.Context, opts Options, targets *TargetSet, planIDs map[string][]string) (files, named []core.IndexFile, err error) {
	if opts.Fixture != "" {
		if len(opts.Files) > 0 {
			return nil, nil, fmt.Errorf("-fixture parses a local file, so it takes no -files")
		}
		return []core.IndexFile{fixtureFile(opts.Fixture)}, nil, nil
	}
	byName, err := readIndex()
	if err != nil {
		return nil, nil, err
	}
	// -files bypasses target selection and the parse record: whoever names a file
	// has decided it should be read now.
	if len(opts.Files) > 0 {
		for _, name := range opts.Files {
			f, err := oneFile(byName, name)
			if err != nil {
				return nil, nil, err
			}
			if err := stopIfStopped(f); err != nil {
				return nil, nil, err
			}
			files = append(files, f)
		}
		return files, nil, nil
	}
	plansOf, err := readLinks()
	if err != nil {
		return nil, nil, err
	}
	seen := map[int64]bool{}
	for _, t := range targets.Targets {
		picked, err := pickNamed(targets.Path, t, byName, plansOf, planIDs[t.Name])
		if err != nil {
			return nil, nil, err
		}
		for _, f := range picked {
			if seen[f.FileID] {
				continue
			}
			seen[f.FileID] = true
			named = append(named, f)
			if err := stopIfStopped(f); err != nil {
				return nil, nil, err
			}
			due, err := dueForParse(ctx, f)
			if err != nil {
				return nil, nil, err
			}
			if due {
				files = append(files, f)
			}
		}
	}
	if opts.Limit > 0 && len(files) > opts.Limit {
		files = files[:opts.Limit]
	}
	return files, named, nil
}

// dueForParse says whether parse downloads a named file: when it has no parse
// record, and when it was parsed or skipped only if Anthem has updated it since
// the copy that parse read, which it asks Anthem with a HEAD request. A failed
// file waits until someone has looked at why and names it (Critical Rule 2).
func dueForParse(ctx context.Context, f core.IndexFile) (bool, error) {
	rec, err := readRecord(f)
	if err != nil {
		return false, err
	}
	if rec == nil {
		return true, nil
	}
	// A file at a new address is a new file, whatever became of the old one.
	if rec.Address != f.Address {
		log.Printf("🔄 %s was last read from %s, and this month's index lists it at %s, so it's downloaded", f.Name, rec.Address, f.Address)
		return true, nil
	}
	if rec.Status == statusFailed {
		log.Printf("🎯 %s failed on %s: %s. It isn't downloaded again until someone looks at why and runs `make parse FILE=%s`",
			f.Name, rec.FinishedAt.UTC().Format(time.DateTime), rec.Reason, f.Name)
		return false, nil
	}
	anthem, err := lastModified(ctx, f.Location)
	if err != nil {
		return false, fmt.Errorf("%s was %s, and parse can't tell whether Anthem has updated it since: %w", f.Name, rec.Status, err)
	}
	if !changedSince(rec.AnthemUpdatedAt, anthem) {
		log.Printf("🎯 %s was %s, and Anthem hasn't updated it since (%s), so it isn't downloaded",
			f.Name, rec.Status, anthem.UTC().Format(time.DateTime))
		return false, nil
	}
	read := "an earlier copy"
	if rec.AnthemUpdatedAt != nil {
		read = "the copy from " + rec.AnthemUpdatedAt.UTC().Format(time.DateTime)
	}
	log.Printf("🔄 %s was %s, but Anthem updated it on %s and parse read %s, so it's downloaded again",
		f.Name, rec.Status, anthem.UTC().Format(time.DateTime), read)
	return true, nil
}

// Run is `etl parse`: stream the files targets.yaml names into Parquet.
func Run(ctx context.Context, opts Options) error {
	log.Println("🚀 Starting parse: Anthem rate files → Parquet...")

	// Which plans are we pricing? Loaded even for a -files run so a broken
	// targets.yaml fails loudly at the start rather than on the next run.
	targets, err := LoadTargets(opts.Targets)
	if err != nil {
		return err
	}
	chosen := len(opts.Files) > 0 || opts.Fixture != ""
	switch {
	case targets == nil && !chosen:
		return fmt.Errorf(`no targets file and no -files: parse downloads only the files ingestion/etl/targets.yaml or -files names`)
	case targets == nil:
		log.Println("⚠️ no targets file — the probe has no network signal")
	case chosen:
		log.Printf("🎯 targets %s loaded - not used to select (-files or -fixture given), still used by the probe", targets.Path)
	default:
		log.Printf("🎯 target plans from %s: %s", targets.Path, strings.Join(targets.Names(), ", "))
	}

	// The files a target names have to price a plan on its exchange networks, so
	// each target's plans come from Georgia's plan file. A run that doesn't
	// select doesn't need that file.
	var planIDs map[string][]string
	if targets != nil && !chosen {
		if planIDs, err = targets.PlanIDs(core.ExchangePlansPath); err != nil {
			return err
		}
		log.Printf("🎯 %d plan(s) on the target exchange networks", len(allPlanIDs(planIDs)))
	}

	files, named, err := selectFiles(ctx, opts, targets, planIDs)
	if err != nil {
		return fmt.Errorf("select files: %w", err)
	}
	if len(files) == 0 {
		log.Printf("✅ Nothing to download: every file %s names is parsed and current, or failed and waiting to be looked at.", targets.Path)
		return nil
	}
	log.Printf("Found %d file(s) to download. Processing...", len(files))

	if opts.DryRun {
		log.Println("🔍 Dry-run mode - streaming only, no writes.")
	}

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
	// chosen (a -files re-parse included): the question "does this file price
	// anyone on a target plan?" doesn't depend on how it was chosen.
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

	outcomes := make(map[parseOutcome]int, 4)
	for i, f := range files {
		_, outcome := parseRates(ctx, f, opts.Fixture, i == 0, gaNPIs, pb, opts.DryRun)
		outcomes[outcome]++
	}
	return guardAgainstSilentSkip(opts, targets, named, outcomes)
}

// guardAgainstSilentSkip turns the probe's quietest failure mode — every file
// a target names abandoned on the *network* signal, none completing - into a
// loud one. That is what a stale `network_patterns` in targets.yaml looks like:
// the one MRF that actually carries the plan's rates gets skipped because
// Anthem renamed the network, and without this the run exits 0 with nothing
// ingested.
//
// It fires only for a target-selected run with the network signal active (a
// -files re-parse or a probe with no network_patterns can't hit this mode). If
// no file a target names has Parquet on disk, the run fails; if some do, the
// build still has rates, so it is a warning - but a renamed network still
// means today's rates are stale, so it is a loud one.
func guardAgainstSilentSkip(opts Options, targets *TargetSet, named []core.IndexFile, outcomes map[parseOutcome]int) error {
	if opts.DryRun || named == nil || targets == nil || targets.NetworkMatcher() == nil {
		return nil
	}
	netSkipped := outcomes[outcomeSkippedNetwork]
	if netSkipped == 0 || outcomes[outcomeCompleted] > 0 {
		return nil
	}

	completedEver := 0
	for _, f := range named {
		if _, err := os.Stat(filepath.Join(core.CodesOutputDir, f.Stem()+".parquet")); err == nil {
			completedEver++
		}
	}

	fatal, warn := silentSkipVerdict(netSkipped, outcomes[outcomeCompleted], completedEver)
	if !warn {
		return nil
	}

	log.Printf("╔══════════════════════════════════════════════════════════════════════")
	log.Printf("║ 🛑 PROBE SKIPPED EVERY TARGET FILE ON THE NETWORK SIGNAL")
	log.Printf("║ %d file(s) %s names were abandoned because no", netSkipped, targets.Path)
	log.Printf("║ provider_references network_name matched {%s}, and none completed.", strings.Join(targets.NetworkPatterns(), ", "))
	log.Printf("║ If a plan's rates now land under a network Anthem renamed, the")
	log.Printf("║ network_patterns in ingestion/etl/targets.yaml is stale - check the abort")
	log.Printf("║ messages above for the labels the files actually carry.")
	log.Printf("╚══════════════════════════════════════════════════════════════════════")

	if fatal {
		return fmt.Errorf("probe skipped every target file on the network signal and none has ever been parsed - "+
			"ingestion/etl/targets.yaml network_patterns is stale, or names the wrong file (%d skipped)", netSkipped)
	}
	log.Printf("⚠️ %d target file(s) were parsed in an earlier run, so the build still has rates - "+
		"but if a network was renamed those rates are now stale.", completedEver)
	return nil
}

// silentSkipVerdict decides what an end-of-run probe tally means. It is fatal
// only when the network signal skipped files, nothing completed this run, and
// no target file has *ever* been parsed (completedEver == 0) - a first run that
// found nothing, which is a stale pattern or the wrong file. If earlier runs did
// land data (completedEver > 0), it is a warning: the build still has rates,
// but a renamed network means they are stale. Not warned at all when the
// signal never fired or something did complete.
func silentSkipVerdict(netSkipped, completedThisRun, completedEver int) (fatal, warn bool) {
	if netSkipped == 0 || completedThisRun > 0 {
		return false, false
	}
	return completedEver == 0, true
}
