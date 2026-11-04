package extraction

// Target-plan selection: which pending files does `etl parse` pull, and why.
//
// A target names the insurer files to parse, and nothing else is downloaded,
// even a file the index links to the same plans. The master index says which
// plan IDs a file prices (`etl discover` keeps that in index_file_plans), and
// Georgia's exchange plan file says which plan IDs are on each provider network
// (`make reference STEP=exchange-plans`), so parse stops unless the index links
// each named file to a plan someone can buy on one of the target's networks.

import (
	"fmt"
	"log"
	"os"
	"path/filepath"
	"slices"
	"sort"
	"strings"
	"time"

	parquet "github.com/parquet-go/parquet-go"
	"gopkg.in/yaml.v3"
)

// DefaultTargetsPath is the -targets default, spelled from the repo root.
// resolveTargetsPath makes it work from the etl module dir too.
const DefaultTargetsPath = "etl/targets.yaml"

// Target is one family of plans worth parsing files for.
type Target struct {
	Name string `yaml:"name"`
	// ExchangeNetworks are network IDs from Georgia's exchange plan file, issuer
	// and network (`45334-GAN001`). Every plan on them is a target plan.
	ExchangeNetworks []string `yaml:"exchange_networks"`
	// Files are the insurer files that price the target's plans, each by the
	// last part of its index location without the query (`GA_JBNKMED0001.json.gz`).
	// Parse downloads only these, and only while the index links each one to a
	// plan on the target's networks.
	Files []string `yaml:"files"`
	// NetworkPatterns are the `provider_references[].network_name` labels this
	// plan's rates are expected to carry, `*`-wildcarded and matched
	// case-insensitively. They are not a row filter — every network in a file
	// that passes is written, and the build step selects. They are the probe's
	// second signal (probe.go): a file the index links to the plan but whose
	// provider_references carry none of these labels is abandoned before
	// in_network. Optional; a target with none contributes no network signal.
	NetworkPatterns []string `yaml:"network_patterns"`
}

// TargetSet is a parsed etl/targets.yaml.
type TargetSet struct {
	Path    string   `yaml:"-"`
	Targets []Target `yaml:"targets"`
}

// resolveTargetsPath finds the targets file whether the caller is standing in
// the repo root or in the etl module dir (`etl parse` runs from etl/, but the
// flag and the docs both spell the path from the root).
func resolveTargetsPath(path string) (string, error) {
	candidates := []string{path}
	if rest := strings.TrimPrefix(path, "etl"+string(filepath.Separator)); rest != path {
		candidates = append(candidates, rest)
	} else if !filepath.IsAbs(path) {
		candidates = append(candidates, filepath.Join("etl", path))
	}
	for _, c := range candidates {
		if st, err := os.Stat(c); err == nil && !st.IsDir() {
			return c, nil
		}
	}
	return "", fmt.Errorf("targets file not found (looked in %s)", strings.Join(candidates, ", "))
}

// LoadTargets reads and validates a targets file. An empty path is not an error
// — it means "no target filter", and returns a nil TargetSet.
func LoadTargets(path string) (*TargetSet, error) {
	if strings.TrimSpace(path) == "" {
		return nil, nil
	}
	resolved, err := resolveTargetsPath(path)
	if err != nil {
		return nil, err
	}
	raw, err := os.ReadFile(resolved)
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", resolved, err)
	}
	var ts TargetSet
	if err := yaml.Unmarshal(raw, &ts); err != nil {
		return nil, fmt.Errorf("parse %s: %w", resolved, err)
	}
	ts.Path = resolved
	if len(ts.Targets) == 0 {
		return nil, fmt.Errorf("%s lists no targets — parse would select nothing", resolved)
	}
	names := map[string]bool{}
	for i, t := range ts.Targets {
		if strings.TrimSpace(t.Name) == "" {
			return nil, fmt.Errorf("%s: target %d has no name", resolved, i+1)
		}
		if names[t.Name] {
			return nil, fmt.Errorf("%s: two targets are named %q", resolved, t.Name)
		}
		names[t.Name] = true
		if len(t.ExchangeNetworks) == 0 {
			return nil, fmt.Errorf("%s: target %q lists no exchange_networks, so it would match nothing", resolved, t.Name)
		}
		if len(t.Files) == 0 {
			return nil, fmt.Errorf("%s: target %q names no files, so parse would download nothing for it", resolved, t.Name)
		}
		for j, f := range t.Files {
			f = strings.TrimSpace(f)
			if f == "" || strings.ContainsAny(f, "/?") {
				return nil, fmt.Errorf("%s: target %q names file %q; give only the last part of its location, with no folder or query", resolved, t.Name, f)
			}
			ts.Targets[i].Files[j] = f
		}
	}
	return &ts, nil
}

// Names lists the target names, for logging.
func (ts *TargetSet) Names() []string {
	if ts == nil {
		return nil
	}
	out := make([]string, 0, len(ts.Targets))
	for _, t := range ts.Targets {
		out = append(out, t.Name)
	}
	return out
}

// NetworkPatterns is the union of every target's network_patterns, sorted and
// deduped — the spec the probe reports it is matching against.
func (ts *TargetSet) NetworkPatterns() []string {
	if ts == nil {
		return nil
	}
	seen := map[string]struct{}{}
	var out []string
	for _, t := range ts.Targets {
		for _, p := range t.NetworkPatterns {
			p = strings.TrimSpace(p)
			if p == "" {
				continue
			}
			if _, dup := seen[p]; dup {
				continue
			}
			seen[p] = struct{}{}
			out = append(out, p)
		}
	}
	sort.Strings(out)
	return out
}

// NetworkMatcher compiles the union of the targets' network_patterns into the
// probe's network predicate: does this provider group's network_name look like a
// network one of the target plans is priced on? The candidate may itself be
// "|"-joined (a group tagged with several networks) — it passes if ANY member
// matches ANY pattern. Returns nil when no target declares a pattern, which the
// probe reads as "no network signal configured".
func (ts *TargetSet) NetworkMatcher() func(string) bool {
	patterns := ts.NetworkPatterns()
	if len(patterns) == 0 {
		return nil
	}
	lowered := make([]string, len(patterns))
	for i, p := range patterns {
		lowered[i] = strings.ToLower(p)
	}
	return func(networkName string) bool {
		for _, member := range strings.Split(networkName, "|") {
			member = strings.ToLower(strings.TrimSpace(member))
			if member == "" {
				continue
			}
			for _, p := range lowered {
				if globMatch(p, member) {
					return true
				}
			}
		}
		return false
	}
}

// globMatch reports whether s matches a `*`-wildcard pattern. Both must already
// be lower-cased by the caller — this is called once per provider_references
// entry on multi-GB files, so it allocates nothing and folds no case itself.
// A pattern with no `*` is an exact match.
func globMatch(pattern, s string) bool {
	parts := strings.Split(pattern, "*")
	if len(parts) == 1 {
		return pattern == s
	}
	first, last := parts[0], parts[len(parts)-1]
	if !strings.HasPrefix(s, first) {
		return false
	}
	s = s[len(first):]
	for _, mid := range parts[1 : len(parts)-1] {
		i := strings.Index(s, mid)
		if i < 0 {
			return false
		}
		s = s[i+len(mid):]
	}
	return strings.HasSuffix(s, last)
}

// exchangePlan is the two columns of exchange_plans_ga.parquet selection needs.
type exchangePlan struct {
	PlanID    string `parquet:"plan_id"`
	NetworkID string `parquet:"network_id"`
}

// PlanIDs reads Georgia's exchange plan file and returns, for each target by
// name, every plan ID on its networks, sorted. A missing file, or a target
// network with no plans in it, is an error: checking a file against an empty
// list would reject every file for the wrong reason.
func (ts *TargetSet) PlanIDs(plansPath string) (map[string][]string, error) {
	rows, err := parquet.ReadFile[exchangePlan](plansPath)
	if err != nil {
		return nil, fmt.Errorf("read %s (run `make reference STEP=exchange-plans`): %w", plansPath, err)
	}
	byNetwork := map[string][]string{}
	for _, r := range rows {
		byNetwork[r.NetworkID] = append(byNetwork[r.NetworkID], r.PlanID)
	}
	byTarget := map[string][]string{}
	for _, t := range ts.Targets {
		var ids []string
		for _, n := range t.ExchangeNetworks {
			plans, ok := byNetwork[strings.TrimSpace(n)]
			if !ok {
				return nil, fmt.Errorf("%s: target %q names exchange network %q, which has no plans in %s", ts.Path, t.Name, n, plansPath)
			}
			ids = append(ids, plans...)
		}
		sort.Strings(ids)
		byTarget[t.Name] = ids
	}
	return byTarget, nil
}

// allPlanIDs is every target's plan IDs in one sorted list, or nil for none.
func allPlanIDs(byTarget map[string][]string) []string {
	var ids []string
	for _, plans := range byTarget {
		ids = append(ids, plans...)
	}
	sort.Strings(ids)
	return slices.Compact(ids)
}

// queueRow is a queue row whose file name a target names.
type queueRow struct {
	pendingFile
	Name   string
	Status string
	Linked bool       // the index links it to a plan on the target's networks
	Parsed *time.Time // Anthem's Last-Modified for the copy we last parsed, if known
}

// pickNamed checks the queue rows found for a target's files and returns, in the
// order given, the ones parse may download: pending ones, and parsed or skipped
// ones, which are downloaded again only if Anthem has updated them since
// (dueForParse). It stops unless every file the target names is in the queue
// exactly once and the index links it to one of the target's plans, so a
// mistyped name or a file that prices another plan can't slip by.
func pickNamed(path string, t Target, rows []queueRow) ([]queueRow, error) {
	ids := map[string][]int{}
	for _, r := range rows {
		ids[r.Name] = append(ids[r.Name], r.ID)
	}
	for _, name := range t.Files {
		switch len(ids[name]) {
		case 0:
			return nil, fmt.Errorf("%s: target %q names %s, which isn't in the queue: run `make discover`, or check the name against the index", path, t.Name, name)
		case 1:
		default:
			return nil, fmt.Errorf("%s: target %q names %s, which matches queue rows %v; it has to match one", path, t.Name, name, ids[name])
		}
	}
	var picked []queueRow
	for _, r := range rows {
		if !r.Linked {
			return nil, fmt.Errorf("%s: target %q names %s (file %d), but the index doesn't link it to a plan on %s, so it doesn't price the target's plans",
				path, t.Name, r.Name, r.ID, strings.Join(t.ExchangeNetworks, ", "))
		}
		switch r.Status {
		case "pending", "completed", "skipped":
			picked = append(picked, r)
		default:
			log.Printf("🎯 %s (file %d) is %s, so it isn't downloaded", r.Name, r.ID, r.Status)
		}
	}
	return picked, nil
}

// changedSince says whether Anthem's copy of a file, last updated at anthem,
// differs from the copy we parsed. A parse that didn't record its copy counts
// as a different one.
func changedSince(parsed *time.Time, anthem time.Time) bool {
	return parsed == nil || !parsed.Equal(anthem)
}

// planMatchSQL is the condition that an index_file_plans row (aliased alias)
// prices one of planIDs, bound as one array parameter numbered arg.
func planMatchSQL(alias string, arg int) string {
	return fmt.Sprintf(`%s.plan_id = ANY($%d)`, alias, arg)
}
