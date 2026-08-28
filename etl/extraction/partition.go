package extraction

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	parquet "github.com/parquet-go/parquet-go"
	"github.com/wmespi/honest-healthcare/etl/core"
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

// priceFanout writes price rows into one Parquet file per network_name partition
// (prices/net=<slug>/<id>.parquet), created lazily. All files land under a
// scratch dir first and are promoted together on a clean stream.
type priceFanout struct {
	scratchPricesDir string // …/.inflight/<id>/prices
	fileName         string // "<id>.parquet"
	parts            map[string]*pricePart
}

type pricePart struct {
	writer *parquet.GenericWriter[core.PriceRow]
	file   io.Closer
	path   string // scratch path
}

func newPriceFanout(scratchPricesDir, fileName string) *priceFanout {
	return &priceFanout{
		scratchPricesDir: scratchPricesDir,
		fileName:         fileName,
		parts:            map[string]*pricePart{},
	}
}

// write routes a batch of price rows into per-network Parquet writers.
func (f *priceFanout) write(rows []core.PriceRow) error {
	bySlug := map[string][]core.PriceRow{}
	for _, r := range rows {
		s := slugifyNetwork(r.NetworkName)
		bySlug[s] = append(bySlug[s], r)
	}
	for slug, batch := range bySlug {
		part := f.parts[slug]
		if part == nil {
			dir := filepath.Join(f.scratchPricesDir, "net="+slug)
			if err := os.MkdirAll(dir, os.ModePerm); err != nil {
				return err
			}
			path := filepath.Join(dir, f.fileName)
			w, c, err := newParquetWriter[core.PriceRow](path)
			if err != nil {
				return err
			}
			part = &pricePart{writer: w, file: c, path: path}
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
func (f *priceFanout) close() {
	for _, p := range f.parts {
		p.writer.Close()
		p.file.Close()
	}
}

// fanoutCloser adapts *priceFanout to io.Closer so it can sit in the same close
// list as the provider/code writers.
type fanoutCloser struct{ f *priceFanout }

func (c fanoutCloser) Close() error {
	if c.f != nil {
		c.f.close()
	}
	return nil
}

// promote moves each scratch partition file to
// finalPricesDir/net=<slug>/<fileName>, first deleting any stale copy of this
// file id in a partition it no longer writes to.
func (f *priceFanout) promote(finalPricesDir string) error {
	// Drop stale partitions for this file id from a previous parse.
	if existing, _ := filepath.Glob(filepath.Join(finalPricesDir, "net=*", f.fileName)); existing != nil {
		want := map[string]bool{}
		for slug := range f.parts {
			want[filepath.Join(finalPricesDir, "net="+slug, f.fileName)] = true
		}
		for _, p := range existing {
			if !want[p] {
				os.Remove(p)
			}
		}
	}
	for slug, part := range f.parts {
		dstDir := filepath.Join(finalPricesDir, "net="+slug)
		if err := os.MkdirAll(dstDir, os.ModePerm); err != nil {
			return err
		}
		if err := os.Rename(part.path, filepath.Join(dstDir, f.fileName)); err != nil {
			return fmt.Errorf("promote %s: %w", slug, err)
		}
	}
	return nil
}
