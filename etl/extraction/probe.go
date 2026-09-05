package extraction

// The provider probe — the gate that decides, from the cheap front of a file,
// whether the expensive rest of it is worth downloading.
//
// Anthem writes `provider_references` before `in_network`, so by the time that
// first section closes the parser already knows every provider group the file
// prices and every network label it carries — typically inside the first few MB
// of a body that can run to gigabytes. If neither signal points at a plan we are
// pricing, the request is cancelled there and the file is marked `skipped`
// instead of streamed to the end and rolled back.
//
// Two signals, because one is not enough (issue #98):
//
//   - **provider overlap** — how many provider groups survived the GA NPPES NPI
//     filter. Zero means the file prices nobody we can serve.
//   - **network label** — whether any `provider_references[].network_name`
//     matches a `network_patterns` entry of a target plan in targets.yaml. This
//     is the signal that catches a national BlueCard-mirror shard: it *does*
//     list Georgia NPIs (so overlap alone passes it) but carries no
//     Blue-Value-labelled network, and every one of its rows would be discarded
//     downstream. ~40 GB of the store was downloaded and rolled back for exactly
//     that reason.
//
// Either signal failing aborts the file. Neither is configured only when the
// caller asked for that (`-min-groups 0`, or `-targets ""` / a target list with
// no `network_patterns`).

import (
	"errors"
	"fmt"
	"sort"
	"strings"
)

// errNoWantedProviders is the sentinel every probe abort wraps. parseRates tests
// for it with errors.Is to tell "this file is not for us" (→ `skipped`) apart
// from "this file broke" (→ `failed`).
var errNoWantedProviders = errors.New("no wanted providers")

// Probe signals, carried on probeAbortError.Signal. The end-of-run guard
// (extraction.go) needs to tell a network-label miss apart from a plain overlap
// miss: every target file missing on the *network* signal, with none completing,
// is how a stale targets.yaml network_patterns shows up.
const (
	signalOverlap = "overlap"
	signalNetwork = "network"
)

// probeAbortError is what check() returns when a file should be abandoned. It
// satisfies errors.Is(err, errNoWantedProviders) so the existing skip/fail
// branch is unchanged, and carries Signal so Run can classify the abort.
type probeAbortError struct {
	Signal string // signalOverlap | signalNetwork
	detail string
}

func (e *probeAbortError) Error() string {
	// Lands in index_files.failure_reason — the "probe: no wanted providers"
	// prefix is stable and queried on (queue.md, the end-of-run guard).
	return "probe: no wanted providers — " + e.detail
}

func (e *probeAbortError) Is(target error) bool { return target == errNoWantedProviders }

// probeAbort builds the abort error for one signal.
func probeAbort(signal, format string, args ...any) error {
	return &probeAbortError{Signal: signal, detail: fmt.Sprintf(format, args...)}
}

// providerProbe is the configured gate. The zero value is inactive.
type providerProbe struct {
	// minGroups is how many provider groups must survive the provider filter
	// for the file to be worth streaming. 0 disables this signal.
	minGroups int
	// networkMatch reports whether one provider group's (possibly "|"-joined)
	// network_name matches a target plan's network_patterns. nil disables this
	// signal.
	networkMatch func(networkName string) bool
	// networkSpec is networkMatch's patterns, for logs and the abort message.
	networkSpec string
}

func (p providerProbe) active() bool { return p.minGroups > 0 || p.networkMatch != nil }

// probeReading is what the parser measured while streaming provider_references.
type probeReading struct {
	// KeptGroups is the number of distinct provider groups that survived the
	// provider filter (every group in the file when the filter is off).
	KeptGroups int
	// Refs is the raw provider_references entry count, before any filter.
	Refs int64
	// NetworkHit is true once any group's network_name matched.
	NetworkHit bool
	// NetworkSample is a capped sample of the labels actually seen, so an abort
	// says what the file *does* carry rather than only what it lacks.
	NetworkSample map[string]struct{}
}

// networkSampleCap bounds the labels kept for the abort message. A file has a
// handful of distinct networks; the cap is only there so a pathological one
// cannot grow the set with the stream.
const networkSampleCap = 8

// note records a network label for the abort message, up to the cap.
func (r *probeReading) note(name string) {
	if name == "" || len(r.NetworkSample) >= networkSampleCap {
		return
	}
	if r.NetworkSample == nil {
		r.NetworkSample = map[string]struct{}{}
	}
	r.NetworkSample[name] = struct{}{}
}

// check returns a *probeAbortError (which is errNoWantedProviders, with the
// numbers and the signal that fired) when the file should be abandoned, or nil
// to carry on into in_network.
func (p providerProbe) check(r probeReading) error {
	if p.minGroups > 0 && r.KeptGroups < p.minGroups {
		return probeAbort(signalOverlap, "%d of %d provider groups carry a wanted provider (need %d)",
			r.KeptGroups, r.Refs, p.minGroups)
	}
	if p.networkMatch != nil && !r.NetworkHit {
		return probeAbort(signalNetwork, "no network_name in %d provider groups matches {%s} — file carries {%s}",
			r.KeptGroups, p.networkSpec, sampleList(r.NetworkSample))
	}
	return nil
}

// sampleList renders a probeReading's network sample for the abort message.
func sampleList(sample map[string]struct{}) string {
	if len(sample) == 0 {
		return "no network labels"
	}
	names := make([]string, 0, len(sample))
	for n := range sample {
		names = append(names, n)
	}
	sort.Strings(names)
	out := strings.Join(names, ", ")
	if len(names) == networkSampleCap {
		out += ", …"
	}
	return out
}
