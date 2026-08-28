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

			res, err := streamMRF(gz, "individual | group", 1, true,
				map[string]bool{}, map[int64]string{}, map[string]bool{}, nil, nil,
				mrfWriters{}, nil)
			if err != nil {
				t.Fatalf("streamMRF: %v", err)
			}
			if res.ProviderRows == 0 && res.PriceRows == 0 {
				t.Errorf("fixture produced no rows")
			}
			// Every price row's group_set_id must have membership edges.
			if res.PriceRows > 0 && res.GroupSetMemberRows == 0 {
				t.Errorf("price rows but no group-set edges")
			}
			t.Logf("%d provider rows, %d price rows, %d group-set edges (%d sets), %d codes, networks=%v",
				res.ProviderRows, res.PriceRows, res.GroupSetMemberRows, res.GroupSets,
				res.NewBillingCodes, sortedKeys(res.NetworkNames))
		})
	}
}

const testFileID = int64(7)

type collected struct {
	res     *mrfResult
	prices  []PriceRow
	members []GroupSetMemberRow
	provs   []ProviderRow
	codes   []BillingCodeRow
}

// membersBySet groups the collected membership edges by group_set_id.
func (c collected) membersBySet() map[int64][]int64 {
	m := map[int64][]int64{}
	for _, e := range c.members {
		m[e.GroupSetID] = append(m[e.GroupSetID], e.ProviderGroupID)
	}
	return m
}

func collect(t *testing.T, path string) collected {
	return collectFiltered2(t, path, nil, nil)
}

func collectFiltered(t *testing.T, path string, gaNPIs map[int64]struct{}) collected {
	return collectFiltered2(t, path, gaNPIs, nil)
}

func collectFiltered2(t *testing.T, path string, gaNPIs map[int64]struct{}, networkAllow func(string) bool) collected {
	t.Helper()
	f, err := os.Open(path)
	if err != nil {
		t.Fatalf("open %s: %v", path, err)
	}
	defer f.Close()

	var c collected
	w := mrfWriters{
		prices:          func(r []PriceRow) { c.prices = append(c.prices, r...) },
		groupSetMembers: func(r []GroupSetMemberRow) { c.members = append(c.members, r...) },
		providers:       func(p []ProviderRow) { c.provs = append(c.provs, p...) },
		code:            func(bc BillingCodeRow) { c.codes = append(c.codes, bc) },
	}

	res, err := streamMRF(f, "individual | group", testFileID, true,
		map[string]bool{}, map[int64]string{}, map[string]bool{}, gaNPIs, networkAllow, w, nil)
	if err != nil {
		t.Fatalf("streamMRF: %v", err)
	}
	c.res = res
	return c
}

func TestStreamMRF_Counts(t *testing.T) {
	c := collect(t, "testdata/synthetic_mrf.json")
	res := c.res

	if got := len(c.provs); got != 5 {
		t.Errorf("provider rows = %d, want 5", got)
	}
	if res.ProviderRows != 5 {
		t.Errorf("res.ProviderRows = %d, want 5", res.ProviderRows)
	}
	// 99213 block1: 2 networks × 2 prices = 4; block2: 1 × 1 = 1; 80053: 1 × 1 = 1.
	if got := len(c.prices); got != 6 {
		t.Errorf("price rows = %d, want 6", got)
	}
	if res.PriceRows != 6 {
		t.Errorf("res.PriceRows = %d, want 6", res.PriceRows)
	}
	// Distinct rosters: {1001}, {1002}, {1003}. {1001} is reused by 80053, not re-emitted.
	if res.GroupSets != 3 {
		t.Errorf("res.GroupSets = %d, want 3", res.GroupSets)
	}
	if got := len(c.members); got != 3 {
		t.Errorf("group-set edges = %d, want 3", got)
	}
	if res.GroupSetMemberRows != 3 {
		t.Errorf("res.GroupSetMemberRows = %d, want 3", res.GroupSetMemberRows)
	}
	for _, e := range c.members {
		if e.FileID != testFileID {
			t.Errorf("member edge file_id = %d, want %d", e.FileID, testFileID)
		}
	}
	if got := len(c.codes); got != 2 {
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
	c := collect(t, "testdata/synthetic_mrf.json")

	wantByGroup := map[int64]string{
		1001: "GA Blue Value HIX Individual Network",
		1002: "GA Blue Open Access POS Network",
		1003: "",
	}

	// Provider rows carry the group's network directly.
	for _, p := range c.provs {
		if want := wantByGroup[p.ProviderGroupID]; p.NetworkName != want {
			t.Errorf("provider group %d network_name = %q, want %q", p.ProviderGroupID, p.NetworkName, want)
		}
	}

	// Each price row's group_set must contain only groups whose network matches
	// the price row's network_name.
	bySet := c.membersBySet()
	for _, pr := range c.prices {
		for _, gid := range bySet[pr.GroupSetID] {
			if want := wantByGroup[gid]; want != pr.NetworkName {
				t.Errorf("price row net=%q references group %d (net %q)", pr.NetworkName, gid, want)
			}
		}
	}
}

func TestStreamMRF_GroupSetReuseAcrossCodes(t *testing.T) {
	c := collect(t, "testdata/synthetic_mrf.json")
	// 99213 and 80053 both price the roster {1001} under the GA Blue Value
	// network — they must share one group_set_id, emitted once.
	var gvSets = map[int64]struct{}{}
	for _, pr := range c.prices {
		if pr.NetworkName == "GA Blue Value HIX Individual Network" {
			gvSets[pr.GroupSetID] = struct{}{}
		}
	}
	if len(gvSets) != 1 {
		t.Errorf("GA Blue Value price rows span %d group_set_ids, want 1 (shared roster)", len(gvSets))
	}
}

func TestStreamMRF_ServiceCodeJoin(t *testing.T) {
	c := collect(t, "testdata/synthetic_mrf.json")
	found := false
	for _, r := range c.prices {
		if r.ServiceCode == "11|22" {
			found = true
		}
	}
	if !found {
		t.Errorf("expected a price row with service_code %q (|-joined array)", "11|22")
	}
}

func TestStreamMRF_SetsAndReportingEntity(t *testing.T) {
	res := collect(t, "testdata/synthetic_mrf.json").res

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
	// Group 1003 (NPI 5555555555) has no GA NPI → dropped, and its price row too.
	gaNPIs := map[int64]struct{}{2222222222: {}, 4444444444: {}}
	c := collectFiltered(t, "testdata/synthetic_mrf.json", gaNPIs)
	res := c.res

	if len(c.provs) != 2 {
		t.Errorf("provider rows = %d, want 2 (only the GA NPIs)", len(c.provs))
	}
	if res.ProviderRowsDropped != 3 {
		t.Errorf("ProviderRowsDropped = %d, want 3", res.ProviderRowsDropped)
	}
	if res.GroupsDropped != 1 {
		t.Errorf("GroupsDropped = %d, want 1 (group 1003)", res.GroupsDropped)
	}
	if len(c.prices) != 5 {
		t.Errorf("price rows = %d, want 5 (group 1003's block dropped)", len(c.prices))
	}
	if res.PriceRowsDropped != 1 {
		t.Errorf("PriceRowsDropped = %d, want 1", res.PriceRowsDropped)
	}
	bySet := c.membersBySet()
	for _, pr := range c.prices {
		for _, gid := range bySet[pr.GroupSetID] {
			if gid == 1003 {
				t.Errorf("price row references dropped group 1003")
			}
		}
	}
	for _, p := range c.provs {
		if _, ok := gaNPIs[p.NPI]; !ok {
			t.Errorf("provider row for non-GA NPI %d leaked through", p.NPI)
		}
	}
}

func TestStreamMRF_NetworkAllowlist(t *testing.T) {
	// Exact match — only group 1001 ("GA Blue Value HIX Individual Network").
	exact := buildNetworkAllow("GA Blue Value HIX Individual Network")
	c := collectFiltered2(t, "testdata/synthetic_mrf.json", nil, exact)
	if len(c.provs) != 3 {
		t.Errorf("exact: provider rows = %d, want 3 (group 1001 only)", len(c.provs))
	}
	if len(c.prices) != 3 {
		t.Errorf("exact: price rows = %d, want 3", len(c.prices))
	}
	if c.res.GroupsDroppedNetwork != 2 {
		t.Errorf("exact: GroupsDroppedNetwork = %d, want 2 (1002 + 1003)", c.res.GroupsDroppedNetwork)
	}
	bySet := c.membersBySet()
	for _, pr := range c.prices {
		for _, gid := range bySet[pr.GroupSetID] {
			if gid != 1001 {
				t.Errorf("exact: leaked price row for group %d", gid)
			}
		}
	}

	// Prefix match — groups 1001 and 1002 (both "GA ..."), 1003 (no network) dropped.
	prefix := buildNetworkAllow("GA *")
	c2 := collectFiltered2(t, "testdata/synthetic_mrf.json", nil, prefix)
	if len(c2.provs) != 4 {
		t.Errorf("prefix: provider rows = %d, want 4 (1001+1002)", len(c2.provs))
	}
	if len(c2.prices) != 5 {
		t.Errorf("prefix: price rows = %d, want 5", len(c2.prices))
	}
	if c2.res.GroupsDroppedNetwork != 1 {
		t.Errorf("prefix: GroupsDroppedNetwork = %d, want 1 (group 1003)", c2.res.GroupsDroppedNetwork)
	}
}

func TestStreamMRF_NetworkAndNPIFilterCombine(t *testing.T) {
	// Network allowlist keeps 1001 + 1002; GA NPI set then keeps only NPI
	// 2222222222 (in 1001) — so 1002 survives the network filter but is dropped
	// by the NPI filter (its only NPI 4444444444 isn't GA).
	c := collectFiltered2(t, "testdata/synthetic_mrf.json",
		map[int64]struct{}{2222222222: {}}, buildNetworkAllow("GA *"))
	if len(c.provs) != 1 || c.provs[0].NPI != 2222222222 {
		t.Errorf("combined: provider rows = %+v, want just NPI 2222222222", c.provs)
	}
	bySet := c.membersBySet()
	for _, pr := range c.prices {
		for _, gid := range bySet[pr.GroupSetID] {
			if gid != 1001 {
				t.Errorf("combined: leaked price row for group %d", gid)
			}
		}
	}
	if c.res.GroupsDroppedNetwork != 1 {
		t.Errorf("combined: GroupsDroppedNetwork = %d, want 1 (group 1003)", c.res.GroupsDroppedNetwork)
	}
}

func TestSlugifyNetwork(t *testing.T) {
	cases := map[string]string{
		"GA Blue Value HIX Individual Network": "ga-blue-value-hix-individual-network",
		"EXCHANGES SPECIALIST  GATEKEEPER":     "exchanges-specialist-gatekeeper",
		"CO HMO|CO PPO":                        "co-hmo-co-ppo",
		"  ":                                   "_unattributed",
		"":                                     "_unattributed",
		"A/B — C":                              "a-b-c",
	}
	for in, want := range cases {
		if got := slugifyNetwork(in); got != want {
			t.Errorf("slugifyNetwork(%q) = %q, want %q", in, got, want)
		}
	}
}

// buildPriceRows collector for the unit tests below.
func priceRows(t *testing.T, item InNetworkItem, netByGroup map[int64]string, kept map[int64]struct{}) ([]PriceRow, map[int64][]int64) {
	t.Helper()
	seen := map[int64]struct{}{}
	members := map[int64][]int64{}
	emit := func(_, gsid int64, ids []int64) {
		cp := append([]int64(nil), ids...)
		members[gsid] = cp
	}
	var dropped int64
	rows := buildPriceRows(item, testFileID, netByGroup, kept, seen, emit, &dropped)
	return rows, members
}

func TestBuildPriceRows_NetworkSplit(t *testing.T) {
	item := InNetworkItem{
		BillingCode: "99999", BillingCodeType: "CPT",
		NegotiatedRates: []NegotiatedRate{{
			ProviderReferences: []int{1, 2},
			NegotiatedPrices:   []NegotiatedPrice{{NegotiatedRate: 10}},
		}},
	}
	rows, _ := priceRows(t, item, map[int64]string{1: "GA One", 2: "GA Two"}, nil)
	if len(rows) != 2 {
		t.Fatalf("got %d rows, want 2 (one per network bucket)", len(rows))
	}
	got := map[string]bool{rows[0].NetworkName: true, rows[1].NetworkName: true}
	if !got["GA One"] || !got["GA Two"] {
		t.Errorf("networks not split out: %+v", got)
	}
}

func TestBuildPriceRows_MultiNetworkGroup(t *testing.T) {
	item := InNetworkItem{
		BillingCode: "99999", BillingCodeType: "CPT",
		NegotiatedRates: []NegotiatedRate{{
			ProviderReferences: []int{1},
			NegotiatedPrices:   []NegotiatedPrice{{NegotiatedRate: 10}},
		}},
	}
	rows, members := priceRows(t, item, map[int64]string{1: "GA One|GA Two"}, nil)
	if len(rows) != 2 {
		t.Fatalf("got %d rows, want 2 (group in two networks)", len(rows))
	}
	for _, r := range rows {
		if got := members[r.GroupSetID]; len(got) != 1 || got[0] != 1 {
			t.Errorf("group_set %d members = %v, want [1]", r.GroupSetID, got)
		}
	}
}

func TestBuildPriceRows_PriceFanOut(t *testing.T) {
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
	// Both refs in the same network → one roster {7,8}, 2 prices → 2 price rows.
	rows, members := priceRows(t, item, map[int64]string{7: "Net", 8: "Net"}, nil)
	if len(rows) != 2 {
		t.Fatalf("got %d rows, want 2 (2 prices, one roster)", len(rows))
	}
	if got := members[rows[0].GroupSetID]; len(got) != 2 {
		t.Errorf("roster = %v, want 2 members", got)
	}
}

func TestBuildPriceRows_FilterEmptiesRoster(t *testing.T) {
	item := InNetworkItem{
		BillingCode: "99999", BillingCodeType: "CPT",
		NegotiatedRates: []NegotiatedRate{{
			ProviderReferences: []int{1, 2},
			NegotiatedPrices:   []NegotiatedPrice{{NegotiatedRate: 1}, {NegotiatedRate: 2}},
		}},
	}
	seen := map[int64]struct{}{}
	var dropped int64
	rows := buildPriceRows(item, testFileID, map[int64]string{1: "N", 2: "N"},
		map[int64]struct{}{}, seen, nil, &dropped)
	if len(rows) != 0 {
		t.Errorf("all groups filtered → want 0 price rows, got %d", len(rows))
	}
	if dropped != 2 {
		t.Errorf("dropped = %d, want 2 (both prices)", dropped)
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
	c := collectFiltered(t, "testdata/synthetic_mrf.json", nil)
	if len(c.provs) != 5 || len(c.prices) != 6 {
		t.Errorf("nil filter changed output: provs=%d prices=%d", len(c.provs), len(c.prices))
	}
	if c.res.ProviderRowsDropped != 0 || c.res.PriceRowsDropped != 0 {
		t.Errorf("nil filter reported drops: %+v", c.res)
	}
}
