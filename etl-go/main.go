package main

import (
	"encoding/json"
	"flag"
	"log"
	"os"
)

func main() {
	discoverFlag := flag.Bool("discover", false, "Run index discovery logic")
	parseFlag := flag.Bool("parse", false, "Run rate parsing logic")
	limitFlag := flag.Int("limit", 0, "Override the default limits for discovery or parsing")
	indexUrlFlag := flag.String("index-url", "", "Override the Master Index URL")
	testFlag := flag.Bool("test", false, "Run in isolated test mode (protects production data)")
	flag.Parse()

	// 1. Enforce Test Isolation Rules
	if *testFlag {
		OutputPath = "../data-test/anthem/index_urls.json"
		ParseOutputPath = "../data-test/anthem/parsed_rates.csv"
		ParseProvidersOutputPath = "../data-test/anthem/parsed_providers.csv"
		ParseBillingCodesOutputPath = "../data-test/anthem/parsed_billing_codes.csv"
		ExampleOutputPath = "../data-test/anthem/mrf_example.json"
		if *limitFlag == 0 {
			*limitFlag = 100 // Default to 100 files in test mode
		}
	} else {
		OutputPath = "../data/anthem/index_urls.json" // Production path
		ParseOutputPath = "../data/anthem/parsed_rates.csv"
		ParseProvidersOutputPath = "../data/anthem/parsed_providers.csv"
		ParseBillingCodesOutputPath = "../data/anthem/parsed_billing_codes.csv"
		ExampleOutputPath = "../data/anthem/mrf_example.json"
	}

	// 2. Set the Index URL
	if *indexUrlFlag != "" {
		IndexURL = *indexUrlFlag
	} else {
		IndexURL = getDefaultIndexURL()
	}

	// 3. Routing
	if *discoverFlag {
		discoverLinks(*limitFlag)
		return
	}
	
	if *parseFlag {
		log.Println("🚀 Starting Anthem Bronze Layer: Rate Parsing (Go)...")
		
		file, err := os.Open(OutputPath)
		if err != nil {
			log.Fatalf("❌ Failed to open index_urls.json: %v", err)
		}
		defer file.Close()

		var candidates []CandidateFile
		if err := json.NewDecoder(file).Decode(&candidates); err != nil {
			log.Fatalf("❌ Failed to parse index_urls.json: %v", err)
		}

		if len(candidates) == 0 {
			log.Fatalf("❌ No candidate rate files found in %s", OutputPath)
		}

		limit := len(candidates)
		if *testFlag {
			limit = 1 // Only parse the first 1 in test mode
		}

		log.Printf("Found %d candidate files. Processing %d files...", len(candidates), limit)
		
		// Clear old parsed files to prevent duplicating data on subsequent runs
		os.Remove(ParseOutputPath)
		os.Remove(ParseProvidersOutputPath)
		os.Remove(ParseBillingCodesOutputPath)
		
		seenBillingCodes := make(map[string]bool)
		
		for i := 0; i < limit; i++ {
			// Pass i == 0 so we only write the CSV header on the very first file
			parseRates(candidates[i].Location, candidates[i].PlanNames[0], i == 0, seenBillingCodes)
		}
		return
	}

	log.Println("⚠️ Please specify an action: -discover or -parse (and optionally -test)")
}
