package extraction

import (
	"context"
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

func writeTargets(t *testing.T, body string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "targets.yaml")
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatalf("write fixture: %v", err)
	}
	return path
}

// The shipped ingestion/etl/targets.yaml is the file `etl parse` reads by default - if it
// stops loading, or stops matching the plan the whole project exists to price,
// every target-selected run silently selects nothing.
func TestLoadTargets_ShippedFile(t *testing.T) {
	ts, err := LoadTargets(filepath.Join("..", "targets.yaml"))
	if err != nil {
		t.Fatalf("LoadTargets(ingestion/etl/targets.yaml): %v", err)
	}
	if len(ts.Targets) == 0 {
		t.Fatal("shipped targets.yaml lists no targets")
	}
	var networks []string
	for _, tg := range ts.Targets {
		networks = append(networks, tg.ExchangeNetworks...)
	}
	if !slices.Contains(networks, "45334-GAN001") {
		t.Errorf("shipped targets.yaml no longer names Blue Value's exchange network 45334-GAN001: %v", networks)
	}
	var files []string
	for _, tg := range ts.Targets {
		files = append(files, tg.Files...)
	}
	if !slices.Contains(files, "GA_JBNKMED0001.json.gz") {
		t.Errorf("shipped targets.yaml no longer names Blue Value's file GA_JBNKMED0001.json.gz: %v", files)
	}

	// The probe's network signal is only as good as the shipped patterns: drop
	// them and every BlueCard shard the index links to Blue Value is downloaded
	// in full again (#98).
	match := ts.NetworkMatcher()
	if match == nil {
		t.Fatal("shipped targets.yaml declares no network_patterns — the probe loses its network signal")
	}
	if !match("GA Blue Value HIX Individual Network") {
		t.Errorf("shipped network_patterns %v no longer match the target plan's own network label", ts.NetworkPatterns())
	}
	if match("BlueCard PPO National") {
		t.Error("shipped network_patterns match a national BlueCard label — the probe would let the shards through")
	}
}

func TestNetworkMatcher(t *testing.T) {
	path := writeTargets(t, `
targets:
  - name: Blue Value
    exchange_networks: ["45334-GAN001"]
    files: ["GA_JBNKMED0001.json.gz"]
    network_patterns: ["GA Blue Value HIX*", "  ", "GA Blue Value HIX*"]
  - name: Other
    exchange_networks: ["49046-GAN008"]
    files: ["GA_JBLDMEDTR01.json.gz"]
    network_patterns: ["EXCHANGES SPECIALIST GATEKEEPER ON INDIVIDUAL"]
`)
	ts, err := LoadTargets(path)
	if err != nil {
		t.Fatalf("LoadTargets: %v", err)
	}
	// Deduped, trimmed, sorted — this list is what the abort message quotes.
	want := []string{"EXCHANGES SPECIALIST GATEKEEPER ON INDIVIDUAL", "GA Blue Value HIX*"}
	got := ts.NetworkPatterns()
	if len(got) != len(want) || got[0] != want[0] || got[1] != want[1] {
		t.Errorf("NetworkPatterns() = %v, want %v", got, want)
	}

	match := ts.NetworkMatcher()
	cases := map[string]bool{
		"GA Blue Value HIX Individual Network":          true,
		"ga blue value hix individual network":          true, // labels are not case-stable across files
		"EXCHANGES SPECIALIST GATEKEEPER ON INDIVIDUAL": true, // exact, no wildcard
		"EXCHANGES SPECIALIST GATEKEEPER":               false,
		"GA Blue Open Access POS Network":               false,
		"BlueCard PPO National":                         false,
		"":                                              false,
		// A group tagged with several networks passes if any member does.
		"BlueCard PPO National|GA Blue Value HIX Individual Network": true,
		"BlueCard PPO National|National Advantage Program":           false,
	}
	for name, expect := range cases {
		if got := match(name); got != expect {
			t.Errorf("match(%q) = %v, want %v", name, got, expect)
		}
	}
}

// A target list with no network_patterns must leave the probe's network signal
// off rather than matching nothing — that would abort every file.
func TestNetworkMatcher_AbsentIsNoSignal(t *testing.T) {
	path := writeTargets(t, "targets:\n  - name: Blue Value\n    exchange_networks: [\"45334-GAN001\"]\n    files: [\"GA_JBNKMED0001.json.gz\"]\n")
	ts, err := LoadTargets(path)
	if err != nil {
		t.Fatalf("LoadTargets: %v", err)
	}
	if ts.NetworkMatcher() != nil {
		t.Error("a target list with no network_patterns produced a matcher")
	}
	var nilSet *TargetSet
	if nilSet.NetworkMatcher() != nil || nilSet.NetworkPatterns() != nil {
		t.Error("a nil TargetSet produced a network signal")
	}
}

func TestGlobMatch(t *testing.T) {
	cases := []struct {
		pattern, s string
		want       bool
	}{
		{"ga blue value hix*", "ga blue value hix individual network", true},
		{"ga blue value hix*", "ga blue value hi", false},
		{"exact", "exact", true},
		{"exact", "exactly", false},
		{"*network", "ga blue value network", true},
		{"*network", "network ga", false},
		{"*value*", "ga blue value network", true},
		{"ga*value*network", "ga blue value hix network", true},
		{"ga*value*network", "ga blue network", false},
		{"*", "anything", true},
		{"*", "", true},
	}
	for _, c := range cases {
		if got := globMatch(c.pattern, c.s); got != c.want {
			t.Errorf("globMatch(%q, %q) = %v, want %v", c.pattern, c.s, got, c.want)
		}
	}
}

func TestLoadTargets_EmptyPathIsNoFilter(t *testing.T) {
	ts, err := LoadTargets("")
	if err != nil {
		t.Fatalf("LoadTargets(\"\"): %v", err)
	}
	if ts != nil {
		t.Fatalf("want nil TargetSet for an empty path, got %+v", ts)
	}
}

func TestLoadTargets_Rejects(t *testing.T) {
	cases := map[string]string{
		"no targets key":   "# nothing here\n",
		"empty list":       "targets: []\n",
		"unnamed target":   "targets:\n  - exchange_networks: [\"45334-GAN001\"]\n",
		"matches nothing":  "targets:\n  - name: Ghost\n",
		"names no files":   "targets:\n  - name: BV\n    exchange_networks: [\"45334-GAN001\"]\n",
		"a blank file":     "targets:\n  - name: BV\n    exchange_networks: [\"45334-GAN001\"]\n    files: [\" \"]\n",
		"a file's folder":  "targets:\n  - name: BV\n    exchange_networks: [\"45334-GAN001\"]\n    files: [\"anthem/GA_JBNKMED0001.json.gz\"]\n",
		"a file's query":   "targets:\n  - name: BV\n    exchange_networks: [\"45334-GAN001\"]\n    files: [\"GA_JBNKMED0001.json.gz?Expires=1\"]\n",
		"one name twice":   "targets:\n  - name: BV\n    exchange_networks: [\"45334-GAN001\"]\n    files: [\"A.json.gz\"]\n  - name: BV\n    exchange_networks: [\"49046-GAN008\"]\n    files: [\"B.json.gz\"]\n",
		"not valid yaml":   "targets: [\n",
		"missing the file": "",
	}
	for name, body := range cases {
		t.Run(name, func(t *testing.T) {
			path := writeTargets(t, body)
			if name == "missing the file" {
				path = filepath.Join(t.TempDir(), "absent.yaml")
			}
			if _, err := LoadTargets(path); err == nil {
				t.Errorf("LoadTargets accepted %q", body)
			}
		})
	}
}

// writePlans writes a small exchange_plans_ga.parquet, with a column selection
// doesn't read, the way the reference builder writes it.
func writePlans(t *testing.T) string {
	t.Helper()
	type row struct {
		PlanID    string `parquet:"plan_id"`
		Name      string `parquet:"name"`
		NetworkID string `parquet:"network_id"`
	}
	path := filepath.Join(t.TempDir(), "exchange_plans_ga.parquet")
	rows := []row{
		{"45334GA0020008", "Anthem Bronze Blue Value HMO 5000", "45334-GAN001"},
		{"45334GA0020002", "Anthem Silver Blue Value HMO 4000", "45334-GAN001"},
		{"49046GA0700053", "Anthem Silver Pathway PCP Copay Choice", "49046-GAN008"},
		{"89942GA0050030", "KP GA Silver HMO", "89942-GAN003"},
	}
	if err := parquet.WriteFile(path, rows); err != nil {
		t.Fatalf("write plans: %v", err)
	}
	return path
}

// A target's plans are every plan on its exchange networks, from Georgia's plan
// file, so a plan that isn't sold any more can't pull a file in.
func TestPlanIDs(t *testing.T) {
	plans := writePlans(t)
	ts, err := LoadTargets(writeTargets(t, `
targets:
  - name: Blue Value
    exchange_networks: ["45334-GAN001"]
    files: ["GA_JBNKMED0001.json.gz"]
  - name: Pathway PCP Copay Choice
    exchange_networks: ["49046-GAN008"]
    files: ["GA_JBLDMEDTR01.json.gz"]
`))
	if err != nil {
		t.Fatalf("LoadTargets: %v", err)
	}
	got, err := ts.PlanIDs(plans)
	if err != nil {
		t.Fatalf("PlanIDs: %v", err)
	}
	if want := []string{"45334GA0020002", "45334GA0020008"}; !slices.Equal(got["Blue Value"], want) {
		t.Errorf("PlanIDs[Blue Value] = %v, want %v", got["Blue Value"], want)
	}
	if want := []string{"49046GA0700053"}; !slices.Equal(got["Pathway PCP Copay Choice"], want) {
		t.Errorf("PlanIDs[Pathway PCP Copay Choice] = %v, want %v", got["Pathway PCP Copay Choice"], want)
	}
	if all := allPlanIDs(got); !slices.Equal(all, []string{"45334GA0020002", "45334GA0020008", "49046GA0700053"}) {
		t.Errorf("allPlanIDs = %v", all)
	}
}

func TestPlanIDs_Rejects(t *testing.T) {
	plans := writePlans(t)
	stale, err := LoadTargets(writeTargets(t, "targets:\n  - name: Gone\n    exchange_networks: [\"45334-GAN009\"]\n    files: [\"GA_GONE.json.gz\"]\n"))
	if err != nil {
		t.Fatalf("LoadTargets: %v", err)
	}
	if _, err := stale.PlanIDs(plans); err == nil || !strings.Contains(err.Error(), "45334-GAN009") {
		t.Errorf("a network with no plans gave %v, want an error naming it", err)
	}
	if _, err := stale.PlanIDs(filepath.Join(t.TempDir(), "absent.parquet")); err == nil ||
		!strings.Contains(err.Error(), "make reference STEP=exchange-plans") {
		t.Errorf("a missing plan file gave %v, want an error saying how to build it", err)
	}
}

// The flag default is spelled from the repo root, but `etl parse` runs from the
// etl module dir. Both have to resolve or the default is useless in one of them.
func TestResolveTargetsPath_BothWorkingDirs(t *testing.T) {
	root := t.TempDir()
	module := filepath.Join(root, "ingestion", "etl")
	if err := os.MkdirAll(module, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(module, "targets.yaml"), []byte("targets: []\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	for _, dir := range []string{root, module} {
		t.Run(filepath.Base(dir), func(t *testing.T) {
			cwd, _ := os.Getwd()
			t.Cleanup(func() { os.Chdir(cwd) })
			if err := os.Chdir(dir); err != nil {
				t.Fatal(err)
			}
			if _, err := resolveTargetsPath(DefaultTargetsPath); err != nil {
				t.Errorf("resolveTargetsPath(%q) from %s: %v", DefaultTargetsPath, dir, err)
			}
		})
	}
}

// Parse downloads only the files a target names, and only while the index links
// each one to a plan on the target's networks.
func TestPickNamed(t *testing.T) {
	bv := Target{Name: "Blue Value", ExchangeNetworks: []string{"45334-GAN001"},
		Files: []string{"GA_NEW.json.gz", "GA_JBNKMED0001.json.gz"}}
	plans := []string{"45334GA0020002", "45334GA0020008"}
	file := func(id int64, host, name string) core.IndexFile {
		addr := "https://" + host + "/" + name
		return core.IndexFile{FileID: id, Name: name, Address: addr, Location: addr}
	}
	index := func(files ...core.IndexFile) map[string][]core.IndexFile {
		byName := map[string][]core.IndexFile{}
		for _, f := range files {
			byName[f.Name] = append(byName[f.Name], f)
		}
		return byName
	}
	linked := map[int64][]string{1: {"45334GA0020008"}, 2: {"45334GA0010112", "45334GA0020002"}}

	got, err := pickNamed("targets.yaml", bv, index(file(1, "x", "GA_JBNKMED0001.json.gz"), file(2, "x", "GA_NEW.json.gz")), linked, plans)
	if err != nil {
		t.Fatalf("pickNamed: %v", err)
	}
	if len(got) != 2 || got[0].FileID != 2 || got[1].FileID != 1 {
		t.Errorf("pickNamed picked %v, want files 2 then 1, in the order the target names them", got)
	}

	rejects := map[string]struct {
		index map[string][]core.IndexFile
		want  string
	}{
		"a name not in the index": {
			index(file(1, "x", "GA_JBNKMED0001.json.gz")),
			"target \"Blue Value\": GA_NEW.json.gz isn't in this month's index"},
		// One BlueCard file can be mirrored on many hosts, and a name has to say which.
		"a name on two hosts": {
			index(file(1, "x", "GA_JBNKMED0001.json.gz"), file(2, "x", "GA_NEW.json.gz"), file(3, "y", "GA_NEW.json.gz")),
			"GA_NEW.json.gz matches 2 files in this month's index, https://x/GA_NEW.json.gz, https://y/GA_NEW.json.gz"},
		// GA_JBKEMED0001 carries Blue Value's label, but the index links it only
		// to a plan no longer sold.
		"a file that prices another plan": {
			index(file(1, "x", "GA_JBNKMED0001.json.gz"), file(4, "x", "GA_NEW.json.gz")),
			"names GA_NEW.json.gz, but the index doesn't link it to a plan on 45334-GAN001"},
	}
	for name, c := range rejects {
		t.Run(name, func(t *testing.T) {
			if _, err := pickNamed("targets.yaml", bv, c.index, linked, plans); err == nil || !strings.Contains(err.Error(), c.want) {
				t.Errorf("pickNamed gave %v, want an error containing %q", err, c.want)
			}
		})
	}
}

func TestChangedSince(t *testing.T) {
	oct17 := time.Date(2026, 10, 17, 4, 0, 0, 0, time.UTC)
	oct31 := time.Date(2026, 10, 31, 4, 0, 0, 0, time.UTC)
	if changedSince(&oct17, oct17.In(time.FixedZone("EST", -5*3600))) {
		t.Error("the copy we parsed counts as changed when only its time zone differs")
	}
	if !changedSince(&oct17, oct31) {
		t.Error("an update after the copy we parsed doesn't count as changed")
	}
	if !changedSince(nil, oct17) {
		t.Error("a parse that didn't record its copy doesn't count as changed")
	}
}

func TestLastModified(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("/dated.json.gz", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Last-Modified", "Sat, 31 Oct 2026 04:12:09 GMT")
	})
	mux.HandleFunc("/undated.json.gz", func(w http.ResponseWriter, r *http.Request) {})
	srv := httptest.NewServer(mux)
	defer srv.Close()
	ctx := context.Background()

	got, err := lastModified(ctx, srv.URL+"/dated.json.gz")
	if want := time.Date(2026, 10, 31, 4, 12, 9, 0, time.UTC); err != nil || !got.Equal(want) {
		t.Errorf("lastModified = %v, %v; want %v", got, err, want)
	}
	if _, err := lastModified(ctx, srv.URL+"/undated.json.gz"); err == nil || !strings.Contains(err.Error(), "no Last-Modified") {
		t.Errorf("a file with no Last-Modified gave %v, want an error saying so", err)
	}
	if _, err := lastModified(ctx, srv.URL+"/missing.json.gz"); err == nil || !strings.Contains(err.Error(), "HTTP 404") {
		t.Errorf("a missing file gave %v, want HTTP 404", err)
	}

	// The download reads the same header, so parse records the copy it read.
	body, _, updated, cancel, err := openMRF(ctx, srv.URL+"/dated.json.gz", "")
	if err != nil {
		t.Fatalf("openMRF: %v", err)
	}
	body.Close()
	cancel()
	if !updated.Equal(time.Date(2026, 10, 31, 4, 12, 9, 0, time.UTC)) {
		t.Errorf("openMRF read Last-Modified %v, want 2026-10-31 04:12:09 UTC", updated)
	}
}
