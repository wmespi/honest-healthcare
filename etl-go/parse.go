package main

import (
	"compress/gzip"
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/jackc/pgx/v5"
	parquet "github.com/parquet-go/parquet-go"
)

const copyBatchSize = 1_000_000

func skipValue(decoder *json.Decoder) {
	t, err := decoder.Token()
	if err != nil {
		return
	}
	if delim, ok := t.(json.Delim); ok {
		if delim == '{' || delim == '[' {
			depth := 1
			for depth > 0 {
				t, err := decoder.Token()
				if err != nil {
					return
				}
				if d, ok := t.(json.Delim); ok {
					if d == '{' || d == '[' {
						depth++
					} else if d == '}' || d == ']' {
						depth--
					}
				}
			}
		}
	}
}

func flushProviders(w *parquet.GenericWriter[ProviderRow], rows []ProviderRow, dryRun bool) {
	if len(rows) == 0 || dryRun || w == nil {
		return
	}
	if _, err := w.Write(rows); err != nil {
		log.Printf("⚠️ Failed to write providers to parquet: %v", err)
		return
	}
	if err := w.Flush(); err != nil {
		log.Printf("⚠️ Failed to flush providers parquet: %v", err)
	}
}

func flushRates(w *parquet.GenericWriter[RateRow], rows []RateRow, dryRun bool) {
	if len(rows) == 0 || dryRun || w == nil {
		return
	}
	if _, err := w.Write(rows); err != nil {
		log.Printf("⚠️ Failed to write rates to parquet: %v", err)
		return
	}
	if err := w.Flush(); err != nil {
		log.Printf("⚠️ Failed to flush rates parquet: %v", err)
	}
}

func upsertBillingCode(ctx context.Context, conn *pgx.Conn, bcType, bc, name, desc string, dryRun bool) {
	if dryRun {
		return
	}
	if _, err := conn.Exec(ctx,
		`INSERT INTO billing_codes (billing_code_type, billing_code, name, description)
		 VALUES ($1, $2, $3, $4)
		 ON CONFLICT (billing_code) DO NOTHING`,
		bcType, bc, name, desc,
	); err != nil {
		log.Printf("⚠️ Failed to upsert billing code %s: %v", bc, err)
	}
}

func markFailed(ctx context.Context, conn *pgx.Conn, fileID int, reason error, dryRun bool) {
	log.Printf("❌ File %d failed: %v", fileID, reason)
	if !dryRun {
		conn.Exec(ctx, "UPDATE index_files SET status = 'failed' WHERE id = $1", fileID)
	}
}

func writeNPILookup(seenNPIs map[int64]string) {
	if len(seenNPIs) == 0 {
		return
	}
	if err := os.MkdirAll(filepath.Dir(NPILookupPath), os.ModePerm); err != nil {
		log.Printf("⚠️ Failed to create dir for npi_lookup: %v", err)
		return
	}
	f, err := os.Create(NPILookupPath)
	if err != nil {
		log.Printf("⚠️ Failed to create npi_lookup.parquet: %v", err)
		return
	}
	defer f.Close()
	w := parquet.NewGenericWriter[NPILookupRow](f, parquet.Compression(&parquet.Zstd))
	defer w.Close()

	rows := make([]NPILookupRow, 0, len(seenNPIs))
	for npi, tin := range seenNPIs {
		rows = append(rows, NPILookupRow{NPI: npi, TINValue: tin})
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].NPI < rows[j].NPI })

	if _, err := w.Write(rows); err != nil {
		log.Printf("⚠️ Failed to write npi_lookup.parquet: %v", err)
		return
	}
	log.Printf("✅ Wrote npi_lookup.parquet — %d unique NPIs", len(rows))
}

func parseRates(ctx context.Context, conn *pgx.Conn, fileID int, url string, planName string, isFirstFile bool, seenBillingCodes map[string]bool, seenNPIs map[int64]string, dryRun bool) {
	log.Printf("⚙️ Processing Rate File [id=%d]: %s", fileID, url)

	if !dryRun {
		if _, err := conn.Exec(ctx, "UPDATE index_files SET status = 'processing' WHERE id = $1", fileID); err != nil {
			log.Printf("⚠️ Failed to mark file %d as processing: %v", fileID, err)
		}
	}

	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		markFailed(ctx, conn, fileID, err, dryRun)
		return
	}
	resp, err := (&http.Client{}).Do(req)
	if err != nil {
		markFailed(ctx, conn, fileID, err, dryRun)
		return
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		markFailed(ctx, conn, fileID, fmt.Errorf("HTTP %d", resp.StatusCode), dryRun)
		return
	}

	pr := NewProgressReader(resp.Body, resp.ContentLength)

	if resp.ContentLength > 0 {
		compressedGB := float64(resp.ContentLength) / 1e9
		log.Printf("  📦 %.2f GB compressed | ~%.1f GB uncompressed (est. ×12) | ~%.1f GB Parquet (est. ×4, rough)",
			compressedGB, compressedGB*12, compressedGB*4)
	}
	if !dryRun && resp.ContentLength > 0 {
		conn.Exec(ctx, "UPDATE index_files SET file_size_bytes = $1 WHERE id = $2", resp.ContentLength, fileID)
	}

	// Create parquet writers (skipped in dry-run)
	var ratesWriter *parquet.GenericWriter[RateRow]
	var providersWriter *parquet.GenericWriter[ProviderRow]
	var codesWriter *parquet.GenericWriter[BillingCodeRow]

	if !dryRun {
		ratesPath := filepath.Join(RatesOutputDir, fmt.Sprintf("%d.parquet", fileID))
		providersPath := filepath.Join(ProvidersOutputDir, fmt.Sprintf("%d.parquet", fileID))
		codesPath := filepath.Join(CodesOutputDir, fmt.Sprintf("%d.parquet", fileID))

		for _, dir := range []string{RatesOutputDir, ProvidersOutputDir, CodesOutputDir} {
			if err := os.MkdirAll(dir, os.ModePerm); err != nil {
				markFailed(ctx, conn, fileID, fmt.Errorf("create dir %s: %w", dir, err), dryRun)
				return
			}
		}

		// defer file.Close registered before defer writer.Close so writer flushes first (LIFO)
		ratesFile, err := os.Create(ratesPath)
		if err != nil {
			markFailed(ctx, conn, fileID, fmt.Errorf("create rates parquet: %w", err), dryRun)
			return
		}
		defer ratesFile.Close()
		ratesWriter = parquet.NewGenericWriter[RateRow](ratesFile, parquet.Compression(&parquet.Zstd))
		defer ratesWriter.Close()

		providersFile, err := os.Create(providersPath)
		if err != nil {
			markFailed(ctx, conn, fileID, fmt.Errorf("create providers parquet: %w", err), dryRun)
			return
		}
		defer providersFile.Close()
		providersWriter = parquet.NewGenericWriter[ProviderRow](providersFile, parquet.Compression(&parquet.Zstd))
		defer providersWriter.Close()

		codesFile, err := os.Create(codesPath)
		if err != nil {
			markFailed(ctx, conn, fileID, fmt.Errorf("create codes parquet: %w", err), dryRun)
			return
		}
		defer codesFile.Close()
		codesWriter = parquet.NewGenericWriter[BillingCodeRow](codesFile, parquet.Compression(&parquet.Zstd))
		defer codesWriter.Close()

		log.Printf("  📂 Writing to %s, %s, and %s", ratesPath, providersPath, codesPath)
	}

	gz, err := gzip.NewReader(pr)
	if err != nil {
		markFailed(ctx, conn, fileID, err, dryRun)
		return
	}
	defer gz.Close()

	decoder := json.NewDecoder(gz)
	schemaExample := make(map[string]interface{})

	log.Println("  🔄 Starting Single-Pass Extract...")

	t, err := decoder.Token()
	if err != nil {
		markFailed(ctx, conn, fileID, fmt.Errorf("failed to read root token: %w", err), dryRun)
		return
	}
	if delim, ok := t.(json.Delim); !ok || delim != '{' {
		markFailed(ctx, conn, fileID, fmt.Errorf("expected root '{', got %v", t), dryRun)
		return
	}

	var providerBuf []ProviderRow
	var rateBuf []RateRow
	totalProviders := 0
	totalRates := 0
	providerBatches := 0
	rateBatches := 0
	const logEveryNBatches = 10 // log every 10M rows

	for decoder.More() {
		t, err := decoder.Token()
		if err != nil {
			break
		}
		key, ok := t.(string)
		if !ok {
			continue
		}

		if key == "provider_references" {
			log.Println("    🎯 Found 'provider_references'. Streaming to providers parquet...")
			decoder.Token() // '['

			for decoder.More() {
				var ref ProviderReference

				if isFirstFile && schemaExample["provider_references"] == nil {
					var raw map[string]interface{}
					if err := decoder.Decode(&raw); err == nil {
						schemaExample["provider_references"] = []interface{}{raw}
						b, _ := json.Marshal(raw)
						json.Unmarshal(b, &ref)
					}
				} else {
					if err := decoder.Decode(&ref); err != nil {
						log.Printf("⚠️ Error decoding ProviderReference: %v", err)
						continue
					}
				}

				for _, pg := range ref.ProviderGroups {
					for _, npi := range pg.NPIs {
						npi64 := int64(npi)
						if _, seen := seenNPIs[npi64]; !seen {
							seenNPIs[npi64] = pg.TIN.Value
						}
						providerBuf = append(providerBuf, ProviderRow{
							ProviderGroupID: int64(ref.ProviderGroupID),
							NPI:             npi64,
							TINType:         pg.TIN.Type,
							TINValue:        pg.TIN.Value,
						})
					}
				}

				if len(providerBuf) >= copyBatchSize {
					flushProviders(providersWriter, providerBuf, dryRun)
					totalProviders += len(providerBuf)
					providerBuf = providerBuf[:0]
					providerBatches++
					if providerBatches%logEveryNBatches == 0 {
						log.Printf("    ⚙️  Loaded %d provider rows... | %s", totalProviders, pr.GetProgressString())
					}
				}
			}
			decoder.Token() // ']'

			flushProviders(providersWriter, providerBuf, dryRun)
			totalProviders += len(providerBuf)
			providerBuf = providerBuf[:0]
			log.Printf("    ✅ Streamed %d provider rows. %s", totalProviders, pr.GetProgressString())

		} else if key == "in_network" {
			log.Println("    🎯 Found 'in_network'. Streaming to rates parquet...")
			decoder.Token() // '['

			for decoder.More() {
				var item InNetworkItem

				if isFirstFile && schemaExample["in_network"] == nil {
					var raw map[string]interface{}
					if err := decoder.Decode(&raw); err == nil {
						schemaExample["in_network"] = []interface{}{raw}
						b, _ := json.Marshal(raw)
						json.Unmarshal(b, &item)
					}
				} else {
					if err := decoder.Decode(&item); err != nil {
						log.Printf("⚠️ Error decoding InNetworkItem: %v", err)
						continue
					}
				}

				if !seenBillingCodes[item.BillingCode] {
					seenBillingCodes[item.BillingCode] = true
					upsertBillingCode(ctx, conn, item.BillingCodeType, item.BillingCode, item.Name, item.Description, dryRun)
					if !dryRun && codesWriter != nil {
						codesWriter.Write([]BillingCodeRow{{
							BillingCodeType: item.BillingCodeType,
							BillingCode:     item.BillingCode,
							Name:            item.Name,
							Description:     item.Description,
						}})
					}
				}

				for _, rate := range item.NegotiatedRates {
					for _, refID := range rate.ProviderReferences {
						for _, price := range rate.NegotiatedPrices {
							rateBuf = append(rateBuf, RateRow{
								ProviderGroupID:        int64(refID),
								PlanName:               planName,
								BillingCodeType:        item.BillingCodeType,
								BillingCode:            item.BillingCode,
								NegotiationArrangement: item.NegotiationArrangement,
								NegotiatedType:         price.NegotiatedType,
								NegotiatedRate:         price.NegotiatedRate,
								ExpirationDate:         price.ExpirationDate,
								ServiceCode:            strings.Join(price.ServiceCode, "|"),
								BillingClass:           price.BillingClass,
								Setting:                price.Setting,
							})
						}
					}
				}

				if len(rateBuf) >= copyBatchSize {
					flushRates(ratesWriter, rateBuf, dryRun)
					totalRates += len(rateBuf)
					rateBuf = rateBuf[:0]
					rateBatches++
					if rateBatches%logEveryNBatches == 0 {
						log.Printf("    ⚙️  Loaded %d rate rows... | %s", totalRates, pr.GetProgressString())
					}
				}
			}
			decoder.Token() // ']'

			flushRates(ratesWriter, rateBuf, dryRun)
			totalRates += len(rateBuf)
			rateBuf = rateBuf[:0]
			log.Printf("    ✅ Streamed %d total rate rows. %s", totalRates, pr.GetProgressString())

		} else {
			if isFirstFile {
				tPeek, _ := decoder.Token()
				if delim, ok := tPeek.(json.Delim); ok && (delim == '[' || delim == '{') {
					log.Printf("    🔍 Schema Discovery: Found unexpected root structure: '%s' (skipping) %s", key, pr.GetProgressString())
					depth := 1
					for depth > 0 {
						tSkip, _ := decoder.Token()
						if dSkip, ok := tSkip.(json.Delim); ok {
							if dSkip == '{' || dSkip == '[' {
								depth++
							} else if dSkip == '}' || dSkip == ']' {
								depth--
							}
						}
					}
				} else {
					schemaExample[key] = tPeek
					log.Printf("    🔍 Schema Discovery: Captured root key '%s' = %v", key, tPeek)
				}
			} else {
				log.Printf("    🔍 Schema Discovery: Found unexpected root key: '%s' (skipping) %s", key, pr.GetProgressString())
				skipValue(decoder)
			}
		}
	}

	decoder.Token() // '}'

	if isFirstFile {
		os.MkdirAll(filepath.Dir(ExampleOutputPath), os.ModePerm)
		if out, err := os.Create(ExampleOutputPath); err == nil {
			enc := json.NewEncoder(out)
			enc.SetIndent("", "  ")
			enc.Encode(schemaExample)
			out.Close()
			log.Println("    ✅ Wrote ERD snippet to mrf_example.json")
		}
	}

	if !dryRun {
		if _, err := conn.Exec(ctx, "UPDATE index_files SET status = 'completed', completed_at = NOW() WHERE id = $1", fileID); err != nil {
			log.Printf("⚠️ Failed to mark file %d as completed: %v", fileID, err)
		}
	}

	log.Printf("  ✅ Completed. %d provider rows | %d rate rows", totalProviders, totalRates)
}
