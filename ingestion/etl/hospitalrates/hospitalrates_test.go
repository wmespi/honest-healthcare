package hospitalrates

import (
	"archive/zip"
	"bytes"
	"compress/gzip"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"sync"
	"testing"

	parquet "github.com/parquet-go/parquet-go"

	"github.com/wmespi/honest-healthcare/ingestion/etl/core"
)

// ── fixtures ────────────────────────────────────────────────────────────────

const testPayers = `payers:
  - anthem
  - 'blue ?cross'
  - bcbs
`

// useTempDirs points the output and download folders at a temp dir.
func useTempDirs(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	out, raw := core.HospitalRatesDir, core.HospitalRatesRawDir
	core.HospitalRatesDir = filepath.Join(root, "ingestion", "hospital_rates")
	core.HospitalRatesRawDir = filepath.Join(root, "raw", "hospital_rates")
	t.Cleanup(func() { core.HospitalRatesDir, core.HospitalRatesRawDir = out, raw })
	return root
}

// writeList writes a hospital list with the test payers and these hospitals.
func writeList(t *testing.T, hospitals string) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "hospitals.yaml")
	if err := os.WriteFile(p, []byte(testPayers+"hospitals:\n"+hospitals), 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}

func readFixture(t *testing.T, name string) []byte {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("testdata", name))
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func writeFile(t *testing.T, name string, b []byte) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), name)
	if err := os.WriteFile(p, b, 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}

// show prints a row as "type | code | setting | class | modifiers | payer |
// plan | gross | cash | dollar | pct | methodology", "-" for null.
func show(r core.HospitalRateRow) string {
	s := func(p *string) string {
		if p == nil {
			return "-"
		}
		return *p
	}
	n := func(p *float64) string {
		if p == nil {
			return "-"
		}
		return strconv.FormatFloat(*p, 'f', -1, 64)
	}
	return strings.Join([]string{r.CodeType, r.Code, s(r.Setting), s(r.BillingClass), s(r.Modifiers),
		s(r.Payer), s(r.Plan), n(r.GrossCharge), n(r.CashPrice), n(r.NegotiatedDollar), n(r.NegotiatedPercentage),
		s(r.Methodology)}, " ; ")
}

// readRows reads a hospital's output, checking every row carries its CCN.
func readRows(t *testing.T, ccn string) []string {
	t.Helper()
	rows, err := parquet.ReadFile[core.HospitalRateRow](OutputPath(ccn))
	if err != nil {
		t.Fatal(err)
	}
	var out []string
	for _, r := range rows {
		if r.CCN != ccn {
			t.Fatalf("row has ccn %q, want %q", r.CCN, ccn)
		}
		out = append(out, show(r))
	}
	return out
}

func assertRows(t *testing.T, got, want []string) {
	t.Helper()
	if !slices.Equal(got, want) {
		t.Fatalf("rows:\n  got  %s\n  want %s", strings.Join(got, "\n       "), strings.Join(want, "\n       "))
	}
}

var wantTall = []string{
	"CPT ; 70553 ; outpatient ; facility ; - ; Anthem Blue Cross ; Blue Value ; 3200 ; 1600 ; 1234.5 ; - ; fee schedule",
	// Aetna isn't kept, so its line leaves the charges, once: Cigna's repeats them.
	"CPT ; 70553 ; outpatient ; facility ; - ; - ; - ; 3200 ; 1600 ; - ; - ; -",
	"CPT ; 70553 ; outpatient ; facility ; - ; BCBS ; PPO ; 3200 ; 1600 ; - ; 45.5 ; percent of total billed charges",
	"HCPCS ; J1100 ; outpatient ; facility ; 26|TC ; BlueCross BlueShield ; - ; 25 ; 12 ; 12.5 ; - ; fee schedule",
	// "N/A" is no rate, so a kept payer's line can leave only the charges; the APR-DRG is dropped.
	"MS-DRG ; 065 ; inpatient ; facility ; - ; - ; - ; 50000 ; - ; - ; - ; -",
	// One row per distinct kept code.
	"CPT ; 99213 ; outpatient ; professional ; - ; Anthem ; PPO ; 200 ; 100 ; 100 ; - ; fee schedule",
	"HCPCS ; G0463 ; outpatient ; professional ; - ; Anthem ; PPO ; 200 ; 100 ; 100 ; - ; fee schedule",
	"CPT ; 80053 ; outpatient ; facility ; - ; - ; - ; 25 ; - ; - ; - ; -",
	"CPT ; 80048 ; outpatient ; facility ; - ; Anthem ; PPO ; - ; - ; 10 ; - ; fee schedule",
	"MS-DRG ; 470 ; inpatient ; facility ; - ; Blue Cross ; PPO ; 40000 ; 20000 ; 21000 ; - ; case rate",
	// The anesthesia line's "846" is not five characters, so it is dropped.
}

// ── extraction, end to end through Run -file ────────────────────────────────

func extractFixture(t *testing.T, path, ccn string) {
	t.Helper()
	if err := Run(Options{ListPath: writeList(t, ""), File: path, CCN: ccn}); err != nil {
		t.Fatal(err)
	}
}

func TestExtract_TallCSV(t *testing.T) {
	useTempDirs(t)
	extractFixture(t, filepath.Join("testdata", "tall.csv"), "110010")
	assertRows(t, readRows(t, "110010"), wantTall)
}

func TestExtract_WideCSV(t *testing.T) {
	useTempDirs(t)
	extractFixture(t, filepath.Join("testdata", "wide.csv"), "110079")
	// The file has no billing_class column; Aetna's columns are dropped and
	// the estimated_amount column is not a rate.
	assertRows(t, readRows(t, "110079"), []string{
		"CPT ; 70553 ; outpatient ; - ; - ; Anthem Blue Cross ; Blue Value ; 3000 ; 1500 ; 1200 ; - ; fee schedule",
		"CPT ; 70553 ; outpatient ; - ; - ; BCBS ; Pathway ; 3000 ; 1500 ; - ; 50 ; percent of total billed charges",
		"CPT ; 80053 ; outpatient ; - ; - ; - ; - ; 30 ; - ; - ; - ; -",
		"MS-DRG ; 470 ; inpatient ; - ; - ; Anthem Blue Cross ; Blue Value ; 90000 ; - ; 25000 ; - ; case rate",
		"CPT ; 70553 ; outpatient ; - ; 26 ; Anthem Blue Cross ; Blue Value ; 400 ; - ; 200 ; - ; fee schedule",
	})
}

func TestExtract_JSON(t *testing.T) {
	useTempDirs(t)
	extractFixture(t, filepath.Join("testdata", "standard_charges.json"), "110029")
	assertRows(t, readRows(t, "110029"), []string{
		// Aetna isn't kept, and BCBS's HMO has an algorithm but no dollar or percentage.
		"CPT ; 70553 ; outpatient ; facility ; TC ; Anthem ; Blue Value ; 3000 ; 1500 ; 1200.5 ; - ; fee schedule",
		"CPT ; 70553 ; outpatient ; facility ; TC ; Blue Cross Blue Shield ; PPO ; 3000 ; 1500 ; - ; 45 ; percent of total billed charges",
		"CPT ; 70553 ; inpatient ; - ; - ; - ; - ; 4000 ; - ; - ; - ; -",
		"MS-DRG ; 871 ; inpatient ; facility ; - ; Anthem ; HMO ; 60000 ; - ; 22000 ; - ; case rate",
		"HCPCS ; C1713 ; both ; - ; LT|RT ; - ; - ; 500 ; 250 ; - ; - ; -",
	})
}

// The same CSV zipped, gzipped and with a byte-order mark, at addresses with
// no telling extension, gives the same rows.
func TestExtract_Wrappers(t *testing.T) {
	tall := readFixture(t, "tall.csv")
	bom := append([]byte{0xef, 0xbb, 0xbf}, tall...)

	zipped := func(entries ...[2]string) []byte {
		var buf bytes.Buffer
		zw := zip.NewWriter(&buf)
		for _, e := range entries {
			w, err := zw.Create(e[0])
			if err != nil {
				t.Fatal(err)
			}
			w.Write([]byte(e[1]))
		}
		if err := zw.Close(); err != nil {
			t.Fatal(err)
		}
		return buf.Bytes()
	}
	gzipped := func(b []byte) []byte {
		var buf bytes.Buffer
		gw := gzip.NewWriter(&buf)
		gw.Write(b)
		gw.Close()
		return buf.Bytes()
	}

	for name, b := range map[string][]byte{
		"bom":                bom,
		"gzip":               gzipped(bom),
		"zip, csv entry":     zipped([2]string{"README.txt", strings.Repeat("read me ", 10000)}, [2]string{"data/rates.csv", string(tall)}),
		"zip, largest entry": zipped([2]string{"notes", "short"}, [2]string{"rates", string(bom)}),
		"zip of a gzip":      zipped([2]string{"rates.csv.gz", string(gzipped(tall))}),
	} {
		t.Run(name, func(t *testing.T) {
			useTempDirs(t)
			extractFixture(t, writeFile(t, "download", b), "110010")
			assertRows(t, readRows(t, "110010"), wantTall)
		})
	}
}

func TestExtract_Failures(t *testing.T) {
	for name, tc := range map[string]struct {
		body, want string
	}{
		"no header":            {"a,b,c\n1,2,3\n", "no header row"},
		"no rows":              {"description,code|1,code|1|type,standard_charge|gross\nx,0450,RC,100\n", "no CPT, HCPCS or MS-DRG rows"},
		"no code types":        {"description,code|1,standard_charge|gross\nx,70553,100\n", "no code|N column"},
		"json without charges": {`{"hospital_name": "x", "standard_charges": []}`, "no standard_charge_information"},
		"broken json":          {`{"standard_charge_information": [{"code_information": [`, "read standard_charge_information"},
		"empty":                {"", "empty"},
		"utf-16":               {"\xff\xfed\x00e\x00", "UTF-16"},
	} {
		t.Run(name, func(t *testing.T) {
			useTempDirs(t)
			err := Run(Options{ListPath: writeList(t, ""), File: writeFile(t, "download", []byte(tc.body)), CCN: "110010"})
			if err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("err = %v, want it to say %q", err, tc.want)
			}
			if _, err := os.Stat(OutputPath("110010")); !os.IsNotExist(err) {
				t.Fatalf("a failed file left an output: %v", err)
			}
			if _, err := os.Stat(OutputPath("110010") + ".part"); !os.IsNotExist(err) {
				t.Fatalf("a failed file left its part file: %v", err)
			}
		})
	}
}

func TestRunFile_Flags(t *testing.T) {
	useTempDirs(t)
	list := writeList(t, "")
	tall := filepath.Join("testdata", "tall.csv")
	for name, opts := range map[string]Options{
		"no ccn":       {ListPath: list, File: tall},
		"bad ccn":      {ListPath: list, File: tall, CCN: "../x"},
		"with -only":   {ListPath: list, File: tall, CCN: "110010", Only: []string{"110010"}},
		"ccn, no file": {ListPath: list, CCN: "110010"},
	} {
		if err := Run(opts); err == nil {
			t.Errorf("%s: Run succeeded", name)
		}
	}
}

// ── normalisation ───────────────────────────────────────────────────────────

func TestParseNumber(t *testing.T) {
	for in, want := range map[string]string{
		"1234.5": "1234.5", `1,234.50`: "1234.5", "$1,234.50": "1234.5", " 42 ": "42", "0": "0",
		".5": "0.5", "5.": "5", "-12.5": "-12.5", "$-3": "-3", "-$3": "-3", "1,000,000": "1000000",
		"": "-", "N/A": "-", "abc": "-", "1.2.3": "-", "12,34": "-", "1,2345": "-", ",123": "-",
		"1e5": "-", "NaN": "-", "Inf": "-", "0x10": "-", "45%": "-", "$": "-", "-": "-", "1 234": "-",
	} {
		got := "-"
		if v := parseNumber(in); v != nil {
			got = strconv.FormatFloat(*v, 'f', -1, 64)
		}
		if got != want {
			t.Errorf("parseNumber(%q) = %s, want %s", in, got, want)
		}
	}
}

func TestNormCode(t *testing.T) {
	for _, tc := range []struct{ typ, code, want string }{
		{"CPT", "70553", "CPT 70553"},
		{" cpt ", " 0001u ", "CPT 0001U"},
		{"HCPCS", "j1100", "HCPCS J1100"},
		// A shorter number may be an anesthesia code that lost its zeros, or
		// another code labelled CPT, so it isn't padded.
		{"CPT", "846", ""},
		{"CPT", "1996", ""},
		{"CPT", "7055", ""},
		{"HCPCS", "0450", ""},
		{"CPT", "99", ""},
		{"CPT", "70", ""},
		{"CPT", "705531", ""},
		{"HCPCS", "25010066", ""},
		{"CPT", "70-55", ""},
		{"MS-DRG", "470", "MS-DRG 470"},
		{"DRG", "65", "MS-DRG 065"},
		{"MS DRG", "5", "MS-DRG 005"},
		{"msdrg", "0470", "MS-DRG 470"},
		{"MS_DRG", "MS-177", "MS-DRG 177"},
		{"MS-DRG", "DRG 871", "MS-DRG 871"},
		{"MS-DRG", "1000", ""},
		{"MS-DRG", "0", ""},
		{"MS-DRG", "47a", ""},
		{"APR-DRG", "470", ""},
		{"TRIS-DRG", "470", ""},
		{"RC", "0450", ""},
		{"CDM", "70553", ""},
		{"", "70553", ""},
	} {
		got := ""
		if c := addCode(nil, tc.typ, tc.code); len(c) == 1 {
			got = c[0].typ + " " + c[0].code
		}
		if got != tc.want {
			t.Errorf("addCode(%q, %q) = %q, want %q", tc.typ, tc.code, got, tc.want)
		}
	}
	codes := addCode(addCode(addCode(nil, "CPT", "70553"), "cpt", " 70553"), "HCPCS", "70553")
	if len(codes) != 2 {
		t.Errorf("an item's codes repeat: %v", codes)
	}
}

func TestNormModifiers(t *testing.T) {
	for _, tc := range []struct {
		in   []string
		want string
	}{
		{[]string{"26"}, "26"},
		{[]string{"tc|26"}, "26|TC"},
		{[]string{"26, TC", " lt "}, "26|LT|TC"},
		{[]string{"RT", "RT"}, "RT"},
		{[]string{"", " "}, "-"},
		{nil, "-"},
	} {
		got := "-"
		if m := normModifiers(tc.in...); m != nil {
			got = *m
		}
		if got != tc.want {
			t.Errorf("normModifiers(%q) = %q, want %q", tc.in, got, tc.want)
		}
	}
}

// ── the list ────────────────────────────────────────────────────────────────

func TestLoadList(t *testing.T) {
	ok := writeList(t, `  - ccn: "110010"
    name: Emory University Hospital
    index: https://www.emoryhealthcare.org/cms-hpt.txt
    location: Emory University Hospital
  - ccn: 11t001
    name: Direct
    url: https://example.org/rates.csv
`)
	l, err := LoadList(ok)
	if err != nil {
		t.Fatal(err)
	}
	if h, found := l.Find("11T001"); !found || h.URL == "" {
		t.Errorf("Find(11T001) = %+v, %v", h, found)
	}
	for _, name := range []string{"Anthem", "ANTHEM BCBS", "BlueCross BlueShield", "Blue Cross", "bcbs of georgia"} {
		if !l.KeepsPayer(name) {
			t.Errorf("KeepsPayer(%q) = false", name)
		}
	}
	for _, name := range []string{"Aetna", "Cigna", "Ambetter", "Blue Shield"} {
		if l.KeepsPayer(name) {
			t.Errorf("KeepsPayer(%q) = true", name)
		}
	}

	for name, body := range map[string]string{
		"bad ccn":     "  - {ccn: \"1100\", name: x, url: u}\n",
		"twice":       "  - {ccn: \"110010\", name: x, url: u}\n  - {ccn: \"110010\", name: y, url: v}\n",
		"no name":     "  - {ccn: \"110010\", url: u}\n",
		"both":        "  - {ccn: \"110010\", name: x, url: u, index: i, location: l}\n",
		"no location": "  - {ccn: \"110010\", name: x, index: i}\n",
	} {
		if _, err := LoadList(writeList(t, body)); err == nil {
			t.Errorf("%s: LoadList succeeded", name)
		}
	}
	noPayers := writeFile(t, "hospitals.yaml", []byte("hospitals: []\n"))
	if _, err := LoadList(noPayers); err == nil {
		t.Error("a list with no payers loaded")
	}
	badPattern := writeFile(t, "hospitals.yaml", []byte("payers: ['(']\n"))
	if _, err := LoadList(badPattern); err == nil {
		t.Error("a list with a bad payer pattern loaded")
	}
}

// ── cms-hpt.txt ─────────────────────────────────────────────────────────────

const testIndex = "\ufefflocation-name: Hospital A\r\n" +
	"Source-Page-URL: https://sys.org/prices\r\n" +
	"MRF-URL: files/a.csv\r\n" +
	"contact-name: Pat\r\n" +
	"contact-email: pat@sys.org\r\n" +
	"\r\n" +
	"location-name: Hospital B\r\n" +
	"location-name: Hospital B Annex\r\n" +
	"mrf-url: https://cdn.sys.org/b.json\r\n" +
	"location-name: Hospital C\r\n" +
	"mrf-url: https://cdn.sys.org/c.csv\r\n" +
	"\r\n" +
	"  \r\n" +
	"mrf-url: https://cdn.sys.org/orphan.csv\r\n"

func TestParseIndex(t *testing.T) {
	entries, err := ParseIndex(strings.NewReader(testIndex))
	if err != nil {
		t.Fatal(err)
	}
	var got []string
	for _, e := range entries {
		got = append(got, strings.Join(e.Locations, "+")+" = "+e.MRFURL)
	}
	assertRows(t, got, []string{
		"Hospital A = files/a.csv",
		"Hospital B+Hospital B Annex = https://cdn.sys.org/b.json",
		"Hospital C = https://cdn.sys.org/c.csv",
	})
	if entries[0].SourcePageURL != "https://sys.org/prices" || entries[0].ContactEmail != "pat@sys.org" || entries[0].ContactName != "Pat" {
		t.Errorf("entry A = %+v", entries[0])
	}
	if _, err := ParseIndex(strings.NewReader("<html>not found</html>")); err == nil {
		t.Error("a page with no location-name parsed")
	}
}

func TestFindLocation(t *testing.T) {
	entries, err := ParseIndex(strings.NewReader(testIndex))
	if err != nil {
		t.Fatal(err)
	}
	const index = "https://www.sys.org/cms-hpt.txt"
	for location, want := range map[string]string{
		"Hospital A":          "https://www.sys.org/files/a.csv",
		" hospital b annex  ": "https://cdn.sys.org/b.json",
		"HOSPITAL C":          "https://cdn.sys.org/c.csv",
	} {
		got, warning, err := FindLocation(entries, location, index)
		if err != nil || got != want || warning != "" {
			t.Errorf("FindLocation(%q) = %q, %q, %v; want %q", location, got, warning, err, want)
		}
	}
	if _, _, err := FindLocation(entries, "Hospital", index); err == nil || !strings.Contains(err.Error(), "Hospital B Annex") {
		t.Errorf("a missing location: err = %v, want it to list the index's locations", err)
	}
	twice := append(entries, IndexEntry{Locations: []string{"Hospital A"}, MRFURL: "https://cdn.sys.org/a2.csv"})
	if got, warning, _ := FindLocation(twice, "Hospital A", index); got != "https://www.sys.org/files/a.csv" || warning == "" {
		t.Errorf("a location listed twice = %q, warning %q", got, warning)
	}
}

// ── a run: the index, -missing, failures ────────────────────────────────────

// hospitalServer serves files by path and counts the requests for each,
// failing the test when one lacks the project's User-Agent.
type hospitalServer struct {
	*httptest.Server
	mu       sync.Mutex
	requests map[string]int
}

func newHospitalServer(t *testing.T, files map[string][]byte) *hospitalServer {
	t.Helper()
	s := &hospitalServer{requests: map[string]int{}}
	s.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s.mu.Lock()
		s.requests[r.URL.Path]++
		s.mu.Unlock()
		if ua := r.Header.Get("User-Agent"); ua != core.UserAgent {
			t.Errorf("%s sent User-Agent %q", r.URL.Path, ua)
		}
		b, ok := files[r.URL.Path]
		if !ok {
			http.NotFound(w, r)
			return
		}
		w.Write(b)
	}))
	t.Cleanup(s.Close)
	return s
}

func (s *hospitalServer) count(path string) int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.requests[path]
}

func TestRun_Index(t *testing.T) {
	useTempDirs(t)
	srv := newHospitalServer(t, map[string][]byte{
		"/cms-hpt.txt":     []byte(strings.ReplaceAll(testIndex, "https://cdn.sys.org/b.json", "/files/b.json")),
		"/files/a.csv":     readFixture(t, "tall.csv"),
		"/files/b.json":    readFixture(t, "standard_charges.json"),
		"/files/wide.csv":  readFixture(t, "wide.csv"),
		"/files/empty.csv": []byte("description,code|1,code|1|type\n"),
	})
	list := writeList(t, `  - {ccn: "110001", name: A, index: "`+srv.URL+`/cms-hpt.txt", location: Hospital A}
  - {ccn: "110002", name: B Annex, index: "`+srv.URL+`/cms-hpt.txt", location: hospital b annex}
  - {ccn: "110003", name: Direct, url: "`+srv.URL+`/files/wide.csv"}
`)
	if err := Run(Options{ListPath: list}); err != nil {
		t.Fatal(err)
	}
	if n := srv.count("/cms-hpt.txt"); n != 1 {
		t.Errorf("the index was fetched %d times, want once", n)
	}
	for _, ccn := range []string{"110001", "110002", "110003"} {
		if len(readRows(t, ccn)) == 0 {
			t.Errorf("%s wrote no rows", ccn)
		}
	}
	assertRows(t, readRows(t, "110001"), wantTall)
	if _, err := os.Stat(core.HospitalRatesRawDir); !os.IsNotExist(err) {
		t.Errorf("the download folder was left behind: %v", err)
	}

	// A folder with something else in it stays.
	if err := os.MkdirAll(core.HospitalRatesRawDir, 0o755); err != nil {
		t.Fatal(err)
	}
	keep := filepath.Join(core.HospitalRatesRawDir, "notes.txt")
	if err := os.WriteFile(keep, []byte("mine"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := Run(Options{ListPath: list, Only: []string{"110003"}}); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(keep); err != nil {
		t.Errorf("a run removed a file it didn't write: %v", err)
	}
}

func TestRun_Missing(t *testing.T) {
	useTempDirs(t)
	srv := newHospitalServer(t, map[string][]byte{
		"/a.csv": readFixture(t, "tall.csv"),
		"/b.csv": readFixture(t, "wide.csv"),
	})
	list := writeList(t, `  - {ccn: "110001", name: A, url: "`+srv.URL+`/a.csv"}
  - {ccn: "110002", name: B, url: "`+srv.URL+`/b.csv"}
`)
	if err := os.MkdirAll(core.HospitalRatesDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(OutputPath("110001"), []byte("earlier"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := Run(Options{ListPath: list, Missing: true}); err != nil {
		t.Fatal(err)
	}
	if n := srv.count("/a.csv"); n != 0 {
		t.Errorf("-missing downloaded a hospital that has rates (%d times)", n)
	}
	if b, _ := os.ReadFile(OutputPath("110001")); string(b) != "earlier" {
		t.Errorf("-missing rewrote a hospital that has rates")
	}
	if len(readRows(t, "110002")) == 0 {
		t.Error("-missing skipped a hospital with no rates")
	}

	// Once every hospital has rates, -missing has nothing to do, and that's fine.
	if err := Run(Options{ListPath: list, Missing: true}); err != nil {
		t.Fatal(err)
	}
	if n := srv.count("/b.csv"); n != 1 {
		t.Errorf("the second -missing run downloaded again (%d)", n)
	}
	// Without -missing, every hospital is fetched again.
	os.Remove(OutputPath("110001"))
	if err := Run(Options{ListPath: list, Only: []string{"110001"}}); err != nil {
		t.Fatal(err)
	}
	assertRows(t, readRows(t, "110001"), wantTall)
}

func TestRun_FailureKeepsPreviousOutput(t *testing.T) {
	useTempDirs(t)
	srv := newHospitalServer(t, map[string][]byte{
		"/bad.csv":     []byte("<html>Access denied</html>\n"),
		"/good.csv":    readFixture(t, "tall.csv"),
		"/cms-hpt.txt": []byte("location-name: Somewhere Else\nmrf-url: /good.csv\n"),
	})
	list := writeList(t, `  - {ccn: "110001", name: Bad file, url: "`+srv.URL+`/bad.csv"}
  - {ccn: "110002", name: Gone, url: "`+srv.URL+`/gone.csv"}
  - {ccn: "110003", name: Not in its index, index: "`+srv.URL+`/cms-hpt.txt", location: Hospital A}
  - {ccn: "110004", name: Good, url: "`+srv.URL+`/good.csv"}
`)
	if err := os.MkdirAll(core.HospitalRatesDir, 0o755); err != nil {
		t.Fatal(err)
	}
	for _, ccn := range []string{"110001", "110002", "110003"} {
		if err := os.WriteFile(OutputPath(ccn), []byte("earlier "+ccn), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	// One hospital succeeding is a successful run.
	if err := Run(Options{ListPath: list}); err != nil {
		t.Fatalf("a run with one good hospital failed: %v", err)
	}
	for _, ccn := range []string{"110001", "110002", "110003"} {
		if b, _ := os.ReadFile(OutputPath(ccn)); string(b) != "earlier "+ccn {
			t.Errorf("%s failed but its earlier output changed: %q", ccn, b)
		}
	}
	assertRows(t, readRows(t, "110004"), wantTall)
	left, _ := filepath.Glob(filepath.Join(core.HospitalRatesDir, "*.part"))
	if _, err := os.Stat(core.HospitalRatesRawDir); len(left) != 0 || !os.IsNotExist(err) {
		t.Errorf("a failure left files behind: %v, download folder: %v", left, err)
	}

	// Every hospital failing fails the run.
	if err := Run(Options{ListPath: list, Only: []string{"110001", "110002", "110003"}}); err == nil {
		t.Fatal("a run where every hospital failed succeeded")
	}
	if err := Run(Options{ListPath: list, Only: []string{"999999"}}); err == nil {
		t.Fatal("-only with a CCN that isn't listed succeeded")
	}
}
