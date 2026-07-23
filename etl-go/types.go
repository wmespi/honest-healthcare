package main

import (
	"fmt"
	"os"
	"time"
)

var (
	DatabaseURL = "postgres://postgres:postgres@db:5432/honest_healthcare?sslmode=disable"
	IndexURL    = ""
	OutputPath  = "../data/anthem/index_urls.json"
	ProdLimit   = int64(5 * 1024 * 1024 * 1024)
	TestLimit   = int64(250 * 1024 * 1024)
)

func init() {
	if url := os.Getenv("DATABASE_URL"); url != "" {
		DatabaseURL = url
	}
}

func getDefaultIndexURL() string {
	now := time.Now()
	// Anthem index URLs usually fall on the 1st of the month: YYYY-MM-01
	return fmt.Sprintf("https://antm-pt-prod-dataz-nogbd-nophi-us-east1.s3.amazonaws.com/anthem/%04d-%02d-01_anthem_index.json.gz", now.Year(), now.Month())
}

type ReportingPlan struct {
	PlanName string `json:"plan_name"`
}

type InNetworkFile struct {
	Description string `json:"description"`
	Location    string `json:"location"`
}

type ReportingStructure struct {
	ReportingPlans  []ReportingPlan `json:"reporting_plans"`
	InNetworkFiles  []InNetworkFile `json:"in_network_files"`
}

type CandidateFile struct {
	Description   string   `json:"description"`
	Location      string   `json:"location"`
	PlanNames     []string `json:"plan_names"`
	FileSizeBytes int64    `json:"file_size_bytes"`
}

type Facility struct {
	NPI          string
	BusinessName string
	TIN          string
}

type ProviderMap struct {
	NetworkName string
	Facilities  []Facility
}


