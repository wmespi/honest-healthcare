package main

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	parquet "github.com/parquet-go/parquet-go"
)

// networkSlugUnattributed is the partition for rate rows with no network_name.
const networkSlugUnattributed = "_unattributed"

// slugifyNetwork turns a network_name into a filesystem- and Hive-safe partition
// key: lowercase, every run of non-alphanumerics collapsed to '-', trimmed,
// capped at 100 chars. MUST stay identical to backend/main.py:network_slug so the
// API can prune partitions by name.
func slugifyNetwork(name string) string {
	var b strings.Builder
	prevDash := false
	for _, r := range strings.ToLower(strings.TrimSpace(name)) {
		if (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') {
			b.WriteRune(r)
			prevDash = false
		} else if !prevDash {
			b.WriteByte('-')
			prevDash = true
		}
	}
	s := strings.Trim(b.String(), "-")
	if len(s) > 100 {
		s = strings.Trim(s[:100], "-")
	}
	if s == "" {
		return networkSlugUnattributed
	}
	return s
}

// rateFanout writes rate rows into one Parquet file per network_name partition
// (rates/net=<slug>/<id>.parquet), created lazily. All files land under a
// scratch dir first and are promoted together on a clean stream.
type rateFanout struct {
	scratchRatesDir string // …/.inflight/<id>/rates
	fileName        string // "<id>.parquet"
	parts           map[string]*ratePart
}

type ratePart struct {
	writer *parquet.GenericWriter[RateRow]
	file   io.Closer
	path   string // scratch path
}

func newRateFanout(scratchRatesDir, fileName string) *rateFanout {
	return &rateFanout{
		scratchRatesDir: scratchRatesDir,
		fileName:        fileName,
		parts:           map[string]*ratePart{},
	}
}

// write routes a batch into per-network Parquet writers.
func (f *rateFanout) write(rows []RateRow) error {
	bySlug := map[string][]RateRow{}
	for _, r := range rows {
		s := slugifyNetwork(r.NetworkName)
		bySlug[s] = append(bySlug[s], r)
	}
	for slug, batch := range bySlug {
		part := f.parts[slug]
		if part == nil {
			dir := filepath.Join(f.scratchRatesDir, "net="+slug)
			if err := os.MkdirAll(dir, os.ModePerm); err != nil {
				return err
			}
			path := filepath.Join(dir, f.fileName)
			w, c, err := newParquetWriter[RateRow](path)
			if err != nil {
				return err
			}
			part = &ratePart{writer: w, file: c, path: path}
			f.parts[slug] = part
		}
		if _, err := part.writer.Write(batch); err != nil {
			return err
		}
		part.writer.Flush()
	}
	return nil
}

// close flushes and closes every partition writer (writer before its file).
func (f *rateFanout) close() {
	for _, p := range f.parts {
		p.writer.Close()
		p.file.Close()
	}
}

// fanoutCloser adapts *rateFanout to io.Closer so it can sit in the same close
// list as the provider/code writers.
type fanoutCloser struct{ f *rateFanout }

func (c fanoutCloser) Close() error {
	if c.f != nil {
		c.f.close()
	}
	return nil
}

// promote moves each scratch partition file to
// finalRatesDir/net=<slug>/<fileName>, first deleting any stale copy of this
// file id in a partition it no longer writes to.
func (f *rateFanout) promote(finalRatesDir string) error {
	// Drop stale partitions for this file id from a previous parse.
	if existing, _ := filepath.Glob(filepath.Join(finalRatesDir, "net=*", f.fileName)); existing != nil {
		want := map[string]bool{}
		for slug := range f.parts {
			want[filepath.Join(finalRatesDir, "net="+slug, f.fileName)] = true
		}
		for _, p := range existing {
			if !want[p] {
				os.Remove(p)
			}
		}
	}
	for slug, part := range f.parts {
		dstDir := filepath.Join(finalRatesDir, "net="+slug)
		if err := os.MkdirAll(dstDir, os.ModePerm); err != nil {
			return err
		}
		if err := os.Rename(part.path, filepath.Join(dstDir, f.fileName)); err != nil {
			return fmt.Errorf("promote %s: %w", slug, err)
		}
	}
	return nil
}
