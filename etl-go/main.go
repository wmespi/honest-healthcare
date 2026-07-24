package main

import (
	"context"
	"flag"
	"log"

	"github.com/jackc/pgx/v5"
)

func main() {
	discoverFlag := flag.Bool("discover", false, "Run index discovery logic")
	parseFlag := flag.Bool("parse", false, "Run rate parsing logic")
	limitFlag := flag.Int("limit", 0, "Override the default limits for discovery or parsing")
	indexUrlFlag := flag.String("index-url", "", "Override the Master Index URL")
	testFlag := flag.Bool("test", false, "Run in isolated test mode (writes to test schema, not production)")
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

		query := `SELECT id, location, COALESCE(array_to_string(plan_names, ' | '), '') FROM index_files WHERE status = 'pending' ORDER BY file_size_bytes ASC NULLS LAST, id`
		args := []any{}
		if parseLimit > 0 {
			query += ` LIMIT $1`
			args = append(args, parseLimit)
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

		seenBillingCodes := make(map[string]bool)
		for i, f := range files {
			parseRates(ctx, conn, f.ID, f.Location, f.PlanName, i == 0, seenBillingCodes)
		}
		return
	}

	log.Println("⚠️ Please specify an action: -discover or -parse (and optionally -test)")
}
