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
	"strings"

	"github.com/jackc/pgx/v5"
)

const copyBatchSize = 50_000

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

func getDBSize(ctx context.Context, conn *pgx.Conn) string {
	var size string
	if err := conn.QueryRow(ctx, "SELECT pg_size_pretty(pg_database_size(current_database()))").Scan(&size); err != nil {
		return "unknown"
	}
	return size
}

func flushProviders(ctx context.Context, conn *pgx.Conn, rows [][]any, dryRun bool) {
	if len(rows) == 0 || dryRun {
		return
	}
	if _, err := conn.CopyFrom(ctx,
		pgx.Identifier{"provider_mappings"},
		[]string{"provider_group_id", "npi", "tin_type", "tin_value"},
		pgx.CopyFromRows(rows),
	); err != nil {
		log.Printf("⚠️ Failed to flush provider_mappings: %v", err)
	}
}

func flushRates(ctx context.Context, conn *pgx.Conn, rows [][]any, dryRun bool) {
	if len(rows) == 0 || dryRun {
		return
	}
	if _, err := conn.CopyFrom(ctx,
		pgx.Identifier{"negotiated_rates"},
		[]string{"provider_group_id", "plan_name", "billing_code_type", "billing_code",
			"negotiation_arrangement", "negotiated_type", "negotiated_rate", "expiration_date", "service_code"},
		pgx.CopyFromRows(rows),
	); err != nil {
		log.Printf("⚠️ Failed to flush negotiated_rates: %v", err)
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

func parseRates(ctx context.Context, conn *pgx.Conn, fileID int, url string, planName string, isFirstFile bool, seenBillingCodes map[string]bool, dryRun bool) {
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

	if !dryRun && resp.ContentLength > 0 {
		conn.Exec(ctx, "UPDATE index_files SET file_size_bytes = $1 WHERE id = $2", resp.ContentLength, fileID)
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

	var providerBuf [][]any
	var rateBuf [][]any
	totalProviders := 0
	totalRates := 0

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
			log.Println("    🎯 Found 'provider_references'. Streaming to provider_mappings...")
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
						providerBuf = append(providerBuf, []any{ref.ProviderGroupID, npi, pg.TIN.Type, pg.TIN.Value})
					}
				}

				if len(providerBuf) >= copyBatchSize {
					flushProviders(ctx, conn, providerBuf, dryRun)
					totalProviders += len(providerBuf)
					providerBuf = providerBuf[:0]
					log.Printf("    ⚙️  Loaded %d provider rows... | DB Size: %s | %s", totalProviders, getDBSize(ctx, conn), pr.GetProgressString())
				}
			}
			decoder.Token() // ']'

			flushProviders(ctx, conn, providerBuf, dryRun)
			totalProviders += len(providerBuf)
			providerBuf = providerBuf[:0]
			log.Printf("    ✅ Streamed %d provider rows. %s", totalProviders, pr.GetProgressString())

		} else if key == "in_network" {
			log.Println("    🎯 Found 'in_network'. Streaming to negotiated_rates...")
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
				}

				for _, rate := range item.NegotiatedRates {
					for _, refID := range rate.ProviderReferences {
						for _, price := range rate.NegotiatedPrices {
							rateBuf = append(rateBuf, []any{
								refID, planName,
								item.BillingCodeType, item.BillingCode,
								item.NegotiationArrangement, price.NegotiatedType,
								price.NegotiatedRate, price.ExpirationDate,
								strings.Join(price.ServiceCode, "|"),
							})
						}
					}
				}

				if len(rateBuf) >= copyBatchSize {
					flushRates(ctx, conn, rateBuf, dryRun)
					totalRates += len(rateBuf)
					rateBuf = rateBuf[:0]
					log.Printf("    ⚙️  Loaded %d rate rows... | DB Size: %s | %s", totalRates, getDBSize(ctx, conn), pr.GetProgressString())
				}
			}
			decoder.Token() // ']'

			flushRates(ctx, conn, rateBuf, dryRun)
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

	log.Printf("  ✅ Completed. %d provider rows | %d rate rows | DB Size: %s", totalProviders, totalRates, getDBSize(ctx, conn))
}
