package discovery

import (
	"cmp"
	"compress/gzip"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/wmespi/honest-healthcare/etl/core"
)

// Options configure a discover run (the flags behind `etl discover`).
type Options struct {
	// Limit reads only the first Limit reporting structures, so main allows it
	// only in test mode or with SchemaOnly.
	Limit int
	// KeepIndex keeps the downloaded master index after the run, so the next run
	// of the same month reads it from disk; NoCache downloads it again anyway.
	KeepIndex, NoCache bool
	SchemaOnly         bool // stream the index, write index_schema.json and nothing else
}

// indexCachePaths are where the downloaded master index and its URL are kept.
func indexCachePaths() (cachePath, cacheURLPath string) {
	cacheDir := filepath.Dir(core.ExampleOutputPath)
	return filepath.Join(cacheDir, "index_cache.json.gz"), filepath.Join(cacheDir, "index_cache_url.txt")
}

// ensureIndexCached downloads the master index to disk unless a copy of this
// month's is already there, from a run with KeepIndex or one that stopped part
// way, then returns the local path.
func ensureIndexCached(noCache bool) string {
	cacheDir := filepath.Dir(core.ExampleOutputPath)
	cachePath, cacheURLPath := indexCachePaths()

	if !noCache {
		if data, err := os.ReadFile(cacheURLPath); err == nil && strings.TrimSpace(string(data)) == core.IndexURL {
			if _, err := os.Stat(cachePath); err == nil {
				log.Printf("📦 Cache hit — skipping download, reading from disk")
				return cachePath
			}
		}
	}

	os.MkdirAll(cacheDir, os.ModePerm)
	tmpPath := cachePath + ".tmp"

	if err := downloadParallel(core.IndexURL, tmpPath, 8); err != nil {
		log.Fatalf("❌ Download failed: %v", err)
	}

	if err := os.Rename(tmpPath, cachePath); err != nil {
		log.Fatalf("❌ Failed to finalize cache: %v", err)
	}
	os.WriteFile(cacheURLPath, []byte(core.IndexURL), 0644)
	log.Printf("💾 Downloaded to %s", filepath.Base(cachePath))
	return cachePath
}

// dropIndex deletes the downloaded master index once a run has read it, since
// it's several GB and only needed during a run.
func dropIndex() {
	cachePath, cacheURLPath := indexCachePaths()
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

// Run is Phase 1: it reads this month's master index and writes the files it
// lists (index_files.parquet) and the Georgia individual-market plans each one
// prices (index_file_plans.parquet).
func Run(opts Options) {
	limit, schemaOnly := opts.Limit, opts.SchemaOnly
	if schemaOnly {
		log.Println("🚀 Starting Anthem Bronze Layer: Index Schema Capture (Go)...")
	} else {
		log.Println("🚀 Starting Anthem Bronze Layer: Index Discovery (Go)...")
	}

	cachePath := ensureIndexCached(opts.NoCache)

	f, err := os.Open(cachePath)
	if err != nil {
		log.Fatalf("❌ Failed to open cached index: %v", err)
	}
	defer f.Close()
	fi, _ := f.Stat()

	pr := core.NewProgressReader(f, fi.Size())
	gz, err := gzip.NewReader(pr)
	if err != nil {
		log.Fatalf("❌ Failed to unzip: %v", err)
	}

	decoder := json.NewDecoder(gz)

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
		log.Fatalf("❌ Failed to read root token: %v", err)
	}
	if delim, ok := tok.(json.Delim); !ok || delim != '{' {
		log.Fatalf("❌ Expected root '{', got %v", tok)
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
					links[core.PlanLink{FileID: file.FileID, PlanID: plan.PlanID}] = struct{}{}
				}
			}
		}
	}

	for decoder.More() {
		kt, _ := decoder.Token()
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
						log.Printf("⚠️ Decode error on first item: %v", err)
						continue
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
						log.Printf("⚠️ Decode error: %v", err)
						continue
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
	decoder.Token() // '}'

	gz.Close()

	// Write schema alongside the rate file example.
	schemaPath := filepath.Join(filepath.Dir(core.ExampleOutputPath), "index_schema.json")
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
		if !opts.KeepIndex {
			dropIndex()
		}
		return
	}

	list, err := fileList(files)
	if err != nil {
		log.Fatalf("❌ %v", err)
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
	filesTmp, linksTmp := core.IndexFilesPath+".tmp", core.IndexFilePlansPath+".tmp"
	if err := core.WriteParquet(filesTmp, list); err != nil {
		log.Fatalf("❌ %v", err)
	}
	if err := core.WriteParquet(linksTmp, pairs); err != nil {
		os.Remove(filesTmp)
		log.Fatalf("❌ %v", err)
	}
	if err := os.Rename(filesTmp, core.IndexFilesPath); err != nil {
		log.Fatalf("❌ %v", err)
	}
	if err := os.Rename(linksTmp, core.IndexFilePlansPath); err != nil {
		log.Fatalf("❌ %s is this month's but %s isn't: %v. Run discover again.", core.IndexFilesPath, core.IndexFilePlansPath, err)
	}
	if truncated {
		log.Printf("⚠️ The limit stopped the read part way, so these lists hold only the first %d reporting structures.", count)
	}
	log.Printf("✅ Wrote %s: %d file(s), and %s: %d (file, plan ID) pair(s).",
		core.IndexFilesPath, len(list), core.IndexFilePlansPath, len(pairs))
	if !opts.KeepIndex {
		dropIndex()
	}
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

// hiosStateCode returns the 2-letter state code embedded at position [5:7] of a
// HIOS plan ID (e.g. "45334GA0770001" → "GA"), upper-cased. Returns "" when the
// plan is not HIOS, is too short, or the slice is not two ASCII letters.
func hiosStateCode(plan core.ReportingPlan) string {
	if plan.PlanIDType != "HIOS" || len(plan.PlanID) < 7 {
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
