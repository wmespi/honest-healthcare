package main

import (
	"encoding/json"
	"fmt"
	"io"
	"log"
	"strings"
)

// mrfWriters are the side-effecting sinks for a streamed MRF. The production
// caller wires these to Parquet writers + a Postgres billing-code upsert; tests
// wire them to in-memory slices. All three may be nil (dry run / counting only).
type mrfWriters struct {
	rates     func([]RateRow)
	providers func([]ProviderRow)
	// code is called exactly once per billing code not already in seenBillingCodes.
	code func(BillingCodeRow)
}

// mrfResult is the per-file coverage summary — what this one file contributed.
type mrfResult struct {
	ProviderRows        int64
	RateRows            int64
	NewBillingCodes     int
	NewNPIs             int
	NewTINs             int
	NetworkNames        map[string]struct{}
	Settings            map[string]struct{}
	BillingClasses      map[string]struct{}
	BillingCodeTypes    map[string]struct{}
	ReportingEntityName string
	ReportingEntityType string
	SchemaExample       map[string]interface{}

	// GA NPI filter accounting (0 when the filter is off).
	ProviderRowsDropped int64
	RateRowsDropped     int64
	GroupsDropped       int
}

func newStringSet() map[string]struct{} { return map[string]struct{}{} }

// SortedKeys returns a set's members as a sorted slice (stable output for logs,
// Parquet, and Postgres array columns).
func sortedKeys(m map[string]struct{}) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	// insertion sort — sets here are tiny (settings, classes, networks)
	for i := 1; i < len(out); i++ {
		for j := i; j > 0 && out[j-1] > out[j]; j-- {
			out[j-1], out[j] = out[j], out[j-1]
		}
	}
	return out
}

// buildRateRows expands one in_network item into rate rows: one per
// (provider_reference × negotiated_price), stamped with the network_name carried
// by that provider group. Pure — no I/O — so it is unit-testable directly.
func buildRateRows(item InNetworkItem, networkByGroup map[int64]string, planName string) []RateRow {
	var rows []RateRow
	for _, rate := range item.NegotiatedRates {
		for _, refID := range rate.ProviderReferences {
			networkName := networkByGroup[int64(refID)]
			for _, price := range rate.NegotiatedPrices {
				rows = append(rows, RateRow{
					ProviderGroupID:        int64(refID),
					PlanName:               planName,
					NetworkName:            networkName,
					BillingCodeType:        item.BillingCodeType,
					BillingCode:            item.BillingCode,
					NegotiationArrangement: item.NegotiationArrangement,
					NegotiatedType:         price.NegotiatedType,
					NegotiatedRate:         price.NegotiatedRate,
					ExpirationDate:         price.ExpirationDate,
					ServiceCode:            strings.Join(price.ServiceCode, "|"),
					BillingClass:           price.BillingClass,
					Setting:                price.Setting,
				})
			}
		}
	}
	return rows
}

// buildProviderRows flattens one provider_references entry into provider rows
// (one per NPI) and returns the "|"-joined network_name for that group. Pure.
func buildProviderRows(ref ProviderReference) ([]ProviderRow, string) {
	networkName := strings.Join(ref.NetworkName, "|")
	var rows []ProviderRow
	for _, pg := range ref.ProviderGroups {
		for _, npi := range pg.NPIs {
			rows = append(rows, ProviderRow{
				ProviderGroupID: int64(ref.ProviderGroupID),
				NetworkName:     networkName,
				NPI:             int64(npi),
				TINType:         pg.TIN.Type,
				TINValue:        pg.TIN.Value,
			})
		}
	}
	return rows, networkName
}

// streamMRF does the single-pass token-level scan of one MRF document (already
// gzip-decompressed). It never buffers the whole file. provider_references is
// expected before in_network so network_name attribution is available for rates.
func streamMRF(
	r io.Reader,
	planName string,
	wantSchema bool,
	seenBillingCodes map[string]bool,
	seenNPIs map[int64]string,
	seenTINs map[string]bool,
	gaNPIs map[int64]struct{}, // nil = keep everything; non-nil = drop providers/rates with no GA NPI
	w mrfWriters,
	pr *ProgressReader,
) (*mrfResult, error) {
	progress := func() string {
		if pr == nil {
			return ""
		}
		return pr.GetProgressString()
	}

	decoder := json.NewDecoder(r)
	res := &mrfResult{
		NetworkNames:     newStringSet(),
		Settings:         newStringSet(),
		BillingClasses:   newStringSet(),
		BillingCodeTypes: newStringSet(),
		SchemaExample:    map[string]interface{}{},
	}
	networkByGroup := make(map[int64]string)
	// When the GA filter is on, keptGroups holds every provider_group_id that had
	// at least one GA NPPES NPI — rate rows for any other group are dropped.
	keptGroups := make(map[int64]struct{})

	t, err := decoder.Token()
	if err != nil {
		return nil, fmt.Errorf("failed to read root token: %w", err)
	}
	if delim, ok := t.(json.Delim); !ok || delim != '{' {
		return nil, fmt.Errorf("expected root '{', got %v", t)
	}

	var providerBuf []ProviderRow
	var rateBuf []RateRow
	providerBatches, rateBatches := 0, 0
	const logEveryNBatches = 10

	flushProv := func() {
		if len(providerBuf) == 0 {
			return
		}
		if w.providers != nil {
			w.providers(providerBuf)
		}
		res.ProviderRows += int64(len(providerBuf))
		providerBuf = providerBuf[:0]
	}
	flushRate := func() {
		if len(rateBuf) == 0 {
			return
		}
		if w.rates != nil {
			w.rates(rateBuf)
		}
		res.RateRows += int64(len(rateBuf))
		rateBuf = rateBuf[:0]
	}

	for decoder.More() {
		t, err := decoder.Token()
		if err != nil {
			break
		}
		key, ok := t.(string)
		if !ok {
			continue
		}

		switch key {
		case "provider_references":
			log.Println("    🎯 Found 'provider_references'. Streaming...")
			decoder.Token() // '['
			for decoder.More() {
				var ref ProviderReference
				if wantSchema && res.SchemaExample["provider_references"] == nil {
					var raw map[string]interface{}
					if err := decoder.Decode(&raw); err == nil {
						res.SchemaExample["provider_references"] = []interface{}{raw}
						b, _ := json.Marshal(raw)
						json.Unmarshal(b, &ref)
					}
				} else if err := decoder.Decode(&ref); err != nil {
					log.Printf("⚠️ decode provider_reference: %v", err)
					continue
				}

				rows, networkName := buildProviderRows(ref)

				// GA NPI filter: keep only rows whose NPI is a Georgia NPPES NPI.
				// A group with none is dropped entirely (its rates go too).
				if gaNPIs != nil {
					dropped := int64(0)
					kept := rows[:0]
					for _, row := range rows {
						if _, ok := gaNPIs[row.NPI]; ok {
							kept = append(kept, row)
						} else {
							dropped++
						}
					}
					res.ProviderRowsDropped += dropped
					rows = kept
					if len(rows) == 0 {
						res.GroupsDropped++
						continue
					}
					keptGroups[int64(ref.ProviderGroupID)] = struct{}{}
				}

				if networkName != "" {
					networkByGroup[int64(ref.ProviderGroupID)] = networkName
					res.NetworkNames[networkName] = struct{}{}
				}
				for _, row := range rows {
					if _, seen := seenNPIs[row.NPI]; !seen {
						seenNPIs[row.NPI] = row.TINValue
						res.NewNPIs++
					}
					if row.TINValue != "" && seenTINs != nil && !seenTINs[row.TINValue] {
						seenTINs[row.TINValue] = true
						res.NewTINs++
					}
				}
				providerBuf = append(providerBuf, rows...)
				if len(providerBuf) >= copyBatchSize {
					flushProv()
					if providerBatches++; providerBatches%logEveryNBatches == 0 {
						log.Printf("    ⚙️  %d provider rows... | %s", res.ProviderRows, progress())
					}
				}
			}
			decoder.Token() // ']'
			flushProv()
			log.Printf("    ✅ Streamed %d provider rows. %s", res.ProviderRows, progress())

		case "in_network":
			log.Println("    🎯 Found 'in_network'. Streaming...")
			decoder.Token() // '['
			for decoder.More() {
				var item InNetworkItem
				if wantSchema && res.SchemaExample["in_network"] == nil {
					var raw map[string]interface{}
					if err := decoder.Decode(&raw); err == nil {
						res.SchemaExample["in_network"] = []interface{}{raw}
						b, _ := json.Marshal(raw)
						json.Unmarshal(b, &item)
					}
				} else if err := decoder.Decode(&item); err != nil {
					log.Printf("⚠️ decode in_network item: %v", err)
					continue
				}

				if item.BillingCodeType != "" {
					res.BillingCodeTypes[item.BillingCodeType] = struct{}{}
				}
				if !seenBillingCodes[item.BillingCode] {
					seenBillingCodes[item.BillingCode] = true
					res.NewBillingCodes++
					if w.code != nil {
						w.code(BillingCodeRow{
							BillingCodeType: item.BillingCodeType,
							BillingCode:     item.BillingCode,
							Name:            item.Name,
							Description:     item.Description,
						})
					}
				}

				rows := buildRateRows(item, networkByGroup, planName)
				if gaNPIs != nil {
					kept := rows[:0]
					for _, row := range rows {
						if _, ok := keptGroups[row.ProviderGroupID]; ok {
							kept = append(kept, row)
						} else {
							res.RateRowsDropped++
						}
					}
					rows = kept
				}
				for _, row := range rows {
					if row.Setting != "" {
						res.Settings[row.Setting] = struct{}{}
					}
					if row.BillingClass != "" {
						res.BillingClasses[row.BillingClass] = struct{}{}
					}
				}
				rateBuf = append(rateBuf, rows...)
				if len(rateBuf) >= copyBatchSize {
					flushRate()
					if rateBatches++; rateBatches%logEveryNBatches == 0 {
						log.Printf("    ⚙️  %d rate rows... | %s", res.RateRows, progress())
					}
				}
			}
			decoder.Token() // ']'
			flushRate()
			log.Printf("    ✅ Streamed %d rate rows. %s", res.RateRows, progress())

		case "reporting_entity_name", "reporting_entity_type":
			tVal, _ := decoder.Token()
			s, _ := tVal.(string)
			if key == "reporting_entity_name" {
				res.ReportingEntityName = s
			} else {
				res.ReportingEntityType = s
			}
			if wantSchema {
				res.SchemaExample[key] = s
			}

		default:
			if wantSchema {
				tPeek, _ := decoder.Token()
				if delim, ok := tPeek.(json.Delim); ok && (delim == '[' || delim == '{') {
					depth := 1
					for depth > 0 {
						tSkip, _ := decoder.Token()
						if dSkip, ok := tSkip.(json.Delim); ok {
							if dSkip == '{' || dSkip == '[' {
								depth++
							} else if dSkip == '}' || dSkip == ']' {
								depth--
							}
						}
					}
				} else {
					res.SchemaExample[key] = tPeek
				}
			} else {
				log.Printf("    🔍 Unexpected root key %q (skipping) %s", key, progress())
				skipValue(decoder)
			}
		}
	}
	decoder.Token() // '}'

	return res, nil
}
