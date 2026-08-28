package core

import (
	"fmt"
	"io"
	"strings"
	"sync/atomic"
	"time"
)

// ProgressReader wraps an io.Reader to track download progress and ETA without blocking.
type ProgressReader struct {
	Reader     io.Reader
	TotalBytes int64
	ReadBytes  atomic.Int64
	StartTime  time.Time
	done       chan struct{}
}

// NewProgressReader creates a new tracker.
func NewProgressReader(r io.Reader, total int64) *ProgressReader {
	return &ProgressReader{
		Reader:     r,
		TotalBytes: total,
		StartTime:  time.Now(),
	}
}

// Read intercepts the stream and counts bytes.
func (pr *ProgressReader) Read(p []byte) (int, error) {
	n, err := pr.Reader.Read(p)
	if n > 0 {
		pr.ReadBytes.Add(int64(n))
	}
	return n, err
}

// GetProgressString returns a formatted string of the current progress.
func (pr *ProgressReader) GetProgressString() string {
	current := pr.ReadBytes.Load()
	if pr.TotalBytes <= 0 || current <= 0 {
		return ""
	}

	percent := float64(current) / float64(pr.TotalBytes) * 100
	elapsed := time.Since(pr.StartTime).Seconds()
	bytesPerSec := float64(current) / elapsed
	remainingBytes := float64(pr.TotalBytes - current)
	etaSeconds := remainingBytes / bytesPerSec
	eta := time.Duration(etaSeconds) * time.Second

	loc, _ := time.LoadLocation("America/New_York")
	completionTime := time.Now().Add(eta).In(loc).Format("3:04 PM MST")

	barLen := 15
	filled := int(float64(barLen) * (percent / 100))
	if filled > barLen {
		filled = barLen
	}
	bar := strings.Repeat("█", filled) + strings.Repeat("░", barLen-filled)

	return fmt.Sprintf("[%s] %5.1f%% | ETA: %v (%s)", bar, percent, eta.Round(time.Second), completionTime)
}
