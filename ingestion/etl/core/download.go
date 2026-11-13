package core

import (
	"context"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"sync/atomic"
	"time"
)

// UserAgent names the project on every request DownloadToFile and Get send, so
// a site that refuses anonymous clients can tell who is asking.
const UserAgent = "honest-healthcare (https://github.com/wmespi/honest-healthcare)"

// client gives up on a server that takes a request but never answers it.
// DownloadToFile also gives up on one that stops sending part way, and
// otherwise a download takes as long as it needs.
var client = &http.Client{Transport: func() http.RoundTripper {
	t := http.DefaultTransport.(*http.Transport).Clone()
	t.ResponseHeaderTimeout = 2 * time.Minute
	return t
}()}

// DownloadIdleTimeout is how long DownloadToFile waits for the next byte
// before it gives up on a download.
var DownloadIdleTimeout = 2 * time.Minute

// Get sends a GET with UserAgent and returns the response when it is a 200.
// The caller closes the body.
func Get(url string) (*http.Response, error) {
	return getContext(context.Background(), url)
}

func getContext(ctx context.Context, url string) (*http.Response, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", UserAgent)
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		resp.Body.Close()
		return nil, fmt.Errorf("status %d", resp.StatusCode)
	}
	return resp, nil
}

// DownloadToFile streams url into destPath, logging progress every 10s. It
// fails when no byte arrives for DownloadIdleTimeout, and on any error the
// partial file is removed. Used by discovery (index fallback), nppes (the
// dissemination zip) and hospital-rates (each hospital's price file).
func DownloadToFile(url, destPath string) error {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	resp, err := getContext(ctx, url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	f, err := os.Create(destPath)
	if err != nil {
		return err
	}
	idle := newIdleReader(resp.Body, DownloadIdleTimeout, cancel)
	defer idle.stop()
	pr := NewProgressReader(idle, resp.ContentLength)
	quit := make(chan struct{})
	go func() {
		ticker := time.NewTicker(10 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ticker.C:
				if s := pr.GetProgressString(); s != "" {
					log.Printf("  ⬇️  %s", s)
				}
			case <-quit:
				return
			}
		}
	}()
	_, copyErr := io.Copy(f, pr)
	close(quit)
	if copyErr != nil && idle.stalled.Load() {
		copyErr = fmt.Errorf("no data for %s, so the download was abandoned", idle.timeout)
	}
	if cerr := f.Close(); copyErr == nil {
		copyErr = cerr
	}
	if copyErr != nil {
		os.Remove(destPath)
	}
	return copyErr
}

// idleReader cancels its request when no byte arrives for timeout, which ends
// a body read that would otherwise wait for a stalled server forever.
type idleReader struct {
	r       io.Reader
	timeout time.Duration
	timer   *time.Timer
	stalled atomic.Bool
}

func newIdleReader(r io.Reader, timeout time.Duration, cancel context.CancelFunc) *idleReader {
	ir := &idleReader{r: r, timeout: timeout}
	ir.timer = time.AfterFunc(timeout, func() {
		ir.stalled.Store(true)
		cancel()
	})
	return ir
}

func (ir *idleReader) Read(p []byte) (int, error) {
	n, err := ir.r.Read(p)
	if n > 0 && !ir.stalled.Load() {
		ir.timer.Reset(ir.timeout)
	}
	return n, err
}

func (ir *idleReader) stop() { ir.timer.Stop() }
