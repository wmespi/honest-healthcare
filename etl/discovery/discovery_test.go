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
