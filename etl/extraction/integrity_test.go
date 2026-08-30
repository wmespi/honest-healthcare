package extraction

import (
	"compress/gzip"
	"io"
	"strings"
	"testing"

	"github.com/wmespi/honest-healthcare/etl/core"
)

func TestValidateCompletenessRejectsShortRead(t *testing.T) {
	pr := core.NewProgressReader(strings.NewReader("short"), 10)
	_, _ = io.Copy(io.Discard, pr)
	if err := validateCompleteness(pr, 10, nil, &mrfResult{PriceRows: 1}); err == nil || !strings.Contains(err.Error(), "short read") {
		t.Fatalf("validateCompleteness() error = %v, want short read", err)
	}
}

func TestValidateCompletenessRejectsGzipTrailerError(t *testing.T) {
	var compressed strings.Builder
	zw := gzip.NewWriter(&compressed)
	_, _ = zw.Write([]byte(`{"in_network":[]}`))
	if err := zw.Close(); err != nil {
		t.Fatal(err)
	}
	truncated := compressed.String()[:compressed.Len()-1]
	gr, err := gzip.NewReader(strings.NewReader(truncated))
	if err != nil {
		t.Fatal(err)
	}
	_, readErr := io.Copy(io.Discard, gr)
	pr := core.NewProgressReader(strings.NewReader(truncated), int64(len(truncated)))
	_, _ = io.Copy(io.Discard, pr)
	gzipErr := gr.Close()
	if gzipErr == nil {
		gzipErr = readErr
	}
	if err := validateCompleteness(pr, int64(len(truncated)), gzipErr, &mrfResult{PriceRows: 1}); err == nil || !strings.Contains(err.Error(), "invalid gzip") {
		t.Fatalf("validateCompleteness() error = %v, want invalid gzip", err)
	}
}

func TestStreamMRFRejectsTruncatedInNetworkObject(t *testing.T) {
	_, err := streamMRF(strings.NewReader(`{"provider_references":[],"in_network":[{"billing_code":"99213"`), "", 1, false, map[string]bool{}, map[int64]string{}, map[string]bool{}, nil, nil, mrfWriters{}, nil)
	if err == nil || !strings.Contains(err.Error(), "decode in_network item") {
		t.Fatalf("streamMRF() error = %v, want truncated item error", err)
	}
}
