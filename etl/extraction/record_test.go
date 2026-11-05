package extraction

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/wmespi/honest-healthcare/etl/core"
)

// useStore points the parse records and the scratch folder at a temp dir.
func useStore(t *testing.T) {
	t.Helper()
	dir := t.TempDir()
	parses, prices := core.ParsesDir, core.PricesOutputDir
	core.ParsesDir, core.PricesOutputDir = filepath.Join(dir, "parses"), filepath.Join(dir, "prices")
	t.Cleanup(func() { core.ParsesDir, core.PricesOutputDir = parses, prices })
}

func TestParseRecord_RoundTrip(t *testing.T) {
	useStore(t)
	if rec, err := readRecord(7); rec != nil || err != nil {
		t.Fatalf("readRecord of a file never parsed = %v, %v; want nil, nil", rec, err)
	}
	updated := time.Date(2026, 10, 31, 9, 24, 31, 0, time.UTC)
	want := parseRecord{FileID: 7, Name: "GA_X.json.gz", Address: "https://x/GA_X.json.gz", Status: statusParsed,
		AnthemUpdatedAt: &updated, FinishedAt: time.Date(2026, 11, 5, 12, 0, 0, 0, time.UTC), PriceRows: 3}
	if err := writeRecord(want); err != nil {
		t.Fatalf("writeRecord: %v", err)
	}
	got, err := readRecord(7)
	if err != nil || got == nil {
		t.Fatalf("readRecord = %v, %v", got, err)
	}
	if got.AnthemUpdatedAt == nil || !got.AnthemUpdatedAt.Equal(updated) || !got.FinishedAt.Equal(want.FinishedAt) ||
		got.Status != want.Status || got.PriceRows != 3 || got.Address != want.Address {
		t.Errorf("readRecord = %+v, want %+v", got, want)
	}

	// A fixture's record has no Anthem update.
	if err := writeRecord(parseRecord{FileID: 8, Status: statusParsed}); err != nil {
		t.Fatalf("writeRecord: %v", err)
	}
	if got, err := readRecord(8); err != nil || got.AnthemUpdatedAt != nil {
		t.Errorf("readRecord of a record with no Anthem update = %+v, %v", got, err)
	}
}

func TestDueForParse(t *testing.T) {
	useStore(t)
	mux := http.NewServeMux()
	mux.HandleFunc("/GA_X.json.gz", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Last-Modified", "Sat, 31 Oct 2026 09:24:31 GMT")
	})
	srv := httptest.NewServer(mux)
	defer srv.Close()
	ctx := context.Background()
	f := core.IndexFile{FileID: 7, Name: "GA_X.json.gz", Location: srv.URL + "/GA_X.json.gz"}
	due := func() bool {
		t.Helper()
		ok, err := dueForParse(ctx, f)
		if err != nil {
			t.Fatalf("dueForParse: %v", err)
		}
		return ok
	}

	if !due() {
		t.Error("a file never parsed isn't due")
	}
	oct17, oct31 := time.Date(2026, 10, 17, 4, 0, 0, 0, time.UTC), time.Date(2026, 10, 31, 9, 24, 31, 0, time.UTC)
	for _, c := range []struct {
		status string
		read   *time.Time
		want   bool
	}{
		{statusParsed, &oct31, false}, // Anthem hasn't updated it since
		{statusParsed, &oct17, true},  // Anthem has
		{statusSkipped, &oct31, false},
		{statusSkipped, &oct17, true},
		{statusParsed, nil, true}, // a parse that didn't record its copy
		{statusFailed, &oct17, false},
	} {
		rec := parseRecord{FileID: 7, Status: c.status, AnthemUpdatedAt: c.read, FinishedAt: oct31, Reason: "HTTP 503"}
		if err := writeRecord(rec); err != nil {
			t.Fatal(err)
		}
		if got := due(); got != c.want {
			t.Errorf("a %s file that read %v: due = %v, want %v", c.status, c.read, got, c.want)
		}
	}

	// A failed file isn't due even when Anthem can't be asked.
	f.Location = srv.URL + "/missing.json.gz"
	if due() {
		t.Error("a failed file is due")
	}
	if err := writeRecord(parseRecord{FileID: 7, Status: statusParsed, AnthemUpdatedAt: &oct31, FinishedAt: oct31}); err != nil {
		t.Fatal(err)
	}
	if _, err := dueForParse(ctx, f); err == nil || !strings.Contains(err.Error(), "can't tell whether Anthem has updated it") {
		t.Errorf("a parsed file Anthem can't be asked about gave %v, want an error", err)
	}
}

func TestStopIfStopped(t *testing.T) {
	useStore(t)
	f := core.IndexFile{FileID: 7, Name: "GA_X.json.gz"}
	if err := stopIfStopped(f); err != nil {
		t.Fatalf("stopIfStopped with no scratch folder: %v", err)
	}
	if err := os.MkdirAll(scratchDir(7), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := stopIfStopped(f); err == nil || !strings.Contains(err.Error(), "stopped part way") {
		t.Errorf("stopIfStopped with a scratch folder left = %v, want an error", err)
	}
}

func TestFixtureFile(t *testing.T) {
	f := fixtureFile("extraction/testdata/fixtures/synthetic.json.gz")
	if f.Name != "synthetic.json.gz" || f.Address != "fixture://synthetic.json.gz" || f.FileID != core.FileID(f.Address) || f.Location != "" {
		t.Errorf("fixtureFile = %+v", f)
	}
}
