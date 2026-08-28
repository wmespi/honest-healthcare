package main

import (
	"compress/gzip"
	"os"
	"path/filepath"
	"testing"
)

// TestFixtures_Parse runs the real streamMRF over every committed *.json.gz
// fixture — a regression guard that the parser still handles each distinct MRF
// shape (a GA plan file, a vision/dental file, the file that failed in July).
// Add a fixture only when a file has a genuinely new shape, not one per file.
func TestFixtures_Parse(t *testing.T) {
	matches, _ := filepath.Glob("testdata/fixtures/*.json.gz")
	if len(matches) == 0 {
		t.Fatal("no fixtures found")
	}
	for _, path := range matches {
		t.Run(filepath.Base(path), func(t *testing.T) {
			f, err := os.Open(path)
			if err != nil {
				t.Fatal(err)
			}
			defer f.Close()
			gz, err := gzip.NewReader(f)
			if err != nil {
				t.Fatal(err)
			}
			defer gz.Close()

			res, err := streamMRF(gz, "individual | group", true,
				map[string]bool{}, map[int64]string{}, map[string]bool{}, nil, nil,
				mrfWriters{}, nil)
			if err != nil {
				t.Fatalf("streamMRF: %v", err)
			}
			if res.ProviderRows == 0 && res.RateRows == 0 {
				t.Errorf("fixture produced no rows")
			}
			t.Logf("%d provider rows, %d rate rows, %d codes, networks=%v",
				res.ProviderRows, res.RateRows, res.NewBillingCodes, sortedKeys(res.NetworkNames))
		})
	}
}

// collect runs streamMRF over a decompressed JSON reader and gathers every row.
func collect(t *testing.T, path string) (*mrfResult, []RateRow, []ProviderRow, []BillingCodeRow) {
	return collectFiltered(t, path, nil)
}

func collectFiltered(t *testing.T, path string, gaNPIs map[int64]struct{}) (*mrfResult, []RateRow, []ProviderRow, []BillingCodeRow) {
	return collectFiltered2(t, path, gaNPIs, nil)
}

func collectFiltered2(t *testing.T, path string, gaNPIs map[int64]struct{}, networkAllow func(string) bool) (*mrfResult, []RateRow, []ProviderRow, []BillingCodeRow) {
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
		map[string]bool{}, map[int64]string{}, map[string]bool{}, gaNPIs, networkAllow, w, nil)
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

func TestStreamMRF_GANPIFilter(t *testing.T) {
	// Keep NPI 2222222222 (in group 1001) and 4444444444 (in group 1002).
	// Group 1003 (NPI 5555555555) has no GA NPI → dropped, and its rate row too.
	gaNPIs := map[int64]struct{}{2222222222: {}, 4444444444: {}}
	res, rates, provs, _ := collectFiltered(t, "testdata/synthetic_mrf.json", gaNPIs)

	if len(provs) != 2 {
		t.Errorf("provider rows = %d, want 2 (only the GA NPIs)", len(provs))
	}
	if res.ProviderRowsDropped != 3 {
		t.Errorf("ProviderRowsDropped = %d, want 3", res.ProviderRowsDropped)
	}
	if res.GroupsDropped != 1 {
		t.Errorf("GroupsDropped = %d, want 1 (group 1003)", res.GroupsDropped)
	}
	if len(rates) != 5 {
		t.Errorf("rate rows = %d, want 5 (group 1003's rate dropped)", len(rates))
	}
	if res.RateRowsDropped != 1 {
		t.Errorf("RateRowsDropped = %d, want 1", res.RateRowsDropped)
	}
	for _, r := range rates {
		if r.ProviderGroupID == 1003 {
			t.Errorf("rate row for dropped group 1003 leaked through")
		}
	}
	for _, p := range provs {
		if _, ok := gaNPIs[p.NPI]; !ok {
			t.Errorf("provider row for non-GA NPI %d leaked through", p.NPI)
		}
	}
}

func TestStreamMRF_NetworkAllowlist(t *testing.T) {
	// Exact match — only group 1001 ("GA Blue Value HIX Individual Network").
	exact := buildNetworkAllow("GA Blue Value HIX Individual Network")
	res, rates, provs, _ := collectFiltered2(t, "testdata/synthetic_mrf.json", nil, exact)
	if len(provs) != 3 {
		t.Errorf("exact: provider rows = %d, want 3 (group 1001 only)", len(provs))
	}
	if len(rates) != 3 {
		t.Errorf("exact: rate rows = %d, want 3", len(rates))
	}
	if res.GroupsDroppedNetwork != 2 {
		t.Errorf("exact: GroupsDroppedNetwork = %d, want 2 (1002 + 1003)", res.GroupsDroppedNetwork)
	}
	for _, r := range rates {
		if r.ProviderGroupID != 1001 {
			t.Errorf("exact: leaked rate for group %d", r.ProviderGroupID)
		}
	}

	// Prefix match — groups 1001 and 1002 (both "GA ..."), 1003 (no network) dropped.
	prefix := buildNetworkAllow("GA *")
	res2, rates2, provs2, _ := collectFiltered2(t, "testdata/synthetic_mrf.json", nil, prefix)
	if len(provs2) != 4 {
		t.Errorf("prefix: provider rows = %d, want 4 (1001+1002)", len(provs2))
	}
	if len(rates2) != 5 {
		t.Errorf("prefix: rate rows = %d, want 5", len(rates2))
	}
	if res2.GroupsDroppedNetwork != 1 {
		t.Errorf("prefix: GroupsDroppedNetwork = %d, want 1 (group 1003)", res2.GroupsDroppedNetwork)
	}
}

func TestStreamMRF_NetworkAndNPIFilterCombine(t *testing.T) {
	// Network allowlist keeps 1001 + 1002; GA NPI set then keeps only NPI
	// 2222222222 (in 1001) — so 1002 survives the network filter but is dropped
	// by the NPI filter (its only NPI 4444444444 isn't GA).
	res, rates, provs, _ := collectFiltered2(t, "testdata/synthetic_mrf.json",
		map[int64]struct{}{2222222222: {}}, buildNetworkAllow("GA *"))
	if len(provs) != 1 || provs[0].NPI != 2222222222 {
		t.Errorf("combined: provider rows = %+v, want just NPI 2222222222", provs)
	}
	for _, r := range rates {
		if r.ProviderGroupID != 1001 {
			t.Errorf("combined: leaked rate for group %d", r.ProviderGroupID)
		}
	}
	if res.GroupsDroppedNetwork != 1 {
		t.Errorf("combined: GroupsDroppedNetwork = %d, want 1 (group 1003)", res.GroupsDroppedNetwork)
	}
}

func TestBuildNetworkAllow(t *testing.T) {
	if buildNetworkAllow("") != nil || buildNetworkAllow("  ") != nil {
		t.Fatal("empty spec should return nil (no filter)")
	}
	f := buildNetworkAllow("GA *, ACCESS NETWORK")
	cases := map[string]bool{
		"GA Blue Value HIX Individual Network":   true,
		"ACCESS NETWORK":                         true,
		"CO TRADITIONAL NETWORK":                 false,
		"NV HMO OA":                              false,
		"":                                       false,
		"CO HMO|GA Blue Open Access POS Network": true,  // |-joined — one member passes
		"GABC Something":                         false, // prefix is "GA " (with the space)
	}
	for name, want := range cases {
		if got := f(name); got != want {
			t.Errorf("allow(%q) = %v, want %v", name, got, want)
		}
	}
}

func TestStreamMRF_NilFilterKeepsEverything(t *testing.T) {
	res, rates, provs, _ := collectFiltered(t, "testdata/synthetic_mrf.json", nil)
	if len(provs) != 5 || len(rates) != 6 {
		t.Errorf("nil filter changed output: provs=%d rates=%d", len(provs), len(rates))
	}
	if res.ProviderRowsDropped != 0 || res.RateRowsDropped != 0 {
		t.Errorf("nil filter reported drops: %+v", res)
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
