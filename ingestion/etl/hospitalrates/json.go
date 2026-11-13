package hospitalrates

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strconv"

	"github.com/wmespi/honest-healthcare/ingestion/etl/core"
)

// flexString is a JSON string or number as its text; anything else is "".
type flexString string

func (s *flexString) UnmarshalJSON(b []byte) error {
	b = bytes.TrimSpace(b)
	if len(b) == 0 {
		return nil
	}
	switch {
	case b[0] == '"':
		var v string
		if json.Unmarshal(b, &v) == nil {
			*s = flexString(v)
		}
	case b[0] == '-' || isDigit(b[0]):
		*s = flexString(b)
	}
	return nil
}

// flexStrings is a JSON array of strings or numbers, or one of them alone.
type flexStrings []string

func (s *flexStrings) UnmarshalJSON(b []byte) error {
	b = bytes.TrimSpace(b)
	if len(b) == 0 {
		return nil
	}
	if b[0] == '[' {
		var vs []flexString
		if json.Unmarshal(b, &vs) == nil {
			for _, v := range vs {
				*s = append(*s, string(v))
			}
		}
		return nil
	}
	var v flexString
	_ = v.UnmarshalJSON(b)
	if v != "" {
		*s = flexStrings{string(v)}
	}
	return nil
}

// flexNum is a JSON number, or a string parseNumber reads; anything else is null.
type flexNum struct{ v *float64 }

func (n *flexNum) UnmarshalJSON(b []byte) error {
	b = bytes.TrimSpace(b)
	if len(b) == 0 {
		return nil
	}
	switch {
	case b[0] == '"':
		var v string
		if json.Unmarshal(b, &v) == nil {
			n.v = parseNumber(v)
		}
	case b[0] == '-' || isDigit(b[0]):
		if f, err := strconv.ParseFloat(string(b), 64); err == nil {
			n.v = &f
		}
	}
	return nil
}

// jsonItem is one entry of standard_charge_information, in the CMS JSON
// template (v2 and v3), with only the fields the extractor reads.
type jsonItem struct {
	CodeInformation []struct {
		Code flexString `json:"code"`
		Type flexString `json:"type"`
	} `json:"code_information"`
	StandardCharges []struct {
		Setting      flexString  `json:"setting"`
		BillingClass flexString  `json:"billing_class"`
		Gross        flexNum     `json:"gross_charge"`
		Cash         flexNum     `json:"discounted_cash"`
		ModifierCode flexStrings `json:"modifier_code"`
		Modifiers    flexStrings `json:"modifiers"`
		Payers       []struct {
			Payer       flexString `json:"payer_name"`
			Plan        flexString `json:"plan_name"`
			Dollar      flexNum    `json:"standard_charge_dollar"`
			Pct         flexNum    `json:"standard_charge_percentage"`
			Methodology flexString `json:"methodology"`
		} `json:"payers_information"`
	} `json:"standard_charges"`
}

// extractJSON streams a JSON price file into e: it walks the top-level object
// past every key but standard_charge_information, and decodes that array one
// item at a time.
func extractJSON(r io.Reader, e *emitter, keep func(string) bool) error {
	dec := json.NewDecoder(r)
	if t, err := dec.Token(); err != nil || t != json.Delim('{') {
		return fmt.Errorf("the file is not a JSON object")
	}
	found := false
	for dec.More() {
		t, err := dec.Token()
		if err != nil {
			return fmt.Errorf("read JSON key: %w", err)
		}
		if key, _ := t.(string); key != "standard_charge_information" || found {
			core.SkipJSONValue(dec)
			continue
		}
		found = true
		if err := decodeItems(dec, e, keep); err != nil {
			return err
		}
	}
	if !found {
		return errors.New("no standard_charge_information in the file")
	}
	return nil
}

func decodeItems(dec *json.Decoder, e *emitter, keep func(string) bool) error {
	if t, err := dec.Token(); err != nil || t != json.Delim('[') {
		return errors.New("standard_charge_information is not an array")
	}
	keepPayer := map[string]bool{}
	var prices []payerPrice
	for dec.More() {
		var ji jsonItem
		if err := dec.Decode(&ji); err != nil {
			// A field of the wrong type is skipped and the rest of the item
			// read; anything else leaves the stream unreadable.
			var typeErr *json.UnmarshalTypeError
			if !errors.As(err, &typeErr) {
				return fmt.Errorf("read standard_charge_information: %w", err)
			}
		}
		var codes []code
		for _, ci := range ji.CodeInformation {
			codes = addCode(codes, string(ci.Type), string(ci.Code))
		}
		for _, sc := range ji.StandardCharges {
			prices = prices[:0]
			for _, p := range sc.Payers {
				payer := text(string(p.Payer))
				if payer == nil {
					continue
				}
				plan := text(string(p.Plan))
				e.saw(*payer, deref(plan))
				kept, ok := keepPayer[*payer]
				if !ok {
					kept = keep(*payer)
					keepPayer[*payer] = kept
				}
				if kept && (p.Dollar.v != nil || p.Pct.v != nil) {
					prices = append(prices, payerPrice{payer: payer, plan: plan,
						dollar: p.Dollar.v, pct: p.Pct.v, methodology: lower(string(p.Methodology))})
				}
			}
			it := &item{
				codes:        codes,
				setting:      lower(string(sc.Setting)),
				billingClass: lower(string(sc.BillingClass)),
				modifiers:    normModifiers(append(sc.ModifierCode, sc.Modifiers...)...),
				gross:        sc.Gross.v,
				cash:         sc.Cash.v,
			}
			if err := e.emit(it, prices); err != nil {
				return err
			}
		}
	}
	if _, err := dec.Token(); err != nil {
		return fmt.Errorf("read the end of standard_charge_information: %w", err)
	}
	return nil
}
