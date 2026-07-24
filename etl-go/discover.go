package main

import (
	"compress/gzip"
	"context"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"path/filepath"

	"github.com/jackc/pgx/v5"
)

func discoverLinks(ctx context.Context, conn *pgx.Conn, limit int) {
	log.Println("🚀 Starting Anthem Bronze Layer: Index Discovery (Go)...")

	req, err := http.NewRequest("GET", IndexURL, nil)
	if err != nil {
		log.Fatalf("❌ Failed to create request: %v", err)
	}

	client := &http.Client{}
	resp, err := client.Do(req)
	if err != nil {
		log.Fatalf("❌ Failed to fetch index: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		log.Fatalf("❌ Failed to fetch index, status code: %d", resp.StatusCode)
	}

	pr := NewProgressReader(resp.Body, resp.ContentLength)
	gz, err := gzip.NewReader(pr)
	if err != nil {
		log.Fatalf("❌ Failed to unzip: %v", err)
	}
	defer gz.Close()

	decoder := json.NewDecoder(gz)

	// urlToCand accumulates ALL plan names per URL.
	// A single rate file is shared across many plans — the map key deduplicates
	// the URL while the planNames slice captures every plan that references it.
	type candidate struct {
		planNames   []string
		description string
		location    string
	}
	urlToCand := make(map[string]*candidate)

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
		planName := ""
		if len(rs.ReportingPlans) > 0 {
			planName = rs.ReportingPlans[0].PlanName
		}
		for _, f := range rs.InNetworkFiles {
			if f.Location == "" {
				continue
			}
			if c, exists := urlToCand[f.Location]; exists {
				if planName != "" {
					c.planNames = append(c.planNames, planName)
				}
			} else {
				pn := []string{}
				if planName != "" {
					pn = []string{planName}
				}
				urlToCand[f.Location] = &candidate{
					planNames:   pn,
					description: f.Description,
					location:    f.Location,
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
			log.Printf("  ✅ Done with 'reporting_structure'. %d unique URLs. %s", len(urlToCand), pr.GetProgressString())

		} else {
			log.Printf("  🔍 Root key '%s' — capturing for schema. %s", key, pr.GetProgressString())
			schemaExample[key] = captureSchemaValue(decoder)
			log.Printf("  ✅ Done with '%s'. %s", key, pr.GetProgressString())
		}
	}
	decoder.Token() // '}'

	// Write schema alongside the rate file example
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

	// Upsert via pgx.Batch — one TCP exchange for all rows.
	// ON CONFLICT merges plan_names arrays so re-running discovery accumulates
	// new plan names without duplicating URLs.
	log.Printf("📥 Upserting %d unique URLs into index_files...", len(urlToCand))
	batch := &pgx.Batch{}
	for _, c := range urlToCand {
		batch.Queue(
			`INSERT INTO index_files (plan_names, description, location)
			 VALUES ($1, $2, $3)
			 ON CONFLICT ON CONSTRAINT uq_index_files_location DO UPDATE
			 SET plan_names = (
			     SELECT array_agg(DISTINCT n)
			     FROM unnest(index_files.plan_names || $1::text[]) AS t(n)
			 )`,
			c.planNames, c.description, c.location,
		)
	}
	br := conn.SendBatch(ctx, batch)
	if err := br.Close(); err != nil {
		log.Printf("⚠️ Failed to upsert index_files: %v", err)
	} else {
		log.Printf("✅ Discovery complete! Upserted %d unique file URLs into index_files.", len(urlToCand))
	}
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
