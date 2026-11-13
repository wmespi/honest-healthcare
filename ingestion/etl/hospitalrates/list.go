package hospitalrates

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"gopkg.in/yaml.v3"
)

// DefaultListPath is the -list default, spelled from the repo root.
// resolveListPath makes it work from the etl module dir too.
const DefaultListPath = "ingestion/etl/hospitals.yaml"

// Hospital is one entry of hospitals.yaml: where its price file is, either a
// location in its system's cms-hpt.txt or a direct address.
type Hospital struct {
	// CCN is the hospital's CMS Certification Number; it names the output file.
	CCN string `yaml:"ccn"`
	// Name is the hospital's everyday name, for logs.
	Name string `yaml:"name"`
	// Index is the system's cms-hpt.txt, and Location the hospital's
	// location-name in it.
	Index    string `yaml:"index"`
	Location string `yaml:"location"`
	// URL is the price file itself, for a hospital with no usable cms-hpt.txt.
	URL string `yaml:"url"`
}

// List is a parsed hospitals.yaml.
type List struct {
	// Payers are case-insensitive regular expressions: a row whose payer name
	// matches one is kept.
	Payers    []string   `yaml:"payers"`
	Hospitals []Hospital `yaml:"hospitals"`

	payers []*regexp.Regexp
}

var ccnRe = regexp.MustCompile(`^[0-9A-Z]{6}$`)

// validCCN reports whether ccn looks like a CMS Certification Number, which
// also keeps it safe to name a file with.
func validCCN(ccn string) bool { return ccnRe.MatchString(ccn) }

// resolveListPath finds the list whether the caller is standing in the repo
// root or in the etl module dir, as parse finds targets.yaml.
func resolveListPath(path string) (string, error) {
	candidates := []string{path}
	if !filepath.IsAbs(path) {
		candidates = append(candidates, filepath.Join("..", "..", path))
	}
	for _, c := range candidates {
		if st, err := os.Stat(c); err == nil && !st.IsDir() {
			return c, nil
		}
	}
	return "", fmt.Errorf("hospital list not found (looked in %s)", strings.Join(candidates, ", "))
}

// LoadList reads and checks a hospital list.
func LoadList(path string) (*List, error) {
	resolved, err := resolveListPath(path)
	if err != nil {
		return nil, err
	}
	raw, err := os.ReadFile(resolved)
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", resolved, err)
	}
	var l List
	if err := yaml.Unmarshal(raw, &l); err != nil {
		return nil, fmt.Errorf("parse %s: %w", resolved, err)
	}
	if len(l.Payers) == 0 {
		return nil, fmt.Errorf("%s lists no payers, so every payer's rates would be dropped", path)
	}
	for _, p := range l.Payers {
		re, err := regexp.Compile("(?i)" + p)
		if err != nil {
			return nil, fmt.Errorf("%s: payer pattern %q: %w", path, p, err)
		}
		l.payers = append(l.payers, re)
	}
	seen := map[string]bool{}
	for i := range l.Hospitals {
		h := &l.Hospitals[i]
		h.CCN = strings.ToUpper(strings.TrimSpace(h.CCN))
		h.Name, h.Index, h.Location, h.URL = strings.TrimSpace(h.Name), strings.TrimSpace(h.Index),
			strings.TrimSpace(h.Location), strings.TrimSpace(h.URL)
		switch {
		case !validCCN(h.CCN):
			return nil, fmt.Errorf("%s: hospital %d has CCN %q; give its six-character CMS Certification Number, quoted", path, i+1, h.CCN)
		case seen[h.CCN]:
			return nil, fmt.Errorf("%s: CCN %s is listed twice", path, h.CCN)
		case h.Name == "":
			return nil, fmt.Errorf("%s: hospital %s has no name", path, h.CCN)
		case h.URL != "" && (h.Index != "" || h.Location != ""):
			return nil, fmt.Errorf("%s: hospital %s gives a url and an index; give one", path, h.CCN)
		case h.URL == "" && (h.Index == "" || h.Location == ""):
			return nil, fmt.Errorf("%s: hospital %s needs an index and its location there, or a url", path, h.CCN)
		}
		seen[h.CCN] = true
	}
	return &l, nil
}

// KeepsPayer reports whether a payer name matches one of the list's payers.
func (l *List) KeepsPayer(name string) bool {
	for _, re := range l.payers {
		if re.MatchString(name) {
			return true
		}
	}
	return false
}

// Find returns the listed hospital with this CCN.
func (l *List) Find(ccn string) (Hospital, bool) {
	for _, h := range l.Hospitals {
		if h.CCN == ccn {
			return h, true
		}
	}
	return Hospital{}, false
}
