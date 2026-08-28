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
	indexSchemaFlag := flag.Bool("index-schema", false, "Stream the master index and write a compact schema example to data/anthem/index_schema.json (no DB writes)")
	parseFlag := flag.Bool("parse", false, "Run rate parsing logic")
	sizeFlag := flag.Bool("size", false, "Fetch file sizes (HEAD requests) for all unsized index_files entries")
	limitFlag := flag.Int("limit", 0, "Override the default limits for discovery or parsing")
	indexUrlFlag := flag.String("index-url", "", "Override the Master Index URL")
	testFlag := flag.Bool("test", false, "Run in isolated test mode (writes to test schema, not production)")
	fileIDsFlag := flag.String("file-ids", "", "Comma-separated list of index_files IDs to parse (skips normal queue ordering)")
	makeFixtureFlag := flag.Bool("make-fixture", false, "Stream one MRF and write a truncated *.json.gz fixture to testdata/fixtures/")
	fixtureURLFlag := flag.String("fixture-url", "", "Source URL for -make-fixture (alternative to -file-ids)")
	fixtureNameFlag := flag.String("fixture-name", "", "Output name for -make-fixture (default: the file id)")
	fixtureFlag := flag.String("fixture", "", "Parse a local *.json.gz fixture instead of downloading (use with -parse -file-ids N)")
	priorityFlag := flag.Bool("priority", false, "Parse GA/individual priority files first (see gaPriorityOrder)")
	dryRunFlag := flag.Bool("dry-run", false, "Stream and capture schema but skip all DB writes")
	noCacheFlag := flag.Bool("no-cache", false, "Force re-download of the master index even if a local cache exists")
	flag.Parse()

	ctx := context.Background()

	// 1. Test mode isolation — swap to test schema before any DB connection is made
	if *testFlag {
		DatabaseURL = TestDatabaseURL
		ExampleOutputPath = "../data-test/anthem/mrf_example.json"
		RatesOutputDir = "../data-test/anthem/rates"
		ProvidersOutputDir = "../data-test/anthem/providers"
		CodesOutputDir = "../data-test/anthem/codes"
		NPILookupPath = "../data-test/anthem/npi_lookup.parquet"
		if *limitFlag == 0 {
			*limitFlag = 100
		}
		log.Println("🧪 Test mode enabled — writing to 'test' schema and ../data-test/")
	}

	// 2. Set Index URL
	if *indexUrlFlag != "" {
		IndexURL = *indexUrlFlag
	} else {
		IndexURL = getDefaultIndexURL()
	}

	// 2b. Index-schema mode: stream the index, write the schema example, no DB needed.
	if *indexSchemaFlag {
		discoverLinks(ctx, nil, *limitFlag, *noCacheFlag, true)
		return
	}

	// 2c. Fixture mode with an explicit URL needs no DB.
	firstFileID := 0
	if *fileIDsFlag != "" {
		if id, err := strconv.Atoi(strings.TrimSpace(strings.Split(*fileIDsFlag, ",")[0])); err == nil {
			firstFileID = id
		}
	}
	if *makeFixtureFlag && *fixtureURLFlag != "" {
		makeFixture(ctx, nil, firstFileID, *fixtureURLFlag, *fixtureNameFlag)
		return
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
		discoverLinks(ctx, conn, *limitFlag, *noCacheFlag, false)
		return
	}

	if *sizeFlag {
		fetchFileSizes(ctx, conn, *limitFlag)
		return
	}

	if *makeFixtureFlag {
		makeFixture(ctx, conn, firstFileID, *fixtureURLFlag, *fixtureNameFlag)
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
			query = `SELECT id, location, COALESCE(array_to_string(market_types, ' | '), '') FROM index_files WHERE id = ANY($1) ORDER BY id`
			args = []any{ids}
		} else {
			order := `file_size_bytes ASC NULLS LAST, id`
			if *priorityFlag {
				// GA / individual files first, then smallest-first within each tier.
				order = gaPriorityExpr + ` DESC, file_size_bytes ASC NULLS LAST, id`
			}
			query = `SELECT id, location, COALESCE(array_to_string(market_types, ' | '), '') FROM index_files WHERE status = 'pending' ORDER BY ` + order
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

		if *fixtureFlag != "" && len(files) != 1 {
			log.Fatalf("❌ -fixture needs exactly one -file-ids target (got %d)", len(files))
		}

		seenBillingCodes := make(map[string]bool)
		seenNPIs := make(map[int64]string)
		seenTINs := make(map[string]bool)

		totalCodes := 0
		if !*dryRunFlag {
			conn.QueryRow(ctx, "SELECT count(*) FROM billing_codes").Scan(&totalCodes)
		}

		for i, f := range files {
			res := parseRates(ctx, conn, f.ID, f.Location, f.PlanName, *fixtureFlag,
				i == 0, seenBillingCodes, seenNPIs, seenTINs, totalCodes, *dryRunFlag)
			if res != nil {
				totalCodes += res.NewBillingCodes
			}
		}
		if !*dryRunFlag {
			writeNPILookup(seenNPIs)
		}
		return
	}

	log.Println("⚠️ Please specify an action: -discover, -index-schema, -size, or -parse (and optionally -test, -no-cache)")
}
