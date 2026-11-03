package extraction

import (
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	parquet "github.com/parquet-go/parquet-go"
)

func writeTargets(t *testing.T, body string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "targets.yaml")
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatalf("write fixture: %v", err)
	}
	return path
}

// The shipped etl/targets.yaml is the file `etl parse` reads by default — if it
// stops loading, or stops matching the plan the whole project exists to price,
// every queue run silently selects nothing.
func TestLoadTargets_ShippedFile(t *testing.T) {
	ts, err := LoadTargets(filepath.Join("..", "targets.yaml"))
	if err != nil {
		t.Fatalf("LoadTargets(etl/targets.yaml): %v", err)
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
    network_patterns: ["GA Blue Value HIX*", "  ", "GA Blue Value HIX*"]
  - name: Other
    exchange_networks: ["49046-GAN008"]
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
	path := writeTargets(t, "targets:\n  - name: Blue Value\n    exchange_networks: [\"45334-GAN001\"]\n")
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
  - name: Pathway PCP Copay Choice
    exchange_networks: ["49046-GAN008"]
`))
	if err != nil {
		t.Fatalf("LoadTargets: %v", err)
	}
	got, err := ts.PlanIDs(plans)
	if err != nil {
		t.Fatalf("PlanIDs: %v", err)
	}
	want := []string{"45334GA0020002", "45334GA0020008", "49046GA0700053"}
	if !slices.Equal(got, want) {
		t.Errorf("PlanIDs = %v, want %v", got, want)
	}
	if sql := planMatchSQL("p", 3); sql != "p.plan_id = ANY($3)" {
		t.Errorf("planMatchSQL = %q", sql)
	}
}

func TestPlanIDs_Rejects(t *testing.T) {
	plans := writePlans(t)
	stale, err := LoadTargets(writeTargets(t, "targets:\n  - name: Gone\n    exchange_networks: [\"45334-GAN009\"]\n"))
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
	if err := os.MkdirAll(filepath.Join(root, "etl"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "etl", "targets.yaml"), []byte("targets: []\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	for _, dir := range []string{root, filepath.Join(root, "etl")} {
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
