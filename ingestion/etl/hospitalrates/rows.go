package hospitalrates

import (
	"encoding/binary"
	"hash"
	"hash/fnv"
	"math"
	"slices"
	"strconv"
	"strings"

	"github.com/wmespi/honest-healthcare/ingestion/etl/core"
)

// code is one billing code an item is priced under, once normalised.
type code struct{ typ, code string }

var codeTypeSeparators = strings.NewReplacer(" ", "", "-", "", "_", "")

// normCodeType maps a file's code type to CPT, HCPCS or MS-DRG, and any other
// type (APR-DRG, revenue codes, NDC, the hospital's own) to "", which drops it.
func normCodeType(t string) string {
	compact := codeTypeSeparators.Replace(strings.ToUpper(strings.TrimSpace(t)))
	switch compact {
	case "CPT":
		return "CPT"
	case "HCPCS":
		return "HCPCS"
	case "DRG", "MSDRG":
		return "MS-DRG"
	}
	return ""
}

// normCode upper-cases and trims a code and checks its shape: a CPT or HCPCS
// code is five letters or digits as given, and an MS-DRG is digits, after any
// "MS-" or "DRG" prefix, padded to three.
func normCode(typ, c string) (string, bool) {
	c = strings.ToUpper(strings.TrimSpace(c))
	switch typ {
	case "CPT", "HCPCS":
		// A shorter number isn't padded: "846" could be anesthesia code 00846
		// that lost its zeros, or a revenue code or DRG labelled CPT, and a
		// missing price beats a wrong one.
		if len(c) != 5 {
			return "", false
		}
		for i := 0; i < len(c); i++ {
			if !isDigit(c[i]) && (c[i] < 'A' || c[i] > 'Z') {
				return "", false
			}
		}
		return c, true
	case "MS-DRG":
		// Some files repeat the type in the code ("MS-177").
		for _, prefix := range []string{"MS-DRG", "MSDRG", "MS DRG", "DRG", "MS"} {
			if rest, ok := strings.CutPrefix(c, prefix); ok {
				c = strings.TrimLeft(rest, " -:#")
				break
			}
		}
		if c == "" || !allDigits(c) {
			return "", false
		}
		c = strings.TrimLeft(c, "0")
		if c == "" || len(c) > 3 {
			return "", false
		}
		return strings.Repeat("0", 3-len(c)) + c, true
	}
	return "", false
}

// addCode appends a kept code unless the item already has it.
func addCode(codes []code, typ, raw string) []code {
	t := normCodeType(typ)
	if t == "" {
		return codes
	}
	c, ok := normCode(t, raw)
	if !ok {
		return codes
	}
	k := code{t, c}
	if slices.Contains(codes, k) {
		return codes
	}
	return append(codes, k)
}

func isDigit(b byte) bool { return b >= '0' && b <= '9' }

func allDigits(s string) bool {
	for i := 0; i < len(s); i++ {
		if !isDigit(s[i]) {
			return false
		}
	}
	return true
}

// parseNumber reads a dollar amount or a percentage as a file writes it:
// 1234.5, 1,234.50 or $1,234.50, with an optional minus sign. Anything else,
// and a blank, is null.
func parseNumber(s string) *float64 {
	s = strings.TrimSpace(s)
	neg := false
	if rest, ok := strings.CutPrefix(s, "-"); ok {
		neg, s = true, rest
	}
	s = strings.TrimPrefix(s, "$")
	if rest, ok := strings.CutPrefix(s, "-"); ok && !neg {
		neg, s = true, rest
	}
	whole, frac, dot := strings.Cut(s, ".")
	if (whole == "" && frac == "") || !allDigits(frac) {
		return nil
	}
	if strings.Contains(whole, ",") {
		groups := strings.Split(whole, ",")
		if len(groups[0]) == 0 || len(groups[0]) > 3 || !allDigits(groups[0]) {
			return nil
		}
		for _, g := range groups[1:] {
			if len(g) != 3 || !allDigits(g) {
				return nil
			}
		}
		whole = strings.Join(groups, "")
	} else if !allDigits(whole) {
		return nil
	}
	if dot {
		whole += "." + frac
	}
	v, err := strconv.ParseFloat(whole, 64)
	if err != nil {
		return nil
	}
	if neg {
		v = -v
	}
	return &v
}

// modifierSep splits a modifiers field that lists several.
var modifierSep = func(r rune) bool { return r == '|' || r == ',' || r == ';' || r == ' ' || r == '\t' }

// normModifiers upper-cases the modifiers an item lists, in one field or
// several, and joins them sorted with "|", as the payer rates' modifier is.
// Null when there are none.
func normModifiers(fields ...string) *string {
	var mods []string
	for _, f := range fields {
		for _, m := range strings.FieldsFunc(f, modifierSep) {
			m = strings.ToUpper(m)
			if !slices.Contains(mods, m) {
				mods = append(mods, m)
			}
		}
	}
	if len(mods) == 0 {
		return nil
	}
	slices.Sort(mods)
	s := strings.Join(mods, "|")
	return &s
}

// text is a trimmed field, or null when blank.
func text(s string) *string {
	s = strings.TrimSpace(s)
	if s == "" {
		return nil
	}
	return &s
}

// lower is a trimmed, lower-cased field, or null when blank.
func lower(s string) *string {
	s = strings.ToLower(strings.TrimSpace(s))
	if s == "" {
		return nil
	}
	return &s
}

// item is what one CSV row or one JSON standard_charges entry says about the
// codes it prices, apart from its payers.
type item struct {
	codes        []code
	setting      *string
	billingClass *string
	modifiers    *string
	gross, cash  *float64
}

func (it *item) row(ccn string, c code) core.HospitalRateRow {
	return core.HospitalRateRow{
		CCN: ccn, CodeType: c.typ, Code: c.code,
		Setting: it.setting, BillingClass: it.billingClass, Modifiers: it.modifiers,
		GrossCharge: it.gross, CashPrice: it.cash,
	}
}

// payerPrice is one kept payer and plan's rate for an item.
type payerPrice struct {
	payer, plan *string
	dollar, pct *float64
	methodology *string
}

// pairKey is a payer and plan, for counting the distinct ones.
type pairKey [2]string

func pair(payer, plan *string) pairKey { return pairKey{deref(payer), deref(plan)} }

func deref(s *string) string {
	if s == nil {
		return ""
	}
	return *s
}

// emitter turns items into rows for one hospital, and counts what it wrote.
type emitter struct {
	ccn  string
	w    *core.RowWriter[core.HospitalRateRow]
	rows int
	// repeats counts the rows skipped because the file had already given them.
	repeats int
	// kept is every payer and plan that wrote a row, and seen every one the
	// file named, kept or not.
	kept, seen map[pairKey]bool
	// written holds a hash of every row written, so a row the file gives again
	// is skipped.
	written map[[16]byte]struct{}
	h       hash.Hash
	buf     []byte
}

func newEmitter(ccn string, w *core.RowWriter[core.HospitalRateRow]) *emitter {
	return &emitter{ccn: ccn, w: w, kept: map[pairKey]bool{}, seen: map[pairKey]bool{},
		written: map[[16]byte]struct{}{}, h: fnv.New128a()}
}

// saw counts a payer and plan the file names.
func (e *emitter) saw(payer, plan string) {
	k := pairKey{payer, plan}
	if !e.seen[k] {
		e.seen[pairKey{strings.Clone(payer), strings.Clone(plan)}] = true
	}
}

// emit writes an item's rows: one per kept code for each payer price, or,
// when it has none, one charge-only row per kept code carrying its gross
// charge and cash price.
func (e *emitter) emit(it *item, prices []payerPrice) error {
	if len(it.codes) == 0 {
		return nil
	}
	for _, p := range prices {
		for _, c := range it.codes {
			r := it.row(e.ccn, c)
			r.Payer, r.Plan = p.payer, p.plan
			r.NegotiatedDollar, r.NegotiatedPercentage, r.Methodology = p.dollar, p.pct, p.methodology
			if err := e.add(r); err != nil {
				return err
			}
		}
		if k := pair(p.payer, p.plan); !e.kept[k] {
			e.kept[pairKey{strings.Clone(k[0]), strings.Clone(k[1])}] = true
		}
	}
	if len(prices) > 0 || (it.gross == nil && it.cash == nil) {
		return nil
	}
	for _, c := range it.codes {
		if err := e.add(it.row(e.ccn, c)); err != nil {
			return err
		}
	}
	return nil
}

// add writes a row unless the file has given the same one before. A tall file
// repeats an item's charges on every payer's line, and many files list one
// code and price under several items, which the rows can't tell apart.
func (e *emitter) add(r core.HospitalRateRow) error {
	key := e.key(r)
	if _, dup := e.written[key]; dup {
		e.repeats++
		return nil
	}
	e.written[key] = struct{}{}
	e.rows++
	return e.w.Add(r)
}

// key is a 128-bit hash of every field of a row but the CCN, which is the
// same for all of them.
func (e *emitter) key(r core.HospitalRateRow) [16]byte {
	b := e.buf[:0]
	str := func(s string) { b = append(binary.AppendUvarint(b, uint64(len(s))), s...) }
	opt := func(s *string) {
		if s == nil {
			b = append(b, 0)
			return
		}
		b = append(b, 1)
		str(*s)
	}
	num := func(f *float64) {
		if f == nil {
			b = append(b, 0)
			return
		}
		b = binary.LittleEndian.AppendUint64(append(b, 1), math.Float64bits(*f))
	}
	str(r.CodeType)
	str(r.Code)
	opt(r.Setting)
	opt(r.BillingClass)
	opt(r.Modifiers)
	opt(r.Payer)
	opt(r.Plan)
	num(r.GrossCharge)
	num(r.CashPrice)
	num(r.NegotiatedDollar)
	num(r.NegotiatedPercentage)
	opt(r.Methodology)
	e.buf = b
	e.h.Reset()
	e.h.Write(b)
	var k [16]byte
	e.h.Sum(k[:0])
	return k
}
