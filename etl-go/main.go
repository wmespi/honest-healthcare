package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"strconv"
	"strings"

	"github.com/jackc/pgx/v5"
)

const usageText = `etl-go — Anthem MRF ingestion

Usage: etl-go <command> [flags]

Commands:
  discover   Phase 1 — sync the Anthem master index into the index_files queue
  parse      Phase 2 — stream pending files into Parquet (+ a little Postgres)
  size       Backfill index_files.file_size_bytes via concurrent HEAD requests
  nppes      Download the NPPES national file, write the GA subset Parquet
  fixture    Build a truncated *.json.gz fixture from a file id or URL

Run "etl-go <command> -h" for that command's flags.
Most workflows have a matching "make" target — see "make help".
`

func main() {
	if len(os.Args) < 2 {
		fmt.Fprint(os.Stderr, usageText)
		os.Exit(2)
	}

	cmd, args := os.Args[1], os.Args[2:]
	switch cmd {
	case "discover":
		cmdDiscover(args)
	case "parse":
		cmdParse(args)
	case "size":
		cmdSize(args)
	case "nppes":
		cmdNPPES(args)
	case "fixture":
		cmdFixture(args)
	case "help", "-h", "--help":
		fmt.Fprint(os.Stdout, usageText)
	default:
		if strings.HasPrefix(cmd, "-") {
			fmt.Fprintf(os.Stderr, "etl-go now uses subcommands — try %q instead of %q\n\n", strings.TrimLeft(cmd, "-"), cmd)
		} else {
			fmt.Fprintf(os.Stderr, "unknown command %q\n\n", cmd)
		}
		fmt.Fprint(os.Stderr, usageText)
		os.Exit(2)
	}
}

// applyTestMode swaps every output path and the DB URL to their test-isolation
// equivalents. Callers pass their own default limit (discovery caps reporting
// structures, parsing caps files) which only applies when -limit was not set.
func applyTestMode(limit *int, defaultLimit int) {
	DatabaseURL = TestDatabaseURL
	ExampleOutputPath = "../data-test/anthem/mrf_example.json"
	PricesOutputDir = "../data-test/anthem/prices"
	GroupSetsOutputDir = "../data-test/anthem/group_sets"
	ProvidersOutputDir = "../data-test/anthem/providers"
	CodesOutputDir = "../data-test/anthem/codes"
	NPILookupPath = "../data-test/anthem/npi_lookup.parquet"
	NPPESOutputPath = "../data-test/nppes/ga_providers.parquet"
	GAProvidersPath = "../data-test/nppes/ga_providers.parquet"
	if *limit == 0 {
		*limit = defaultLimit
	}
	log.Println("🧪 Test mode enabled — writing to 'test' schema and ../data-test/")
}

func resolveIndexURL(override string) {
	if override != "" {
		IndexURL = override
	} else {
		IndexURL = getDefaultIndexURL()
	}
}

// mustConnect opens the single pgx connection the DB-backed commands share.
func mustConnect(ctx context.Context) *pgx.Conn {
	conn, err := pgx.Connect(ctx, DatabaseURL)
	if err != nil {
		log.Fatalf("❌ Failed to connect to database: %v", err)
	}
	log.Printf("✅ Connected to database.")
	return conn
}

// parseFileIDs turns "1,2,3" into []int, fatal on a bad token.
func parseFileIDs(spec string) []int {
	parts := strings.Split(spec, ",")
	ids := make([]int, 0, len(parts))
	for _, p := range parts {
		id, err := strconv.Atoi(strings.TrimSpace(p))
		if err != nil {
			log.Fatalf("❌ Invalid file ID %q: %v", p, err)
		}
		ids = append(ids, id)
	}
	return ids
}

// ── discover ────────────────────────────────────────────────────────────────

func cmdDiscover(args []string) {
	fs := flag.NewFlagSet("discover", flag.ExitOnError)
	test := fs.Bool("test", false, "isolated test mode (test schema + ../data-test/)")
	schemaOnly := fs.Bool("index-schema", false, "stream the index, write data/anthem/index_schema.json, no DB writes")
	limit := fs.Int("limit", 0, "cap reporting structures (default 100 in test mode)")
	indexURL := fs.String("index-url", "", "override the monthly master-index URL")
	noCache := fs.Bool("no-cache", false, "force re-download of the master index")
	_ = fs.Parse(args)

	if *test {
		applyTestMode(limit, 100)
	}
	resolveIndexURL(*indexURL)

	ctx := context.Background()
	if *schemaOnly {
		discoverLinks(ctx, nil, *limit, *noCache, true)
		return
	}
	conn := mustConnect(ctx)
	defer conn.Close(ctx)
	discoverLinks(ctx, conn, *limit, *noCache, false)
}

// ── parse ───────────────────────────────────────────────────────────────────

func cmdParse(args []string) {
	fs := flag.NewFlagSet("parse", flag.ExitOnError)
	test := fs.Bool("test", false, "isolated test mode (test schema + ../data-test/)")
	limit := fs.Int("limit", 0, "cap files processed (default 1 in test mode)")
	fileIDs := fs.String("file-ids", "", "comma-separated index_files IDs to parse (skips queue ordering)")
	priority := fs.Bool("priority", false, "GA / individual-market files first (gaPriorityExpr)")
	fixture := fs.String("fixture", "", "read a local *.json.gz instead of downloading (with -file-ids N)")
	allNPIs := fs.Bool("all-npis", false, "keep every NPI/rate (disable the GA NPPES filter)")
	networks := fs.String("networks", "", "network_name allowlist, comma-separated (trailing * = prefix). Default 'GA *' unless -all-networks")
	allNetworks := fs.Bool("all-networks", false, "keep every network (disable the network_name allowlist)")
	dryRun := fs.Bool("dry-run", false, "stream only, skip all writes")
	indexURL := fs.String("index-url", "", "override the monthly master-index URL")
	_ = fs.Parse(args)

	if *test {
		applyTestMode(limit, 1)
	}
	resolveIndexURL(*indexURL)

	ctx := context.Background()
	conn := mustConnect(ctx)
	defer conn.Close(ctx)

	log.Println("🚀 Starting Anthem Bronze Layer: Rate Parsing (Go)...")

	var query string
	var qArgs []any
	if *fileIDs != "" {
		query = `SELECT id, location, COALESCE(array_to_string(market_types, ' | '), '') FROM index_files WHERE id = ANY($1) ORDER BY id`
		qArgs = []any{parseFileIDs(*fileIDs)}
	} else {
		order := `file_size_bytes ASC NULLS LAST, id`
		if *priority {
			order = gaPriorityExpr + ` DESC, file_size_bytes ASC NULLS LAST, id`
		}
		query = `SELECT id, location, COALESCE(array_to_string(market_types, ' | '), '') FROM index_files WHERE status = 'pending' ORDER BY ` + order
		qArgs = []any{}
		if *limit > 0 {
			query += ` LIMIT $1`
			qArgs = append(qArgs, *limit)
		}
	}

	rows, err := conn.Query(ctx, query, qArgs...)
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
		log.Println("✅ No pending files found in index_files. Run 'etl-go discover' first, or all files are already completed.")
		return
	}
	log.Printf("Found %d pending file(s). Processing...", len(files))

	if *dryRun {
		log.Println("🔍 Dry-run mode — streaming only, no DB writes.")
	}
	if *fixture != "" && len(files) != 1 {
		log.Fatalf("❌ -fixture needs exactly one -file-ids target (got %d)", len(files))
	}

	seenBillingCodes := make(map[string]bool)
	seenNPIs := make(map[int64]string)
	seenTINs := make(map[string]bool)

	var gaNPIs map[int64]struct{}
	if *allNPIs {
		log.Println("⚠️ -all-npis — keeping every NPI/rate (GA filter disabled)")
	} else {
		gaNPIs = loadGANPISet(GAProvidersPath)
		if gaNPIs == nil {
			log.Printf("ℹ️  no %s — keeping all NPIs (run 'etl-go nppes' first to enable the GA filter)", GAProvidersPath)
		}
	}

	// Network allowlist. Default 'GA *' unless overridden. NOT applied to
	// anthem/GA_* plan-specific files unless the user set -networks (their
	// network_name labels vary wildly). See etl-go/parse.md.
	networksSpec := *networks
	userSetNetworks := *networks != ""
	if *allNetworks {
		networksSpec = ""
		log.Println("⚠️ -all-networks — keeping every network (network_name allowlist disabled)")
	} else if networksSpec == "" {
		networksSpec = "GA *"
	}
	networkAllow := buildNetworkAllow(networksSpec)
	if networkAllow != nil {
		log.Printf("🗺️  network allowlist active — keeping only network_name in {%s} (skipped for anthem/GA_* files unless -networks is set)", networksSpec)
	}

	totalCodes := 0
	if !*dryRun {
		conn.QueryRow(ctx, "SELECT count(*) FROM billing_codes").Scan(&totalCodes)
	}

	for i, f := range files {
		fileNetworkAllow := networkAllow
		if !userSetNetworks && isGAPlanSpecific(f.Location) {
			fileNetworkAllow = nil // trust the GA_* filename; keep every network
		}
		res := parseRates(ctx, conn, f.ID, f.Location, f.PlanName, *fixture,
			i == 0, seenBillingCodes, seenNPIs, seenTINs, gaNPIs, fileNetworkAllow, totalCodes, *dryRun)
		if res != nil {
			totalCodes += res.NewBillingCodes
		}
	}
	if !*dryRun {
		writeNPILookup(seenNPIs)
	}
}

// ── size ────────────────────────────────────────────────────────────────────

func cmdSize(args []string) {
	fs := flag.NewFlagSet("size", flag.ExitOnError)
	test := fs.Bool("test", false, "isolated test mode (test schema)")
	limit := fs.Int("limit", 0, "cap rows to HEAD (0 = all unsized)")
	_ = fs.Parse(args)

	if *test {
		applyTestMode(limit, 0)
	}
	ctx := context.Background()
	conn := mustConnect(ctx)
	defer conn.Close(ctx)
	fetchFileSizes(ctx, conn, *limit)
}

// ── nppes ───────────────────────────────────────────────────────────────────

func cmdNPPES(args []string) {
	fs := flag.NewFlagSet("nppes", flag.ExitOnError)
	test := fs.Bool("test", false, "isolated test mode (../data-test/nppes/)")
	url := fs.String("url", "", "override the NPPES dissemination zip URL")
	file := fs.String("file", "", "use a local NPPES zip (or plain .csv) instead of downloading")
	limit := fs.Int("limit", 0, "cap GA rows written (0 = all)")
	_ = fs.Parse(args)

	if *test {
		applyTestMode(limit, 0)
	}
	runNPPES(*url, *file, *limit)
}

// ── fixture ─────────────────────────────────────────────────────────────────

func cmdFixture(args []string) {
	fs := flag.NewFlagSet("fixture", flag.ExitOnError)
	fileIDs := fs.String("file-ids", "", "index_files id to build the fixture from")
	url := fs.String("url", "", "source URL (alternative to -file-ids)")
	name := fs.String("name", "", "output name (default: the file id)")
	indexURL := fs.String("index-url", "", "override the monthly master-index URL")
	_ = fs.Parse(args)

	resolveIndexURL(*indexURL)

	firstFileID := 0
	if *fileIDs != "" {
		firstFileID = parseFileIDs(*fileIDs)[0]
	}

	ctx := context.Background()
	if *url != "" {
		makeFixture(ctx, nil, firstFileID, *url, *name)
		return
	}
	conn := mustConnect(ctx)
	defer conn.Close(ctx)
	makeFixture(ctx, conn, firstFileID, "", *name)
}
