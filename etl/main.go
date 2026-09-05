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

	"github.com/wmespi/honest-healthcare/etl/core"
	"github.com/wmespi/honest-healthcare/etl/discovery"
	"github.com/wmespi/honest-healthcare/etl/extraction"
	"github.com/wmespi/honest-healthcare/etl/fixture"
	"github.com/wmespi/honest-healthcare/etl/nppes"
)

const usageText = `etl — Anthem MRF ingestion

Usage: etl <command> [flags]

Commands:
  discover   Phase 1 — sync the Anthem master index into the index_files queue
  parse      Phase 2 — stream pending files into Parquet (+ a little Postgres)
  size       Backfill index_files.file_size_bytes via concurrent HEAD requests
  nppes      Download the NPPES national file, write the GA subset Parquet
  fixture    Build a truncated *.json.gz fixture from a file id or URL

Run "etl <command> -h" for that command's flags.
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
			fmt.Fprintf(os.Stderr, "etl now uses subcommands — try %q instead of %q\n\n", strings.TrimLeft(cmd, "-"), cmd)
		} else {
			fmt.Fprintf(os.Stderr, "unknown command %q\n\n", cmd)
		}
		fmt.Fprint(os.Stderr, usageText)
		os.Exit(2)
	}
}

func resolveIndexURL(override string) {
	if override != "" {
		core.IndexURL = override
	} else {
		core.IndexURL = core.DefaultIndexURL()
	}
}

// mustConnect opens the single pgx connection the DB-backed commands share.
func mustConnect(ctx context.Context) *pgx.Conn {
	conn, err := pgx.Connect(ctx, core.DatabaseURL)
	if err != nil {
		log.Fatalf("❌ Failed to connect to database: %v", err)
	}
	log.Printf("✅ Connected to database.")
	return conn
}

// parseFileIDs turns "1,2,3" into []int, fatal on a bad token.
func parseFileIDs(spec string) []int {
	if spec == "" {
		return nil
	}
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
		core.ApplyTestMode()
		if *limit == 0 {
			*limit = 100
		}
		log.Println("🧪 Test mode enabled — writing to 'test' schema and ../data-test/")
	}
	resolveIndexURL(*indexURL)

	ctx := context.Background()
	if *schemaOnly {
		discovery.Run(ctx, nil, *limit, *noCache, true)
		return
	}
	conn := mustConnect(ctx)
	defer conn.Close(ctx)
	discovery.Run(ctx, conn, *limit, *noCache, false)
}

// ── parse ───────────────────────────────────────────────────────────────────

func cmdParse(args []string) {
	fs := flag.NewFlagSet("parse", flag.ExitOnError)
	test := fs.Bool("test", false, "isolated test mode (test schema + ../data-test/)")
	limit := fs.Int("limit", 0, "cap files processed (default 1 in test mode)")
	fileIDs := fs.String("file-ids", "", "comma-separated index_files IDs to parse (skips target selection)")
	targets := fs.String("targets", extraction.DefaultTargetsPath, `target-plan list; only files serving one of its plans are parsed ("" = every pending file)`)
	fixturePath := fs.String("fixture", "", "read a local *.json.gz instead of downloading (with -file-ids N)")
	allNPIs := fs.Bool("all-npis", false, "keep every NPI/rate (disable the GA NPPES filter)")
	networks := fs.String("networks", "", "network_name allowlist, comma-separated (trailing * = prefix). Default 'GA *' unless -all-networks")
	allNetworks := fs.Bool("all-networks", false, "keep every network (disable the network_name allowlist)")
	dryRun := fs.Bool("dry-run", false, "stream only, skip all writes")
	indexURL := fs.String("index-url", "", "override the monthly master-index URL")
	_ = fs.Parse(args)

	if *test {
		core.ApplyTestMode()
		if *limit == 0 {
			*limit = 1
		}
		log.Println("🧪 Test mode enabled — writing to 'test' schema and ../data-test/")
	}
	resolveIndexURL(*indexURL)

	ctx := context.Background()
	conn := mustConnect(ctx)
	defer conn.Close(ctx)

	err := extraction.Run(ctx, conn, extraction.Options{
		FileIDs:     parseFileIDs(*fileIDs),
		Targets:     *targets,
		Limit:       *limit,
		Fixture:     *fixturePath,
		AllNPIs:     *allNPIs,
		Networks:    *networks,
		AllNetworks: *allNetworks,
		DryRun:      *dryRun,
	})
	if err != nil {
		log.Fatalf("❌ %v", err)
	}
}

// ── size ────────────────────────────────────────────────────────────────────

func cmdSize(args []string) {
	fs := flag.NewFlagSet("size", flag.ExitOnError)
	test := fs.Bool("test", false, "isolated test mode (test schema)")
	limit := fs.Int("limit", 0, "cap rows to HEAD (0 = all unsized)")
	_ = fs.Parse(args)

	if *test {
		core.ApplyTestMode()
		log.Println("🧪 Test mode enabled — writing to 'test' schema and ../data-test/")
	}
	ctx := context.Background()
	conn := mustConnect(ctx)
	defer conn.Close(ctx)
	discovery.Sizes(ctx, conn, *limit)
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
		core.ApplyTestMode()
		log.Println("🧪 Test mode enabled — writing to 'test' schema and ../data-test/")
	}
	nppes.Run(*url, *file, *limit)
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
	if ids := parseFileIDs(*fileIDs); len(ids) > 0 {
		firstFileID = ids[0]
	}

	ctx := context.Background()
	if *url != "" {
		fixture.Make(ctx, nil, firstFileID, *url, *name)
		return
	}
	conn := mustConnect(ctx)
	defer conn.Close(ctx)
	fixture.Make(ctx, conn, firstFileID, "", *name)
}
