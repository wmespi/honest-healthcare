package core

import (
	"fmt"
	"os"
	"time"
)

// Mutable process config. ApplyTestMode rewrites the paths + DB URL for an
// isolated test run; nothing else should mutate these after startup.
var (
	DatabaseURL     = "postgres://postgres:postgres@db:5432/honest_healthcare?sslmode=disable"
	TestDatabaseURL = "postgres://postgres:postgres@db:5432/honest_healthcare?sslmode=disable&search_path=test"
	IndexURL        = ""

	ExampleOutputPath  = "../data/anthem/mrf_example.json"
	PricesOutputDir    = "../data/anthem/prices"
	GroupSetsOutputDir = "../data/anthem/group_sets"
	ProvidersOutputDir = "../data/anthem/providers"
	CodesOutputDir     = "../data/anthem/codes"
	NPILookupPath      = "../data/anthem/npi_lookup.parquet"
	GAProvidersPath    = "../data/nppes/ga_providers.parquet"
	NPPESOutputPath    = "../data/nppes/ga_providers.parquet"
)

func init() {
	if url := os.Getenv("DATABASE_URL"); url != "" {
		DatabaseURL = url
	}
	if url := os.Getenv("TEST_DATABASE_URL"); url != "" {
		TestDatabaseURL = url
	}
}

// ApplyTestMode swaps the DB URL and every output path to their test-isolation
// equivalents (the `test` schema + ../data-test/). Call before opening any
// database connection.
func ApplyTestMode() {
	DatabaseURL = TestDatabaseURL
	ExampleOutputPath = "../data-test/anthem/mrf_example.json"
	PricesOutputDir = "../data-test/anthem/prices"
	GroupSetsOutputDir = "../data-test/anthem/group_sets"
	ProvidersOutputDir = "../data-test/anthem/providers"
	CodesOutputDir = "../data-test/anthem/codes"
	NPILookupPath = "../data-test/anthem/npi_lookup.parquet"
	NPPESOutputPath = "../data-test/nppes/ga_providers.parquet"
	GAProvidersPath = "../data-test/nppes/ga_providers.parquet"
}

// DefaultIndexURL is the current month's Anthem master-index URL.
func DefaultIndexURL() string {
	now := time.Now()
	return fmt.Sprintf("https://antm-pt-prod-dataz-nogbd-nophi-us-east1.s3.amazonaws.com/anthem/%04d-%02d-01_anthem_index.json.gz", now.Year(), now.Month())
}
