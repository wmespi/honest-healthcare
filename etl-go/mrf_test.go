package main

import (
	"os"
	"testing"
)

// collect runs streamMRF over a decompressed JSON reader and gathers every row.
func collect(t *testing.T, path string) (*mrfResult, []RateRow, []ProviderRow, []BillingCodeRow) {
	t.Helper()
	f, err := os.Open(path)
	if err != nil {
		t.Fatalf("open %s: %v", path, err)
	}
	defer f.Close()

	var rates []RateRow
	var provs []ProviderRow
	var codes []BillingCodeRow
	w := mrfWriters{
		rates:     func(r []RateRow) { rates = append(rates, r...) },
		providers: func(p []ProviderRow) { provs = append(provs, p...) },
		code:      func(c BillingCodeRow) { codes = append(codes, c) },
	}

	res, err := streamMRF(f, "individual | group", true,
		map[string]bool{}, map[int64]string{}, map[string]bool{}, w, nil)
	if err != nil {
		t.Fatalf("streamMRF: %v", err)
	}
	return res, rates, provs, codes
}

func TestStreamMRF_Counts(t *testing.T) {
	res, rates, provs, codes := collect(t, "testdata/synthetic_mrf.json")

	if got := len(provs); got != 5 {
		t.Errorf("provider rows = %d, want 5", got)
	}
	if res.ProviderRows != 5 {
		t.Errorf("res.ProviderRows = %d, want 5", res.ProviderRows)
	}
	if got := len(rates); got != 6 {
		t.Errorf("rate rows = %d, want 6", got)
	}
	if res.RateRows != 6 {
		t.Errorf("res.RateRows = %d, want 6", res.RateRows)
	}
	if got := len(codes); got != 2 {
		t.Errorf("billing code rows = %d, want 2", got)
	}
	if res.NewBillingCodes != 2 {
		t.Errorf("res.NewBillingCodes = %d, want 2", res.NewBillingCodes)
	}
	if res.NewNPIs != 5 {
		t.Errorf("res.NewNPIs = %d, want 5", res.NewNPIs)
	}
	if res.NewTINs != 4 {
		t.Errorf("res.NewTINs = %d, want 4", res.NewTINs)
	}
}

func TestStreamMRF_NetworkNameAttribution(t *testing.T) {
	_, rates, provs, _ := collect(t, "testdata/synthetic_mrf.json")

	wantByGroup := map[int64]string{
		1001: "GA Blue Value HIX Individual Network",
		1002: "GA Blue Open Access POS Network",
		1003: "",
	}
	for _, r := range rates {
		if want := wantByGroup[r.ProviderGroupID]; r.NetworkName != want {
			t.Errorf("rate group %d network_name = %q, want %q", r.ProviderGroupID, r.NetworkName, want)
		}
	}
	for _, p := range provs {
		if want := wantByGroup[p.ProviderGroupID]; p.NetworkName != want {
			t.Errorf("provider group %d network_name = %q, want %q", p.ProviderGroupID, p.NetworkName, want)
		}
	}
}

func TestStreamMRF_ServiceCodeJoin(t *testing.T) {
	_, rates, _, _ := collect(t, "testdata/synthetic_mrf.json")
	found := false
	for _, r := range rates {
		if r.ServiceCode == "11|22" {
			found = true
		}
	}
	if !found {
		t.Errorf("expected a rate row with service_code %q (|-joined array)", "11|22")
	}
}

func TestStreamMRF_SetsAndReportingEntity(t *testing.T) {
	res, _, _, _ := collect(t, "testdata/synthetic_mrf.json")

	if res.ReportingEntityName != "Anthem Blue Cross and Blue Shield Georgia" {
		t.Errorf("reporting_entity_name = %q", res.ReportingEntityName)
	}
	if res.ReportingEntityType != "Health Insurance Network" {
		t.Errorf("reporting_entity_type = %q", res.ReportingEntityType)
	}
	assertSet(t, "settings", res.Settings, "inpatient", "outpatient")
	assertSet(t, "billing_classes", res.BillingClasses, "institutional", "professional")
	assertSet(t, "billing_code_types", res.BillingCodeTypes, "CPT")
	assertSet(t, "network_names", res.NetworkNames,
		"GA Blue Open Access POS Network", "GA Blue Value HIX Individual Network")
}

func assertSet(t *testing.T, label string, got map[string]struct{}, want ...string) {
	t.Helper()
	keys := sortedKeys(got)
	if len(keys) != len(want) {
		t.Errorf("%s = %v, want %v", label, keys, want)
		return
	}
	for i := range want {
		if keys[i] != want[i] {
			t.Errorf("%s = %v, want %v", label, keys, want)
			return
		}
	}
}

func TestHiosStateCode(t *testing.T) {
	cases := []struct {
		id, idType, want string
	}{
		{"45334GA0770001", "HIOS", "GA"},
		{"12345tx0010002", "HIOS", "TX"},
		{"49046", "HIOS", ""},          // too short for [5:7]
		{"45334GA07", "EIN", ""},       // not HIOS
		{"45334120770001", "HIOS", ""}, // digits, not letters
	}
	for _, c := range cases {
		got := hiosStateCode(ReportingPlan{PlanID: c.id, PlanIDType: c.idType})
		if got != c.want {
			t.Errorf("hiosStateCode(%q,%q) = %q, want %q", c.id, c.idType, got, c.want)
		}
	}
}

func TestBuildRateRows_PriceFanOut(t *testing.T) {
	item := InNetworkItem{
		BillingCode:     "99214",
		BillingCodeType: "CPT",
		NegotiatedRates: []NegotiatedRate{{
			ProviderReferences: []int{7, 8},
			NegotiatedPrices: []NegotiatedPrice{
				{NegotiatedRate: 1, ServiceCode: []string{"11"}},
				{NegotiatedRate: 2, ServiceCode: []string{"22"}},
			},
		}},
	}
	rows := buildRateRows(item, map[int64]string{7: "Net7"}, "plan")
	if len(rows) != 4 { // 2 refs × 2 prices
		t.Fatalf("got %d rows, want 4", len(rows))
	}
	if rows[0].NetworkName != "Net7" || rows[2].NetworkName != "" {
		t.Errorf("network attribution wrong: %q / %q", rows[0].NetworkName, rows[2].NetworkName)
	}
}
