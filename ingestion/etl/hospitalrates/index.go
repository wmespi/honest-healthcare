package hospitalrates

import (
	"bufio"
	"fmt"
	"io"
	"net/url"
	"strings"

	"github.com/wmespi/honest-healthcare/ingestion/etl/core"
)

// IndexEntry is one block of a cms-hpt.txt: the locations one price file
// covers. A block usually names one location; consecutive location-name lines
// share the block's file.
type IndexEntry struct {
	Locations     []string
	SourcePageURL string
	MRFURL        string
	ContactName   string
	ContactEmail  string
}

// maxIndexBytes caps how much of a cms-hpt.txt is read: the file is a few
// lines per location, so anything larger is not one.
const maxIndexBytes = 10 << 20

// ParseIndex reads a cms-hpt.txt: blocks of "key: value" lines separated by
// blank lines, keys case-insensitive, with or without a BOM and CRLF line ends.
// A location-name after a named block's other keys starts a new block, so a
// file missing its blank lines still parses. Blocks with no location-name are
// dropped.
func ParseIndex(r io.Reader) ([]IndexEntry, error) {
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 64<<10), 1<<20)
	var (
		out      []IndexEntry
		cur      IndexEntry
		hasOther bool
	)
	flush := func() {
		if len(cur.Locations) > 0 {
			out = append(out, cur)
		}
		cur, hasOther = IndexEntry{}, false
	}
	first := true
	for sc.Scan() {
		line := sc.Text()
		if first {
			line = strings.TrimPrefix(line, "\ufeff")
			first = false
		}
		line = strings.TrimSpace(line)
		if line == "" {
			flush()
			continue
		}
		key, value, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		value = strings.TrimSpace(value)
		switch strings.ToLower(strings.TrimSpace(key)) {
		case "location-name":
			if hasOther && len(cur.Locations) > 0 {
				flush()
			}
			if value != "" {
				cur.Locations = append(cur.Locations, value)
			}
			continue
		case "source-page-url":
			cur.SourcePageURL = value
		case "mrf-url":
			if cur.MRFURL == "" {
				cur.MRFURL = value
			}
		case "contact-name":
			cur.ContactName = value
		case "contact-email":
			cur.ContactEmail = value
		default:
			continue
		}
		hasOther = true
	}
	if err := sc.Err(); err != nil {
		return nil, err
	}
	flush()
	if len(out) == 0 {
		return nil, fmt.Errorf("no location-name lines")
	}
	return out, nil
}

// FindLocation returns the address of the price file for location (matched
// case-insensitively once trimmed), resolved against the index's own address.
// A warning comes back with it when the index lists that location against more
// than one file, and the first is used.
func FindLocation(entries []IndexEntry, location, indexURL string) (file, warning string, err error) {
	want := strings.TrimSpace(location)
	var found []string
	for _, e := range entries {
		for _, l := range e.Locations {
			if strings.EqualFold(strings.TrimSpace(l), want) && e.MRFURL != "" {
				found = append(found, e.MRFURL)
			}
		}
	}
	if len(found) == 0 {
		var names []string
		for _, e := range entries {
			names = append(names, e.Locations...)
		}
		if len(names) > 12 {
			names = append(names[:12], "...")
		}
		return "", "", fmt.Errorf("no location-name %q with an mrf-url in %s (it lists %s)", want, indexURL, strings.Join(names, "; "))
	}
	for _, f := range found[1:] {
		if f != found[0] {
			warning = fmt.Sprintf("%s lists %q against %d files; using the first", indexURL, want, len(found))
			break
		}
	}
	base, err := url.Parse(indexURL)
	if err != nil {
		return "", "", fmt.Errorf("index address %q: %w", indexURL, err)
	}
	ref, err := url.Parse(found[0])
	if err != nil {
		return "", "", fmt.Errorf("mrf-url %q in %s: %w", found[0], indexURL, err)
	}
	return base.ResolveReference(ref).String(), warning, nil
}

// fetchIndex downloads and parses one cms-hpt.txt.
func fetchIndex(indexURL string) ([]IndexEntry, error) {
	resp, err := core.Get(indexURL)
	if err != nil {
		return nil, fmt.Errorf("fetch %s: %w", indexURL, err)
	}
	defer resp.Body.Close()
	entries, err := ParseIndex(io.LimitReader(resp.Body, maxIndexBytes))
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", indexURL, err)
	}
	return entries, nil
}
