package core

import "testing"

func TestFileAddress(t *testing.T) {
	cases := map[string]string{
		// A Georgia file's location carries no query, so it is its own address.
		"https://anthem.s3.amazonaws.com/anthem/GA_JBNKMED0001.json.gz": "https://anthem.s3.amazonaws.com/anthem/GA_JBNKMED0001.json.gz",
		// A signed location changes every month; its address doesn't.
		"https://mrf.example.com/2026-10_301_71B0_in-network-rates_01_of_02.json.gz?Expires=1&Signature=a": "https://mrf.example.com/2026-10_301_71B0_in-network-rates_01_of_02.json.gz",
		"https://x/a.json.gz?b=1?c=2": "https://x/a.json.gz",
	}
	for location, want := range cases {
		if got := FileAddress(location); got != want {
			t.Errorf("FileAddress(%q) = %q, want %q", location, got, want)
		}
	}
}

func TestFileName(t *testing.T) {
	if got := FileName("https://anthem.s3.amazonaws.com/anthem/GA_JBNKMED0001.json.gz"); got != "GA_JBNKMED0001.json.gz" {
		t.Errorf("FileName = %q", got)
	}
}

func TestStem(t *testing.T) {
	for name, want := range map[string]string{
		"GA_JBNKMED0001.json.gz":                             "GA_JBNKMED0001",
		"2026-10_301_71B0_in-network-rates_01_of_02.json.gz": "2026-10_301_71B0_in-network-rates_01_of_02",
		"plain.json": "plain",
	} {
		if got := (IndexFile{Name: name}).Stem(); got != want {
			t.Errorf("Stem of %q = %q, want %q", name, got, want)
		}
	}
}

// Every parsed row carries one of these IDs, so they must never change.
func TestFileID_Pinned(t *testing.T) {
	cases := map[string]int64{
		"https://antm-pt-prod-dataz-nogbd-nophi-us-east1.s3.amazonaws.com/anthem/GA_JBNKMED0001.json.gz": 3985741317353967653,
		"https://antm-pt-prod-dataz-nogbd-nophi-us-east1.s3.amazonaws.com/anthem/GA_JBLDMEDTR01.json.gz": 2193404193943343938,
	}
	for address, want := range cases {
		if got := FileID(address); got != want {
			t.Errorf("FileID(%q) = %d, want %d: changing the hash breaks the link to every parsed file", address, got, want)
		}
	}
	if FileID("https://x/a.json.gz") < 0 {
		t.Error("FileID is negative")
	}
}
