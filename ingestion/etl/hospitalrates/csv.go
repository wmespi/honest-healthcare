package hospitalrates

import (
	"encoding/csv"
	"errors"
	"fmt"
	"io"
	"log"
	"sort"
	"strconv"
	"strings"
)

// headerSearchRows is how far into a CSV the header is looked for: the CMS
// template puts two rows of the hospital's details above it.
const headerSearchRows = 10

// csvColumns is where each field the extractor reads sits in a CSV's header,
// -1 when the file doesn't have it.
type csvColumns struct {
	codes                                 []codeColumn
	setting, modifiers, billingClass      int
	gross, cash                           int
	payer, plan, dollar, pct, methodology int // the tall layout
	wide                                  []wideGroup
}

type codeColumn struct{ code, typ int }

// wideGroup is one payer and plan's columns in the wide layout, where a row
// carries every payer's rate for its item.
type wideGroup struct {
	payer, plan              *string
	dollar, pct, methodology int
}

func (c *csvColumns) tall() bool { return c.payer >= 0 }

// headerCell is a header cell as the columns are matched: trimmed,
// lower-cased, and without a stray byte-order mark.
func headerCell(h string) string {
	return strings.ToLower(strings.TrimSpace(strings.TrimPrefix(h, "\ufeff")))
}

// isHeader reports whether a row is the CSV's header: it names a description
// and the first code column.
func isHeader(rec []string) bool {
	var desc, code1 bool
	for _, h := range rec {
		switch headerCell(h) {
		case "description":
			desc = true
		case "code|1":
			code1 = true
		}
	}
	return desc && code1
}

// resolveCSVColumns maps the header to the fields the extractor reads, and
// says which payers and plans in a wide header are kept.
func resolveCSVColumns(header []string, e *emitter, keep func(string) bool) (*csvColumns, error) {
	c := &csvColumns{setting: -1, modifiers: -1, billingClass: -1, gross: -1, cash: -1,
		payer: -1, plan: -1, dollar: -1, pct: -1, methodology: -1}
	codeCols, typeCols := map[int]int{}, map[int]int{}
	type wideCols struct{ dollar, pct, methodology int }
	wide := map[pairKey]*wideCols{}
	var wideOrder []pairKey
	for i, raw := range header {
		h := headerCell(raw)
		switch h {
		case "setting":
			c.setting = i
		case "modifiers":
			c.modifiers = i
		case "billing_class":
			c.billingClass = i
		case "standard_charge|gross":
			c.gross = i
		case "standard_charge|discounted_cash":
			c.cash = i
		case "payer_name":
			c.payer = i
		case "plan_name":
			c.plan = i
		case "standard_charge|negotiated_dollar":
			c.dollar = i
		case "standard_charge|negotiated_percentage":
			c.pct = i
		case "standard_charge|methodology":
			c.methodology = i
		}
		parts := strings.Split(h, "|")
		if parts[0] == "code" && (len(parts) == 2 || (len(parts) == 3 && parts[2] == "type")) {
			if n, err := strconv.Atoi(parts[1]); err == nil {
				if len(parts) == 2 {
					codeCols[n] = i
				} else {
					typeCols[n] = i
				}
			}
			continue
		}
		// standard_charge|<payer>|<plan>|negotiated_dollar, as the file spells
		// the payer and plan.
		if parts[0] != "standard_charge" || len(parts) < 4 {
			continue
		}
		field := parts[len(parts)-1]
		if field != "negotiated_dollar" && field != "negotiated_percentage" && field != "methodology" {
			continue
		}
		orig := strings.Split(strings.TrimSpace(strings.TrimPrefix(raw, "\ufeff")), "|")
		k := pairKey{strings.TrimSpace(orig[1]), strings.TrimSpace(strings.Join(orig[2:len(orig)-1], "|"))}
		g := wide[k]
		if g == nil {
			g = &wideCols{-1, -1, -1}
			wide[k] = g
			wideOrder = append(wideOrder, k)
		}
		switch field {
		case "negotiated_dollar":
			g.dollar = i
		case "negotiated_percentage":
			g.pct = i
		default:
			g.methodology = i
		}
	}
	for n, ci := range codeCols {
		if ti, ok := typeCols[n]; ok {
			c.codes = append(c.codes, codeColumn{ci, ti})
		}
	}
	sort.Slice(c.codes, func(a, b int) bool { return c.codes[a].code < c.codes[b].code })
	if len(c.codes) == 0 {
		return nil, errors.New("the header has no code|N column with its code|N|type")
	}
	if !c.tall() {
		for _, k := range wideOrder {
			e.saw(k[0], k[1])
			if k[0] == "" || !keep(k[0]) {
				continue
			}
			g := wide[k]
			c.wide = append(c.wide, wideGroup{payer: text(k[0]), plan: text(k[1]),
				dollar: g.dollar, pct: g.pct, methodology: g.methodology})
		}
	}
	if !c.tall() && len(wideOrder) == 0 && c.gross < 0 && c.cash < 0 {
		return nil, errors.New("the header has no payer_name, no standard_charge|<payer>|<plan> columns and no charges")
	}
	return c, nil
}

// extractCSV streams a CSV in the CMS template, tall (a row per payer and
// plan) or wide (a column group per payer and plan), into e.
func extractCSV(r io.Reader, e *emitter, keep func(string) bool) error {
	cr := csv.NewReader(r)
	cr.LazyQuotes = true
	cr.FieldsPerRecord = -1
	cr.ReuseRecord = true

	var header []string
	for n := 0; n < headerSearchRows && header == nil; n++ {
		rec, err := cr.Read()
		if err == io.EOF {
			break
		}
		if err != nil {
			continue
		}
		if isHeader(rec) {
			header = cloneRecord(rec)
		}
	}
	if header == nil {
		return fmt.Errorf("no header row naming description and code|1 in the first %d rows", headerSearchRows)
	}
	cols, err := resolveCSVColumns(header, e, keep)
	if err != nil {
		return err
	}

	keepPayer := map[string]bool{}
	var prices []payerPrice
	it := &item{}
	bad := 0
	for {
		rec, err := cr.Read()
		if err == io.EOF {
			if bad > 0 {
				log.Printf("  skipped %d lines the CSV reader couldn't parse", bad)
			}
			return nil
		}
		if err != nil {
			// a bad line is skipped rather than abort the file
			bad++
			continue
		}
		get := func(i int) string { return field(rec, i) }
		it.codes = it.codes[:0]
		for _, cc := range cols.codes {
			it.codes = addCode(it.codes, get(cc.typ), get(cc.code))
		}
		prices = prices[:0]
		if cols.tall() {
			payer, plan := strings.TrimSpace(get(cols.payer)), strings.TrimSpace(get(cols.plan))
			if payer != "" {
				e.saw(payer, plan)
				kept, ok := keepPayer[payer]
				if !ok {
					kept = keep(payer)
					keepPayer[strings.Clone(payer)] = kept
				}
				if kept && len(it.codes) > 0 {
					p := payerPrice{dollar: parseNumber(get(cols.dollar)), pct: parseNumber(get(cols.pct))}
					if p.dollar != nil || p.pct != nil {
						p.payer, p.plan, p.methodology = text(payer), text(plan), lower(get(cols.methodology))
						prices = append(prices, p)
					}
				}
			}
		}
		if len(it.codes) == 0 {
			continue
		}
		for _, g := range cols.wide {
			p := payerPrice{payer: g.payer, plan: g.plan, dollar: parseNumber(get(g.dollar)), pct: parseNumber(get(g.pct))}
			if p.dollar != nil || p.pct != nil {
				p.methodology = lower(get(g.methodology))
				prices = append(prices, p)
			}
		}
		it.setting, it.billingClass = lower(get(cols.setting)), lower(get(cols.billingClass))
		it.modifiers = normModifiers(get(cols.modifiers))
		it.gross, it.cash = parseNumber(get(cols.gross)), parseNumber(get(cols.cash))
		if err := e.emit(it, prices); err != nil {
			return err
		}
	}
}

// field is a row's i-th field, or "" when the row is short or the column absent.
func field(rec []string, i int) string {
	if i < 0 || i >= len(rec) {
		return ""
	}
	return rec[i]
}

// cloneRecord copies a row the reader will reuse.
func cloneRecord(rec []string) []string {
	out := make([]string, len(rec))
	for i, s := range rec {
		out[i] = strings.Clone(s)
	}
	return out
}
