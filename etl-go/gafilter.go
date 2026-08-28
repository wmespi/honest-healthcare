package main

import (
	"log"
	"os"
	"strings"

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

// isGAPlanSpecific reports whether a file URL is one of Anthem's Georgia
// plan-specific rate files (…amazonaws.com/anthem/GA_<plan>.json.gz). These are
// unambiguously Georgia by Anthem's own naming — the same signal priority.go
// scores — so the network_name allowlist is skipped for them.
func isGAPlanSpecific(location string) bool {
	return strings.Contains(location, "amazonaws.com/anthem/GA_")
}

// buildNetworkAllow compiles a comma-separated network_name allowlist into a
// predicate. An entry ending in "*" is a prefix match (inner spaces kept, e.g.
// "GA *" matches "GA Blue Value HIX Individual Network"); any other entry is an
// exact match. The candidate name may itself be "|"-joined (a group tagged with
// several networks) — it passes if ANY member matches. An empty spec returns nil
// (no filter — every network kept).
func buildNetworkAllow(spec string) func(string) bool {
	spec = strings.TrimSpace(spec)
	if spec == "" {
		return nil
	}
	var exact, prefix []string
	for _, part := range strings.Split(spec, ",") {
		p := strings.TrimSpace(part)
		switch {
		case p == "":
			continue
		case strings.HasSuffix(p, "*"):
			prefix = append(prefix, strings.TrimSuffix(p, "*"))
		default:
			exact = append(exact, p)
		}
	}
	return func(name string) bool {
		for _, member := range strings.Split(name, "|") {
			member = strings.TrimSpace(member)
			if member == "" {
				continue
			}
			for _, e := range exact {
				if member == e {
					return true
				}
			}
			for _, pfx := range prefix {
				if strings.HasPrefix(member, pfx) {
					return true
				}
			}
		}
		return false
	}
}
