package extraction

import (
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"hash/fnv"
	"io"
	"log"
	"sort"
	"strings"

	"github.com/wmespi/honest-healthcare/ingestion/etl/core"
)

// mrfWriters are the side-effecting sinks for a streamed MRF. The production
// caller wires these to Parquet writers; tests
// wire them to in-memory slices. Every field may be nil (dry run / counting only).
// The stream stops at the first error a sink returns, so a full disk fails the
// file instead of leaving a short one to be promoted.
type mrfWriters struct {
	prices          func([]core.PriceRow) error
	groupSetMembers func([]core.GroupSetMemberRow) error
	providers       func([]core.ProviderRow) error
	// code is called exactly once per billing code in the file.
	code func(core.BillingCodeRow) error
}

// errInNetworkFirst is what a single pass returns, at the in_network key and
// before any row is written, for a file whose in_network comes before its
// provider_references: it learns each provider group's network from
// provider_references, so it can't attribute rates it has not yet seen the
// groups of. parseRates answers it by reading the file from disk in two passes
// (sectionsReferences, then sectionsRates) over one streamState.
var errInNetworkFirst = errors.New("malformed MRF: in_network comes before provider_references, so its rates can't be given a network in one pass")

// sections says which of a document's two big sections a pass reads. The other
// is skipped, one entry at a time, without being kept.
type sections int

const (
	sectionsBoth       sections = iota // one pass; provider_references must come first
	sectionsReferences                 // provider_references only: providers, networks, filter, probe
	sectionsRates                      // in_network only, using what sectionsReferences learned
)

// streamState is what a file's passes share: the result so far and everything
// the rates need from provider_references.
type streamState struct {
	res            *mrfResult
	networkByGroup map[int64]string
	// keptGroups holds every provider_group_id that survived the filters - with
	// the GA filter on, the ones that had at least one GA NPPES NPI (price rows
	// for any other group are dropped); with it off, all of them. Its size is the
	// probe's overlap reading either way.
	keptGroups map[int64]struct{}
	// reading is what the probe judges the file on, filled in as
	// provider_references streams past.
	reading probeReading
	// seenProviderRefs says provider_references has started, which in_network
	// needs in a single pass (errInNetworkFirst).
	seenProviderRefs bool
	// seenSets dedupes provider-group rosters across the whole file so each
	// distinct roster's membership edges are written to group_sets exactly once.
	seenSets map[int64]struct{}
	// seenBillingCodes dedupes the file's codes, so codes/ lists each once.
	seenBillingCodes map[string]bool
}

func newStreamState() *streamState {
	return &streamState{
		res: &mrfResult{
			NetworkNames:  newStringSet(),
			SchemaExample: map[string]interface{}{},
		},
		networkByGroup:   make(map[int64]string),
		keptGroups:       make(map[int64]struct{}),
		seenSets:         make(map[int64]struct{}),
		seenBillingCodes: make(map[string]bool),
	}
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
	ProviderRows       int64
	PriceRows          int64
	GroupSetMemberRows int64
	GroupSets          int
	BillingCodes       int
	NetworkNames       map[string]struct{}
	SchemaExample      map[string]interface{}

	// Completeness signals (issue #52). streamMRF errors on a truncated or
	// malformed document, so a non-nil result already means the stream closed
	// cleanly; these two are the raw section counts, used for the "neither
	// section present" guard and available for coverage logging.
	InNetworkItems int64 // in_network entries seen (before any filter)
	ProviderRefs   int64 // provider_references entries seen (before any filter)

	// Filter accounting (0 when the GA NPI filter is off).
	ProviderRowsDropped int64
	// PriceRowsDropped counts price rows not emitted because a block's entire
	// network roster was filtered out.
	PriceRowsDropped int64
	GroupsDropped    int
	// GroupsKept is how many distinct provider groups survived the filter — the
	// probe's overlap reading, and what the abort message quotes.
	GroupsKept int
}

func newStringSet() map[string]struct{} { return map[string]struct{}{} }

// sortedKeys returns a set's members as a sorted slice (stable output for logs).
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
// keptGroups, when non-nil, is the GA NPI filter's surviving set: a referenced
// group not in it is excluded from the roster. dropped receives the count of price rows that
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
// gzip-decompressed). It never buffers the whole file. provider_references has
// to come before in_network, so network_name attribution is available for rates
// and the probe can abandon a file at the end of that first section, before a
// byte of in_network is read (probe.go); a file in the other order returns
// errInNetworkFirst, before anything is written, and the caller reads it in two
// passes with scanMRF.
func streamMRF(
	r io.Reader,
	fileID int64,
	wantSchema bool,
	gaNPIs map[int64]struct{}, // nil = keep everything; non-nil = drop providers/rates with no GA NPI
	pb providerProbe, // the pre-in_network gate; the zero value never aborts
	w mrfWriters,
	pr *core.ProgressReader,
) (*mrfResult, error) {
	return scanMRF(r, newStreamState(), sectionsBoth, fileID, wantSchema, gaNPIs, pb, w, pr)
}

// scanMRF reads the sections mode names of one document into st. A file that
// lists its rates first is read twice, over the same st: sectionsReferences,
// then sectionsRates on a fresh reader of the same bytes. The result in st.res
// is complete after the last pass.
func scanMRF(
	r io.Reader,
	st *streamState,
	mode sections,
	fileID int64,
	wantSchema bool,
	gaNPIs map[int64]struct{},
	pb providerProbe,
	w mrfWriters,
	pr *core.ProgressReader,
) (*mrfResult, error) {
	progress := func() string {
		if pr == nil {
			return ""
		}
		return pr.GetProgressString()
	}

	decoder := json.NewDecoder(r)
	res := st.res
	networkByGroup := st.networkByGroup
	keptGroups := st.keptGroups
	reading := &st.reading
	seenSets := st.seenSets
	seenBillingCodes := st.seenBillingCodes

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

	flushProv := func() error {
		if len(providerBuf) == 0 {
			return nil
		}
		if w.providers != nil {
			if err := w.providers(providerBuf); err != nil {
				return fmt.Errorf("write providers: %w", err)
			}
		}
		res.ProviderRows += int64(len(providerBuf))
		providerBuf = providerBuf[:0]
		return nil
	}
	flushMembers := func() error {
		if len(memberBuf) == 0 {
			return nil
		}
		if w.groupSetMembers != nil {
			if err := w.groupSetMembers(memberBuf); err != nil {
				return fmt.Errorf("write group_sets: %w", err)
			}
		}
		res.GroupSetMemberRows += int64(len(memberBuf))
		memberBuf = memberBuf[:0]
		return nil
	}
	flushPrice := func() error {
		if len(priceBuf) == 0 {
			return nil
		}
		if w.prices != nil {
			if err := w.prices(priceBuf); err != nil {
				return fmt.Errorf("write prices: %w", err)
			}
		}
		res.PriceRows += int64(len(priceBuf))
		priceBuf = priceBuf[:0]
		return nil
	}
	// emitMembers is handed to buildPriceRows; it appends one edge per roster
	// member and is called at most once per distinct roster (seenSets dedupe).
	// A failed flush is kept in memberErr, which the in_network loop checks after
	// each item.
	var memberErr error
	emitMembers := func(fileID, groupSetID int64, groupIDs []int64) {
		res.GroupSets++
		for _, gid := range groupIDs {
			memberBuf = append(memberBuf, core.GroupSetMemberRow{
				FileID:          fileID,
				GroupSetID:      groupSetID,
				ProviderGroupID: gid,
			})
		}
		if len(memberBuf) >= copyBatchSize && memberErr == nil {
			memberErr = flushMembers()
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
			if mode == sectionsRates {
				if err := skipSection(decoder, key); err != nil {
					return nil, err
				}
				continue
			}
			log.Println("    🎯 Found 'provider_references'. Streaming...")
			st.seenProviderRefs = true
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

				// Probe reading — taken before the filters, so the network signal
				// is about the file's identity, not about which of its groups
				// happen to hold a Georgia NPI.
				if pb.networkMatch != nil {
					for _, n := range splitNetworks(networkName) {
						reading.note(n)
					}
					if !reading.NetworkHit && pb.networkMatch(networkName) {
						reading.NetworkHit = true
					}
				}

				// GA NPI filter: keep only rows whose NPI is a Georgia NPPES NPI;
				// a group left with none is dropped (its rates go too).
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

				if networkName != "" {
					networkByGroup[int64(ref.ProviderGroupID)] = networkName
					for _, n := range splitNetworks(networkName) {
						res.NetworkNames[n] = struct{}{}
					}
				}
				providerBuf = append(providerBuf, rows...)
				if len(providerBuf) >= copyBatchSize {
					if err := flushProv(); err != nil {
						return nil, err
					}
					if providerBatches++; providerBatches%logEveryNBatches == 0 {
						log.Printf("    ⚙️  %d provider rows... | %s", res.ProviderRows, progress())
					}
				}
			}
			if _, err := decoder.Token(); err != nil { // ']'
				return nil, fmt.Errorf("malformed MRF: provider_references not closed: %w", err)
			}
			if err := flushProv(); err != nil {
				return nil, err
			}
			res.GroupsKept = len(keptGroups)
			log.Printf("    ✅ Streamed %d provider rows. %s", res.ProviderRows, progress())

			// The probe, at the one moment it is cheap: every provider group and
			// network label is known, and in_network — the rest of the file — has
			// not been touched. Returning here means the caller cancels the
			// download instead of paying for gigabytes it would discard.
			if pb.active() {
				reading.KeptGroups = res.GroupsKept
				reading.Refs = res.ProviderRefs
				if err := pb.check(*reading); err != nil {
					return nil, err
				}
				log.Printf("    🛰️  probe passed — %d provider groups kept of %d references",
					reading.KeptGroups, reading.Refs)
			}

		case "in_network":
			if mode == sectionsReferences {
				if err := skipSection(decoder, key); err != nil {
					return nil, err
				}
				continue
			}
			if mode == sectionsBoth && !st.seenProviderRefs {
				return nil, errInNetworkFirst
			}
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

				if !seenBillingCodes[item.BillingCode] {
					seenBillingCodes[item.BillingCode] = true
					res.BillingCodes++
					if w.code != nil {
						if err := w.code(core.BillingCodeRow{
							BillingCodeType: item.BillingCodeType,
							BillingCode:     item.BillingCode,
							Name:            item.Name,
							Description:     item.Description,
						}); err != nil {
							return nil, fmt.Errorf("write codes: %w", err)
						}
					}
				}

				// Only the GA NPI filter narrows rosters. With it off, a rate may
				// reference a group the file never declared, and passing
				// keptGroups would silently drop it.
				var keptFilter map[int64]struct{}
				if gaNPIs != nil {
					keptFilter = keptGroups
				}
				rows := buildPriceRows(item, fileID, networkByGroup, keptFilter,
					seenSets, emitMembers, &res.PriceRowsDropped)
				if memberErr != nil {
					return nil, memberErr
				}
				priceBuf = append(priceBuf, rows...)
				if len(priceBuf) >= copyBatchSize {
					if err := flushPrice(); err != nil {
						return nil, err
					}
					if priceBatches++; priceBatches%logEveryNBatches == 0 {
						log.Printf("    ⚙️  %d price rows... | %s", res.PriceRows, progress())
					}
				}
			}
			if _, err := decoder.Token(); err != nil { // ']'
				return nil, fmt.Errorf("malformed MRF: in_network not closed: %w", err)
			}
			if err := flushPrice(); err != nil {
				return nil, err
			}
			if err := flushMembers(); err != nil {
				return nil, err
			}
			log.Printf("    ✅ Streamed %d price rows, %d group-set edges (%d sets). %s",
				res.PriceRows, res.GroupSetMemberRows, res.GroupSets, progress())

		case "reporting_entity_name", "reporting_entity_type":
			tVal, _ := decoder.Token()
			s, _ := tVal.(string)
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
	// never a file worth recording as `parsed`.
	// (The references pass has not read in_network yet, so it can't say.)
	if mode != sectionsReferences && res.InNetworkItems == 0 && res.ProviderRefs == 0 {
		return nil, fmt.Errorf("malformed MRF: no in_network or provider_references entries")
	}

	return res, nil
}

// skipSection reads past the array at a root key one entry at a time, so a
// pass that doesn't want the section never holds more than one entry of it.
func skipSection(decoder *json.Decoder, key string) error {
	t, err := decoder.Token()
	if err != nil {
		return fmt.Errorf("malformed MRF: %s opening: %w", key, err)
	}
	if d, ok := t.(json.Delim); !ok || d != '[' {
		return fmt.Errorf("malformed MRF: %s is not an array", key)
	}
	for decoder.More() {
		var skip json.RawMessage
		if err := decoder.Decode(&skip); err != nil {
			return fmt.Errorf("malformed MRF: skipping %s: %w", key, err)
		}
	}
	if _, err := decoder.Token(); err != nil { // ']'
		return fmt.Errorf("malformed MRF: %s not closed: %w", key, err)
	}
	return nil
}
