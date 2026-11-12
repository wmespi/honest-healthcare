package core

import (
	"fmt"
	"time"
)

// Mutable process config. The paths are relative to the etl module dir
// (ingestion/etl/): Anthem's files and what parse and discover keep about them
// live in data/raw/anthem/, which only ingestion reads, and NPPES and Georgia's
// plan file in data/ingestion/, which integration reads too (docs/schema.md).
// ApplyTestMode moves them all under data-test/; nothing else should mutate
// these after startup.
var (
	IndexURL = ""

	ExampleOutputPath  string
	PricesOutputDir    string
	GroupSetsOutputDir string
	ProvidersOutputDir string
	CodesOutputDir     string
	ParsesDir          string
	IndexFilesPath     string
	IndexFilePlansPath string
	GAProvidersPath    string
	NPPESOutputPath    string
	NPPESLocationsPath string
	ExchangePlansPath  string
)

func init() { usePaths("../../data") }

// ApplyTestMode swaps every path to its test-isolation equivalent under
// data-test/, so a test run never touches data/.
func ApplyTestMode() { usePaths("../../data-test") }

func usePaths(root string) {
	anthem := root + "/raw/anthem"
	ExampleOutputPath = anthem + "/mrf_example.json"
	PricesOutputDir = anthem + "/prices"
	GroupSetsOutputDir = anthem + "/group_sets"
	ProvidersOutputDir = anthem + "/providers"
	CodesOutputDir = anthem + "/codes"
	ParsesDir = anthem + "/parses"
	IndexFilesPath = anthem + "/index_files.parquet"
	IndexFilePlansPath = anthem + "/index_file_plans.parquet"
	GAProvidersPath = root + "/ingestion/nppes/ga_providers.parquet"
	NPPESOutputPath = GAProvidersPath
	NPPESLocationsPath = root + "/ingestion/nppes/ga_locations.parquet"
	ExchangePlansPath = root + "/ingestion/reference/exchange_plans_ga.parquet"
}

// DefaultIndexURL is the current month's Anthem master-index URL.
func DefaultIndexURL() string {
	now := time.Now()
	return fmt.Sprintf("https://antm-pt-prod-dataz-nogbd-nophi-us-east1.s3.amazonaws.com/anthem/%04d-%02d-01_anthem_index.json.gz", now.Year(), now.Month())
}
