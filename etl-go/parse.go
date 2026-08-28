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
	"sort"
	"strings"

	"github.com/jackc/pgx/v5"
	parquet "github.com/parquet-go/parquet-go"
)

const copyBatchSize = 1_000_000

// inflightSubdir holds parquet files still being written. They are renamed up one
// level (into the backend's glob path) only after a clean single-pass stream.
const inflightSubdir = ".inflight"

// skipValue consumes and discards one JSON value (scalar, object, or array).
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

func upsertBillingCode(ctx context.Context, conn *pgx.Conn, row BillingCodeRow) {
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

// openMRF returns a reader for the raw (still gzipped) MRF bytes, its size, and a
// cleanup func. A fixturePath reads from disk (offline); otherwise it GETs url.
func openMRF(url, fixturePath string) (io.ReadCloser, int64, error) {
	if fixturePath != "" {
		f, err := os.Open(fixturePath)
		if err != nil {
			return nil, 0, err
		}
		fi, _ := f.Stat()
		return f, fi.Size(), nil
	}
	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return nil, 0, err
	}
	resp, err := (&http.Client{}).Do(req)
	if err != nil {
		return nil, 0, err
	}
	if resp.StatusCode != 200 {
		resp.Body.Close()
		return nil, 0, fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	return resp.Body, resp.ContentLength, nil
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

	body, contentLength, err := openMRF(url, fixturePath)
	if err != nil {
		markFailed(ctx, conn, fileID, err, dryRun)
		return nil
	}
	defer body.Close()

	pr := NewProgressReader(body, contentLength)

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
	anthemDir := filepath.Dir(PricesOutputDir)
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
		gsW, gsC, err := newParquetWriter[GroupSetMemberRow](groupSetsScratch)
		if err != nil {
			markFailed(ctx, conn, fileID, err, dryRun)
			return nil
		}
		provW, provC, err := newParquetWriter[ProviderRow](provScratch)
		if err != nil {
			markFailed(ctx, conn, fileID, err, dryRun)
			return nil
		}
		codesW, codesC, err := newParquetWriter[BillingCodeRow](codesScratch)
		if err != nil {
			markFailed(ctx, conn, fileID, err, dryRun)
			return nil
		}
		closers = []io.Closer{fanoutCloser{fanout}, gsW, gsC, provW, provC, codesW, codesC}
		w = mrfWriters{
			prices: func(rows []PriceRow) {
				if err := fanout.write(rows); err != nil {
					log.Printf("⚠️ write prices parquet: %v", err)
				}
			},
			groupSetMembers: func(rows []GroupSetMemberRow) {
				if _, err := gsW.Write(rows); err != nil {
					log.Printf("⚠️ write group_sets parquet: %v", err)
				}
				gsW.Flush()
			},
			providers: func(rows []ProviderRow) {
				if _, err := provW.Write(rows); err != nil {
					log.Printf("⚠️ write providers parquet: %v", err)
				}
				provW.Flush()
			},
			code: func(row BillingCodeRow) {
				upsertBillingCode(ctx, conn, row)
				codesW.Write([]BillingCodeRow{row})
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
	// Parquet writers must be closed (flushed) before we read the files back or
	// mark the row completed — close in LIFO order (writer before its file).
	closeAll(closers)
	if err != nil {
		markFailed(ctx, conn, fileID, err, dryRun)
		return nil
	}

	// Stream succeeded — move the scratch parquet into place.
	if !dryRun {
		if err := fanout.promote(PricesOutputDir); err != nil {
			markFailed(ctx, conn, fileID, err, dryRun)
			return nil
		}
		for _, mv := range [][2]string{
			{groupSetsScratch, filepath.Join(GroupSetsOutputDir, name)},
			{provScratch, filepath.Join(ProvidersOutputDir, name)},
			{codesScratch, filepath.Join(CodesOutputDir, name)},
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
		os.MkdirAll(filepath.Dir(ExampleOutputPath), os.ModePerm)
		if out, err := os.Create(ExampleOutputPath); err == nil {
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
// index_files metadata (market_types etc.) is joined in from the row itself.
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
