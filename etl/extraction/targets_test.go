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
