package extraction

import (
	"log"
	"os"

	parquet "github.com/parquet-go/parquet-go"
)

// gaNPIProjection reads only the npi column from ga_providers.parquet.
type gaNPIProjection struct {
	NPI int64 `parquet:"npi"`
}

// loadGANPISet loads the set of NPIs that NPPES lists with a Georgia
// practice-location. Returns nil (no filter) if the file is absent.
func loadGANPISet(path string) map[int64]struct{} {
	if _, err := os.Stat(path); err != nil {
		return nil
	}
	rows, err := parquet.ReadFile[gaNPIProjection](path)
	if err != nil {
		log.Printf("⚠️ could not read %s for the GA NPI filter (%v) — keeping all NPIs", path, err)
		return nil
	}
	set := make(map[int64]struct{}, len(rows))
	for _, r := range rows {
		if r.NPI != 0 {
			set[r.NPI] = struct{}{}
		}
	}
	log.Printf("🗺️  GA NPI filter active — %d Georgia NPIs from %s", len(set), path)
	return set
}
