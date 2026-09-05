package extraction

// Target-plan selection: which pending files does `etl parse` pull, and why.
//
// Files used to be chosen by what their URL looked like (the `anthem/GA_` name
// check and the gaPriorityExpr scoring that replaced it). Both were guesses at
// the question that actually matters — "does this file serve the plan we are
// pricing?" — which the master index answers directly. `etl discover` now keeps
// that answer in index_file_plans, and the target list here says which plans to
// care about.

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"gopkg.in/yaml.v3"
)

// DefaultTargetsPath is the -targets default, spelled from the repo root.
// resolveTargetsPath makes it work from the etl module dir too.
const DefaultTargetsPath = "etl/targets.yaml"

// Target is one plan (or family of plans) worth parsing files for.
type Target struct {
	Name string `yaml:"name"`
	// PlanNamePatterns match index_file_plans.plan_name case-insensitively; `*`
	// is a wildcard. A pattern with no `*` is an exact match.
	PlanNamePatterns []string `yaml:"plan_name_patterns"`
	// PlanIDPrefixes are literal HIOS plan_id prefixes (positional: [0:5] issuer,
	// [5:7] state).
	PlanIDPrefixes []string `yaml:"plan_id_prefixes"`
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
	for i, t := range ts.Targets {
		if strings.TrimSpace(t.Name) == "" {
			return nil, fmt.Errorf("%s: target %d has no name", resolved, i+1)
		}
		if len(t.PlanNamePatterns) == 0 && len(t.PlanIDPrefixes) == 0 {
			return nil, fmt.Errorf("%s: target %q has neither plan_name_patterns nor plan_id_prefixes — it would match nothing", resolved, t.Name)
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

// likeEscape neutralises the LIKE metacharacters in a literal so a plan name
// containing '%' or '_' is matched as itself. The backslash escape has to be
// doubled first or it would escape the escapes this adds.
func likeEscape(s string) string {
	s = strings.ReplaceAll(s, `\`, `\\`)
	s = strings.ReplaceAll(s, "%", `\%`)
	s = strings.ReplaceAll(s, "_", `\_`)
	return s
}

// globToLike turns a target's `*`-wildcard pattern into a SQL LIKE pattern.
func globToLike(pattern string) string {
	return strings.ReplaceAll(likeEscape(pattern), "*", "%")
}

// PlanMatchSQL builds the boolean expression that decides whether one
// index_file_plans row (aliased alias) belongs to a target, together with the
// arguments it needs. startArg is the 1-based number of the first placeholder;
// the returned nextArg is the number the caller's next placeholder should use.
//
// Patterns are parameters, never interpolated — a plan name is index-supplied
// text and targets.yaml is user-editable, so neither goes into the statement.
func (ts *TargetSet) PlanMatchSQL(alias string, startArg int) (expr string, args []any, nextArg int) {
	if ts == nil || len(ts.Targets) == 0 {
		return "", nil, startArg
	}
	arg := startArg
	var terms []string
	for _, t := range ts.Targets {
		for _, p := range t.PlanNamePatterns {
			terms = append(terms, fmt.Sprintf(`%s.plan_name ILIKE $%d`, alias, arg))
			args = append(args, globToLike(p))
			arg++
		}
		for _, p := range t.PlanIDPrefixes {
			terms = append(terms, fmt.Sprintf(`%s.plan_id LIKE $%d`, alias, arg))
			args = append(args, likeEscape(p)+"%")
			arg++
		}
	}
	if len(terms) == 0 {
		return "", nil, startArg
	}
	return strings.Join(terms, " OR "), args, arg
}
