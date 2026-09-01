package extraction

import (
	"encoding/binary"
	"encoding/json"
	"fmt"
	"hash/fnv"
	"io"
	"log"
	"sort"
	"strings"

	"github.com/wmespi/honest-healthcare/etl/core"
)

// mrfWriters are the side-effecting sinks for a streamed MRF. The production
// caller wires these to Parquet writers + a Postgres billing-code upsert; tests
// wire them to in-memory slices. Every field may be nil (dry run / counting only).
type mrfWriters struct {
	prices          func([]core.PriceRow)
	groupSetMembers func([]core.GroupSetMemberRow)
	providers       func([]core.ProviderRow)
	// code is called exactly once per billing code not already in seenBillingCodes.
	code func(core.BillingCodeRow)
}

// hashGroupSet fingerprints a sorted provider-group roster. FNV-64a over the
// little-endian id bytes — deterministic across runs and machines. Paired with
// the file_id column, (file_id, group_set_id) uniquely identifies a roster.
func hashGroupSet(sortedIDs []int64) int64 {
	h := fnv.New64a()
	var buf [8]byte
	for _, id := range sortedIDs {
		binary.LittleEndian.PutUint64(buf[:], uint64(id))
		h.Write(buf[:])
	}
	return int64(h.Sum64())
}

// mrfResult is the per-file coverage summary — what this one file contributed.
type mrfResult struct {
	ProviderRows        int64
	PriceRows           int64
	GroupSetMemberRows  int64
	GroupSets           int
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

	// Completeness signals (issue #52). streamMRF errors on a truncated or
	// malformed document, so a non-nil result already means the stream closed
	// cleanly; these two are the raw section counts, used for the "neither
	// section present" guard and available for coverage logging.
	InNetworkItems int64 // in_network entries seen (before any filter)
	ProviderRefs   int64 // provider_references entries seen (before any filter)

	// Filter accounting (0 when the filters are off). Covers both the GA NPI
	// filter and the network_name allowlist — a group dropped by either counts here.
	ProviderRowsDropped int64
	// PriceRowsDropped counts price rows not emitted because a block's entire
	// network roster was filtered out.
	PriceRowsDropped int64
	GroupsDropped    int
	// GroupsDroppedNetwork is the subset of GroupsDropped rejected by the
	// network_name allowlist (not a Georgia network).
	GroupsDroppedNetwork int
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

// buildPriceRows expands one in_network item into price rows: one per
// (network × negotiated_price). The provider references in each negotiated_rate
// block are bucketed by network into a roster; the roster is fingerprinted
// (group_set_id) and, if not already seen in this file, its membership edges are
// emitted via emitMembers. Pure except for those two callbacks (seenSets +
// emitMembers dedupe rosters across the whole file).
//
// keptGroups, when non-nil, is the GA/network filter: a referenced group not in
// it is excluded from the roster. dropped receives the count of price rows that
// would have been emitted but were not because a block's whole network roster
// was filtered away.
func buildPriceRows(
	item core.InNetworkItem,
	fileID int64,
	networkByGroup map[int64]string,
	keptGroups map[int64]struct{},
	seenSets map[int64]struct{},
	emitMembers func(fileID, groupSetID int64, groupIDs []int64),
	dropped *int64,
) []core.PriceRow {
	var rows []core.PriceRow
	for _, rate := range item.NegotiatedRates {
		// Bucket this block's provider references by network. A group in "A|B"
		// joins both rosters.
		byNet := map[string][]int64{}
		hadRefs := len(rate.ProviderReferences) > 0
		for _, refID := range rate.ProviderReferences {
			gid := int64(refID)
			if keptGroups != nil {
				if _, ok := keptGroups[gid]; !ok {
					continue
				}
			}
			for _, net := range splitNetworks(networkByGroup[gid]) {
				byNet[net] = append(byNet[net], gid)
			}
		}
		if hadRefs && len(byNet) == 0 && dropped != nil {
			*dropped += int64(len(rate.NegotiatedPrices))
		}
		for net, groupIDs := range byNet {
			sort.Slice(groupIDs, func(i, j int) bool { return groupIDs[i] < groupIDs[j] })
			gsid := hashGroupSet(groupIDs)
			if _, ok := seenSets[gsid]; !ok {
				seenSets[gsid] = struct{}{}
				if emitMembers != nil {
					emitMembers(fileID, gsid, groupIDs)
				}
			}
			for _, price := range rate.NegotiatedPrices {
				rows = append(rows, core.PriceRow{
					FileID:                 fileID,
					GroupSetID:             gsid,
					NetworkName:            net,
					BillingCodeType:        item.BillingCodeType,
					BillingCode:            item.BillingCode,
					NegotiationArrangement: item.NegotiationArrangement,
					NegotiatedType:         price.NegotiatedType,
					NegotiatedRate:         price.NegotiatedRate,
					ExpirationDate:         price.ExpirationDate,
					ServiceCode:            strings.Join(price.ServiceCode, "|"),
					BillingClass:           price.BillingClass,
					Modifier:               joinModifiers(price.BillingCodeModifier),
					Setting:                price.Setting,
				})
			}
		}
	}
	return rows
}

// joinModifiers sorts and "|"-joins a billing_code_modifier array so
// ["TC","26"] and ["26","TC"] produce the same stable key ("26|TC").
func joinModifiers(mods []string) string {
	if len(mods) == 0 {
		return ""
	}
	clean := make([]string, 0, len(mods))
	for _, m := range mods {
		if m = strings.TrimSpace(m); m != "" {
			clean = append(clean, m)
		}
	}
	sort.Strings(clean)
	return strings.Join(clean, "|")
}

// splitNetworks turns a "|"-joined network_name into its members, always
// returning at least one element ("" for an unattributed group).
func splitNetworks(joined string) []string {
	if joined == "" {
		return []string{""}
	}
	parts := strings.Split(joined, "|")
	out := parts[:0]
	for _, p := range parts {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	if len(out) == 0 {
		return []string{""}
	}
	return out
}

// buildProviderRows flattens one provider_references entry into provider rows
// (one per NPI) and returns the "|"-joined network_name for that group. Pure.
func buildProviderRows(ref core.ProviderReference, fileID int64) ([]core.ProviderRow, string) {
	networkName := strings.Join(ref.NetworkName, "|")
	var rows []core.ProviderRow
	for _, pg := range ref.ProviderGroups {
		for _, npi := range pg.NPIs {
			rows = append(rows, core.ProviderRow{
				FileID:          fileID,
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
	fileID int64,
	wantSchema bool,
	seenBillingCodes map[string]bool,
	seenNPIs map[int64]string,
	seenTINs map[string]bool,
	gaNPIs map[int64]struct{}, // nil = keep everything; non-nil = drop providers/rates with no GA NPI
	networkAllow func(networkName string) bool, // nil = allow every network; else keep only groups whose network_name passes
	w mrfWriters,
	pr *core.ProgressReader,
) (*mrfResult, error) {
	_ = planName // no longer stamped onto rows — see core.PriceRow / Known gaps
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
	// at least one GA NPPES NPI — price rows for any other group are dropped.
	keptGroups := make(map[int64]struct{})
	// seenSets dedupes provider-group rosters across the whole file so each
	// distinct roster's membership edges are written to group_sets exactly once.
	seenSets := make(map[int64]struct{})

	t, err := decoder.Token()
	if err != nil {
		return nil, fmt.Errorf("failed to read root token: %w", err)
	}
	if delim, ok := t.(json.Delim); !ok || delim != '{' {
		return nil, fmt.Errorf("expected root '{', got %v", t)
	}

	var providerBuf []core.ProviderRow
	var priceBuf []core.PriceRow
	var memberBuf []core.GroupSetMemberRow
	providerBatches, priceBatches := 0, 0
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
	flushMembers := func() {
		if len(memberBuf) == 0 {
			return
		}
		if w.groupSetMembers != nil {
			w.groupSetMembers(memberBuf)
		}
		res.GroupSetMemberRows += int64(len(memberBuf))
		memberBuf = memberBuf[:0]
	}
	flushPrice := func() {
		if len(priceBuf) == 0 {
			return
		}
		if w.prices != nil {
			w.prices(priceBuf)
		}
		res.PriceRows += int64(len(priceBuf))
		priceBuf = priceBuf[:0]
	}
	// emitMembers is handed to buildPriceRows; it appends one edge per roster
	// member and is called at most once per distinct roster (seenSets dedupe).
	emitMembers := func(fileID, groupSetID int64, groupIDs []int64) {
		res.GroupSets++
		for _, gid := range groupIDs {
			memberBuf = append(memberBuf, core.GroupSetMemberRow{
				FileID:          fileID,
				GroupSetID:      groupSetID,
				ProviderGroupID: gid,
			})
		}
		if len(memberBuf) >= copyBatchSize {
			flushMembers()
		}
	}

	for decoder.More() {
		t, err := decoder.Token()
		if err != nil {
			return nil, fmt.Errorf("malformed MRF: reading root key: %w", err)
		}
		key, ok := t.(string)
		if !ok {
			continue
		}

		switch key {
		case "provider_references":
			log.Println("    🎯 Found 'provider_references'. Streaming...")
			if _, err := decoder.Token(); err != nil { // '['
				return nil, fmt.Errorf("malformed MRF: provider_references opening: %w", err)
			}
			for decoder.More() {
				var ref core.ProviderReference
				if wantSchema && res.SchemaExample["provider_references"] == nil {
					var raw map[string]interface{}
					if err := decoder.Decode(&raw); err != nil {
						return nil, fmt.Errorf("malformed MRF: provider_references[0]: %w", err)
					}
					res.SchemaExample["provider_references"] = []interface{}{raw}
					b, _ := json.Marshal(raw)
					if err := json.Unmarshal(b, &ref); err != nil {
						return nil, fmt.Errorf("malformed MRF: provider_references[0]: %w", err)
					}
				} else if err := decoder.Decode(&ref); err != nil {
					return nil, fmt.Errorf("malformed MRF: provider_references[%d]: %w", res.ProviderRefs, err)
				}
				res.ProviderRefs++

				rows, networkName := buildProviderRows(ref, fileID)

				// Filters: a provider group must pass every active filter, else the
				// group — and every rate row that references it — is dropped.
				if gaNPIs != nil || networkAllow != nil {
					// Network allowlist: is this a Georgia network at all?
					if networkAllow != nil && !networkAllow(networkName) {
						res.ProviderRowsDropped += int64(len(rows))
						res.GroupsDropped++
						res.GroupsDroppedNetwork++
						continue
					}

					// GA NPI filter: keep only rows whose NPI is a Georgia NPPES
					// NPI; a group left with none is dropped (its rates go too).
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
					}
					keptGroups[int64(ref.ProviderGroupID)] = struct{}{}
				}

				if networkName != "" {
					networkByGroup[int64(ref.ProviderGroupID)] = networkName
					for _, n := range splitNetworks(networkName) {
						res.NetworkNames[n] = struct{}{}
					}
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
			if _, err := decoder.Token(); err != nil { // ']'
				return nil, fmt.Errorf("malformed MRF: provider_references not closed: %w", err)
			}
			flushProv()
			log.Printf("    ✅ Streamed %d provider rows. %s", res.ProviderRows, progress())

		case "in_network":
			log.Println("    🎯 Found 'in_network'. Streaming...")
			if _, err := decoder.Token(); err != nil { // '['
				return nil, fmt.Errorf("malformed MRF: in_network opening: %w", err)
			}
			for decoder.More() {
				var item core.InNetworkItem
				if wantSchema && res.SchemaExample["in_network"] == nil {
					var raw map[string]interface{}
					if err := decoder.Decode(&raw); err != nil {
						return nil, fmt.Errorf("malformed MRF: in_network[0]: %w", err)
					}
					res.SchemaExample["in_network"] = []interface{}{raw}
					b, _ := json.Marshal(raw)
					if err := json.Unmarshal(b, &item); err != nil {
						return nil, fmt.Errorf("malformed MRF: in_network[0]: %w", err)
					}
				} else if err := decoder.Decode(&item); err != nil {
					return nil, fmt.Errorf("malformed MRF: in_network[%d]: %w", res.InNetworkItems, err)
				}
				res.InNetworkItems++

				if item.BillingCodeType != "" {
					res.BillingCodeTypes[item.BillingCodeType] = struct{}{}
				}
				if !seenBillingCodes[item.BillingCode] {
					seenBillingCodes[item.BillingCode] = true
					res.NewBillingCodes++
					if w.code != nil {
						w.code(core.BillingCodeRow{
							BillingCodeType: item.BillingCodeType,
							BillingCode:     item.BillingCode,
							Name:            item.Name,
							Description:     item.Description,
						})
					}
				}

				var keptFilter map[int64]struct{}
				if gaNPIs != nil || networkAllow != nil {
					keptFilter = keptGroups
				}
				rows := buildPriceRows(item, fileID, networkByGroup, keptFilter,
					seenSets, emitMembers, &res.PriceRowsDropped)
				for _, row := range rows {
					if row.Setting != "" {
						res.Settings[row.Setting] = struct{}{}
					}
					if row.BillingClass != "" {
						res.BillingClasses[row.BillingClass] = struct{}{}
					}
				}
				priceBuf = append(priceBuf, rows...)
				if len(priceBuf) >= copyBatchSize {
					flushPrice()
					if priceBatches++; priceBatches%logEveryNBatches == 0 {
						log.Printf("    ⚙️  %d price rows... | %s", res.PriceRows, progress())
					}
				}
			}
			if _, err := decoder.Token(); err != nil { // ']'
				return nil, fmt.Errorf("malformed MRF: in_network not closed: %w", err)
			}
			flushPrice()
			flushMembers()
			log.Printf("    ✅ Streamed %d price rows, %d group-set edges (%d sets). %s",
				res.PriceRows, res.GroupSetMemberRows, res.GroupSets, progress())

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
				tPeek, err := decoder.Token()
				if err != nil {
					return nil, fmt.Errorf("malformed MRF: reading %q: %w", key, err)
				}
				if delim, ok := tPeek.(json.Delim); ok && (delim == '[' || delim == '{') {
					depth := 1
					for depth > 0 {
						tSkip, err := decoder.Token()
						if err != nil {
							return nil, fmt.Errorf("malformed MRF: skipping %q: %w", key, err)
						}
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
				core.SkipJSONValue(decoder)
			}
		}
	}
	if _, err := decoder.Token(); err != nil { // '}'
		return nil, fmt.Errorf("malformed MRF: document not closed (truncated stream): %w", err)
	}

	// A real in-network-rates MRF carries both sections. Neither present means an
	// empty shard, an error page served with a 200, or a truncated header —
	// never a file worth marking `completed`.
	if res.InNetworkItems == 0 && res.ProviderRefs == 0 {
		return nil, fmt.Errorf("malformed MRF: no in_network or provider_references entries")
	}

	return res, nil
}
