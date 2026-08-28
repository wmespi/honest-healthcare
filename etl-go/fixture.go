package main

import (
	"bytes"
	"compress/gzip"
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"

	"github.com/jackc/pgx/v5"
)

// Fixture truncation limits — small enough to commit (KBs), large enough to
// exercise every parser branch (provider_references join, negotiated_prices
// fan-out, billing-code upsert, network_name attribution).
const (
	fixtureProviderRefs   = 25
	fixtureInNetworkItems = 25
	fixtureRatesPerItem   = 5
	fixturePricesPerRate  = 5
	fixtureGroupsPerRef   = 3
	fixtureNPIsPerGroup   = 10
)

// FixtureDir is where committed fixtures live. Relative to the etl-go working dir.
const FixtureDir = "testdata/fixtures"

// makeFixture streams a gzipped MRF and writes a heavily truncated, deterministic
// *.json.gz fixture: the first N provider_references, the first N in_network items
// that reference a kept provider group (negotiated_rates filtered to kept groups
// and capped), and every scalar root key verbatim. provider_references is emitted
// before in_network so the parser's ordering assumption holds.
func makeFixture(ctx context.Context, conn *pgx.Conn, fileID int, fixtureURL, name string) {
	url := fixtureURL
	if url == "" {
		if conn == nil || fileID == 0 {
			log.Fatalf("❌ -make-fixture needs -file-ids N or -fixture-url URL")
		}
		if err := conn.QueryRow(ctx, `SELECT location FROM index_files WHERE id = $1`, fileID).Scan(&url); err != nil {
			log.Fatalf("❌ Could not resolve URL for file id %d: %v", fileID, err)
		}
	}
	if name == "" {
		if fileID != 0 {
			name = fmt.Sprintf("%d", fileID)
		} else {
			name = "fixture"
		}
	}

	log.Printf("🧪 Building fixture %q from %s", name, url)

	resp, err := (&http.Client{}).Get(url)
	if err != nil {
		log.Fatalf("❌ GET failed: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		log.Fatalf("❌ GET returned HTTP %d", resp.StatusCode)
	}

	gz, err := gzip.NewReader(NewProgressReader(resp.Body, resp.ContentLength))
	if err != nil {
		log.Fatalf("❌ gzip: %v", err)
	}
	defer gz.Close()

	dec := json.NewDecoder(gz)

	rootScalars := map[string]json.RawMessage{}
	var keptRefs []json.RawMessage
	keptGroupIDs := map[int64]bool{}
	var keptItems []json.RawMessage

	if t, err := dec.Token(); err != nil {
		log.Fatalf("❌ read root token: %v", err)
	} else if d, ok := t.(json.Delim); !ok || d != '{' {
		log.Fatalf("❌ expected root '{', got %v", t)
	}

	for dec.More() {
		kt, _ := dec.Token()
		key, _ := kt.(string)

		switch key {
		case "provider_references":
			dec.Token() // '['
			for dec.More() {
				if len(keptRefs) >= fixtureProviderRefs {
					skipValue(dec)
					continue
				}
				var pr ProviderReference
				if err := dec.Decode(&pr); err != nil {
					log.Printf("⚠️ decode provider_reference: %v", err)
					continue
				}
				// Cap provider_groups + NPI lists — vision/dental networks put
				// thousands of NPIs on one group and bloat the fixture.
				if len(pr.ProviderGroups) > fixtureGroupsPerRef {
					pr.ProviderGroups = pr.ProviderGroups[:fixtureGroupsPerRef]
				}
				for i := range pr.ProviderGroups {
					if len(pr.ProviderGroups[i].NPIs) > fixtureNPIsPerGroup {
						pr.ProviderGroups[i].NPIs = pr.ProviderGroups[i].NPIs[:fixtureNPIsPerGroup]
					}
				}
				b, _ := json.Marshal(pr)
				keptRefs = append(keptRefs, b)
				keptGroupIDs[int64(pr.ProviderGroupID)] = true
			}
			dec.Token() // ']'

		case "in_network":
			dec.Token() // '['
			for dec.More() {
				if len(keptItems) >= fixtureInNetworkItems {
					skipValue(dec)
					continue
				}
				var raw json.RawMessage
				if err := dec.Decode(&raw); err != nil {
					log.Printf("⚠️ decode in_network item: %v", err)
					continue
				}
				var item InNetworkItem
				json.Unmarshal(raw, &item)

				var rates []NegotiatedRate
				for _, nr := range item.NegotiatedRates {
					if len(rates) >= fixtureRatesPerItem {
						break
					}
					var refs []int
					for _, r := range nr.ProviderReferences {
						if keptGroupIDs[int64(r)] {
							refs = append(refs, r)
						}
					}
					if len(refs) == 0 {
						continue
					}
					nr.ProviderReferences = refs
					if len(nr.NegotiatedPrices) > fixturePricesPerRate {
						nr.NegotiatedPrices = nr.NegotiatedPrices[:fixturePricesPerRate]
					}
					rates = append(rates, nr)
				}
				if len(rates) == 0 {
					continue // item doesn't touch any kept provider group
				}
				item.NegotiatedRates = rates
				b, _ := json.Marshal(item)
				keptItems = append(keptItems, b)
			}
			dec.Token() // ']'

		default:
			var raw json.RawMessage
			if err := dec.Decode(&raw); err != nil {
				log.Printf("⚠️ decode root key %q: %v", key, err)
				continue
			}
			rootScalars[key] = raw
		}
	}

	if len(keptRefs) == 0 || len(keptItems) == 0 {
		log.Fatalf("❌ fixture would be empty (refs=%d items=%d) — source file may not match the expected MRF shape", len(keptRefs), len(keptItems))
	}

	// Assemble with a fixed key order: provider_references, then in_network,
	// then every scalar root key. The parser depends on seeing provider_references
	// before in_network.
	var body bytes.Buffer
	body.WriteByte('{')
	first := true
	writeKey := func(k string, v []byte) {
		if !first {
			body.WriteByte(',')
		}
		first = false
		kb, _ := json.Marshal(k)
		body.Write(kb)
		body.WriteByte(':')
		body.Write(v)
	}
	refsArr, _ := json.Marshal(keptRefs)
	itemsArr, _ := json.Marshal(keptItems)
	writeKey("provider_references", refsArr)
	writeKey("in_network", itemsArr)
	for k, v := range rootScalars {
		writeKey(k, v)
	}
	body.WriteByte('}')

	var pretty bytes.Buffer
	if err := json.Indent(&pretty, body.Bytes(), "", " "); err != nil {
		pretty = body
	}

	if err := os.MkdirAll(FixtureDir, 0o755); err != nil {
		log.Fatalf("❌ mkdir %s: %v", FixtureDir, err)
	}
	outPath := filepath.Join(FixtureDir, name+".json.gz")
	f, err := os.Create(outPath)
	if err != nil {
		log.Fatalf("❌ create %s: %v", outPath, err)
	}
	defer f.Close()
	zw := gzip.NewWriter(f)
	if _, err := zw.Write(pretty.Bytes()); err != nil {
		log.Fatalf("❌ write fixture: %v", err)
	}
	if err := zw.Close(); err != nil {
		log.Fatalf("❌ close gzip: %v", err)
	}

	fi, _ := f.Stat()
	log.Printf("✅ Wrote %s — %d provider_references, %d in_network items, %d bytes gzipped",
		outPath, len(keptRefs), len(keptItems), fi.Size())
}
