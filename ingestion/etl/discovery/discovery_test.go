package discovery

import (
	"testing"

	"github.com/wmespi/honest-healthcare/ingestion/etl/core"
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

func TestFileList(t *testing.T) {
	files := map[string]core.IndexFile{
		"https://x/b.json.gz": {FileID: 2, Address: "https://x/b.json.gz"},
		"https://x/a.json.gz": {FileID: 1, Address: "https://x/a.json.gz"},
	}
	list, err := fileList(files)
	if err != nil || len(list) != 2 || list[0].FileID != 1 || list[1].FileID != 2 {
		t.Fatalf("fileList = %v, %v; want both files in ID order", list, err)
	}
	files["https://x/c.json.gz"] = core.IndexFile{FileID: 2, Address: "https://x/c.json.gz"}
	if _, err := fileList(files); err == nil {
		t.Error("fileList accepted two addresses with one file ID")
	}
}
