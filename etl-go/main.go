package main

import (
	"flag"
	"log"
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
		if *limitFlag == 0 {
			*limitFlag = 100 // Default to 100 files in test mode to prevent accidental massive runs
		}
	} else {
		OutputPath = "../data/anthem/index_urls.json" // Production path
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
		log.Println("🚧 ETL Parse logic is ready to be fleshed out.")
		return
	}

	log.Println("⚠️ Please specify an action: -discover or -parse (and optionally -test)")
}
