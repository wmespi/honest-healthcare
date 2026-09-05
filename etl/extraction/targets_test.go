package extraction

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
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
	expr, args, next := ts.PlanMatchSQL("p", 1)
	if expr == "" {
		t.Fatal("shipped targets.yaml produced an empty match expression")
	}
	if next != 1+len(args) {
		t.Errorf("nextArg = %d, want %d", next, 1+len(args))
	}
	var blueValue bool
	for _, a := range args {
		if s, ok := a.(string); ok && strings.EqualFold(s, "%blue value%") {
			blueValue = true
		}
	}
	if !blueValue {
		t.Errorf("shipped targets.yaml no longer matches Blue Value — args = %v", args)
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
    plan_name_patterns: ["*blue value*"]
    network_patterns: ["GA Blue Value HIX*", "  ", "GA Blue Value HIX*"]
  - name: Other
    plan_name_patterns: ["*other*"]
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
	path := writeTargets(t, "targets:\n  - name: Blue Value\n    plan_name_patterns: [\"*blue value*\"]\n")
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
	// A nil set must degrade to "no restriction", not to "match nothing".
	if expr, args, next := ts.PlanMatchSQL("p", 1); expr != "" || args != nil || next != 1 {
		t.Errorf("nil TargetSet produced %q / %v / %d", expr, args, next)
	}
}

func TestLoadTargets_Rejects(t *testing.T) {
	cases := map[string]string{
		"no targets key":   "# nothing here\n",
		"empty list":       "targets: []\n",
		"unnamed target":   "targets:\n  - plan_name_patterns: [\"*x*\"]\n",
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

func TestPlanMatchSQL(t *testing.T) {
	path := writeTargets(t, `
targets:
  - name: Blue Value
    plan_name_patterns: ["*blue value*", "EXACT PLAN"]
  - name: Georgia issuer
    plan_id_prefixes: ["45334GA"]
`)
	ts, err := LoadTargets(path)
	if err != nil {
		t.Fatalf("LoadTargets: %v", err)
	}

	// Placeholders continue from startArg so the caller can keep numbering
	// (the queue query appends its own LIMIT parameter after these).
	expr, args, next := ts.PlanMatchSQL("p", 3)
	wantExpr := "p.plan_name ILIKE $3 OR p.plan_name ILIKE $4 OR p.plan_id LIKE $5"
	if expr != wantExpr {
		t.Errorf("expr\n got %q\nwant %q", expr, wantExpr)
	}
	if next != 6 {
		t.Errorf("nextArg = %d, want 6", next)
	}
	want := []any{"%blue value%", "EXACT PLAN", "45334GA%"}
	if len(args) != len(want) {
		t.Fatalf("args = %v, want %v", args, want)
	}
	for i := range want {
		if args[i] != want[i] {
			t.Errorf("args[%d] = %v, want %v", i, args[i], want[i])
		}
	}
}

// A plan name is index-supplied text and targets.yaml is hand-edited, so LIKE
// metacharacters in either must match themselves rather than acting as
// wildcards. Only `*` is a wildcard.
func TestGlobToLike_EscapesMetacharacters(t *testing.T) {
	cases := []struct{ in, want string }{
		{"*blue value*", "%blue value%"},
		{"EXACT PLAN", "EXACT PLAN"},
		{"100% PLAN", `100\% PLAN`},
		{"UNDER_SCORE", `UNDER\_SCORE`},
		{`back\slash`, `back\\slash`},
		{`*a_b%c*`, `%a\_b\%c%`},
	}
	for _, c := range cases {
		if got := globToLike(c.in); got != c.want {
			t.Errorf("globToLike(%q) = %q, want %q", c.in, got, c.want)
		}
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
