package main

import (
	"fmt"
	"os"
	"time"
)

var (
	DatabaseURL = "postgres://postgres:postgres@db:5432/honest_healthcare?sslmode=disable"
	IndexURL        = ""
	OutputPath               = "../data/anthem/index_urls.json"
	ParseOutputPath          = "../data/anthem/parsed_rates.csv"
	ParseProvidersOutputPath = "../data/anthem/parsed_providers.csv"
	ParseBillingCodesOutputPath = "../data/anthem/parsed_billing_codes.csv"
	ExampleOutputPath        = "../data/anthem/mrf_example.json"
	ProdLimit                = int64(5 * 1024 * 1024 * 1024)
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

// Structs for parsing Provider References in Rate files
type ProviderGroup struct {
	NPIs []int `json:"npi"`
	TIN  struct {
		Type  string `json:"type"`
		Value string `json:"value"`
	} `json:"tin"`
}

type ProviderReference struct {
	ProviderGroupID int             `json:"provider_group_id"`
	ProviderGroups  []ProviderGroup `json:"provider_groups"`
}

// Structs for parsing Negotiated Rates in Rate files
type NegotiatedPrice struct {
	NegotiatedType string  `json:"negotiated_type"`
	NegotiatedRate float64 `json:"negotiated_rate"`
	ExpirationDate string  `json:"expiration_date"`
	ServiceCode    []string `json:"service_code"`
}

type NegotiatedRate struct {
	ProviderReferences []int             `json:"provider_references"`
	NegotiatedPrices   []NegotiatedPrice `json:"negotiated_prices"`
}

type InNetworkItem struct {
	NegotiationArrangement string           `json:"negotiation_arrangement"`
	Name                   string           `json:"name"`
	BillingCodeType        string           `json:"billing_code_type"`
	BillingCode            string           `json:"billing_code"`
	Description            string           `json:"description"`
	NegotiatedRates        []NegotiatedRate `json:"negotiated_rates"`
}


