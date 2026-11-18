package core

// DataRoot is the data folder, relative to the etl module dir (ingestion/etl/).
// Each payer's own files and what parse and discover keep about them live in
// data/raw/<payer>/ (Payer), which only ingestion reads; NPPES, Georgia's plan
// file and the hospitals' own rates live in data/ingestion/, which integration
// reads too (docs/schema.md). ApplyTestMode moves them all under data-test/;
// nothing else should mutate these after startup.
var (
	DataRoot string

	GAProvidersPath    string
	NPPESOutputPath    string
	NPPESLocationsPath string
	ExchangePlansPath  string
	// HospitalRatesDir holds one <ccn>.parquet per hospital, and
	// HospitalRatesRawDir each hospital's price file while it is read.
	HospitalRatesDir    string
	HospitalRatesRawDir string
)

func init() { usePaths("../../data") }

// ApplyTestMode swaps every path to its test-isolation equivalent under
// data-test/, so a test run never touches data/.
func ApplyTestMode() { usePaths("../../data-test") }

func usePaths(root string) {
	DataRoot = root
	GAProvidersPath = root + "/ingestion/nppes/ga_providers.parquet"
	NPPESOutputPath = GAProvidersPath
	NPPESLocationsPath = root + "/ingestion/nppes/ga_locations.parquet"
	ExchangePlansPath = root + "/ingestion/reference/exchange_plans_ga.parquet"
	HospitalRatesDir = root + "/ingestion/hospital_rates"
	HospitalRatesRawDir = root + "/raw/hospital_rates"
}
