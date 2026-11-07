package extraction

import (
	"bytes"
	"compress/gzip"
	"errors"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/wmespi/honest-healthcare/ingestion/etl/core"
)

// synthetic returns the committed uncompressed MRF fixture as a string.
func synthetic(t *testing.T) string {
	t.Helper()
	b, err := os.ReadFile("testdata/synthetic_mrf.json")
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

// streamString runs streamMRF over s with the schema path on (first-file mode).
func streamString(s string) (*mrfResult, error) {
	return streamMRF(strings.NewReader(s), 1, true, nil, providerProbe{}, mrfWriters{}, nil)
}

// A clean document parses and reports its raw section counts.
func TestStreamMRF_CompleteDocumentSignals(t *testing.T) {
	res, err := streamString(synthetic(t))
	if err != nil {
		t.Fatalf("streamMRF: %v", err)
	}
	if res.InNetworkItems == 0 {
		t.Error("InNetworkItems = 0 on a document with in_network entries")
	}
	if res.ProviderRefs == 0 {
		t.Error("ProviderRefs = 0 on a document with provider_references")
	}
}

// Cutting the stream part-way through in_network must fail, not return a
// partial result that then gets promoted (issue #52).
func TestStreamMRF_TruncatedMidInNetwork(t *testing.T) {
	full := synthetic(t)
	cut := full[:strings.Index(full, "in_network")+400]
	_, err := streamString(cut)
	if err == nil {
		t.Fatal("truncated in_network parsed without error")
	}
	if !strings.Contains(err.Error(), "malformed MRF") {
		t.Errorf("error = %q, want it to mention 'malformed MRF'", err)
	}
}

// The document body is all there but the final brace never arrives.
func TestStreamMRF_MissingClosingBrace(t *testing.T) {
	full := strings.TrimRight(synthetic(t), " \n\t")
	if !strings.HasSuffix(full, "}") {
		t.Fatalf("fixture does not end in '}': %q", full[len(full)-10:])
	}
	_, err := streamString(full[:len(full)-1])
	if err == nil || !strings.Contains(err.Error(), "not closed") {
		t.Fatalf("error = %v, want 'not closed'", err)
	}
}

// An empty object, or an HTTP error page served with a 200, has neither section.
func TestStreamMRF_EmptyDocument(t *testing.T) {
	for _, doc := range []string{`{}`, `{"error":"Access Denied"}`} {
		_, err := streamString(doc)
		if err == nil || !strings.Contains(err.Error(), "no in_network or provider_references") {
			t.Errorf("doc %q: error = %v, want 'no in_network or provider_references'", doc, err)
		}
	}
}

// A structurally invalid in_network entry is a failure, not a skipped item.
func TestStreamMRF_MalformedInNetworkItem(t *testing.T) {
	_, err := streamString(`{"provider_references":[],"in_network":[{"billing_code": 123}]}`)
	if err == nil || !strings.Contains(err.Error(), "malformed MRF: in_network[0]") {
		t.Fatalf("error = %v, want 'malformed MRF: in_network[0]'", err)
	}
}

// in_network before provider_references can't be attributed to networks in one
// pass, so the file fails instead of landing every rate under no network.
func TestStreamMRF_InNetworkBeforeProviderReferences(t *testing.T) {
	for _, doc := range []string{
		`{"in_network":[],"provider_references":[]}`,
		`{"in_network":[]}`, // no provider_references at all, such as inline provider_groups
	} {
		if _, err := streamString(doc); !errors.Is(err, errInNetworkFirst) {
			t.Errorf("doc %s: error = %v, want errInNetworkFirst", doc, err)
		}
	}
}

// A sink that can't write stops the stream with that error, so the file fails
// instead of being promoted short. Each sink is checked on its own.
func TestStreamMRF_WriteErrorStopsTheStream(t *testing.T) {
	diskFull := errors.New("no space left on device")
	for name, w := range map[string]mrfWriters{
		"providers":  {providers: func([]core.ProviderRow) error { return diskFull }},
		"prices":     {prices: func([]core.PriceRow) error { return diskFull }},
		"group_sets": {groupSetMembers: func([]core.GroupSetMemberRow) error { return diskFull }},
		"codes":      {code: func(core.BillingCodeRow) error { return diskFull }},
	} {
		_, err := streamMRF(strings.NewReader(synthetic(t)), 1, false, nil, providerProbe{}, w, nil)
		if !errors.Is(err, diskFull) || !strings.Contains(err.Error(), "write "+name) {
			t.Errorf("%s sink failing: error = %v, want 'write %s: %v'", name, err, name, diskFull)
		}
	}
}

// gz builds a gzip stream from payload.
func gz(t *testing.T, payload []byte) []byte {
	t.Helper()
	var buf bytes.Buffer
	w := gzip.NewWriter(&buf)
	if _, err := w.Write(payload); err != nil {
		t.Fatal(err)
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
}

// validateStreamComplete over a whole, well-formed gzip stream is a no-op.
func TestValidateStreamComplete_OK(t *testing.T) {
	full := gz(t, []byte(synthetic(t)))
	pr := core.NewProgressReader(bytes.NewReader(full), int64(len(full)))
	r, err := gzip.NewReader(pr)
	if err != nil {
		t.Fatal(err)
	}
	if err := validateStreamComplete(r, pr, int64(len(full))); err != nil {
		t.Errorf("validateStreamComplete on a complete stream: %v", err)
	}
}

// A body cut short of its advertised Content-Length is a retryable short read.
func TestValidateStreamComplete_ShortRead(t *testing.T) {
	full := gz(t, []byte(synthetic(t)))
	short := full[:len(full)-30]
	pr := core.NewProgressReader(bytes.NewReader(short), int64(len(full)))
	r, err := gzip.NewReader(pr)
	if err != nil {
		t.Fatal(err)
	}
	err = validateStreamComplete(r, pr, int64(len(full)))
	if err == nil || !strings.Contains(err.Error(), "short read") {
		t.Fatalf("error = %v, want 'short read'", err)
	}
	if strings.Contains(err.Error(), "gzip") {
		t.Errorf("short read misclassified as gzip corruption (would be kept failed): %v", err)
	}
}

// All the bytes arrive but the gzip CRC-32 trailer is wrong — genuine
// corruption, kept failed.
func TestValidateStreamComplete_CorruptTrailer(t *testing.T) {
	full := gz(t, []byte(synthetic(t)))
	corrupt := append([]byte(nil), full...)
	corrupt[len(corrupt)-6] ^= 0xff // flip a byte in the CRC-32 / ISIZE trailer
	pr := core.NewProgressReader(bytes.NewReader(corrupt), int64(len(corrupt)))
	r, err := gzip.NewReader(pr)
	if err != nil {
		t.Fatal(err)
	}
	err = validateStreamComplete(r, pr, int64(len(corrupt)))
	if err == nil || !strings.Contains(err.Error(), "corrupt gzip") {
		t.Fatalf("error = %v, want 'corrupt gzip'", err)
	}
}

// A download that stops delivering bytes is aborted after stallTimeout.
func TestWatchStall_AbortsIdleDownload(t *testing.T) {
	pr := core.NewProgressReader(strings.NewReader(""), 100)
	var cancelled atomic.Bool
	stop, stalled := watchStall(func() { cancelled.Store(true) }, pr, 150*time.Millisecond)
	defer stop()

	deadline := time.After(3 * time.Second)
	for {
		if stalled() && cancelled.Load() {
			return
		}
		select {
		case <-deadline:
			t.Fatal("watchStall never aborted an idle download")
		case <-time.After(10 * time.Millisecond):
		}
	}
}

// A download that keeps moving is left alone even past the timeout.
func TestWatchStall_LeavesActiveDownloadAlone(t *testing.T) {
	pr := core.NewProgressReader(strings.NewReader(""), 0)
	var cancelled atomic.Bool
	stop, stalled := watchStall(func() { cancelled.Store(true) }, pr, 150*time.Millisecond)
	defer stop()

	for i := 0; i < 15; i++ {
		pr.ReadBytes.Add(4096)
		time.Sleep(20 * time.Millisecond)
	}
	if stalled() || cancelled.Load() {
		t.Error("watchStall aborted a download that was still making progress")
	}
}
