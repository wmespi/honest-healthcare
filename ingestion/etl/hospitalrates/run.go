// Package hospitalrates reads hospitals' own price files, the machine-readable
// standard charges CMS's hospital price transparency rule has every hospital
// publish, and writes each hospital's rates for the payers hospitals.yaml keeps
// to data/ingestion/hospital_rates/<ccn>.parquet (ingestion/etl/hospital-rates.md).
package hospitalrates

import (
	"errors"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/wmespi/honest-healthcare/ingestion/etl/core"
)

// Options are `etl hospital-rates`'s flags.
type Options struct {
	// ListPath is the hospital list (DefaultListPath).
	ListPath string
	// Only limits the run to these CCNs from the list.
	Only []string
	// Missing skips a hospital whose output already exists.
	Missing bool
	// File is a local price file to extract for hospital CCN, with no download.
	File, CCN string
}

// OutputPath is where a hospital's rates are written.
func OutputPath(ccn string) string {
	return filepath.Join(core.HospitalRatesDir, ccn+".parquet")
}

// Run is `etl hospital-rates`. A hospital that fails keeps its previous output
// and is named in a closing warning; Run fails only when every hospital it
// tried failed, or the one -file did.
func Run(opts Options) error {
	list, err := LoadList(opts.ListPath)
	if err != nil {
		return err
	}
	if opts.File != "" {
		return runFile(list, opts)
	}
	if opts.CCN != "" {
		return errors.New("-ccn names the hospital a -file is from; use -only to pick hospitals from the list")
	}

	hospitals := list.Hospitals
	if len(opts.Only) > 0 {
		hospitals = nil
		for _, ccn := range opts.Only {
			h, ok := list.Find(strings.ToUpper(strings.TrimSpace(ccn)))
			if !ok {
				return fmt.Errorf("-only %s: no hospital with that CCN in %s", ccn, opts.ListPath)
			}
			hospitals = append(hospitals, h)
		}
	}
	if opts.Missing {
		var todo []Hospital
		for _, h := range hospitals {
			if _, err := os.Stat(OutputPath(h.CCN)); err != nil {
				todo = append(todo, h)
			}
		}
		if skipped := len(hospitals) - len(todo); skipped > 0 {
			log.Printf("⏭️  %d hospitals already have rates in %s - skipping them (-missing)", skipped, core.HospitalRatesDir)
		}
		hospitals = todo
	}
	if len(hospitals) == 0 {
		log.Printf("✅ No hospital to fetch")
		return nil
	}

	r := &runner{list: list, indexes: map[string]indexResult{}}
	var failed []string
	for _, h := range hospitals {
		if err := r.hospital(h); err != nil {
			log.Printf("❌ %s: %v", label(h), err)
			failed = append(failed, fmt.Sprintf("%s: %v", label(h), err))
		}
	}
	// Each download is deleted once read, so the folder is left empty; it
	// goes too, and stays when something else is in it.
	os.Remove(core.HospitalRatesRawDir)
	if len(failed) > 0 {
		log.Printf("╔══════════════════════════════════════════════════════════════")
		log.Printf("║ ⚠️  %d of %d hospitals failed and kept their previous rates:", len(failed), len(hospitals))
		for _, f := range failed {
			log.Printf("║   %s", f)
		}
		log.Printf("╚══════════════════════════════════════════════════════════════")
	}
	if len(failed) == len(hospitals) {
		return fmt.Errorf("every hospital tried failed (%d)", len(failed))
	}
	return nil
}

// runFile extracts one local price file for -ccn.
func runFile(list *List, opts Options) error {
	ccn := strings.ToUpper(strings.TrimSpace(opts.CCN))
	if !validCCN(ccn) {
		return fmt.Errorf("-file needs -ccn, the hospital's six-character CCN (got %q)", opts.CCN)
	}
	if len(opts.Only) > 0 || opts.Missing {
		return errors.New("-file reads one local file; -only and -missing pick hospitals to download")
	}
	h, ok := list.Find(ccn)
	if !ok {
		h = Hospital{CCN: ccn}
	}
	if err := extract(list, h, opts.File); err != nil {
		return fmt.Errorf("%s: %w", label(h), err)
	}
	return nil
}

type indexResult struct {
	entries []IndexEntry
	err     error
}

type runner struct {
	list *List
	// indexes keeps each cms-hpt.txt fetched this run, so a system's is
	// fetched once for all its hospitals.
	indexes map[string]indexResult
}

// hospital downloads a hospital's price file, extracts it and deletes it.
func (r *runner) hospital(h Hospital) error {
	fileURL := h.URL
	if fileURL == "" {
		res, ok := r.indexes[h.Index]
		if !ok {
			log.Printf("📇 Reading %s", h.Index)
			res.entries, res.err = fetchIndex(h.Index)
			r.indexes[h.Index] = res
		}
		if res.err != nil {
			return res.err
		}
		var warning string
		var err error
		if fileURL, warning, err = FindLocation(res.entries, h.Location, h.Index); err != nil {
			return err
		}
		if warning != "" {
			log.Printf("⚠️  %s: %s", label(h), warning)
		}
	}
	if err := os.MkdirAll(core.HospitalRatesRawDir, 0o755); err != nil {
		return err
	}
	download := filepath.Join(core.HospitalRatesRawDir, h.CCN+".download")
	defer os.Remove(download)
	log.Printf("⬇️  %s: %s", label(h), fileURL)
	start := time.Now()
	if err := core.DownloadToFile(fileURL, download); err != nil {
		return fmt.Errorf("download %s: %w", fileURL, err)
	}
	if st, err := os.Stat(download); err == nil {
		log.Printf("  downloaded %.1f MB in %s", float64(st.Size())/1e6, time.Since(start).Round(time.Second))
	}
	return extract(r.list, h, download)
}

// extract streams one price file to the hospital's output, which is replaced
// only when the file yields rows.
func extract(list *List, h Hospital, path string) error {
	pf, err := openPriceFile(path)
	if err != nil {
		return err
	}
	defer pf.Close()
	how := pf.format.String()
	if len(pf.how) > 0 {
		how += " (" + strings.Join(pf.how, ", ") + ")"
	}
	log.Printf("📂 Streaming %s as %s", label(h), how)

	out := OutputPath(h.CCN)
	w, err := core.NewRowWriter[core.HospitalRateRow](out)
	if err != nil {
		return err
	}
	e := newEmitter(h.CCN, w)
	if pf.format == formatJSON {
		err = extractJSON(pf, e, list.KeepsPayer)
	} else {
		err = extractCSV(pf, e, list.KeepsPayer)
	}
	if err == nil && e.rows == 0 {
		err = errors.New("no CPT, HCPCS or MS-DRG rows with a price")
	}
	if err := w.Finish(err); err != nil {
		return err
	}
	log.Printf("✅ %s: %d rows (%d repeats dropped), %d of %d payer/plan names kept → %s",
		label(h), e.rows, e.repeats, len(e.kept), len(e.seen), out)
	return nil
}

// label names a hospital in the log.
func label(h Hospital) string {
	if h.Name == "" {
		return h.CCN
	}
	return h.CCN + " " + h.Name
}
