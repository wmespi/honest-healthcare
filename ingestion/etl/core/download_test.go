package core

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// useIdleTimeout shortens DownloadIdleTimeout for one test.
func useIdleTimeout(t *testing.T, d time.Duration) {
	t.Helper()
	old := DownloadIdleTimeout
	DownloadIdleTimeout = d
	t.Cleanup(func() { DownloadIdleTimeout = old })
}

// A server that starts answering and then stops fails the download once
// nothing has arrived for the idle timeout, and leaves no partial file.
func TestDownloadToFile_StalledServerFails(t *testing.T) {
	useIdleTimeout(t, 200*time.Millisecond)
	release := make(chan struct{})
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("the first part of the file"))
		w.(http.Flusher).Flush()
		select {
		case <-r.Context().Done():
		case <-release:
		}
	}))
	defer srv.Close()
	defer close(release)

	dest := filepath.Join(t.TempDir(), "file.download")
	start := time.Now()
	err := DownloadToFile(srv.URL, dest)
	if err == nil || !strings.Contains(err.Error(), "no data for 200ms") {
		t.Fatalf("err = %v, want the stall named", err)
	}
	if took := time.Since(start); took > 5*time.Second {
		t.Errorf("the stalled download took %s to fail", took)
	}
	if _, err := os.Stat(dest); !os.IsNotExist(err) {
		t.Errorf("the partial file was left: %v", err)
	}
}

// The timeout is on silence, not on the whole download: a server that keeps
// sending, however slowly, finishes past it.
func TestDownloadToFile_SlowServerFinishes(t *testing.T) {
	useIdleTimeout(t, 200*time.Millisecond)
	var ua string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ua = r.Header.Get("User-Agent")
		for i := 0; i < 8; i++ {
			w.Write([]byte("part "))
			w.(http.Flusher).Flush()
			time.Sleep(75 * time.Millisecond)
		}
	}))
	defer srv.Close()

	dest := filepath.Join(t.TempDir(), "file.download")
	if err := DownloadToFile(srv.URL, dest); err != nil {
		t.Fatal(err)
	}
	if b, _ := os.ReadFile(dest); string(b) != strings.Repeat("part ", 8) {
		t.Errorf("downloaded %q", b)
	}
	if ua != UserAgent {
		t.Errorf("User-Agent = %q", ua)
	}
}

func TestDownloadToFile_StatusFails(t *testing.T) {
	srv := httptest.NewServer(http.NotFoundHandler())
	defer srv.Close()
	dest := filepath.Join(t.TempDir(), "file.download")
	if err := DownloadToFile(srv.URL, dest); err == nil || !strings.Contains(err.Error(), "status 404") {
		t.Fatalf("err = %v", err)
	}
	if _, err := os.Stat(dest); !os.IsNotExist(err) {
		t.Errorf("a failed request left a file: %v", err)
	}
}
