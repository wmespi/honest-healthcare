package discovery

import (
	"bytes"
	"compress/gzip"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	parquet "github.com/parquet-go/parquet-go"
	"github.com/wmespi/honest-healthcare/ingestion/etl/core"
)

func TestHiosStateCode(t *testing.T) {
	cases := []struct {
		id, idType, want string
	}{
		{"45334GA0770001", "HIOS", "GA"},
		{"70893GA001", "hios", "GA"}, // Ambetter: a 10-character product ID, in lower case
		{"12345tx0010002", "HIOS", "TX"},
		{"49046", "HIOS", ""},          // too short for [5:7]
		{"45334GA07", "EIN", ""},       // not HIOS
		{"45334120770001", "HIOS", ""}, // digits, not letters
	}
	for _, c := range cases {
		got := hiosStateCode(core.ReportingPlan{PlanID: c.id, PlanIDType: c.idType})
		if got != c.want {
			t.Errorf("hiosStateCode(%q,%q) = %q, want %q", c.id, c.idType, got, c.want)
		}
	}
}

func TestFileList(t *testing.T) {
	files := map[string]core.IndexFile{
		"https://x/b.json.gz": {FileID: 2, Address: "https://x/b.json.gz"},
		"https://x/a.json.gz": {FileID: 1, Address: "https://x/a.json.gz"},
	}
	list, err := fileList(files)
	if err != nil || len(list) != 2 || list[0].FileID != 1 || list[1].FileID != 2 {
		t.Fatalf("fileList = %v, %v; want both files in ID order", list, err)
	}
	files["https://x/c.json.gz"] = core.IndexFile{FileID: 2, Address: "https://x/c.json.gz"}
	if _, err := fileList(files); err == nil {
		t.Error("fileList accepted two addresses with one file ID")
	}
}

func TestPickAmbetterIndex(t *testing.T) {
	page := `<a href="/content/dam/centene/Centene%20Corporate/json/DOCUMENT/2026-09-29_ambetter_index.json">old</a>
	<a href='/content/dam/centene/Centene%20Corporate/json/DOCUMENT/2026-10-27_ambetter_index.json'>new</a>
	<a href="/content/dam/other_index.json">not it</a>`
	got, err := pickAmbetterIndex(ambetterPage, page)
	want := "https://www.centene.com/content/dam/centene/Centene%20Corporate/json/DOCUMENT/2026-10-27_ambetter_index.json"
	if err != nil || got != want {
		t.Errorf("pickAmbetterIndex = %q, %v; want %q", got, err, want)
	}
	if _, err := pickAmbetterIndex(ambetterPage, "<html>no links</html>"); err == nil {
		t.Error("pickAmbetterIndex accepted a page with no index link")
	}
}

const testIndex = `{"reporting_entity_name":"Test","reporting_structure":[
 {"reporting_plans":[{"plan_id_type":"hios","plan_id":"70893GA001","plan_market_type":"individual"},
                     {"plan_id_type":"hios","plan_id":"70893TX001","plan_market_type":"group"}],
  "in_network_files":[{"location":"https://x/2026-10-27_ambetter-ga_in-network.json"}]}]}`

// useIndexServer points the payers' index sources at a server, in a temp data dir.
func useIndexServer(t *testing.T, serve map[string]http.HandlerFunc) *httptest.Server {
	t.Helper()
	savedRoot, savedSources := core.DataRoot, sources
	core.DataRoot = t.TempDir()
	mux := http.NewServeMux()
	for path, h := range serve {
		mux.HandleFunc(path, h)
	}
	srv := httptest.NewServer(mux)
	t.Cleanup(func() { srv.Close(); core.DataRoot, sources = savedRoot, savedSources })
	sources = map[string]source{}
	for _, p := range core.Payers {
		path := "/" + p.Name
		sources[p.Name] = source{url: func() (string, error) { return srv.URL + path, nil }}
	}
	return srv
}

func gzipped(t *testing.T, s string) []byte {
	t.Helper()
	var b bytes.Buffer
	zw := gzip.NewWriter(&b)
	zw.Write([]byte(s))
	zw.Close()
	return b.Bytes()
}

// An index is plain or gzipped by its first bytes, not its name, and each
// payer's lists are written under its own folder, scoped to Georgia.
func TestRun_ReadsPlainAndGzippedIndexes(t *testing.T) {
	useIndexServer(t, map[string]http.HandlerFunc{
		"/anthem":   func(w http.ResponseWriter, r *http.Request) { w.Write(gzipped(t, testIndex)) },
		"/ambetter": func(w http.ResponseWriter, r *http.Request) { w.Write([]byte(testIndex)) },
	})
	if err := RunAll(core.Payers, Options{}); err != nil {
		t.Fatalf("RunAll: %v", err)
	}
	for _, p := range core.Payers {
		files, err := parquet.ReadFile[core.IndexFile](p.IndexFilesPath())
		if err != nil || len(files) != 1 || files[0].Name != "2026-10-27_ambetter-ga_in-network.json" {
			t.Fatalf("%s files = %v, %v", p.Name, files, err)
		}
		links, err := parquet.ReadFile[core.PlanLink](p.IndexFilePlansPath())
		if err != nil || len(links) != 1 || links[0].PlanID != "70893GA001" {
			t.Errorf("%s links = %v, %v; want the Georgia product ID only", p.Name, links, err)
		}
	}
}

// A payer whose index can't be read keeps its last lists and doesn't stop the
// others; discover fails only when no payer's index was read.
func TestRunAll_OnePayerFailing(t *testing.T) {
	bad := false
	useIndexServer(t, map[string]http.HandlerFunc{
		"/anthem": func(w http.ResponseWriter, r *http.Request) { w.Write(gzipped(t, testIndex)) },
		"/ambetter": func(w http.ResponseWriter, r *http.Request) {
			if bad {
				// The body ends before the document does.
				w.Header().Set("Content-Length", "400")
				w.Write([]byte(testIndex[:100]))
				return
			}
			w.Write([]byte(testIndex))
		},
	})
	if err := RunAll(core.Payers, Options{}); err != nil {
		t.Fatal(err)
	}
	before, _ := os.ReadFile(core.Payers[1].IndexFilesPath())
	bad = true
	if err := RunAll(core.Payers, Options{}); err != nil {
		t.Errorf("one payer failing stopped discover: %v", err)
	}
	if after, _ := os.ReadFile(core.Payers[1].IndexFilesPath()); !bytes.Equal(before, after) {
		t.Error("a failed read changed the payer's lists")
	}
	if err := RunAll(core.Payers[1:], Options{}); err == nil || !strings.Contains(err.Error(), "no payer's index was read") {
		t.Errorf("RunAll with only the failing payer = %v, want an error", err)
	}
}
