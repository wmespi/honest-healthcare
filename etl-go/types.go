package main

import (
	"fmt"
	"os"
	"time"
)

var (
	DatabaseURL        = "postgres://postgres:postgres@db:5432/honest_healthcare?sslmode=disable"
	TestDatabaseURL    = "postgres://postgres:postgres@db:5432/honest_healthcare?sslmode=disable&search_path=test"
	IndexURL           = ""
	ExampleOutputPath  = "../data/anthem/mrf_example.json"
	RatesOutputDir     = "../data/anthem/rates"
	ProvidersOutputDir = "../data/anthem/providers"
	CodesOutputDir     = "../data/anthem/codes"
	NPILookupPath      = "../data/anthem/npi_lookup.parquet"
)

func init() {
	if url := os.Getenv("DATABASE_URL"); url != "" {
		DatabaseURL = url
	}
	if url := os.Getenv("TEST_DATABASE_URL"); url != "" {
		TestDatabaseURL = url
	}
}

func getDefaultIndexURL() string {
	now := time.Now()
	return fmt.Sprintf("https://antm-pt-prod-dataz-nogbd-nophi-us-east1.s3.amazonaws.com/anthem/%04d-%02d-01_anthem_index.json.gz", now.Year(), now.Month())
}

type ReportingPlan struct {
	PlanName        string `json:"plan_name"`
	PlanID          string `json:"plan_id"`
	PlanIDType      string `json:"plan_id_type"`
	PlanMarketType  string `json:"plan_market_type"`
	PlanSponsorName string `json:"plan_sponsor_name"`
	IssuerName      string `json:"issuer_name"`
}

type InNetworkFile struct {
	Description string `json:"description"`
	Location    string `json:"location"`
}

type ReportingStructure struct {
	ReportingPlans []ReportingPlan `json:"reporting_plans"`
	InNetworkFiles []InNetworkFile `json:"in_network_files"`
}

type CandidateFile struct {
	Description string   `json:"description"`
	Location    string   `json:"location"`
	PlanNames   []string `json:"plan_names"`
}

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

type NegotiatedPrice struct {
	NegotiatedType string   `json:"negotiated_type"`
	NegotiatedRate float64  `json:"negotiated_rate"`
	ExpirationDate string   `json:"expiration_date"`
	ServiceCode    []string `json:"service_code"`
	BillingClass   string   `json:"billing_class"`
	Setting        string   `json:"setting"`
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

type RateRow struct {
	ProviderGroupID        int64   `parquet:"provider_group_id"`
	PlanName               string  `parquet:"plan_name"`
	BillingCodeType        string  `parquet:"billing_code_type"`
	BillingCode            string  `parquet:"billing_code"`
	NegotiationArrangement string  `parquet:"negotiation_arrangement"`
	NegotiatedType         string  `parquet:"negotiated_type"`
	NegotiatedRate         float64 `parquet:"negotiated_rate"`
	ExpirationDate         string  `parquet:"expiration_date"`
	ServiceCode            string  `parquet:"service_code"`
	BillingClass           string  `parquet:"billing_class"`
	Setting                string  `parquet:"setting"`
}

type ProviderRow struct {
	ProviderGroupID int64  `parquet:"provider_group_id"`
	NPI             int64  `parquet:"npi"`
	TINType         string `parquet:"tin_type"`
	TINValue        string `parquet:"tin_value"`
}

type BillingCodeRow struct {
	BillingCodeType string `parquet:"billing_code_type"`
	BillingCode     string `parquet:"billing_code"`
	Name            string `parquet:"name"`
	Description     string `parquet:"description"`
}

type NPILookupRow struct {
	NPI      int64  `parquet:"npi"`
	TINValue string `parquet:"tin_value"`
}
