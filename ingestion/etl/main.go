package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"strings"

	"github.com/wmespi/honest-healthcare/ingestion/etl/core"
	"github.com/wmespi/honest-healthcare/ingestion/etl/discovery"
	"github.com/wmespi/honest-healthcare/ingestion/etl/extraction"
	"github.com/wmespi/honest-healthcare/ingestion/etl/fixture"
	"github.com/wmespi/honest-healthcare/ingestion/etl/nppes"
)

const usageText = `etl — Anthem MRF ingestion

Usage: etl <command> [flags]

Commands:
  discover   Write this month's file list and plan links from the Anthem master index
  parse      Stream the files ingestion/etl/targets.yaml names into Parquet
  nppes      Download the NPPES national file, write Georgia's providers and their secondary locations
  fixture    Build a truncated *.json.gz fixture from a file name or URL

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

// splitList turns "a,b" into ["a", "b"], and "" into nil.
func splitList(spec string) []string {
	var out []string
	for _, p := range strings.Split(spec, ",") {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}

// ── discover ────────────────────────────────────────────────────────────────

func cmdDiscover(args []string) {
	fs := flag.NewFlagSet("discover", flag.ExitOnError)
	test := fs.Bool("test", false, "isolated test mode (../data-test/)")
	schemaOnly := fs.Bool("index-schema", false, "stream the index, write data/raw/anthem/index_schema.json and nothing else")
	limit := fs.Int("limit", 0, "cap reporting structures, with -test or -index-schema only (default 100 in test mode)")
	indexURL := fs.String("index-url", "", "override the monthly master-index URL")
	noCache := fs.Bool("no-cache", false, "download the master index again even if a copy of this month's is kept")
	keepIndex := fs.Bool("keep-index", false, "keep the downloaded master index after the run, so the next run of the month reads it from disk")
	_ = fs.Parse(args)

	if *test {
		core.ApplyTestMode()
		if *limit == 0 {
			*limit = 100
		}
		log.Println("🧪 Test mode enabled - writing to ../data-test/")
	}
	// A limited read lists only part of the index, and parse and the build read
	// this month's lists as the whole of it.
	if *limit > 0 && !*test && !*schemaOnly {
		log.Fatalf("❌ -limit reads part of the index, so it runs only with -test or -index-schema")
	}
	resolveIndexURL(*indexURL)
	discovery.Run(discovery.Options{Limit: *limit, KeepIndex: *keepIndex, NoCache: *noCache, SchemaOnly: *schemaOnly})
}

// ── parse ───────────────────────────────────────────────────────────────────

func cmdParse(args []string) {
	fs := flag.NewFlagSet("parse", flag.ExitOnError)
	test := fs.Bool("test", false, "isolated test mode (../data-test/)")
	limit := fs.Int("limit", 0, "cap files processed (default 1 in test mode)")
	files := fs.String("files", "", "comma-separated file names from this month's index to parse now (skips target selection and the parse record)")
	targets := fs.String("targets", extraction.DefaultTargetsPath, `target-plan list; only the files it names are parsed ("" needs -files or -fixture, and turns off the probe's network signal)`)
	fixturePath := fs.String("fixture", "", "parse a local *.json.gz instead of a file from the index")
	allNPIs := fs.Bool("all-npis", false, "keep every NPI/rate (disable the GA NPPES filter)")
	minGroups := fs.Int("min-groups", 1, "probe: abort a file before in_network unless this many provider groups survive the filter (0 = no overlap check)")
	dryRun := fs.Bool("dry-run", false, "stream only, skip all writes")
	indexURL := fs.String("index-url", "", "override the monthly master-index URL")
	_ = fs.Parse(args)

	if *test {
		core.ApplyTestMode()
		if *limit == 0 {
			*limit = 1
		}
		log.Println("🧪 Test mode enabled - writing to ../data-test/")
	}
	resolveIndexURL(*indexURL)

	err := extraction.Run(context.Background(), extraction.Options{
		Files:     splitList(*files),
		Targets:   *targets,
		Limit:     *limit,
		Fixture:   *fixturePath,
		AllNPIs:   *allNPIs,
		MinGroups: *minGroups,
		DryRun:    *dryRun,
	})
	if err != nil {
		log.Fatalf("❌ %v", err)
	}
}

// ── nppes ───────────────────────────────────────────────────────────────────

func cmdNPPES(args []string) {
	fs := flag.NewFlagSet("nppes", flag.ExitOnError)
	test := fs.Bool("test", false, "isolated test mode (../data-test/ingestion/nppes/)")
	url := fs.String("url", "", "override the NPPES dissemination zip URL")
	file := fs.String("file", "", "use a local NPPES zip (or plain .csv) instead of downloading")
	locations := fs.String("locations-file", "", "with a plain .csv -file: the secondary practice locations, as a plain .csv")
	limit := fs.Int("limit", 0, "cap GA rows written (0 = all)")
	_ = fs.Parse(args)

	if *test {
		core.ApplyTestMode()
		log.Println("🧪 Test mode enabled - writing to ../data-test/")
	}
	nppes.Run(*url, *file, *locations, *limit)
}

// ── fixture ─────────────────────────────────────────────────────────────────

func cmdFixture(args []string) {
	fs := flag.NewFlagSet("fixture", flag.ExitOnError)
	file := fs.String("file", "", "name of the file in this month's index to build the fixture from")
	url := fs.String("url", "", "source URL (alternative to -file)")
	name := fs.String("name", "", "output name (default: the file's name)")
	indexURL := fs.String("index-url", "", "override the monthly master-index URL")
	_ = fs.Parse(args)

	resolveIndexURL(*indexURL)

	switch {
	case *url != "":
		fixture.Make(*url, *name)
	case *file != "":
		f, err := extraction.FindFile(*file)
		if err != nil {
			log.Fatalf("❌ %v", err)
		}
		if *name == "" {
			*name = strings.TrimSuffix(f.Name, ".json.gz")
		}
		fixture.Make(f.Location, *name)
	default:
		log.Fatalf("❌ fixture needs -file NAME or -url URL")
	}
}
