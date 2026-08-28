package core

// MRF JSON shapes (index + in-network file) and the Parquet row structs written
// by the extraction pass. Shared by discovery, extraction, nppes, and fixture.

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
	NetworkName     []string        `json:"network_name"`
	ProviderGroups  []ProviderGroup `json:"provider_groups"`
}

type NegotiatedPrice struct {
	NegotiatedType      string   `json:"negotiated_type"`
	NegotiatedRate      float64  `json:"negotiated_rate"`
	ExpirationDate      string   `json:"expiration_date"`
	ServiceCode         []string `json:"service_code"`
	BillingClass        string   `json:"billing_class"`
	BillingCodeModifier []string `json:"billing_code_modifier"`
	Setting             string   `json:"setting"`
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

// PriceRow is one negotiated price for a billing code, attributed to a network
// and a provider-group *set* (group_set_id) rather than fanned out one row per
// member. The roster behind group_set_id lives in GroupSetMemberRow. Partitioned
// on disk by network_name (prices/net=<slug>/<file_id>.parquet), so a
// network-filtered query still prunes to one directory.
type PriceRow struct {
	FileID                 int64   `parquet:"file_id"`
	GroupSetID             int64   `parquet:"group_set_id"`
	NetworkName            string  `parquet:"network_name"`
	BillingCodeType        string  `parquet:"billing_code_type"`
	BillingCode            string  `parquet:"billing_code"`
	NegotiationArrangement string  `parquet:"negotiation_arrangement"`
	NegotiatedType         string  `parquet:"negotiated_type"`
	NegotiatedRate         float64 `parquet:"negotiated_rate"`
	ExpirationDate         string  `parquet:"expiration_date"`
	ServiceCode            string  `parquet:"service_code"`
	BillingClass           string  `parquet:"billing_class"`
	// Modifier is the sorted, "|"-joined billing_code_modifier array (e.g. "26",
	// "TC", "26|TC"). It splits the price for one base code into its component
	// parts — 26 = professional (physician work), TC = technical (equipment/
	// facility), none = global. Empty for ~89% of rows.
	Modifier string `parquet:"modifier"`
	Setting  string `parquet:"setting"`
}

// GroupSetMemberRow is one membership edge of a deduplicated provider-group set.
// Written once per distinct (file, group_set_id) roster and joined back from
// PriceRow on (file_id, group_set_id). provider_group_id is the MRF's file-local
// provider_reference id — join to ProviderRow on (file_id, provider_group_id).
type GroupSetMemberRow struct {
	FileID          int64 `parquet:"file_id"`
	GroupSetID      int64 `parquet:"group_set_id"`
	ProviderGroupID int64 `parquet:"provider_group_id"`
}

type ProviderRow struct {
	FileID          int64  `parquet:"file_id"`
	ProviderGroupID int64  `parquet:"provider_group_id"`
	NetworkName     string `parquet:"network_name"`
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

// NPPESRow is one Georgia provider from the NPPES national dissemination file,
// filtered to practice-location state == "GA".
type NPPESRow struct {
	NPI           int64  `parquet:"npi"`
	EntityType    string `parquet:"entity_type"` // "individual" | "organization"
	OrgName       string `parquet:"org_name"`
	LastName      string `parquet:"last_name"`
	FirstName     string `parquet:"first_name"`
	TaxonomyCode  string `parquet:"taxonomy_code"`
	TaxonomyGroup string `parquet:"taxonomy_group"`
	IsHospital    bool   `parquet:"is_hospital"`
	IsClinic      bool   `parquet:"is_clinic"`
	AddressLine1  string `parquet:"address_line1"` // practice-location street address
	AddressLine2  string `parquet:"address_line2"` // suite / floor, often empty
	City          string `parquet:"city"`
	State         string `parquet:"state"`
	PostalCode    string `parquet:"postal_code"`
}
