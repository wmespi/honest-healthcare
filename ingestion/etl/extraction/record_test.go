package extraction

import (
	"context"
	"log"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/wmespi/honest-healthcare/ingestion/etl/core"
)

// useStore points the parse records, the Parquet parse writes and the scratch
// folder at a temp dir.
func useStore(t *testing.T) {
	t.Helper()
	dir := t.TempDir()
	paths := []*string{&core.ParsesDir, &core.PricesOutputDir, &core.GroupSetsOutputDir,
		&core.ProvidersOutputDir, &core.CodesOutputDir}
	saved := make([]string, len(paths))
	for i, p := range paths {
		saved[i] = *p
		*p = filepath.Join(dir, filepath.Base(*p))
	}
	t.Cleanup(func() {
		for i, p := range paths {
			*p = saved[i]
		}
	})
}

// anthemServer serves one file the way Anthem does, with a Last-Modified, and
// returns its index entry and a func that replaces the file.
func anthemServer(t *testing.T) (core.IndexFile, func(body []byte, modified time.Time)) {
	t.Helper()
	var body []byte
	var modified time.Time
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Last-Modified", modified.UTC().Format(http.TimeFormat))
		w.Write(body)
	}))
	t.Cleanup(srv.Close)
	loc := srv.URL + "/GA_X.json.gz"
	return core.IndexFile{FileID: 7, Name: "GA_X.json.gz", Address: loc, Location: loc},
		func(b []byte, m time.Time) { body, modified = b, m }
}

// A failed or skipped parse writes no Parquet, so its record keeps the date of
// the copy whose rates are still on disk.
func TestParseRecord_KeepsTheCopyOnDisk(t *testing.T) {
	useStore(t)
	fixture, err := os.ReadFile("testdata/fixtures/synthetic.json.gz")
	if err != nil {
		t.Fatal(err)
	}
	f, serve := anthemServer(t)
	parse := func() *parseRecord {
		t.Helper()
		parseRates(context.Background(), f, "", false, nil, providerProbe{}, false)
		rec, err := readRecord(f)
		if err != nil || rec == nil {
			t.Fatalf("readRecord = %v, %v", rec, err)
		}
		return rec
	}
	is := func(got *time.Time, want time.Time) bool { return got != nil && got.Equal(want) }
	oct17, nov1 := time.Date(2026, 10, 17, 4, 0, 0, 0, time.UTC), time.Date(2026, 11, 1, 4, 0, 0, 0, time.UTC)

	if err := writeRecord(parseRecord{FileID: f.FileID, Name: f.Name, Address: f.Address, Status: statusFailed}); err != nil {
		t.Fatal(err)
	}
	serve(fixture[:len(fixture)/2], oct17)
	if rec := parse(); rec.Status != statusFailed || rec.RatesUpdatedAt != nil {
		t.Errorf("a first parse that failed: %s, rates from %v; want failed and none", rec.Status, rec.RatesUpdatedAt)
	}
	serve(fixture, oct17)
	if rec := parse(); rec.Status != statusParsed || !is(rec.RatesUpdatedAt, oct17) {
		t.Errorf("a parse: %s, rates from %v; want parsed, from %v", rec.Status, rec.RatesUpdatedAt, oct17)
	}
	serve(fixture[:len(fixture)/2], nov1)
	for range 2 {
		if rec := parse(); rec.Status != statusFailed || !is(rec.AnthemUpdatedAt, nov1) || !is(rec.RatesUpdatedAt, oct17) {
			t.Errorf("a failed parse of a newer copy: %s, read %v, rates from %v; want failed, %v, %v",
				rec.Status, rec.AnthemUpdatedAt, rec.RatesUpdatedAt, nov1, oct17)
		}
	}

	// A parsed record with no rates_updated_at: its rates are the copy it read.
	if err := writeRecord(parseRecord{FileID: f.FileID, Name: f.Name, Address: f.Address, Status: statusParsed,
		AnthemUpdatedAt: &oct17}); err != nil {
		t.Fatal(err)
	}
	if rec := parse(); !is(rec.RatesUpdatedAt, oct17) {
		t.Errorf("a failure after a parsed record with no rates_updated_at keeps rates from %v, want %v", rec.RatesUpdatedAt, oct17)
	}
}

// The end of a target-selected run lists every named file whose latest parse
// failed or was skipped, with the networks it prices and the rates it left.
func TestReportUnparsed(t *testing.T) {
	useStore(t)
	var out strings.Builder
	log.SetOutput(&out)
	t.Cleanup(func() { log.SetOutput(os.Stderr) })
	oct17 := time.Date(2026, 10, 17, 4, 0, 0, 0, time.UTC)
	file := func(name, status, reason string, rates *time.Time, onDisk bool) namedFile {
		t.Helper()
		f := core.IndexFile{FileID: int64(len(name)), Name: name, Address: "https://x/" + name}
		if status != "" {
			if err := writeRecord(parseRecord{FileID: f.FileID, Name: name, Address: f.Address, Status: status,
				Reason: reason, RatesUpdatedAt: rates, FinishedAt: oct17}); err != nil {
				t.Fatal(err)
			}
		}
		if onDisk {
			if err := os.MkdirAll(core.CodesOutputDir, 0o755); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(core.CodesOutputDir, f.Stem()+".parquet"), nil, 0o644); err != nil {
				t.Fatal(err)
			}
		}
		return namedFile{f, []string{"45334-GAN001"}}
	}
	parsed := file("GA_OK.json.gz", statusParsed, "", &oct17, true)
	reportUnparsed(Options{}, []namedFile{parsed, file("GA_NEW.json.gz", "", "", nil, false)})
	if out.Len() != 0 {
		t.Errorf("a parsed file and one never parsed are reported:\n%s", out.String())
	}

	named := []namedFile{parsed,
		file("GA_STALE.json.gz", statusFailed, "malformed MRF: unexpected EOF", &oct17, true),
		file("GA_NONE.json.gz", statusFailed, "HTTP 503", nil, false),
		file("GA_RENAMED.json.gz", statusSkipped, "probe: no wanted providers", &oct17, true)}
	named[2].Networks = []string{"49046-GAN008"}
	reportUnparsed(Options{}, named)
	got := out.String()
	for _, want := range []string{
		"GA_STALE.json.gz (45334-GAN001) failed on 2026-10-17 04:00:00: malformed MRF: unexpected EOF",
		"its rates on disk are the copy Anthem updated on 2026-10-17 04:00:00",
		"runs `make parse FILE=GA_STALE.json.gz`",
		"GA_NONE.json.gz (49046-GAN008) failed on 2026-10-17 04:00:00: HTTP 503",
		"no earlier parse left its rates on disk, so its networks have none",
		"GA_RENAMED.json.gz (45334-GAN001) skipped on 2026-10-17 04:00:00: probe: no wanted providers",
		"the probe stopped it (ingestion/etl/parse.md#the-provider-probe), and it's downloaded again once Anthem updates it",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("the report doesn't say %q:\n%s", want, got)
		}
	}
	if strings.Contains(got, "GA_OK") {
		t.Errorf("the report lists a parsed file:\n%s", got)
	}
}

func TestParseRecord_RoundTrip(t *testing.T) {
	useStore(t)
	x := core.IndexFile{FileID: 7, Name: "GA_X.json.gz", Address: "https://x/GA_X.json.gz"}
	if rec, err := readRecord(x); rec != nil || err != nil {
		t.Fatalf("readRecord of a file never parsed = %v, %v; want nil, nil", rec, err)
	}
	updated := time.Date(2026, 10, 31, 9, 24, 31, 0, time.UTC)
	want := parseRecord{FileID: 7, Name: "GA_X.json.gz", Address: "https://x/GA_X.json.gz", Status: statusParsed,
		AnthemUpdatedAt: &updated, FinishedAt: time.Date(2026, 11, 5, 12, 0, 0, 0, time.UTC), PriceRows: 3}
	if err := writeRecord(want); err != nil {
		t.Fatalf("writeRecord: %v", err)
	}
	if _, err := os.Stat(filepath.Join(core.ParsesDir, "GA_X.parquet")); err != nil {
		t.Errorf("the record isn't saved under the file's stem: %v", err)
	}
	got, err := readRecord(x)
	if err != nil || got == nil {
		t.Fatalf("readRecord = %v, %v", got, err)
	}
	if got.AnthemUpdatedAt == nil || !got.AnthemUpdatedAt.Equal(updated) || !got.FinishedAt.Equal(want.FinishedAt) ||
		got.Status != want.Status || got.PriceRows != 3 || got.Address != want.Address {
		t.Errorf("readRecord = %+v, want %+v", got, want)
	}

	// A fixture's record has no Anthem update.
	fixture := fixtureFile("synthetic.json.gz")
	if err := writeRecord(parseRecord{FileID: fixture.FileID, Name: fixture.Name, Status: statusParsed}); err != nil {
		t.Fatalf("writeRecord: %v", err)
	}
	if got, err := readRecord(fixture); err != nil || got.AnthemUpdatedAt != nil {
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
	f := core.IndexFile{FileID: 7, Name: "GA_X.json.gz", Address: srv.URL + "/GA_X.json.gz", Location: srv.URL + "/GA_X.json.gz"}
	record := func(r parseRecord) {
		t.Helper()
		r.FileID, r.Name, r.Address = f.FileID, f.Name, f.Address
		if err := writeRecord(r); err != nil {
			t.Fatal(err)
		}
	}
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
		record(parseRecord{Status: c.status, AnthemUpdatedAt: c.read, FinishedAt: oct31, Reason: "HTTP 503"})
		if got := due(); got != c.want {
			t.Errorf("a %s file that read %v: due = %v, want %v", c.status, c.read, got, c.want)
		}
	}

	// A failed file isn't due even when Anthem can't be asked.
	f.Location = srv.URL + "/missing.json.gz"
	if due() {
		t.Error("a failed file is due")
	}
	record(parseRecord{Status: statusParsed, AnthemUpdatedAt: &oct31, FinishedAt: oct31})
	if _, err := dueForParse(ctx, f); err == nil || !strings.Contains(err.Error(), "can't tell whether Anthem has updated it") {
		t.Errorf("a parsed file Anthem can't be asked about gave %v, want an error", err)
	}

	// The same name at a new address is a new file, even one that failed.
	record(parseRecord{Status: statusFailed, AnthemUpdatedAt: &oct31, FinishedAt: oct31})
	f.Address = "https://moved/GA_X.json.gz"
	if !due() {
		t.Error("a file Anthem moved to a new address isn't due")
	}
}

func TestStopIfStopped(t *testing.T) {
	useStore(t)
	f := core.IndexFile{FileID: 7, Name: "GA_X.json.gz"}
	if err := stopIfStopped(f); err != nil {
		t.Fatalf("stopIfStopped with no scratch folder: %v", err)
	}
	if err := os.MkdirAll(scratchDir(f), 0o755); err != nil {
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
