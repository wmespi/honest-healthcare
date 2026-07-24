package main

import (
	"context"
	"flag"
	"log"
	"strconv"
	"strings"

	"github.com/jackc/pgx/v5"
)

func main() {
	discoverFlag := flag.Bool("discover", false, "Run index discovery logic")
	parseFlag := flag.Bool("parse", false, "Run rate parsing logic")
	limitFlag := flag.Int("limit", 0, "Override the default limits for discovery or parsing")
	indexUrlFlag := flag.String("index-url", "", "Override the Master Index URL")
	testFlag := flag.Bool("test", false, "Run in isolated test mode (writes to test schema, not production)")
	fileIDsFlag := flag.String("file-ids", "", "Comma-separated list of index_files IDs to parse (skips normal queue ordering)")
	dryRunFlag := flag.Bool("dry-run", false, "Stream and capture schema but skip all DB writes")
	flag.Parse()

	ctx := context.Background()

	// 1. Test mode isolation — swap to test schema before any DB connection is made
	if *testFlag {
		DatabaseURL = TestDatabaseURL
		ExampleOutputPath = "../data-test/anthem/mrf_example.json"
		if *limitFlag == 0 {
			*limitFlag = 100
		}
		log.Println("🧪 Test mode enabled — writing to 'test' schema")
	}

	// 2. Set Index URL
	if *indexUrlFlag != "" {
		IndexURL = *indexUrlFlag
	} else {
		IndexURL = getDefaultIndexURL()
	}

	// 3. Connect to DB
	conn, err := pgx.Connect(ctx, DatabaseURL)
	if err != nil {
		log.Fatalf("❌ Failed to connect to database: %v", err)
	}
	defer conn.Close(ctx)
	log.Printf("✅ Connected to database.")

	// 4. Routing
	if *discoverFlag {
		discoverLinks(ctx, conn, *limitFlag)
		return
	}

	if *parseFlag {
		log.Println("🚀 Starting Anthem Bronze Layer: Rate Parsing (Go)...")

		// In test mode, cap at 1 file unless the caller overrode the limit
		parseLimit := 0
		if *testFlag {
			parseLimit = 1
		}
		if *limitFlag > 0 {
			parseLimit = *limitFlag
		}

		var query string
		var args []any
		if *fileIDsFlag != "" {
			// Parse comma-separated IDs
			parts := strings.Split(*fileIDsFlag, ",")
			ids := make([]int, 0, len(parts))
			for _, p := range parts {
				id, err := strconv.Atoi(strings.TrimSpace(p))
				if err != nil {
					log.Fatalf("❌ Invalid file ID %q: %v", p, err)
				}
				ids = append(ids, id)
			}
			query = `SELECT id, location, COALESCE((SELECT string_agg(DISTINCT p, ' | ' ORDER BY p) FROM unnest(plan_names) p), '') FROM index_files WHERE id = ANY($1) ORDER BY id`
			args = []any{ids}
		} else {
			query = `SELECT id, location, COALESCE((SELECT string_agg(DISTINCT p, ' | ' ORDER BY p) FROM unnest(plan_names) p), '') FROM index_files WHERE status = 'pending' ORDER BY file_size_bytes ASC NULLS LAST, id`
			args = []any{}
			if parseLimit > 0 {
				query += ` LIMIT $1`
				args = append(args, parseLimit)
			}
		}

		rows, err := conn.Query(ctx, query, args...)
		if err != nil {
			log.Fatalf("❌ Failed to query pending files: %v", err)
		}

		type pendingFile struct {
			ID       int
			Location string
			PlanName string
		}

		var files []pendingFile
		for rows.Next() {
			var f pendingFile
			if err := rows.Scan(&f.ID, &f.Location, &f.PlanName); err != nil {
				log.Printf("⚠️ Failed to scan row: %v", err)
				continue
			}
			files = append(files, f)
		}
		rows.Close()

		if len(files) == 0 {
			log.Println("✅ No pending files found in index_files. Run -discover first, or all files are already completed.")
			return
		}

		log.Printf("Found %d pending file(s). Processing...", len(files))

		if *dryRunFlag {
			log.Println("🔍 Dry-run mode — streaming only, no DB writes.")
		}

		seenBillingCodes := make(map[string]bool)
		seenNPIs := make(map[int64]string)
		for i, f := range files {
			parseRates(ctx, conn, f.ID, f.Location, f.PlanName, i == 0, seenBillingCodes, seenNPIs, *dryRunFlag)
		}
		if !*dryRunFlag {
			writeNPILookup(seenNPIs)
		}
		return
	}

	log.Println("⚠️ Please specify an action: -discover or -parse (and optionally -test)")
}
