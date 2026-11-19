package extraction

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	parquet "github.com/parquet-go/parquet-go"
	"github.com/wmespi/honest-healthcare/ingestion/etl/core"
)

var ambetter = core.Payers[1]

const ambetterFixture = "testdata/fixtures/ambetter_ga.json"

func readFixture(t *testing.T) []byte {
	t.Helper()
	b, err := os.ReadFile(ambetterFixture)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

// serve serves body the way a payer's file server does, without gzip, with a
// Content-Length and a Last-Modified, and returns the file's index entry.
func serve(t *testing.T, name string, body []byte) core.IndexFile {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Last-Modified", time.Date(2026, 10, 27, 4, 0, 0, 0, time.UTC).Format(http.TimeFormat))
		w.Write(body)
	}))
	t.Cleanup(srv.Close)
	loc := srv.URL + "/" + name
	return core.IndexFile{FileID: core.FileID(loc), Name: name, Address: loc, Location: loc}
}

// Ambetter's file is plain JSON, its NPIs are JSON integers and its billing
// entities are EINs; the parser reads all of that as it reads Anthem's.
func TestStreamMRF_AmbetterShape(t *testing.T) {
	c := collect(t, ambetterFixture)
	if c.res.ProviderRefs != 3 || c.res.InNetworkItems != 2 {
		t.Errorf("read %d references and %d items, want 3 and 2", c.res.ProviderRefs, c.res.InNetworkItems)
	}
	var npis []int64
	for _, p := range c.provs {
		npis = append(npis, p.NPI)
		if p.TINType != "ein" || p.TINValue != "86-2226051" {
			t.Errorf("provider row has TIN %q %q, want ein 86-2226051 as the file gives it", p.TINType, p.TINValue)
		}
	}
	if !slices.Contains(npis, 1023456789) || len(npis) != 4 {
		t.Errorf("provider NPIs = %v", npis)
	}
	assertSet(t, "networks", c.res.NetworkNames, "Exchange GA", "Exchange Select Plus", "Exchange Select Wellstar", "Exchange Solutions")
	types := map[string]bool{}
	for _, p := range c.prices {
		types[p.NegotiatedType] = true
	}
	for _, want := range []string{"negotiated", "percentage", "per diem"} {
		if !types[want] {
			t.Errorf("no %q price among %v", want, types)
		}
	}

	// The Georgia NPI filter keeps a group by an integer NPI, and a group with no
	// Georgia NPI goes with every price whose roster it was.
	c = collectFiltered(t, ambetterFixture, map[int64]struct{}{1098765432: {}})
	if c.res.GroupsKept != 1 || c.res.ProviderRowsDropped != 3 {
		t.Errorf("filter kept %d groups and dropped %d provider rows, want 1 and 3", c.res.GroupsKept, c.res.ProviderRowsDropped)
	}
}

// A plain file is parsed whole, and recorded under the payer's folder, not Anthem's.
func TestParseRates_PlainJSON(t *testing.T) {
	useStore(t)
	f := serve(t, "2026-10-27_centene_ambetter-ga_in-network.json", readFixture(t))
	parseRates(context.Background(), ambetter, f, "", true, nil, providerProbe{}, false)
	rec, err := readRecord(ambetter, f)
	if err != nil || rec == nil || rec.Status != statusParsed {
		t.Fatalf("record = %+v, %v; want parsed", rec, err)
	}
	if rec.UpdatedAt == nil || rec.PriceRows == 0 || rec.CompressedBytes != int64(len(readFixture(t))) {
		t.Errorf("record = %+v", rec)
	}
	stem := "2026-10-27_centene_ambetter-ga_in-network"
	for _, p := range []string{
		filepath.Join(ambetter.CodesDir(), stem+".parquet"),
		filepath.Join(ambetter.ProvidersDir(), stem+".parquet"),
		filepath.Join(ambetter.ParsesDir(), stem+".parquet"),
		filepath.Join(ambetter.PricesDir(), "net=exchange-ga", stem+".parquet"),
	} {
		if _, err := os.Stat(p); err != nil {
			t.Errorf("parse left no %s: %v", p, err)
		}
	}
	if _, err := os.Stat(core.Payers[0].ParsesDir()); err == nil {
		t.Error("an Ambetter parse wrote under Anthem's folder")
	}
}

// A plain file that stops before its closing brace, or whose body is shorter
// than its Content-Length, is failed, and writes nothing.
func TestParseRates_PlainJSONTruncatedFails(t *testing.T) {
	full := readFixture(t)
	for name, body := range map[string][]byte{
		"cut in in_network":   full[:strings.Index(string(full), "in_network")+200],
		"missing last brace":  []byte(strings.TrimRight(string(full), "\n }") + "\n"),
		"cut in the trailers": full[:len(full)-30],
	} {
		t.Run(name, func(t *testing.T) {
			useStore(t)
			f := serve(t, "x_ambetter-ga_in-network.json", body)
			parseRates(context.Background(), ambetter, f, "", false, nil, providerProbe{}, false)
			rec, err := readRecord(ambetter, f)
			if err != nil || rec == nil || rec.Status != statusFailed {
				t.Fatalf("record = %+v, %v; want failed", rec, err)
			}
			if _, err := os.Stat(ambetter.CodesDir()); err == nil {
				t.Error("a failed parse left Parquet in place")
			}
		})
	}
}

func TestValidatePlainComplete(t *testing.T) {
	full := readFixture(t)
	pr := core.NewProgressReader(strings.NewReader(string(full)), int64(len(full)))
	if err := validatePlainComplete(pr, pr, int64(len(full))); err != nil {
		t.Errorf("a complete body: %v", err)
	}
	pr = core.NewProgressReader(strings.NewReader(string(full[:len(full)-10])), int64(len(full)))
	if err := validatePlainComplete(pr, pr, int64(len(full))); err == nil || !strings.Contains(err.Error(), "short read") {
		t.Errorf("a body short of its Content-Length gave %v, want a short read", err)
	}
}

// A payer's index may list a plan by its product ID, the first 10 characters of
// its plan IDs, in place of each plan.
func TestLinksOneOf_ProductID(t *testing.T) {
	planIDs := []string{"45334GA0020002", "45334GA0020008", "70893GA0010001", "70893GA0030002"}
	for _, c := range []struct {
		listed []string
		want   bool
	}{
		{[]string{"70893GA001"}, true},  // the product of a plan
		{[]string{"70893GA003"}, true},  // the last plan's
		{[]string{"70893GA002"}, false}, // a product with no plan on the networks
		{[]string{"45495GA004"}, false}, // another issuer's
		{[]string{"45334GA0020008"}, true},
		{[]string{"45334GA002"}, true},
		{[]string{"70893GA00"}, false}, // a prefix that isn't a product ID
		{[]string{"OTHER", "70893GA003"}, true},
		{nil, false},
	} {
		if got := linksOneOf(c.listed, planIDs); got != c.want {
			t.Errorf("linksOneOf(%v) = %v, want %v", c.listed, got, c.want)
		}
	}
}

// A name with a `*` stands for the one file of this month it matches.
func TestOneFile_Pattern(t *testing.T) {
	file := func(id int64, name string) core.IndexFile {
		return core.IndexFile{FileID: id, Name: name, Address: "https://x/" + name}
	}
	index := func(files ...core.IndexFile) map[string][]core.IndexFile {
		m := map[string][]core.IndexFile{}
		for _, f := range files {
			m[f.Name] = append(m[f.Name], f)
		}
		return m
	}
	const pattern = "*_ambetter-ga_in-network.json"
	ga := file(1, "2026-10-27_centene-management-company-llc_ambetter-ga_in-network.json")
	other := index(ga, file(2, "2026-10-27_centene-management-company-llc_ambetter-tx_in-network.json"), file(3, "ga_in-network.json"))
	if got, err := oneFile(other, pattern); err != nil || got.FileID != 1 {
		t.Errorf("oneFile = %+v, %v; want the Georgia file", got, err)
	}
	if _, err := oneFile(index(file(2, "2026-10-27_ambetter-tx_in-network.json")), pattern); err == nil || !strings.Contains(err.Error(), "matches no file") {
		t.Errorf("a pattern matching nothing gave %v", err)
	}
	two := index(ga, file(4, "2026-11-27_centene-management-company-llc_ambetter-ga_in-network.json"))
	if _, err := oneFile(two, pattern); err == nil || !strings.Contains(err.Error(), "matches 2 files") {
		t.Errorf("a pattern matching two months' files gave %v", err)
	}
}

func writeIndex(t *testing.T, p core.Payer, files []core.IndexFile, links []core.PlanLink) {
	t.Helper()
	if err := os.MkdirAll(p.RawDir(), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := parquet.WriteFile(p.IndexFilesPath(), files); err != nil {
		t.Fatal(err)
	}
	if err := parquet.WriteFile(p.IndexFilePlansPath(), links); err != nil {
		t.Fatal(err)
	}
}

// The five Ambetter targets name one file, listed by product ID: it is picked
// once, and prices all five networks.
func TestSelectFiles_OneFileForSeveralTargets(t *testing.T) {
	useStore(t)
	ts, err := LoadTargets(filepath.Join("..", "targets.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	amb := ts.ForPayer("ambetter")
	if len(amb.Targets) != 5 {
		t.Fatalf("shipped targets name %d Ambetter targets, want 5", len(amb.Targets))
	}
	name := "2026-10-27_centene-management-company-llc_ambetter-ga_in-network.json"
	loc := "https://www.centene.com/x/" + name
	f := core.IndexFile{FileID: core.FileID(loc), Name: name, Address: loc, Location: loc}
	writeIndex(t, ambetter, []core.IndexFile{f, {FileID: 9, Name: "other.json", Address: "https://x/other.json"}},
		[]core.PlanLink{{FileID: f.FileID, PlanID: "70893GA001"}, {FileID: f.FileID, PlanID: "70893GA003"},
			{FileID: f.FileID, PlanID: "70893GA006"}, {FileID: f.FileID, PlanID: "70893GA007"},
			{FileID: f.FileID, PlanID: "70893GA009"}, {FileID: f.FileID, PlanID: "70893GA011"}})
	planIDs := map[string][]string{
		"70893-GAN001": {"70893GA0010001"}, "70893-GAN002": {"70893GA0030001"}, "70893-GAN003": {"70893GA0060001"},
		"70893-GAN004": {"70893GA0070001"}, "70893-GAN005": {"70893GA0090001"},
	}

	files, named, err := selectFiles(context.Background(), ambetter, Options{}, amb, planIDs)
	if err != nil {
		t.Fatalf("selectFiles: %v", err)
	}
	if len(files) != 1 || len(named) != 1 || files[0].FileID != f.FileID {
		t.Fatalf("selected %d files, named %d; want the one file once", len(files), len(named))
	}
	if len(named[0].Networks) != 5 || named[0].Pattern != "*_ambetter-ga_in-network.json" {
		t.Errorf("the file prices %v by %q, want all five networks by the shipped pattern", named[0].Networks, named[0].Pattern)
	}

	// The probe's labels are each network's own, exactly, so another issuer's
	// label in the same file doesn't pass it.
	match := amb.NetworkMatcher()
	for label, want := range map[string]bool{"Exchange GA": true, "Exchange Select Plus": true, "exchange select st joseph chandler": true,
		"Exchange Solutions": false, "Exchange GA Extra": false} {
		if match(label) != want {
			t.Errorf("probe matches %q = %v, want %v", label, !want, want)
		}
	}
}

// A record written before payers called the copy's date anthem_updated_at is
// read with that date, so a file Anthem hasn't changed isn't downloaded again.
func TestReadRecord_LegacyAnthemUpdatedAt(t *testing.T) {
	useStore(t)
	type legacy struct {
		FileID          int64      `parquet:"file_id"`
		Name            string     `parquet:"name"`
		Address         string     `parquet:"address"`
		Status          string     `parquet:"status"`
		Reason          string     `parquet:"reason"`
		AnthemUpdatedAt *time.Time `parquet:"anthem_updated_at,optional,timestamp(millisecond)"`
		RatesUpdatedAt  *time.Time `parquet:"rates_updated_at,optional,timestamp(millisecond)"`
		FinishedAt      time.Time  `parquet:"finished_at,timestamp(millisecond)"`
		PriceRows       int64      `parquet:"price_rows"`
	}
	oct31 := time.Date(2026, 10, 31, 9, 24, 31, 0, time.UTC)
	if err := os.MkdirAll(testPayer.ParsesDir(), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := parquet.WriteFile(filepath.Join(testPayer.ParsesDir(), "GA_X.parquet"), []legacy{{
		FileID: 7, Name: "GA_X.json.gz", Address: "https://x/GA_X.json.gz", Status: statusParsed,
		AnthemUpdatedAt: &oct31, FinishedAt: oct31, PriceRows: 5}}); err != nil {
		t.Fatal(err)
	}
	rec, err := readRecord(testPayer, core.IndexFile{Name: "GA_X.json.gz"})
	if err != nil || rec == nil {
		t.Fatalf("readRecord = %v, %v", rec, err)
	}
	if rec.UpdatedAt == nil || !rec.UpdatedAt.Equal(oct31) || rec.PriceRows != 5 || rec.ratesCopy() == nil || !rec.ratesCopy().Equal(oct31) {
		t.Errorf("readRecord = %+v, want the copy of %v and its rates", rec, oct31)
	}
	// What it writes next carries the new name, and reads back the same.
	if err := writeRecord(testPayer, *rec); err != nil {
		t.Fatal(err)
	}
	if again, err := readRecord(testPayer, core.IndexFile{Name: "GA_X.json.gz"}); err != nil || again.UpdatedAt == nil || !again.UpdatedAt.Equal(oct31) {
		t.Errorf("a rewritten record = %+v, %v", again, err)
	}
}

// Parsing this month's file deletes what an earlier month's left, and only that.
func TestPruneSuperseded(t *testing.T) {
	useStore(t)
	const pattern = "*_ambetter-ga_in-network.json"
	oldName, newName := "2026-09-27_ambetter-ga_in-network.json", "2026-10-27_ambetter-ga_in-network.json"
	touch := func(path string) {
		t.Helper()
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, nil, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	files := func(name string) []string {
		stem := core.IndexFile{Name: name}.Stem()
		return []string{
			filepath.Join(ambetter.PricesDir(), "net=exchange-ga", stem+".parquet"),
			filepath.Join(ambetter.GroupSetsDir(), stem+".parquet"),
			filepath.Join(ambetter.ProvidersDir(), stem+".parquet"),
			filepath.Join(ambetter.CodesDir(), stem+".parquet"),
		}
	}
	record := func(name, status string) {
		t.Helper()
		if err := writeRecord(ambetter, parseRecord{Name: name, Address: "https://x/" + name, Status: status}); err != nil {
			t.Fatal(err)
		}
		for _, f := range files(name) {
			touch(f)
		}
	}
	record(oldName, statusParsed)
	record("unrelated.json", statusParsed)
	current := namedFile{IndexFile: core.IndexFile{Name: newName, Address: "https://x/" + newName}, Pattern: pattern}

	// A new file that failed leaves the old one's rates: they are all there is.
	record(newName, statusFailed)
	pruneSuperseded(ambetter, Options{}, []namedFile{current})
	if _, err := os.Stat(files(oldName)[0]); err != nil {
		t.Fatalf("an earlier copy was deleted while this month's file hasn't parsed: %v", err)
	}

	record(newName, statusParsed)
	pruneSuperseded(ambetter, Options{}, []namedFile{current})
	for _, f := range append(files(oldName), recordPath(ambetter, core.IndexFile{Name: oldName}.Stem())) {
		if _, err := os.Stat(f); err == nil {
			t.Errorf("%s of the superseded file is still on disk", f)
		}
	}
	for _, f := range append(append(files(newName), files("unrelated.json")...), recordPath(ambetter, "unrelated")) {
		if _, err := os.Stat(f); err != nil {
			t.Errorf("%s was deleted: %v", f, err)
		}
	}
}

// Every payer keeps its files in its own folder under data/raw/.
func TestPayerPaths(t *testing.T) {
	saved := core.DataRoot
	defer func() { core.DataRoot = saved }()
	core.DataRoot = "/d"
	a, m := core.Payers[0], core.Payers[1]
	if a.Name != "anthem" || a.RawDir() != "/d/raw/anthem" || a.ParsesDir() != "/d/raw/anthem/parses" ||
		a.IndexFilesPath() != "/d/raw/anthem/index_files.parquet" {
		t.Errorf("Anthem's paths moved: %s %s %s", a.RawDir(), a.ParsesDir(), a.IndexFilesPath())
	}
	if m.Name != "ambetter" || m.PricesDir() != "/d/raw/ambetter/prices" || m.IndexFilePlansPath() != "/d/raw/ambetter/index_file_plans.parquet" {
		t.Errorf("Ambetter's paths: %s %s", m.PricesDir(), m.IndexFilePlansPath())
	}
	if _, err := core.PayerByName("cigna"); err == nil {
		t.Error("PayerByName accepted an unknown payer")
	}
}

const ratesFirstFixture = "testdata/fixtures/alliant_ratesfirst.json"

// parseTwoPass reads path the way parseRates reads a file that lists its rates
// first, and gathers what the writers were given.
func parseTwoPass(t *testing.T, path string, gaNPIs map[int64]struct{}, pb providerProbe) (collected, error) {
	t.Helper()
	var c collected
	w := mrfWriters{
		prices:          func(r []core.PriceRow) error { c.prices = append(c.prices, r...); return nil },
		groupSetMembers: func(r []core.GroupSetMemberRow) error { c.members = append(c.members, r...); return nil },
		providers:       func(p []core.ProviderRow) error { c.provs = append(c.provs, p...); return nil },
		code:            func(bc core.BillingCodeRow) error { c.codes = append(c.codes, bc); return nil },
	}
	res, _, _, err := parseFromDisk(context.Background(), "", path, "", testFileID, true, gaNPIs, pb, w)
	c.res = res
	return c, err
}

func sortedRows[T any](rows []T) []string {
	out := make([]string, len(rows))
	for i, r := range rows {
		out[i] = fmt.Sprintf("%+v", r)
	}
	slices.Sort(out)
	return out
}

// The same content with its rates first parses to the same rows, networks and
// filter outcome as with its provider groups first.
func TestParseFromDisk_RatesFirstMatchesReferencesFirst(t *testing.T) {
	if _, err := streamMRFFile(t, ratesFirstFixture); !errors.Is(err, errInNetworkFirst) {
		t.Fatalf("one pass over a rates-first file: %v, want errInNetworkFirst", err)
	}
	for name, ga := range map[string]map[int64]struct{}{
		"no filter":    nil,
		"Georgia NPIs": {1098765432: {}, 1222222223: {}},
		"only one NPI": {1098765432: {}},
		"no GA at all": {1: {}},
	} {
		t.Run(name, func(t *testing.T) {
			want := collectFiltered(t, ambetterFixture, ga)
			got, err := parseTwoPass(t, ratesFirstFixture, ga, providerProbe{})
			if err != nil {
				t.Fatal(err)
			}
			for label, pair := range map[string][2][]string{
				"prices":    {sortedRows(got.prices), sortedRows(want.prices)},
				"providers": {sortedRows(got.provs), sortedRows(want.provs)},
				"members":   {sortedRows(got.members), sortedRows(want.members)},
				"codes":     {sortedRows(got.codes), sortedRows(want.codes)},
			} {
				if !slices.Equal(pair[0], pair[1]) {
					t.Errorf("%s differ:\n got %v\nwant %v", label, pair[0], pair[1])
				}
			}
			g, w := got.res, want.res
			if g.PriceRows != w.PriceRows || g.PriceRowsDropped != w.PriceRowsDropped || g.GroupsKept != w.GroupsKept ||
				g.GroupSets != w.GroupSets || g.InNetworkItems != w.InNetworkItems || g.ProviderRefs != w.ProviderRefs ||
				g.BillingCodes != w.BillingCodes {
				t.Errorf("result = %+v, want %+v", g, w)
			}
			if fmt.Sprint(g.SchemaExample) != fmt.Sprint(w.SchemaExample) {
				t.Errorf("schema example = %v, want %v", g.SchemaExample, w.SchemaExample)
			}
		})
	}
}

// The probe judges a rates-first file at the end of its provider groups, and a
// rejected file writes no price rows.
func TestParseFromDisk_ProbeSkipsRatesFirstFile(t *testing.T) {
	c, err := parseTwoPass(t, ratesFirstFixture, nil, probeMatcher(t, "GA Blue Value HIX*"))
	if !errors.Is(err, errNoWantedProviders) {
		t.Fatalf("error = %v, want errNoWantedProviders", err)
	}
	if len(c.prices) != 0 || c.res != nil {
		t.Errorf("a rejected file wrote %d price rows", len(c.prices))
	}
	if _, err := parseTwoPass(t, ratesFirstFixture, nil, probeMatcher(t, "Exchange Select*")); err != nil {
		t.Errorf("a file whose network matches was rejected: %v", err)
	}
}

// End to end: the body is downloaded to disk once, read in two passes, the
// rows land where a references-first file's do, and nothing is left behind.
func TestParseRates_RatesFirstOverHTTP(t *testing.T) {
	useStore(t)
	body, err := os.ReadFile(ratesFirstFixture)
	if err != nil {
		t.Fatal(err)
	}
	f := serve(t, "2026-10-28_alliant-health-plans_in-network-rates-individual_hoa.json", body)
	res, outcome := parseRates(context.Background(), ambetter, f, "", true, nil, providerProbe{}, false)
	if outcome != outcomeCompleted || res == nil || res.PriceRows == 0 {
		t.Fatalf("outcome = %v, result = %+v", outcome, res)
	}
	rec, err := readRecord(ambetter, f)
	if err != nil || rec == nil || rec.Status != statusParsed || rec.PriceRows != res.PriceRows {
		t.Fatalf("record = %+v, %v; want parsed", rec, err)
	}
	if _, err := os.Stat(scratchDir(ambetter, f)); err == nil {
		t.Error("the spooled body or its scratch folder was left behind")
	}
	if _, err := os.Stat(filepath.Join(ambetter.ProvidersDir(), f.Stem()+".parquet")); err != nil {
		t.Error(err)
	}

	// A probe skip removes the spool too and records the file as skipped.
	g := serve(t, "2026-10-28_other.json", body)
	_, outcome = parseRates(context.Background(), ambetter, g, "", false, nil, probeMatcher(t, "Nothing Like This*"), false)
	if outcome != outcomeSkippedNetwork {
		t.Errorf("outcome = %v, want skipped on the network", outcome)
	}
	if _, err := os.Stat(scratchDir(ambetter, g)); err == nil {
		t.Error("a skipped file left its scratch folder")
	}
}

func streamMRFFile(t *testing.T, path string) (*mrfResult, error) {
	t.Helper()
	f, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	return streamMRF(f, testFileID, true, nil, providerProbe{}, mrfWriters{}, nil)
}
