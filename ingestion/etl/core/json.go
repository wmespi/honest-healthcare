package core

import "encoding/json"

// SkipJSONValue consumes and discards exactly one JSON value (scalar, object, or
// array) from dec. Used by the discovery and extraction streamers to walk past
// keys they do not care about without buffering.
func SkipJSONValue(dec *json.Decoder) {
	t, err := dec.Token()
	if err != nil {
		return
	}
	if delim, ok := t.(json.Delim); ok {
		if delim == '{' || delim == '[' {
			depth := 1
			for depth > 0 {
				t, err := dec.Token()
				if err != nil {
					return
				}
				if d, ok := t.(json.Delim); ok {
					if d == '{' || d == '[' {
						depth++
					} else if d == '}' || d == ']' {
						depth--
					}
				}
			}
		}
	}
}
