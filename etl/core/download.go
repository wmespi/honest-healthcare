package core

import (
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"time"
)

// DownloadToFile streams url into destPath, logging progress every 10s. On any
// error the partial file is removed. Used by discovery (index fallback) and
// nppes (the dissemination zip).
func DownloadToFile(url, destPath string) error {
	resp, err := (&http.Client{}).Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return fmt.Errorf("status %d", resp.StatusCode)
	}
	f, err := os.Create(destPath)
	if err != nil {
		return err
	}
	pr := NewProgressReader(resp.Body, resp.ContentLength)
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
	f.Close()
	if copyErr != nil {
		os.Remove(destPath)
	}
	return copyErr
}
