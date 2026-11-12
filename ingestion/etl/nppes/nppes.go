package nppes

import (
	"archive/zip"
	"encoding/csv"
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	parquet "github.com/parquet-go/parquet-go"

	"github.com/wmespi/honest-healthcare/ingestion/etl/core"
)

// defaultNPPESURLs returns this month's dissemination file, then last month's,
// which is the newest one CMS serves until a month's file is out. CMS names a
// re-cut with a _V<n> suffix, so -url overrides both (the list is on
// download.cms.gov/nppes/NPI_Files.html).
func defaultNPPESURLs(now time.Time) []string {
	month := time.Date(now.Year(), now.Month(), 1, 0, 0, 0, 0, time.UTC)
	var urls []string
	for _, m := range []time.Time{month, month.AddDate(0, -1, 0)} {
		urls = append(urls, "https://download.cms.gov/nppes/NPPES_Data_Dissemination_"+m.Format("January_2006")+"_V2.zip")
	}
	return urls
}

var (
	npiDataFileRe   = regexp.MustCompile(`(?i)^npidata_pfile_\d+-\d+\.csv$`)
	locationsFileRe = regexp.MustCompile(`(?i)^pl_pfile_\d+-\d+\.csv$`)
)

// Run downloads (or opens) the NPPES national zip and streams two of its CSVs
// without extracting them, keeping only Georgia rows: the big npidata file, to
// ga_providers.parquet, and the secondary practice locations, to
// ga_locations.parquet. Plain .csv inputs (the test fixtures) skip the zip.
func Run(nppesURL, nppesFile, locationsFile string, limit int) {
	if strings.HasSuffix(strings.ToLower(nppesFile), ".csv") {
		extract("providers", nppesFile, func(r io.Reader) (int, error) {
			return extractNPPESGeorgia(r, core.NPPESOutputPath, limit)
		}, core.NPPESOutputPath)
		if locationsFile != "" {
			extract("secondary locations", locationsFile, func(r io.Reader) (int, error) {
				return extractLocationsGeorgia(r, core.NPPESLocationsPath)
			}, core.NPPESLocationsPath)
		}
		return
	}

	zipPath := nppesFile
	if zipPath == "" {
		tmp, err := os.CreateTemp("", "nppes-*.zip")
		if err != nil {
			log.Fatalf("❌ temp file: %v", err)
		}
		tmp.Close()
		zipPath = tmp.Name()
		defer os.Remove(zipPath)
		urls := defaultNPPESURLs(time.Now())
		if nppesURL != "" {
			urls = []string{nppesURL}
		}
		if err := download(urls, zipPath); err != nil {
			os.Remove(zipPath)
			log.Fatalf("❌ NPPES download failed: %v (try -url with the current _V<n> suffix)", err)
		}
	}

	zr, err := zip.OpenReader(zipPath)
	if err != nil {
		log.Fatalf("❌ open zip: %v", err)
	}
	defer zr.Close()

	streamEntry(zr, npiDataFileRe, "npidata_pfile_*.csv", "providers", core.NPPESOutputPath,
		func(r io.Reader) (int, error) { return extractNPPESGeorgia(r, core.NPPESOutputPath, limit) })
	streamEntry(zr, locationsFileRe, "pl_pfile_*.csv", "secondary locations", core.NPPESLocationsPath,
		func(r io.Reader) (int, error) { return extractLocationsGeorgia(r, core.NPPESLocationsPath) })
}

// download tries each URL in turn and keeps the first that answers.
func download(urls []string, dest string) error {
	var err error
	for _, u := range urls {
		log.Printf("⬇️  Downloading NPPES: %s", u)
		if err = core.DownloadToFile(u, dest); err == nil {
			return nil
		}
		log.Printf("  %s: %v", u, err)
	}
	return err
}

// extract runs one extractor over a plain CSV file.
func extract(what, path string, run func(io.Reader) (int, error), out string) {
	f, err := os.Open(path)
	if err != nil {
		log.Fatalf("❌ open csv: %v", err)
	}
	defer f.Close()
	kept, err := run(f)
	if err != nil {
		log.Fatalf("❌ %v", err)
	}
	log.Printf("✅ Wrote %s — %d Georgia %s", out, kept, what)
}

// streamEntry runs one extractor over the zip entry whose name matches `name`.
func streamEntry(zr *zip.ReadCloser, name *regexp.Regexp, pattern, what, out string, run func(io.Reader) (int, error)) {
	var entry *zip.File
	for _, f := range zr.File {
		if name.MatchString(filepath.Base(f.Name)) {
			entry = f
			break
		}
	}
	if entry == nil {
		log.Fatalf("❌ no %s inside the zip (entries: %d)", pattern, len(zr.File))
	}
	log.Printf("📂 Streaming %s (%.1f MB compressed)", entry.Name, float64(entry.CompressedSize64)/1e6)
	rc, err := entry.Open()
	if err != nil {
		log.Fatalf("❌ open csv entry: %v", err)
	}
	defer rc.Close()
	kept, err := run(rc)
	if err != nil {
		log.Fatalf("❌ %v", err)
	}
	log.Printf("✅ Wrote %s — %d Georgia %s", out, kept, what)
}

// rowWriter writes rows to a Parquet file beside its path and moves it into
// place once complete, so a reader never sees a half-written file.
type rowWriter[T any] struct {
	path string
	f    *os.File
	pw   *parquet.GenericWriter[T]
	buf  []T
}

func newRowWriter[T any](path string) (*rowWriter[T], error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, err
	}
	f, err := os.Create(path + ".part")
	if err != nil {
		return nil, err
	}
	return &rowWriter[T]{path: path, f: f, buf: make([]T, 0, 4096),
		pw: parquet.NewGenericWriter[T](f, parquet.Compression(&parquet.Zstd))}, nil
}

func (w *rowWriter[T]) add(row T) error {
	w.buf = append(w.buf, row)
	if len(w.buf) < cap(w.buf) {
		return nil
	}
	return w.flush()
}

func (w *rowWriter[T]) flush() error {
	_, err := w.pw.Write(w.buf)
	w.buf = w.buf[:0]
	return err
}

// finish writes what's buffered and moves the file into place, or removes it
// when the extraction (`err`) or the write failed.
func (w *rowWriter[T]) finish(err error) error {
	if err == nil {
		err = w.flush()
	}
	if cerr := w.pw.Close(); err == nil {
		err = cerr
	}
	if cerr := w.f.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		os.Remove(w.path + ".part")
		return err
	}
	return os.Rename(w.path+".part", w.path)
}

// nppesColumns resolves the NPPES CSV header (names are stable across releases)
// to the column indices we need.
type nppesColumns struct {
	npi, entityType, orgName, lastName, firstName int
	addr1, addr2, state, city, postal, phone      int
	taxonomy, taxonomySwitch                      []int // _1.._15 pairs
}

func resolveNPPESColumns(header []string) (*nppesColumns, error) {
	idx := map[string]int{}
	for i, h := range header {
		idx[strings.TrimSpace(h)] = i
	}
	get := func(name string) (int, error) {
		if i, ok := idx[name]; ok {
			return i, nil
		}
		return -1, fmt.Errorf("NPPES header missing %q", name)
	}
	c := &nppesColumns{}
	var err error
	if c.npi, err = get("NPI"); err != nil {
		return nil, err
	}
	if c.entityType, err = get("Entity Type Code"); err != nil {
		return nil, err
	}
	if c.orgName, err = get("Provider Organization Name (Legal Business Name)"); err != nil {
		return nil, err
	}
	if c.lastName, err = get("Provider Last Name (Legal Name)"); err != nil {
		return nil, err
	}
	if c.firstName, err = get("Provider First Name"); err != nil {
		return nil, err
	}
	if c.state, err = get("Provider Business Practice Location Address State Name"); err != nil {
		return nil, err
	}
	if c.city, err = get("Provider Business Practice Location Address City Name"); err != nil {
		return nil, err
	}
	// Street address: line 1 required, line 2 optional (absent in some cuts).
	if c.addr1, err = get("Provider First Line Business Practice Location Address"); err != nil {
		return nil, err
	}
	c.addr2 = -1
	if i, ok := idx["Provider Second Line Business Practice Location Address"]; ok {
		c.addr2 = i
	}
	if c.postal, err = get("Provider Business Practice Location Address Postal Code"); err != nil {
		return nil, err
	}
	if c.phone, err = get("Provider Business Practice Location Address Telephone Number"); err != nil {
		return nil, err
	}
	for i := 1; i <= 15; i++ {
		t, e1 := get(fmt.Sprintf("Healthcare Provider Taxonomy Code_%d", i))
		s, e2 := get(fmt.Sprintf("Healthcare Provider Primary Taxonomy Switch_%d", i))
		if e1 != nil || e2 != nil {
			break
		}
		c.taxonomy = append(c.taxonomy, t)
		c.taxonomySwitch = append(c.taxonomySwitch, s)
	}
	if len(c.taxonomy) == 0 {
		return nil, fmt.Errorf("NPPES header has no taxonomy columns")
	}
	return c, nil
}

// primaryTaxonomy returns the taxonomy code flagged as primary (switch == "Y"),
// falling back to the first non-empty code.
func primaryTaxonomy(rec []string, c *nppesColumns) string {
	first := ""
	for i := range c.taxonomy {
		code := strings.TrimSpace(rec[c.taxonomy[i]])
		if code == "" {
			continue
		}
		if first == "" {
			first = code
		}
		if strings.EqualFold(strings.TrimSpace(rec[c.taxonomySwitch[i]]), "Y") {
			return code
		}
	}
	return first
}

// classifyTaxonomy maps an NUCC taxonomy code to (group label, is_hospital,
// is_clinic) using the code set's top-level groupings. is_hospital (28x) and
// is_clinic (261Q) are the load-bearing flags; the rest is a coarse label.
func classifyTaxonomy(code string) (group string, isHospital, isClinic bool) {
	switch {
	case code == "":
		return "Unknown", false, false
	case strings.HasPrefix(code, "28"):
		return "Hospital", true, false
	case strings.HasPrefix(code, "261Q"):
		return "Clinic/Center", false, true
	case strings.HasPrefix(code, "261"), strings.HasPrefix(code, "273"), strings.HasPrefix(code, "275"):
		return "Ambulatory / Hospital Unit", false, false
	case strings.HasPrefix(code, "31"), strings.HasPrefix(code, "32"):
		return "Nursing / Residential Facility", false, false
	case strings.HasPrefix(code, "29"):
		return "Laboratory", false, false
	case strings.HasPrefix(code, "20"), strings.HasPrefix(code, "21"):
		return "Physician (individual)", false, false
	case strings.HasPrefix(code, "1"):
		return "Group Practice", false, false
	case strings.HasPrefix(code, "25"), strings.HasPrefix(code, "34"):
		return "Agency / Transportation", false, false
	default:
		return "Other", false, false
	}
}

// extractNPPESGeorgia is the pure core: read the NPPES CSV from r, keep GA
// practice-location rows, write ga_providers.parquet. Returns the kept count.
func extractNPPESGeorgia(r io.Reader, outPath string, limit int) (int, error) {
	cr := csv.NewReader(r)
	cr.ReuseRecord = true
	cr.FieldsPerRecord = -1 // NPPES rows have a trailing empty field in some cuts

	header, err := cr.Read()
	if err != nil {
		return 0, fmt.Errorf("read header: %w", err)
	}
	cols, err := resolveNPPESColumns(header)
	if err != nil {
		return 0, err
	}
	w, err := newRowWriter[core.NPPESRow](outPath)
	if err != nil {
		return 0, err
	}

	kept, scanned := 0, 0
	for err == nil && (limit <= 0 || kept < limit) {
		rec, rerr := cr.Read()
		if rerr == io.EOF {
			break
		}
		if rerr != nil {
			// tolerate a bad line rather than abort a 10 GB stream
			continue
		}
		scanned++
		if scanned%1_000_000 == 0 {
			log.Printf("  … scanned %d rows, kept %d GA", scanned, kept)
		}
		if strings.ToUpper(strings.TrimSpace(rec[cols.state])) != "GA" {
			continue
		}

		code := primaryTaxonomy(rec, cols)
		group, isHosp, isClinic := classifyTaxonomy(code)
		entity := "organization"
		if strings.TrimSpace(rec[cols.entityType]) == "1" {
			entity = "individual"
		}
		var npi int64
		fmt.Sscan(strings.TrimSpace(rec[cols.npi]), &npi)

		addr2 := ""
		if cols.addr2 >= 0 && cols.addr2 < len(rec) {
			addr2 = strings.TrimSpace(rec[cols.addr2])
		}
		err = w.add(core.NPPESRow{
			NPI:           npi,
			EntityType:    entity,
			OrgName:       strings.TrimSpace(rec[cols.orgName]),
			LastName:      strings.TrimSpace(rec[cols.lastName]),
			FirstName:     strings.TrimSpace(rec[cols.firstName]),
			TaxonomyCode:  code,
			TaxonomyGroup: group,
			IsHospital:    isHosp,
			IsClinic:      isClinic,
			AddressLine1:  strings.TrimSpace(rec[cols.addr1]),
			AddressLine2:  addr2,
			City:          strings.TrimSpace(rec[cols.city]),
			State:         "GA",
			PostalCode:    strings.TrimSpace(rec[cols.postal]),
			Phone:         strings.TrimSpace(rec[cols.phone]),
		})
		kept++
	}
	log.Printf("  scanned %d NPPES rows, kept %d Georgia providers", scanned, kept)
	return kept, w.finish(err)
}

// locationColumns are the secondary-locations file's columns, by the words
// each header ends in once its spacing and punctuation are dropped: the file
// spaces the same prefix three different ways.
var locationColumns = []string{"npi", "addressline1", "addressline2", "cityname", "statename", "postalcode", "telephonenumber"}

var notAlphanumeric = regexp.MustCompile(`[^a-z0-9]+`)

func resolveLocationColumns(header []string) ([]int, error) {
	cols := make([]int, len(locationColumns))
	for c, want := range locationColumns {
		cols[c] = -1
		for i, h := range header {
			if strings.HasSuffix(notAlphanumeric.ReplaceAllString(strings.ToLower(h), ""), want) {
				cols[c] = i
				break
			}
		}
		if cols[c] < 0 {
			return nil, fmt.Errorf("NPPES secondary-locations header has no column ending in %q", want)
		}
	}
	return cols, nil
}

// extractLocationsGeorgia reads the NPPES secondary practice locations from r
// and writes the ones in Georgia to ga_locations.parquet: the other addresses a
// provider practices at, beside the one on its own record. Returns the kept count.
func extractLocationsGeorgia(r io.Reader, outPath string) (int, error) {
	cr := csv.NewReader(r)
	cr.ReuseRecord = true
	cr.FieldsPerRecord = -1

	header, err := cr.Read()
	if err != nil {
		return 0, fmt.Errorf("read header: %w", err)
	}
	cols, err := resolveLocationColumns(header)
	if err != nil {
		return 0, err
	}
	w, err := newRowWriter[core.NPPESLocationRow](outPath)
	if err != nil {
		return 0, err
	}

	kept, scanned := 0, 0
	for err == nil {
		rec, rerr := cr.Read()
		if rerr == io.EOF {
			break
		}
		if rerr != nil || len(rec) <= cols[len(cols)-1] {
			continue
		}
		scanned++
		field := func(c int) string { return strings.TrimSpace(rec[cols[c]]) }
		if strings.ToUpper(field(4)) != "GA" {
			continue
		}
		var npi int64
		fmt.Sscan(field(0), &npi)
		err = w.add(core.NPPESLocationRow{
			NPI: npi, AddressLine1: field(1), AddressLine2: field(2), City: field(3),
			State: "GA", PostalCode: field(5), Phone: field(6),
		})
		kept++
	}
	log.Printf("  scanned %d secondary locations, kept %d in Georgia", scanned, kept)
	return kept, w.finish(err)
}
