package core

import (
	"fmt"
	"time"
)

// Mutable process config. ApplyTestMode rewrites the paths for an isolated test
// run; nothing else should mutate these after startup.
var (
	IndexURL = ""

	ExampleOutputPath  = "../data/anthem/mrf_example.json"
	PricesOutputDir    = "../data/anthem/prices"
	GroupSetsOutputDir = "../data/anthem/group_sets"
	ProvidersOutputDir = "../data/anthem/providers"
	CodesOutputDir     = "../data/anthem/codes"
	ParsesDir          = "../data/anthem/parses"
	NPILookupPath      = "../data/anthem/npi_lookup.parquet"
	IndexFilesPath     = "../data/anthem/index_files.parquet"
	IndexFilePlansPath = "../data/anthem/index_file_plans.parquet"
	GAProvidersPath    = "../data/nppes/ga_providers.parquet"
	NPPESOutputPath    = "../data/nppes/ga_providers.parquet"
	ExchangePlansPath  = "../data/reference/exchange_plans_ga.parquet"
)

// ApplyTestMode swaps every path to its test-isolation equivalent under
// ../data-test/, so a test run never touches ../data/.
func ApplyTestMode() {
	ExampleOutputPath = "../data-test/anthem/mrf_example.json"
	PricesOutputDir = "../data-test/anthem/prices"
	GroupSetsOutputDir = "../data-test/anthem/group_sets"
	ProvidersOutputDir = "../data-test/anthem/providers"
	CodesOutputDir = "../data-test/anthem/codes"
	ParsesDir = "../data-test/anthem/parses"
	NPILookupPath = "../data-test/anthem/npi_lookup.parquet"
	IndexFilesPath = "../data-test/anthem/index_files.parquet"
	IndexFilePlansPath = "../data-test/anthem/index_file_plans.parquet"
	NPPESOutputPath = "../data-test/nppes/ga_providers.parquet"
	GAProvidersPath = "../data-test/nppes/ga_providers.parquet"
	ExchangePlansPath = "../data-test/reference/exchange_plans_ga.parquet"
}

// DefaultIndexURL is the current month's Anthem master-index URL.
func DefaultIndexURL() string {
	now := time.Now()
	return fmt.Sprintf("https://antm-pt-prod-dataz-nogbd-nophi-us-east1.s3.amazonaws.com/anthem/%04d-%02d-01_anthem_index.json.gz", now.Year(), now.Month())
}
