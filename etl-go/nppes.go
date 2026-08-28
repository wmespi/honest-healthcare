package main

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

	parquet "github.com/parquet-go/parquet-go"
)

// NPPESOutputPath is where the GA subset lands (prod). Test mode redirects it.
var NPPESOutputPath = "../data/nppes/ga_providers.parquet"

// getDefaultNPPESURL returns the current monthly NPPES dissemination URL. CMS
// suffixes these with _V<n> when they re-cut mid-month, so this is best-effort —
// pass -nppes-url to override (find it on download.cms.gov/nppes/NPI_Files.html).
func getDefaultNPPESURL() string {
	return "https://download.cms.gov/nppes/NPPES_Data_Dissemination_" +
		nppesMonthYear() + "_V2.zip"
}

func nppesMonthYear() string {
	// Data for a month is published early in that month; use the current month.
	// Kept trivial on purpose — override with -nppes-url when in doubt.
	return "August_2026"
}

var npiDataFileRe = regexp.MustCompile(`(?i)^npidata_pfile_\d+-\d+\.csv$`)

// runNPPES downloads (or opens) the NPPES national zip, streams the big
// npidata CSV without extracting it, keeps only GA practice-location rows,
// classifies hospitals/clinics by taxonomy, and writes ga_providers.parquet.
func runNPPES(nppesURL, nppesFile string, limit int) {
	// A plain .csv input (the test fixture) skips the zip machinery entirely.
	if strings.HasSuffix(strings.ToLower(nppesFile), ".csv") {
		f, err := os.Open(nppesFile)
		if err != nil {
			log.Fatalf("❌ open csv: %v", err)
		}
		defer f.Close()
		kept, err := extractNPPESGeorgia(f, NPPESOutputPath, limit)
		if err != nil {
			log.Fatalf("❌ %v", err)
		}
		log.Printf("✅ Wrote %s — %d Georgia providers", NPPESOutputPath, kept)
		return
	}

	zipPath := nppesFile
	cleanup := func() {}
	if zipPath == "" {
		if nppesURL == "" {
			nppesURL = getDefaultNPPESURL()
		}
		tmp, err := os.CreateTemp("", "nppes-*.zip")
		if err != nil {
			log.Fatalf("❌ temp file: %v", err)
		}
		tmp.Close()
		zipPath = tmp.Name()
		cleanup = func() { os.Remove(zipPath) }
		log.Printf("⬇️  Downloading NPPES: %s", nppesURL)
		if err := downloadSingle(nppesURL, zipPath); err != nil {
			cleanup()
			log.Fatalf("❌ NPPES download failed: %v (try -nppes-url with the current _V<n> suffix)", err)
		}
	}
	defer cleanup()

	zr, err := zip.OpenReader(zipPath)
	if err != nil {
		log.Fatalf("❌ open zip: %v", err)
	}
	defer zr.Close()

	var entry *zip.File
	for _, f := range zr.File {
		if npiDataFileRe.MatchString(filepath.Base(f.Name)) {
			entry = f
			break
		}
	}
	if entry == nil {
		log.Fatalf("❌ no npidata_pfile_*.csv inside the zip (entries: %d)", len(zr.File))
	}
	log.Printf("📂 Streaming %s (%.1f MB compressed)", entry.Name, float64(entry.CompressedSize64)/1e6)

	rc, err := entry.Open()
	if err != nil {
		log.Fatalf("❌ open csv entry: %v", err)
	}
	defer rc.Close()

	kept, err := extractNPPESGeorgia(rc, NPPESOutputPath, limit)
	if err != nil {
		log.Fatalf("❌ %v", err)
	}
	log.Printf("✅ Wrote %s — %d Georgia providers", NPPESOutputPath, kept)
}

// nppesColumns resolves the NPPES CSV header (names are stable across releases)
// to the column indices we need.
type nppesColumns struct {
	npi, entityType, orgName, lastName, firstName int
	state, city, postal                           int
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
	if c.postal, err = get("Provider Business Practice Location Address Postal Code"); err != nil {
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

	if err := os.MkdirAll(filepath.Dir(outPath), 0o755); err != nil {
		return 0, err
	}
	f, err := os.Create(outPath)
	if err != nil {
		return 0, err
	}
	defer f.Close()
	pw := parquet.NewGenericWriter[NPPESRow](f, parquet.Compression(&parquet.Zstd))
	defer pw.Close()

	buf := make([]NPPESRow, 0, 4096)
	kept, scanned := 0, 0
	for {
		rec, err := cr.Read()
		if err == io.EOF {
			break
		}
		if err != nil {
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

		buf = append(buf, NPPESRow{
			NPI:           npi,
			EntityType:    entity,
			OrgName:       strings.TrimSpace(rec[cols.orgName]),
			LastName:      strings.TrimSpace(rec[cols.lastName]),
			FirstName:     strings.TrimSpace(rec[cols.firstName]),
			TaxonomyCode:  code,
			TaxonomyGroup: group,
			IsHospital:    isHosp,
			IsClinic:      isClinic,
			City:          strings.TrimSpace(rec[cols.city]),
			State:         "GA",
			PostalCode:    strings.TrimSpace(rec[cols.postal]),
		})
		kept++
		if len(buf) >= 4096 {
			if _, err := pw.Write(buf); err != nil {
				return kept, err
			}
			buf = buf[:0]
		}
		if limit > 0 && kept >= limit {
			break
		}
	}
	if len(buf) > 0 {
		if _, err := pw.Write(buf); err != nil {
			return kept, err
		}
	}
	log.Printf("  scanned %d NPPES rows, kept %d Georgia providers", scanned, kept)
	return kept, nil
}
