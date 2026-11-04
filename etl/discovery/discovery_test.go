package discovery

import (
	"testing"

	"github.com/wmespi/honest-healthcare/etl/core"
)

func TestHiosStateCode(t *testing.T) {
	cases := []struct {
		id, idType, want string
	}{
		{"45334GA0770001", "HIOS", "GA"},
		{"12345tx0010002", "HIOS", "TX"},
		{"49046", "HIOS", ""},          // too short for [5:7]
		{"45334GA07", "EIN", ""},       // not HIOS
		{"45334120770001", "HIOS", ""}, // digits, not letters
	}
	for _, c := range cases {
		got := hiosStateCode(core.ReportingPlan{PlanID: c.id, PlanIDType: c.idType})
		if got != c.want {
			t.Errorf("hiosStateCode(%q,%q) = %q, want %q", c.id, c.idType, got, c.want)
		}
	}
}

func TestFileAddress(t *testing.T) {
	cases := map[string]string{
		// A Georgia file's location carries no query, so it is its own address.
		"https://anthem.s3.amazonaws.com/anthem/GA_JBNKMED0001.json.gz": "https://anthem.s3.amazonaws.com/anthem/GA_JBNKMED0001.json.gz",
		// A signed location changes every month; its address doesn't.
		"https://mrf.example.com/2026-10_301_71B0_in-network-rates_01_of_02.json.gz?Expires=1&Signature=a": "https://mrf.example.com/2026-10_301_71B0_in-network-rates_01_of_02.json.gz",
		"https://x/a.json.gz?b=1?c=2": "https://x/a.json.gz",
	}
	for location, want := range cases {
		if got := fileAddress(location); got != want {
			t.Errorf("fileAddress(%q) = %q, want %q", location, got, want)
		}
	}
}
