package extraction

import (
	"compress/gzip"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/wmespi/honest-healthcare/etl/core"
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
				map[string]bool{}, map[int64]string{}, map[string]bool{}, nil, providerProbe{},
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
	prices  []core.PriceRow
	members []core.GroupSetMemberRow
	provs   []core.ProviderRow
	codes   []core.BillingCodeRow
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
	c, err := collectProbed(t, path, nil, providerProbe{})
	if err != nil {
		t.Fatalf("streamMRF: %v", err)
	}
	return c
}

func collectFiltered(t *testing.T, path string, gaNPIs map[int64]struct{}) collected {
	c, err := collectProbed(t, path, gaNPIs, providerProbe{})
	if err != nil {
		t.Fatalf("streamMRF: %v", err)
	}
	return c
}

// collectProbed is the one that returns the error, because the probe tests are
// about a stream that stops early.
func collectProbed(t *testing.T, path string, gaNPIs map[int64]struct{}, pb providerProbe) (collected, error) {
	t.Helper()
	f, err := os.Open(path)
	if err != nil {
		t.Fatalf("open %s: %v", path, err)
	}
	defer f.Close()
	return streamCollect(f, gaNPIs, pb)
}

// streamDoc is collectProbed over an inline document, for shapes no committed
// fixture has (a national mirror shard, say).
func streamDoc(t *testing.T, doc string, gaNPIs map[int64]struct{}, pb providerProbe) (collected, error) {
	t.Helper()
	return streamCollect(strings.NewReader(doc), gaNPIs, pb)
}

func streamCollect(r io.Reader, gaNPIs map[int64]struct{}, pb providerProbe) (collected, error) {
	var c collected
	w := mrfWriters{
		prices:          func(r []core.PriceRow) { c.prices = append(c.prices, r...) },
		groupSetMembers: func(r []core.GroupSetMemberRow) { c.members = append(c.members, r...) },
		providers:       func(p []core.ProviderRow) { c.provs = append(c.provs, p...) },
		code:            func(bc core.BillingCodeRow) { c.codes = append(c.codes, bc) },
	}

	res, err := streamMRF(r, "individual | group", testFileID, true,
		map[string]bool{}, map[int64]string{}, map[string]bool{}, gaNPIs, pb, w, nil)
	c.res = res
	return c, err
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

func TestStreamMRF_ModifierSortedJoin(t *testing.T) {
	c := collect(t, "testdata/synthetic_mrf.json")
	var withMod, withoutMod int
	for _, r := range c.prices {
		switch r.Modifier {
		case "26|TC": // sorted from the fixture's ["TC","26"]
			withMod++
		case "":
			withoutMod++
		default:
			t.Errorf("unexpected modifier %q on a price row", r.Modifier)
		}
	}
	if withMod == 0 {
		t.Error(`expected a price row with modifier "26|TC" (sorted, |-joined billing_code_modifier)`)
	}
	if withoutMod == 0 {
		t.Error("expected most price rows to have an empty modifier")
	}
}

func TestJoinModifiers(t *testing.T) {
	cases := []struct {
		in   []string
		want string
	}{
		{nil, ""},
		{[]string{}, ""},
		{[]string{"26"}, "26"},
		{[]string{"TC", "26"}, "26|TC"},
		{[]string{" 26 ", "", "TC"}, "26|TC"},
	}
	for _, tc := range cases {
		if got := joinModifiers(tc.in); got != tc.want {
			t.Errorf("joinModifiers(%q) = %q, want %q", tc.in, got, tc.want)
		}
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

// ── the provider probe (#98) ────────────────────────────────────────────────
//
// The probe's job is to end a stream at the close of provider_references, so
// every assertion below is really two: the right verdict, and no in_network work
// done when the verdict is "abandon".

// probeMatcher builds the network predicate the way a target list does, so the
// probe tests exercise the same matching path targets.yaml drives.
func probeMatcher(t *testing.T, patterns ...string) providerProbe {
	t.Helper()
	ts := &TargetSet{Targets: []Target{{Name: "T", NetworkPatterns: patterns}}}
	m := ts.NetworkMatcher()
	if m == nil {
		t.Fatalf("NetworkMatcher() = nil for %v", patterns)
	}
	return providerProbe{minGroups: 1, networkMatch: m, networkSpec: strings.Join(patterns, ", ")}
}

func TestProbe_PassesWhenANetworkMatches(t *testing.T) {
	c, err := collectProbed(t, "testdata/synthetic_mrf.json", nil, probeMatcher(t, "GA Blue Value HIX*"))
	if err != nil {
		t.Fatalf("probe aborted a file that carries the target network: %v", err)
	}
	// Passing the probe is not a filter — every network in the file is written.
	if len(c.prices) != 6 || len(c.provs) != 5 {
		t.Errorf("probe changed the output: %d price rows, %d provider rows, want 6/5",
			len(c.prices), len(c.provs))
	}
	assertSet(t, "network_names", c.res.NetworkNames,
		"GA Blue Open Access POS Network", "GA Blue Value HIX Individual Network")
}

func TestProbe_AbortsWhenNoNetworkMatches(t *testing.T) {
	c, err := collectProbed(t, "testdata/synthetic_mrf.json", nil, probeMatcher(t, "CO Blue Priority*"))
	if !errors.Is(err, errNoWantedProviders) {
		t.Fatalf("error = %v, want errNoWantedProviders", err)
	}
	// The stream stopped at the end of provider_references: nothing from
	// in_network was read, let alone written.
	if len(c.prices) != 0 || len(c.codes) != 0 || len(c.members) != 0 {
		t.Errorf("in_network was parsed after the probe aborted: %d prices, %d codes, %d edges",
			len(c.prices), len(c.codes), len(c.members))
	}
	// The reason lands in index_files.failure_reason — it has to name both the
	// patterns that were wanted and the labels the file actually carries.
	msg := err.Error()
	for _, want := range []string{"probe: no wanted providers", "CO Blue Priority*", "GA Blue Value HIX Individual Network"} {
		if !strings.Contains(msg, want) {
			t.Errorf("abort reason %q does not mention %q", msg, want)
		}
	}
}

// The BlueCard case, and the whole reason the probe needs a second signal: a
// national mirror shard DOES list Georgia NPIs, so provider overlap waves it
// through. Only the network label says it is not our plan. ~40 GB was downloaded
// and rolled back on files of exactly this shape.
func TestProbe_AbortsBlueCardShardDespiteGANPIs(t *testing.T) {
	const shard = `{
	  "reporting_entity_name": "Anthem",
	  "provider_references": [
	    {"provider_group_id": 1, "network_name": ["BlueCard PPO National"],
	     "provider_groups": [{"npi": [2222222222], "tin": {"type": "ein", "value": "11-1"}}]},
	    {"provider_group_id": 2, "network_name": ["National Advantage Program"],
	     "provider_groups": [{"npi": [3333333333], "tin": {"type": "ein", "value": "11-2"}}]}
	  ],
	  "in_network": [
	    {"billing_code": "99213", "billing_code_type": "CPT", "negotiation_arrangement": "ffs",
	     "negotiated_rates": [{"provider_references": [1, 2],
	       "negotiated_prices": [{"negotiated_type": "negotiated", "negotiated_rate": 100.0,
	         "billing_class": "professional", "setting": "outpatient"}]}]}
	  ]
	}`
	// Both NPIs are Georgia NPIs, so the overlap signal is satisfied.
	gaNPIs := map[int64]struct{}{2222222222: {}, 3333333333: {}}
	c, err := streamDoc(t, shard, gaNPIs, probeMatcher(t, "GA Blue Value HIX*"))
	if !errors.Is(err, errNoWantedProviders) {
		t.Fatalf("error = %v, want errNoWantedProviders (network label mismatch)", err)
	}
	if c.res != nil {
		t.Error("an aborted probe must not return a result to log or promote")
	}
	if len(c.prices) != 0 {
		t.Errorf("in_network parsed on a shard the probe rejected: %d price rows", len(c.prices))
	}

	// Same shard, overlap signal only (no network_patterns) — it passes, which is
	// exactly why the network signal exists.
	if _, err := streamDoc(t, shard, gaNPIs, providerProbe{minGroups: 1}); err != nil {
		t.Fatalf("overlap-only probe rejected a shard full of GA NPIs: %v", err)
	}
}

// An abandoned file must leave nothing behind — including in the run-level NPI
// and TIN sets that npi_lookup.parquet is written from at the end of a run.
func TestProbe_AbortLeavesNoNPIsInTheRun(t *testing.T) {
	seenNPIs, seenTINs := map[int64]string{}, map[string]bool{}
	f, err := os.Open("testdata/synthetic_mrf.json")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	_, err = streamMRF(f, "individual", testFileID, false,
		map[string]bool{}, seenNPIs, seenTINs, nil,
		probeMatcher(t, "CO Blue Priority*"), mrfWriters{}, nil)
	if !errors.Is(err, errNoWantedProviders) {
		t.Fatalf("error = %v, want errNoWantedProviders", err)
	}
	if len(seenNPIs) != 0 || len(seenTINs) != 0 {
		t.Errorf("a skipped file left %d NPIs and %d TINs in the run", len(seenNPIs), len(seenTINs))
	}

	// The same file, probe passed: the NPIs land as before.
	if _, err := f.Seek(0, io.SeekStart); err != nil {
		t.Fatal(err)
	}
	if _, err := streamMRF(f, "individual", testFileID, false,
		map[string]bool{}, seenNPIs, seenTINs, nil,
		probeMatcher(t, "GA Blue Value HIX*"), mrfWriters{}, nil); err != nil {
		t.Fatalf("streamMRF: %v", err)
	}
	if len(seenNPIs) != 5 || len(seenTINs) != 4 {
		t.Errorf("after a passing parse: %d NPIs, %d TINs, want 5 and 4", len(seenNPIs), len(seenTINs))
	}
}

func TestProbe_AbortsWhenNoProviderOverlap(t *testing.T) {
	// No group survives the GA NPI filter → nothing in this file is ours.
	c, err := collectProbed(t, "testdata/synthetic_mrf.json",
		map[int64]struct{}{9999999999: {}}, providerProbe{minGroups: 1})
	if !errors.Is(err, errNoWantedProviders) {
		t.Fatalf("error = %v, want errNoWantedProviders", err)
	}
	if !strings.Contains(err.Error(), "provider groups") {
		t.Errorf("abort reason %q does not report the group counts", err)
	}
	if len(c.prices) != 0 {
		t.Errorf("in_network parsed after an overlap abort: %d price rows", len(c.prices))
	}
}

// The threshold is a floor, not a boolean: 3 groups survive the synthetic file,
// so a min of 3 passes and a min of 4 does not.
func TestProbe_MinGroupsThreshold(t *testing.T) {
	if _, err := collectProbed(t, "testdata/synthetic_mrf.json", nil, providerProbe{minGroups: 3}); err != nil {
		t.Errorf("min-groups 3 rejected a file with 3 groups: %v", err)
	}
	if _, err := collectProbed(t, "testdata/synthetic_mrf.json", nil, providerProbe{minGroups: 4}); !errors.Is(err, errNoWantedProviders) {
		t.Errorf("min-groups 4 accepted a file with 3 groups: %v", err)
	}
}

// The zero value is the "-min-groups 0 -targets ”" configuration — no signal,
// no abort, whatever the file looks like.
func TestProbe_InactiveNeverAborts(t *testing.T) {
	if (providerProbe{}).active() {
		t.Error("zero-value probe reports itself active")
	}
	c, err := collectProbed(t, "testdata/synthetic_mrf.json",
		map[int64]struct{}{9999999999: {}}, providerProbe{})
	if err != nil {
		t.Fatalf("inactive probe aborted: %v", err)
	}
	if c.res.GroupsKept != 0 {
		t.Errorf("GroupsKept = %d, want 0 (no group has that NPI)", c.res.GroupsKept)
	}
}

// GroupsKept is the reading the abort message quotes, so it has to be the count
// of groups that survived — not the count of provider_references entries.
func TestStreamMRF_GroupsKept(t *testing.T) {
	if got := collect(t, "testdata/synthetic_mrf.json").res.GroupsKept; got != 3 {
		t.Errorf("unfiltered GroupsKept = %d, want 3", got)
	}
	c := collectFiltered(t, "testdata/synthetic_mrf.json", map[int64]struct{}{2222222222: {}})
	if c.res.GroupsKept != 1 {
		t.Errorf("filtered GroupsKept = %d, want 1 (only group 1001 has that NPI)", c.res.GroupsKept)
	}
}

func TestSampleList(t *testing.T) {
	if got := sampleList(nil); got != "no network labels" {
		t.Errorf("sampleList(nil) = %q", got)
	}
	if got := sampleList(map[string]struct{}{"B": {}, "A": {}}); got != "A, B" {
		t.Errorf("sampleList = %q, want %q (sorted)", got, "A, B")
	}
	full := map[string]struct{}{}
	for i := 0; i < networkSampleCap; i++ {
		full[string(rune('a'+i))] = struct{}{}
	}
	if got := sampleList(full); !strings.HasSuffix(got, ", …") {
		t.Errorf("a capped sample must say it was capped: %q", got)
	}
}

// note() must not grow without bound on a file with thousands of labels.
func TestProbeReading_NoteIsCapped(t *testing.T) {
	var r probeReading
	for i := 0; i < networkSampleCap*3; i++ {
		r.note(fmt.Sprintf("net-%d", i))
	}
	if len(r.NetworkSample) != networkSampleCap {
		t.Errorf("NetworkSample = %d labels, want the cap of %d", len(r.NetworkSample), networkSampleCap)
	}
	r.note("")
	if _, ok := r.NetworkSample[""]; ok {
		t.Error("an empty network label was sampled")
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
func priceRows(t *testing.T, item core.InNetworkItem, netByGroup map[int64]string, kept map[int64]struct{}) ([]core.PriceRow, map[int64][]int64) {
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
	item := core.InNetworkItem{
		BillingCode: "99999", BillingCodeType: "CPT",
		NegotiatedRates: []core.NegotiatedRate{{
			ProviderReferences: []int{1, 2},
			NegotiatedPrices:   []core.NegotiatedPrice{{NegotiatedRate: 10}},
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
	item := core.InNetworkItem{
		BillingCode: "99999", BillingCodeType: "CPT",
		NegotiatedRates: []core.NegotiatedRate{{
			ProviderReferences: []int{1},
			NegotiatedPrices:   []core.NegotiatedPrice{{NegotiatedRate: 10}},
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
	item := core.InNetworkItem{
		BillingCode:     "99214",
		BillingCodeType: "CPT",
		NegotiatedRates: []core.NegotiatedRate{{
			ProviderReferences: []int{7, 8},
			NegotiatedPrices: []core.NegotiatedPrice{
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
	item := core.InNetworkItem{
		BillingCode: "99999", BillingCodeType: "CPT",
		NegotiatedRates: []core.NegotiatedRate{{
			ProviderReferences: []int{1, 2},
			NegotiatedPrices:   []core.NegotiatedPrice{{NegotiatedRate: 1}, {NegotiatedRate: 2}},
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

func TestStreamMRF_NilFilterKeepsEverything(t *testing.T) {
	c := collectFiltered(t, "testdata/synthetic_mrf.json", nil)
	if len(c.provs) != 5 || len(c.prices) != 6 {
		t.Errorf("nil filter changed output: provs=%d prices=%d", len(c.provs), len(c.prices))
	}
	if c.res.ProviderRowsDropped != 0 || c.res.PriceRowsDropped != 0 {
		t.Errorf("nil filter reported drops: %+v", c.res)
	}
}
