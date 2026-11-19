package discovery

import (
	"bufio"
	"cmp"
	"compress/gzip"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/wmespi/honest-healthcare/ingestion/etl/core"
)

// Options configure a discover run (the flags behind `etl discover`).
type Options struct {
	// Limit reads only the first Limit reporting structures, so main allows it
	// only in test mode or with SchemaOnly.
	Limit int
	// KeepIndex keeps the downloaded master index after the run, so the next run
	// of the same month reads it from disk; NoCache downloads it again anyway.
	// Both are for Anthem's index, the one big enough to be kept.
	KeepIndex, NoCache bool
	SchemaOnly         bool // stream the index, write index_schema.json and nothing else
	// IndexURL overrides the payer's index URL; it names one payer's index, so
	// main allows it for one payer only.
	IndexURL string
}

// source is where a payer's master index is.
type source struct {
	// url is this month's index URL.
	url func() (string, error)
	// cached says the index is big enough to download in parallel Range
	// requests to disk first (Anthem's is several GB), rather than stream it.
	cached bool
}

// sources are the payers' index sources, by Payer.Name.
var sources = map[string]source{
	"anthem":   {url: anthemIndexURL, cached: true},
	"ambetter": {url: ambetterIndexURL},
	"alliant":  {url: alliantIndexURL},
	"oscar":    {url: oscarIndexURL},
}

// anthemIndexURL is the current month's Anthem master-index URL.
func anthemIndexURL() (string, error) {
	now := time.Now()
	return fmt.Sprintf("https://antm-pt-prod-dataz-nogbd-nophi-us-east1.s3.amazonaws.com/anthem/%04d-%02d-01_anthem_index.json.gz", now.Year(), now.Month()), nil
}

// oscarIndexURL is the current month's Oscar master-index URL: a plain JSON file
// at a dated link, `<YYYYMMDD>_oscar_index.json` on the first of the month.
func oscarIndexURL() (string, error) { return oscarIndexURLAt(time.Now()), nil }

func oscarIndexURLAt(now time.Time) string {
	return fmt.Sprintf("https://hioscar-cms-tic-us-east-1.s3.amazonaws.com/oscar/%04d%02d01_oscar_index.json", now.Year(), now.Month())
}

// ambetterPage is Centene's price transparency page. It links the dated index
// (`..._ambetter_index.json`), whose date changes every month.
const ambetterPage = "https://www.centene.com/price-transparency-files.html"

var ambetterIndexLink = regexp.MustCompile(`[^"'\s<>=]*_ambetter_index\.json`)

// alliantPage is Alliant's price transparency entry: a meta-refresh page whose
// link is the dated index (`..._alliant-health-plans_index_nlc.json`), whose date
// changes every month.
const alliantPage = "https://alliantplans.com/json/pt/latestpt_nlc.html"

var alliantIndexLink = regexp.MustCompile(`[^"'\s<>=]*_alliant-health-plans_index_nlc\.json`)

func ambetterIndexURL() (string, error) { return linkedIndexURL(ambetterPage, ambetterIndexLink) }
func alliantIndexURL() (string, error)  { return linkedIndexURL(alliantPage, alliantIndexLink) }

// linkedIndexURL finds this month's index on a payer's page: the newest link
// that matches link.
func linkedIndexURL(pageURL string, link *regexp.Regexp) (string, error) {
	resp, err := core.Get(pageURL)
	if err != nil {
		return "", fmt.Errorf("read %s: %w", pageURL, err)
	}
	defer resp.Body.Close()
	page, err := io.ReadAll(io.LimitReader(resp.Body, 32<<20))
	if err != nil {
		return "", fmt.Errorf("read %s: %w", pageURL, err)
	}
	return pickIndex(pageURL, string(page), link)
}

// pickIndex is the newest index a page links (by link's pattern), resolved
// against the page's own URL.
func pickIndex(pageURL, page string, link *regexp.Regexp) (string, error) {
	base, err := url.Parse(pageURL)
	if err != nil {
		return "", err
	}
	var links []string
	for _, m := range link.FindAllString(page, -1) {
		ref, err := url.Parse(strings.ReplaceAll(m, " ", "%20"))
		if err != nil {
			continue
		}
		links = append(links, base.ResolveReference(ref).String())
	}
	if len(links) == 0 {
		return "", fmt.Errorf("%s has no link matching %s, so there is no index to read", pageURL, link)
	}
	// The date prefix of the name sorts them, so the last is the newest.
	slices.SortFunc(links, func(a, b string) int { return strings.Compare(path.Base(a), path.Base(b)) })
	return links[len(links)-1], nil
}

// indexCachePaths are where the downloaded master index and its URL are kept.
func indexCachePaths(p core.Payer) (cachePath, cacheURLPath string) {
	return filepath.Join(p.RawDir(), "index_cache.json.gz"), filepath.Join(p.RawDir(), "index_cache_url.txt")
}

// ensureIndexCached downloads the master index at indexURL to disk unless a
// copy of this month's is already there, from a run with KeepIndex or one that
// stopped part way, then returns the local path.
func ensureIndexCached(p core.Payer, indexURL string, noCache bool) (string, error) {
	cachePath, cacheURLPath := indexCachePaths(p)

	if !noCache {
		if data, err := os.ReadFile(cacheURLPath); err == nil && strings.TrimSpace(string(data)) == indexURL {
			if _, err := os.Stat(cachePath); err == nil {
				log.Printf("📦 Cache hit - skipping download, reading from disk")
				return cachePath, nil
			}
		}
	}

	os.MkdirAll(p.RawDir(), os.ModePerm)
	tmpPath := cachePath + ".tmp"

	if err := downloadParallel(indexURL, tmpPath, 8); err != nil {
		return "", fmt.Errorf("download %s: %w", indexURL, err)
	}

	if err := os.Rename(tmpPath, cachePath); err != nil {
		return "", fmt.Errorf("finalize the downloaded index: %w", err)
	}
	os.WriteFile(cacheURLPath, []byte(indexURL), 0644)
	log.Printf("💾 Downloaded to %s", filepath.Base(cachePath))
	return cachePath, nil
}

// dropIndex deletes the downloaded master index once a run has read it, since
// it's several GB and only needed during a run.
func dropIndex(p core.Payer) {
	cachePath, cacheURLPath := indexCachePaths(p)
	size := int64(0)
	if fi, err := os.Stat(cachePath); err == nil {
		size = fi.Size()
	}
	if err := os.Remove(cachePath); err != nil && !os.IsNotExist(err) {
		log.Printf("⚠️ Couldn't delete the downloaded index %s: %v", cachePath, err)
		return
	}
	os.Remove(cacheURLPath)
	log.Printf("🧹 Deleted the downloaded index (%.1f GB); KEEP_INDEX=1 keeps it for the next run", float64(size)/1e9)
}

// downloadParallel fetches url into destPath using n concurrent Range requests.
// Falls back to a single stream if the server doesn't advertise Range support.
func downloadParallel(url, destPath string, workers int) error {
	// HEAD first: get total size and confirm Range support.
	head, err := (&http.Client{Timeout: 30 * time.Second}).Head(url)
	if err != nil {
		return fmt.Errorf("HEAD failed: %w", err)
	}
	head.Body.Close()

	totalSize := head.ContentLength
	if totalSize <= 0 || head.Header.Get("Accept-Ranges") != "bytes" {
		log.Printf("⬇️  Range not supported, falling back to single stream...")
		return core.DownloadToFile(url, destPath)
	}

	log.Printf("⬇️  Parallel download: %d workers, %.1f MB", workers, float64(totalSize)/1024/1024)

	// Pre-allocate the full file so concurrent WriteAt calls are safe.
	f, err := os.Create(destPath)
	if err != nil {
		return err
	}
	if err := f.Truncate(totalSize); err != nil {
		f.Close()
		os.Remove(destPath)
		return err
	}

	chunkSize := totalSize / int64(workers)
	byteDone := make([]atomic.Int64, workers)

	var wg sync.WaitGroup
	errCh := make(chan error, workers)

	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			start := int64(i) * chunkSize
			end := start + chunkSize - 1
			if i == workers-1 {
				end = totalSize - 1
			}
			req, _ := http.NewRequest("GET", url, nil)
			req.Header.Set("Range", fmt.Sprintf("bytes=%d-%d", start, end))
			resp, err := (&http.Client{}).Do(req)
			if err != nil {
				errCh <- err
				return
			}
			defer resp.Body.Close()

			buf := make([]byte, 256*1024)
			offset := start
			for {
				n, readErr := resp.Body.Read(buf)
				if n > 0 {
					if _, writeErr := f.WriteAt(buf[:n], offset); writeErr != nil {
						errCh <- writeErr
						return
					}
					offset += int64(n)
					byteDone[i].Add(int64(n))
				}
				if readErr == io.EOF {
					break
				}
				if readErr != nil {
					errCh <- readErr
					return
				}
			}
		}(i)
	}

	// Progress ticker while workers run.
	dlStart := time.Now()
	quit := make(chan struct{})
	go func() {
		ticker := time.NewTicker(10 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ticker.C:
				var done int64
				for i := range byteDone {
					done += byteDone[i].Load()
				}
				if done > 0 {
					pct := float64(done) / float64(totalSize) * 100
					elapsed := time.Since(dlStart).Seconds()
					eta := time.Duration(float64(totalSize-done)/float64(done)*elapsed) * time.Second
					filled := int(pct / 100 * 15)
					bar := strings.Repeat("█", filled) + strings.Repeat("░", 15-filled)
					log.Printf("  ⬇️  [%s] %5.1f%% (%.0f/%.0f MB) | ETA: %v",
						bar, pct, float64(done)/1024/1024, float64(totalSize)/1024/1024, eta.Round(time.Second))
				}
			case <-quit:
				return
			}
		}
	}()

	wg.Wait()
	close(quit)
	f.Close()

	select {
	case err := <-errCh:
		os.Remove(destPath)
		return err
	default:
		return nil
	}
}

// RunAll is `etl discover`: it reads each payer's index in turn. A payer whose
// index can't be read keeps the lists its last run wrote and is named in a
// banner at the end, and the others are still read; it errors only when no
// payer's index was read.
func RunAll(payers []core.Payer, opts Options) error {
	var failed []string
	for _, p := range payers {
		if err := Run(p, opts); err != nil {
			log.Printf("❌ %s: %v", p.Label, err)
			failed = append(failed, fmt.Sprintf("%s: %v", p.Name, err))
		}
	}
	if len(failed) == 0 {
		return nil
	}
	log.Printf("╔══════════════════════════════════════════════════════════════════════")
	log.Printf("║ ⚠️  INDEXES THAT COULDN'T BE READ")
	for _, f := range failed {
		log.Printf("║ %s", f)
	}
	log.Printf("║ Each of these payers keeps the file list and plan links of its last run.")
	log.Printf("╚══════════════════════════════════════════════════════════════════════")
	if len(failed) == len(payers) {
		return fmt.Errorf("no payer's index was read")
	}
	return nil
}

// index is an open master index: the JSON, gunzipped when its body starts with
// the gzip magic bytes whatever its name says.
type index struct {
	r     io.Reader // the JSON
	pr    *core.ProgressReader
	total int64 // the body's size, 0 when unknown
	gz    *gzip.Reader
	body  io.Closer
}

// openIndex opens the payer's index at indexURL, from the downloaded copy for a
// cached source and otherwise straight off the network.
func openIndex(p core.Payer, src source, indexURL string, opts Options) (*index, error) {
	var body io.ReadCloser
	var total int64
	if src.cached {
		path, err := ensureIndexCached(p, indexURL, opts.NoCache)
		if err != nil {
			return nil, err
		}
		f, err := os.Open(path)
		if err != nil {
			return nil, fmt.Errorf("open the downloaded index: %w", err)
		}
		fi, _ := f.Stat()
		body, total = f, fi.Size()
	} else {
		resp, err := core.Get(indexURL)
		if err != nil {
			return nil, fmt.Errorf("download %s: %w", indexURL, err)
		}
		body, total = resp.Body, resp.ContentLength
	}
	ix := &index{pr: core.NewProgressReader(body, total), total: total, body: body}
	br := bufio.NewReader(ix.pr)
	ix.r = br
	if magic, err := br.Peek(2); err == nil && magic[0] == 0x1f && magic[1] == 0x8b {
		gz, err := gzip.NewReader(br)
		if err != nil {
			body.Close()
			return nil, fmt.Errorf("unzip the index: %w", err)
		}
		ix.gz, ix.r = gz, gz
	}
	return ix, nil
}

// finish confirms the whole body arrived once the JSON has been read: the rest
// is drained, which makes gzip check its trailer and the HTTP layer report a
// short body, and the bytes read are reconciled against the size.
func (ix *index) finish() error {
	if _, err := io.Copy(io.Discard, ix.r); err != nil {
		return fmt.Errorf("the index ended early: %w", err)
	}
	if ix.total > 0 {
		if read := ix.pr.ReadBytes.Load(); read < ix.total {
			return fmt.Errorf("short read: got %d of %d bytes of the index", read, ix.total)
		}
	}
	return nil
}

func (ix *index) close() {
	if ix.gz != nil {
		ix.gz.Close()
	}
	ix.body.Close()
}

// Run reads one payer's master index and writes the files it lists
// (index_files.parquet) and the Georgia individual-market plans each one
// prices (index_file_plans.parquet), or only its schema example with
// SchemaOnly. When it errors it has written nothing, so the payer keeps the
// lists of its last run.
func Run(p core.Payer, opts Options) error {
	limit, schemaOnly := opts.Limit, opts.SchemaOnly
	src, ok := sources[p.Name]
	if !ok {
		return fmt.Errorf("no index source for payer %q", p.Name)
	}
	if schemaOnly {
		log.Printf("🚀 Starting discover: %s's index schema only...", p.Label)
	} else {
		log.Printf("🚀 Starting discover: %s's index → this month's file list...", p.Label)
	}

	indexURL := opts.IndexURL
	if indexURL == "" {
		var err error
		if indexURL, err = src.url(); err != nil {
			return err
		}
	}
	log.Printf("📥 %s", indexURL)
	ix, err := openIndex(p, src, indexURL, opts)
	if err != nil {
		return err
	}
	defer ix.close()
	pr := ix.pr
	decoder := json.NewDecoder(ix.r)

	// files holds each file the index lists, by address, and links each
	// (file, plan ID) pair once. The index publishes reporting_plans[] ×
	// in_network_files[], tens of millions of pairs, but only a few thousand
	// files and, kept to Georgia's individual market, a few hundred thousand
	// distinct pairs, so neither grows with the cross-product (Critical Rule 3).
	files := map[string]core.IndexFile{}
	links := map[core.PlanLink]struct{}{}
	// truncated is set when the limit stops the read with reporting structures
	// left, so the run saw only part of the index.
	truncated := false

	schemaExample := map[string]interface{}{}
	count := 0
	capturedSchema := false

	tok, err := decoder.Token()
	if err != nil {
		return fmt.Errorf("read the index's root token: %w", err)
	}
	if delim, ok := tok.(json.Delim); !ok || delim != '{' {
		return fmt.Errorf("the index doesn't start with '{', but %v", tok)
	}

	processRS := func(rs core.ReportingStructure) {
		for _, f := range rs.InNetworkFiles {
			if f.Location == "" {
				continue
			}
			addr := core.FileAddress(f.Location)
			file, seen := files[addr]
			if !seen {
				file = core.IndexFile{FileID: core.FileID(addr), Name: core.FileName(addr), Address: addr, Location: f.Location}
				files[addr] = file
			}
			for _, plan := range rs.ReportingPlans {
				// Only the project's scope, Georgia individual-market plans
				// (docs/direction.md): almost all of the cross-product is employer
				// plans nothing here selects. The boundary is the market or the
				// state in the HIOS plan ID, not today's targets.yaml, so a new
				// target never needs a new discover.
				if strings.EqualFold(plan.PlanMarketType, "individual") || hiosStateCode(plan) == "GA" {
					links[core.PlanLink{FileID: file.FileID, PlanID: planID(plan)}] = struct{}{}
				}
			}
		}
	}

	for decoder.More() {
		kt, err := decoder.Token()
		if err != nil {
			return fmt.Errorf("read the index's root key: %w", err)
		}
		key, _ := kt.(string)

		if key == "reporting_structure" {
			log.Printf("  🎯 Found 'reporting_structure'. Streaming files... %s", pr.GetProgressString())
			decoder.Token() // '['

			for decoder.More() {
				count++

				if !capturedSchema {
					// Decode first item to raw bytes — unmarshal twice so we capture
					// both the schema example and the typed struct in one read.
					var raw json.RawMessage
					if err := decoder.Decode(&raw); err != nil {
						return fmt.Errorf("read reporting_structure[0]: %w", err)
					}
					var schemaItem interface{}
					json.Unmarshal(raw, &schemaItem)
					schemaExample[key] = []interface{}{schemaItem}

					var rs core.ReportingStructure
					json.Unmarshal(raw, &rs)
					processRS(rs)
					capturedSchema = true
				} else {
					var rs core.ReportingStructure
					if err := decoder.Decode(&rs); err != nil {
						// A stream that broke stays broken, so stop rather than skip:
						// the lists of the last run are better than a partial one.
						return fmt.Errorf("read reporting_structure[%d]: %w", count-1, err)
					}
					processRS(rs)
				}

				if count%1000 == 0 {
					log.Printf("  Scanned %d reporting structures... %d unique files. %s", count, len(files), pr.GetProgressString())
				}
				if limit > 0 && count >= limit {
					log.Printf("🛑 Limit of %d reached.", limit)
					for decoder.More() {
						truncated = true
						core.SkipJSONValue(decoder)
					}
					break
				}
			}
			decoder.Token() // ']'
			log.Printf("  ✅ Done with 'reporting_structure'. %d unique files from %d reporting structures. %s", len(files), count, pr.GetProgressString())

		} else {
			log.Printf("  🔍 Root key '%s' — capturing for schema. %s", key, pr.GetProgressString())
			schemaExample[key] = captureSchemaValue(decoder)
			log.Printf("  ✅ Done with '%s'. %s", key, pr.GetProgressString())
		}
	}
	if _, err := decoder.Token(); err != nil { // '}'
		return fmt.Errorf("the index never closes (truncated download?): %w", err)
	}
	if err := ix.finish(); err != nil {
		return err
	}

	// Write schema alongside the rate file example.
	schemaPath := p.IndexSchemaPath()
	if err := os.MkdirAll(filepath.Dir(schemaPath), os.ModePerm); err == nil {
		if f, err := os.Create(schemaPath); err == nil {
			enc := json.NewEncoder(f)
			enc.SetIndent("", "  ")
			enc.Encode(schemaExample)
			f.Close()
			log.Printf("✅ Wrote index schema to %s", schemaPath)
		}
	}

	if schemaOnly {
		log.Printf("✅ Schema-only mode - wrote %s and nothing else. %d unique files seen.", schemaPath, len(files))
		if src.cached && !opts.KeepIndex {
			dropIndex(p)
		}
		return nil
	}

	list, err := fileList(files)
	if err != nil {
		return err
	}
	pairs := make([]core.PlanLink, 0, len(links))
	for l := range links {
		pairs = append(pairs, l)
	}
	slices.SortFunc(pairs, func(a, b core.PlanLink) int {
		return cmp.Or(cmp.Compare(a.FileID, b.FileID), cmp.Compare(a.PlanID, b.PlanID))
	})

	// Both are written beside their final paths and moved into place together,
	// so parse and the build don't read one month's files with another's links.
	filesPath, linksPath := p.IndexFilesPath(), p.IndexFilePlansPath()
	filesTmp, linksTmp := filesPath+".tmp", linksPath+".tmp"
	if err := core.WriteParquet(filesTmp, list); err != nil {
		return err
	}
	if err := core.WriteParquet(linksTmp, pairs); err != nil {
		os.Remove(filesTmp)
		return err
	}
	if err := os.Rename(filesTmp, filesPath); err != nil {
		return err
	}
	if err := os.Rename(linksTmp, linksPath); err != nil {
		return fmt.Errorf("%s is this month's but %s isn't: %w. Run discover again.", filesPath, linksPath, err)
	}
	if truncated {
		log.Printf("⚠️ The limit stopped the read part way, so these lists hold only the first %d reporting structures.", count)
	}
	log.Printf("✅ Wrote %s: %d file(s), and %s: %d (file, plan ID) pair(s).",
		filesPath, len(list), linksPath, len(pairs))
	if src.cached && !opts.KeepIndex {
		dropIndex(p)
	}
	return nil
}

// fileList is the files in ID order, so the same index always writes the same
// bytes. It errors if two addresses hash to one ID, which would key both files'
// Parquet the same.
func fileList(files map[string]core.IndexFile) ([]core.IndexFile, error) {
	list := make([]core.IndexFile, 0, len(files))
	for _, f := range files {
		list = append(list, f)
	}
	slices.SortFunc(list, func(a, b core.IndexFile) int {
		return cmp.Or(cmp.Compare(a.FileID, b.FileID), strings.Compare(a.Address, b.Address))
	})
	for i := 1; i < len(list); i++ {
		if list[i].FileID == list[i-1].FileID {
			return nil, fmt.Errorf("%s and %s have the same file ID %d (core.FileID), so their Parquet would collide",
				list[i-1].Address, list[i].Address, list[i].FileID)
		}
	}
	return list, nil
}

// hiosPlanIDLen is the length of a HIOS plan ID, which Georgia's plan file
// uses. Some payers list a plan with its two-digit cost-sharing variant after it
// (Alliant's `83761GA013000800`), which is the same plan.
const hiosPlanIDLen = 14

// planID is the plan's ID as Georgia's plan file has it: a HIOS plan ID without
// a cost-sharing variant. Any other ID (a product ID, an EIN) is kept as listed.
func planID(plan core.ReportingPlan) string {
	if strings.EqualFold(plan.PlanIDType, "HIOS") && len(plan.PlanID) == hiosPlanIDLen+2 {
		return plan.PlanID[:hiosPlanIDLen]
	}
	return plan.PlanID
}

// hiosStateCode returns the 2-letter state code embedded at position [5:7] of a
// HIOS plan ID (e.g. "45334GA0770001" → "GA"), upper-cased. Returns "" when the
// plan is not HIOS, is too short, or the slice is not two ASCII letters.
func hiosStateCode(plan core.ReportingPlan) string {
	if !strings.EqualFold(plan.PlanIDType, "HIOS") || len(plan.PlanID) < 7 {
		return ""
	}
	st := strings.ToUpper(plan.PlanID[5:7])
	for i := 0; i < len(st); i++ {
		if st[i] < 'A' || st[i] > 'Z' {
			return ""
		}
	}
	return st
}

// captureSchemaValue recursively reads one JSON value from dec,
// truncating arrays to 1 item so the schema example stays compact.
func captureSchemaValue(dec *json.Decoder) interface{} {
	tok, err := dec.Token()
	if err != nil {
		return nil
	}
	switch v := tok.(type) {
	case json.Delim:
		if v == '{' {
			obj := map[string]interface{}{}
			for dec.More() {
				kt, _ := dec.Token()
				key, _ := kt.(string)
				obj[key] = captureSchemaValue(dec)
			}
			dec.Token() // '}'
			return obj
		}
		if v == '[' {
			var arr []interface{}
			if dec.More() {
				arr = append(arr, captureSchemaValue(dec))
				for dec.More() {
					core.SkipJSONValue(dec)
				}
			}
			dec.Token() // ']'
			return arr
		}
	default:
		return v
	}
	return nil
}
