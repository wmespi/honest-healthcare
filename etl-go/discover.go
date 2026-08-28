package main

import (
	"compress/gzip"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/jackc/pgx/v5"
)

// ensureIndexCached downloads the master index to disk if not already cached,
// then returns the local path. Subsequent calls with the same monthly URL are
// instant — no network involved.
func ensureIndexCached(noCache bool) string {
	cacheDir := filepath.Dir(ExampleOutputPath)
	cachePath := filepath.Join(cacheDir, "index_cache.json.gz")
	cacheURLPath := filepath.Join(cacheDir, "index_cache_url.txt")

	if !noCache {
		if data, err := os.ReadFile(cacheURLPath); err == nil && strings.TrimSpace(string(data)) == IndexURL {
			if _, err := os.Stat(cachePath); err == nil {
				log.Printf("📦 Cache hit — skipping download, reading from disk")
				return cachePath
			}
		}
	}

	os.MkdirAll(cacheDir, os.ModePerm)
	tmpPath := cachePath + ".tmp"

	if err := downloadParallel(IndexURL, tmpPath, 8); err != nil {
		log.Fatalf("❌ Download failed: %v", err)
	}

	if err := os.Rename(tmpPath, cachePath); err != nil {
		log.Fatalf("❌ Failed to finalize cache: %v", err)
	}
	os.WriteFile(cacheURLPath, []byte(IndexURL), 0644)
	log.Printf("💾 Cached to %s — future runs will skip the download", filepath.Base(cachePath))
	return cachePath
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
		return downloadSingle(url, destPath)
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

func downloadSingle(url, destPath string) error {
	resp, err := (&http.Client{}).Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return fmt.Errorf("status %d", resp.StatusCode)
	}
	f, err := os.Create(destPath)
	if err != nil {
		return err
	}
	pr := NewProgressReader(resp.Body, resp.ContentLength)
	quit := make(chan struct{})
	go func() {
		ticker := time.NewTicker(10 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ticker.C:
				if s := pr.GetProgressString(); s != "" {
					log.Printf("  ⬇️  %s", s)
				}
			case <-quit:
				return
			}
		}
	}()
	_, copyErr := io.Copy(f, pr)
	close(quit)
	f.Close()
	if copyErr != nil {
		os.Remove(destPath)
	}
	return copyErr
}

func discoverLinks(ctx context.Context, conn *pgx.Conn, limit int, noCache bool, schemaOnly bool) {
	if schemaOnly {
		log.Println("🚀 Starting Anthem Bronze Layer: Index Schema Capture (Go)...")
	} else {
		log.Println("🚀 Starting Anthem Bronze Layer: Index Discovery (Go)...")
	}

	cachePath := ensureIndexCached(noCache)

	f, err := os.Open(cachePath)
	if err != nil {
		log.Fatalf("❌ Failed to open cached index: %v", err)
	}
	defer f.Close()
	fi, _ := f.Stat()

	pr := NewProgressReader(f, fi.Size())
	gz, err := gzip.NewReader(pr)
	if err != nil {
		log.Fatalf("❌ Failed to unzip: %v", err)
	}

	decoder := json.NewDecoder(gz)

	// urlToCand accumulates metadata per unique file URL.
	// planNames are intentionally excluded — with 400k+ unique plans × 10k files
	// the cross-product blows heap. market_types + hios_issuer_ids + network_entity
	// are sufficient for all filtering (individual vs group, GA vs BlueCard).
	type candidate struct {
		marketTypes   map[string]struct{} // set: auto-deduplicates "individual"/"group"
		hiosIssuerIDs map[string]struct{} // set: 5-digit issuer IDs, maps to state via CMS
		planStates    map[string]struct{} // set: 2-letter state codes from HIOS plan_id[5:7]
		description   string
		networkEntity string
		location      string
	}
	urlToCand := make(map[string]*candidate)
	rsCount := 0

	// Index root carries reporting_entity_name / reporting_entity_type (e.g. "Anthem Inc").
	// Less specific than the per-file value the parser captures, but better than NULL.
	var rootEntityName, rootEntityType string

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

	processRS := func(rs ReportingStructure) {
		rsCount++
		for _, f := range rs.InNetworkFiles {
			if f.Location == "" {
				continue
			}

			// BlueCard files have " : " in the description: "BCBS Minnesota : Aware".
			// Home-plan files have no separator — networkEntity stays "" (stored as NULL).
			networkEntity := ""
			if idx := strings.Index(f.Description, " : "); idx != -1 {
				networkEntity = f.Description[:idx]
			}

			c, exists := urlToCand[f.Location]
			if !exists {
				c = &candidate{
					marketTypes:   make(map[string]struct{}),
					hiosIssuerIDs: make(map[string]struct{}),
					planStates:    make(map[string]struct{}),
					description:   f.Description,
					networkEntity: networkEntity,
					location:      f.Location,
				}
				urlToCand[f.Location] = c
			}

			for _, plan := range rs.ReportingPlans {
				if plan.PlanMarketType != "" {
					c.marketTypes[plan.PlanMarketType] = struct{}{}
				}
				// HIOS plan IDs are positional: [0:5] = issuer ID (maps to state via
				// CMS registry), [5:7] = 2-letter state code. Both are deterministic —
				// no regex, no string matching against plan names.
				if plan.PlanIDType == "HIOS" && len(plan.PlanID) >= 5 {
					c.hiosIssuerIDs[plan.PlanID[:5]] = struct{}{}
				}
				if st := hiosStateCode(plan); st != "" {
					c.planStates[st] = struct{}{}
				}
			}
		}
	}

	for decoder.More() {
		kt, _ := decoder.Token()
		key, _ := kt.(string)

		if key == "reporting_structure" {
			log.Printf("  🎯 Found 'reporting_structure'. Streaming candidates... %s", pr.GetProgressString())
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

					var rs ReportingStructure
					json.Unmarshal(raw, &rs)
					processRS(rs)
					capturedSchema = true
				} else {
					var rs ReportingStructure
					if err := decoder.Decode(&rs); err != nil {
						log.Printf("⚠️ Decode error: %v", err)
						continue
					}
					processRS(rs)
				}

				if count%1000 == 0 {
					log.Printf("  Scanned %d reporting structures... %d unique URLs. %s", count, len(urlToCand), pr.GetProgressString())
				}
				if limit > 0 && count >= limit {
					log.Printf("🛑 Limit of %d reached.", limit)
					for decoder.More() {
						skipValue(decoder)
					}
					break
				}
			}
			decoder.Token() // ']'
			log.Printf("  ✅ Done with 'reporting_structure'. %d unique URLs from %d reporting structures. %s", len(urlToCand), rsCount, pr.GetProgressString())

		} else {
			log.Printf("  🔍 Root key '%s' — capturing for schema. %s", key, pr.GetProgressString())
			val := captureSchemaValue(decoder)
			schemaExample[key] = val
			if s, ok := val.(string); ok {
				switch key {
				case "reporting_entity_name":
					rootEntityName = s
				case "reporting_entity_type":
					rootEntityType = s
				}
			}
			log.Printf("  ✅ Done with '%s'. %s", key, pr.GetProgressString())
		}
	}
	decoder.Token() // '}'

	gz.Close()

	// Write schema alongside the rate file example.
	schemaPath := filepath.Join(filepath.Dir(ExampleOutputPath), "index_schema.json")
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
		log.Printf("✅ Schema-only mode — wrote %s, skipping DB upsert. %d unique URLs seen.", schemaPath, len(urlToCand))
		return
	}

	// Build candidate list from the deduplicated map.
	candidates := make([]*candidate, 0, len(urlToCand))
	for _, c := range urlToCand {
		candidates = append(candidates, c)
	}
	log.Printf("📥 Upserting %d unique URLs into index_files via COPY...", len(candidates))

	// COPY into a temp staging table, then a single INSERT...SELECT...ON CONFLICT.
	if _, err := conn.Exec(ctx, `
		CREATE TEMP TABLE _idx_stage (
			market_types          TEXT[],
			hios_issuer_ids       TEXT[],
			plan_states           TEXT[],
			network_entity        TEXT,
			reporting_entity_name TEXT,
			reporting_entity_type TEXT,
			description           TEXT,
			location              TEXT NOT NULL
		)
	`); err != nil {
		log.Fatalf("❌ Failed to create staging table: %v", err)
	}

	var entityName, entityType interface{}
	if rootEntityName != "" {
		entityName = rootEntityName
	}
	if rootEntityType != "" {
		entityType = rootEntityType
	}

	copyRows := make([][]any, len(candidates))
	for i, c := range candidates {
		marketTypes := make([]string, 0, len(c.marketTypes))
		for k := range c.marketTypes {
			marketTypes = append(marketTypes, k)
		}
		hiosIDs := make([]string, 0, len(c.hiosIssuerIDs))
		for k := range c.hiosIssuerIDs {
			hiosIDs = append(hiosIDs, k)
		}
		planStates := make([]string, 0, len(c.planStates))
		for k := range c.planStates {
			planStates = append(planStates, k)
		}
		var networkEntity interface{}
		if c.networkEntity != "" {
			networkEntity = c.networkEntity
		}
		copyRows[i] = []any{marketTypes, hiosIDs, planStates, networkEntity, entityName, entityType, c.description, c.location}
	}

	cols := []string{"market_types", "hios_issuer_ids", "plan_states", "network_entity", "reporting_entity_name", "reporting_entity_type", "description", "location"}
	n, err := conn.CopyFrom(ctx, pgx.Identifier{"_idx_stage"}, cols, pgx.CopyFromRows(copyRows))
	if err != nil {
		log.Fatalf("❌ COPY to staging failed: %v", err)
	}
	log.Printf("  COPY loaded %d rows into staging", n)

	// Drop GIN indexes before writing — maintaining them row-by-row during a bulk
	// update is far slower than dropping and rebuilding once over all rows.
	for _, idx := range []string{"idx_index_files_market", "idx_index_files_hios", "idx_index_files_plan_states"} {
		if _, err := conn.Exec(ctx, "DROP INDEX IF EXISTS "+idx); err != nil {
			log.Printf("⚠️ Could not drop index %s: %v", idx, err)
		}
	}

	// UPDATE existing rows via a join — single hash join over all rows.
	updateTag, err := conn.Exec(ctx, `
		UPDATE index_files t
		SET
		    market_types          = s.market_types,
		    hios_issuer_ids       = s.hios_issuer_ids,
		    plan_states           = s.plan_states,
		    network_entity        = s.network_entity,
		    reporting_entity_name = COALESCE(t.reporting_entity_name, s.reporting_entity_name),
		    reporting_entity_type = COALESCE(t.reporting_entity_type, s.reporting_entity_type)
		FROM _idx_stage s
		WHERE t.location = s.location
	`)
	if err != nil {
		log.Fatalf("❌ Bulk UPDATE failed: %v", err)
	}

	// INSERT any genuinely new URLs (first run, or monthly index additions).
	insertTag, err := conn.Exec(ctx, `
		INSERT INTO index_files (market_types, hios_issuer_ids, plan_states, network_entity, reporting_entity_name, reporting_entity_type, description, location)
		SELECT s.market_types, s.hios_issuer_ids, s.plan_states, s.network_entity, s.reporting_entity_name, s.reporting_entity_type, s.description, s.location
		FROM _idx_stage s
		LEFT JOIN index_files t ON t.location = s.location
		WHERE t.id IS NULL
	`)
	if err != nil {
		log.Fatalf("❌ INSERT new rows failed: %v", err)
	}

	// Rebuild GIN indexes once over all data — one scan is orders of magnitude
	// faster than 10k incremental updates would have been.
	log.Printf("  Rebuilding GIN indexes...")
	for _, ddl := range []string{
		`CREATE INDEX idx_index_files_market      ON index_files USING GIN(market_types)`,
		`CREATE INDEX idx_index_files_hios        ON index_files USING GIN(hios_issuer_ids)`,
		`CREATE INDEX idx_index_files_plan_states ON index_files USING GIN(plan_states)`,
	} {
		if _, err := conn.Exec(ctx, ddl); err != nil {
			log.Printf("⚠️ Index rebuild: %v", err)
		}
	}

	log.Printf("✅ Discovery complete! Updated %s rows, inserted %s new rows.", updateTag, insertTag)
}

// hiosStateCode returns the 2-letter state code embedded at position [5:7] of a
// HIOS plan ID (e.g. "45334GA0770001" → "GA"), upper-cased. Returns "" when the
// plan is not HIOS, is too short, or the slice is not two ASCII letters.
func hiosStateCode(plan ReportingPlan) string {
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
					skipValue(dec)
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
