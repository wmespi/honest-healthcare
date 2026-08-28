package nppes

import (
	"os"
	"path/filepath"
	"testing"

	parquet "github.com/parquet-go/parquet-go"

	"github.com/wmespi/honest-healthcare/etl/core"
)

func TestExtractNPPESGeorgia(t *testing.T) {
	out := filepath.Join(t.TempDir(), "ga_providers.parquet")
	kept, err := extractNPPESGeorgia(mustOpen(t, "testdata/nppes_sample.csv"), out, 0)
	if err != nil {
		t.Fatalf("extractNPPESGeorgia: %v", err)
	}
	if kept != 12 {
		t.Fatalf("kept = %d GA providers, want 12 (14 rows − 1 TX − 1 FL)", kept)
	}

	rows, err := parquet.ReadFile[core.NPPESRow](out)
	if err != nil {
		t.Fatalf("read parquet: %v", err)
	}
	if len(rows) != 12 {
		t.Fatalf("parquet rows = %d, want 12", len(rows))
	}

	var hospitals, clinics int
	byNPI := map[int64]core.NPPESRow{}
	for _, r := range rows {
		byNPI[r.NPI] = r
		if r.State != "GA" {
			t.Errorf("npi %d state = %q, want GA", r.NPI, r.State)
		}
		if r.IsHospital {
			hospitals++
		}
		if r.IsClinic {
			clinics++
		}
	}
	if hospitals != 4 {
		t.Errorf("hospitals = %d, want 4 (282N x3 + primary-switch row)", hospitals)
	}
	if clinics != 2 {
		t.Errorf("clinics = %d, want 2 (261QP + 261QA)", clinics)
	}

	// row 6: primary taxonomy switch points at taxonomy_2 (the hospital code)
	if r := byNPI[1000000006]; !r.IsHospital || r.TaxonomyCode != "282N00000X" {
		t.Errorf("primary-switch row = %+v, want hospital/282N00000X", r)
	}
	// practice street address is captured; line 2 only when present
	if r := byNPI[1000000001]; r.AddressLine1 != "1968 PEACHTREE RD NW" || r.AddressLine2 != "" {
		t.Errorf("addr row 1 = (%q,%q), want (\"1968 PEACHTREE RD NW\",\"\")", r.AddressLine1, r.AddressLine2)
	}
	if r := byNPI[1000000003]; r.AddressLine1 != "310 EISENHOWER DR" || r.AddressLine2 != "STE 12" {
		t.Errorf("addr row 3 = (%q,%q), want (\"310 EISENHOWER DR\",\"STE 12\")", r.AddressLine1, r.AddressLine2)
	}
	// row 11: lowercase "ga" state still matches
	if _, ok := byNPI[1000000011]; !ok {
		t.Errorf("lowercase 'ga' row was dropped")
	}
	// row 7: no taxonomy → Unknown, not hospital/clinic
	if r := byNPI[1000000007]; r.IsHospital || r.IsClinic || r.TaxonomyGroup != "Unknown" {
		t.Errorf("no-taxonomy row = %+v, want Unknown/false/false", r)
	}
	// TX + FL rows excluded
	for _, npi := range []int64{1000000008, 1000000009} {
		if _, ok := byNPI[npi]; ok {
			t.Errorf("non-GA npi %d was kept", npi)
		}
	}
}

func TestClassifyTaxonomy(t *testing.T) {
	cases := []struct {
		code       string
		group      string
		isHospital bool
		isClinic   bool
	}{
		{"282N00000X", "Hospital", true, false},
		{"282NC2000X", "Hospital", true, false},
		{"261QP2300X", "Clinic/Center", false, true},
		{"261QA1903X", "Clinic/Center", false, true},
		{"207Q00000X", "Physician (individual)", false, false},
		{"314000000X", "Nursing / Residential Facility", false, false},
		{"291U00000X", "Laboratory", false, false},
		{"", "Unknown", false, false},
	}
	for _, c := range cases {
		g, h, cl := classifyTaxonomy(c.code)
		if g != c.group || h != c.isHospital || cl != c.isClinic {
			t.Errorf("classifyTaxonomy(%q) = (%q,%v,%v), want (%q,%v,%v)",
				c.code, g, h, cl, c.group, c.isHospital, c.isClinic)
		}
	}
}

func mustOpen(t *testing.T, path string) *os.File {
	t.Helper()
	f, err := os.Open(path)
	if err != nil {
		t.Fatalf("open %s: %v", path, err)
	}
	t.Cleanup(func() { f.Close() })
	return f
}
