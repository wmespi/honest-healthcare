package core

import (
	"fmt"
	"strings"
)

// Payer is an insurer whose master index we read: its files and plan links,
// the raw Parquet parse writes from its rate files, and the parse records live
// under data/raw/<name>/. Add a payer here and in discovery's sources.
type Payer struct {
	// Name is the folder under data/raw/, the `payer:` of a target and the PAYER=
	// of the make targets.
	Name string
	// Label is how logs and docs say it.
	Label string
}

// Payers is every payer, in the order the pipeline runs them.
var Payers = []Payer{
	{Name: "anthem", Label: "Anthem"},
	{Name: "ambetter", Label: "Ambetter"},
	{Name: "alliant", Label: "Alliant"},
}

// PayerByName is the payer called name, or an error naming the ones there are.
func PayerByName(name string) (Payer, error) {
	for _, p := range Payers {
		if p.Name == name {
			return p, nil
		}
	}
	return Payer{}, fmt.Errorf("unknown payer %q (payers: %s)", name, strings.Join(PayerNames(), ", "))
}

// PayerNames lists the payers' names.
func PayerNames() []string {
	names := make([]string, len(Payers))
	for i, p := range Payers {
		names[i] = p.Name
	}
	return names
}

// RawDir is the payer's own folder, which only ingestion reads.
func (p Payer) RawDir() string { return DataRoot + "/raw/" + p.Name }

// ExamplePath is the compact example of a rate file's shape.
func (p Payer) ExamplePath() string { return p.RawDir() + "/mrf_example.json" }

// PricesDir, GroupSetsDir, ProvidersDir and CodesDir hold the Parquet parse
// writes from the payer's rate files; ParsesDir the record of each parse.
func (p Payer) PricesDir() string    { return p.RawDir() + "/prices" }
func (p Payer) GroupSetsDir() string { return p.RawDir() + "/group_sets" }
func (p Payer) ProvidersDir() string { return p.RawDir() + "/providers" }
func (p Payer) CodesDir() string     { return p.RawDir() + "/codes" }
func (p Payer) ParsesDir() string    { return p.RawDir() + "/parses" }

// IndexFilesPath and IndexFilePlansPath are this month's file list and plan
// links, as `etl discover` writes them.
func (p Payer) IndexFilesPath() string     { return p.RawDir() + "/index_files.parquet" }
func (p Payer) IndexFilePlansPath() string { return p.RawDir() + "/index_file_plans.parquet" }

// IndexSchemaPath is the compact example of the index's shape.
func (p Payer) IndexSchemaPath() string { return p.RawDir() + "/index_schema.json" }
