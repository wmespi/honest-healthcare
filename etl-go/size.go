package main

import (
	"context"
	"fmt"
	"log"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/jackc/pgx/v5"
)

const (
	defaultSizeConcurrency = 50
	sizeMaxRetries         = 3
	sizeRetryBackoff       = time.Second
)

func sizeProgressString(done, total int, start time.Time) string {
	if total <= 0 || done <= 0 {
		return fmt.Sprintf("0 / %d", total)
	}
	percent := float64(done) / float64(total) * 100
	elapsed := time.Since(start).Seconds()
	rate := float64(done) / elapsed
	eta := time.Duration(float64(total-done)/rate) * time.Second

	loc, _ := time.LoadLocation("America/New_York")
	completionTime := time.Now().Add(eta).In(loc).Format("3:04 PM MST")

	const barLen = 15
	filled := int(float64(barLen) * (percent / 100))
	if filled > barLen {
		filled = barLen
	}
	bar := strings.Repeat("█", filled) + strings.Repeat("░", barLen-filled)

	return fmt.Sprintf("[%s] %5.1f%% (%d/%d) | ETA: %v (%s)",
		bar, percent, done, total, eta.Round(time.Second), completionTime)
}

func headWithRetry(client *http.Client, id int, url string) (int64, error) {
	var lastErr error
	for attempt := 1; attempt <= sizeMaxRetries; attempt++ {
		req, err := http.NewRequest("HEAD", url, nil)
		if err != nil {
			return 0, err
		}
		resp, err := client.Do(req)
		if err != nil {
			lastErr = err
			if attempt < sizeMaxRetries {
				time.Sleep(sizeRetryBackoff * time.Duration(attempt))
			}
			continue
		}
		resp.Body.Close()
		return resp.ContentLength, nil
	}
	return 0, lastErr
}

func fetchFileSizes(ctx context.Context, conn *pgx.Conn, concurrency int) {
	if concurrency <= 0 {
		concurrency = defaultSizeConcurrency
	}

	type job struct {
		id       int
		location string
	}
	type result struct {
		id   int
		size int64
	}
	type failure struct {
		id  int
		err error
	}

	rows, err := conn.Query(ctx, `SELECT id, location FROM index_files ORDER BY id`)
	if err != nil {
		log.Fatalf("❌ Failed to query unsized files: %v", err)
	}
	var jobs []job
	for rows.Next() {
		var j job
		if err := rows.Scan(&j.id, &j.location); err != nil {
			log.Printf("⚠️ Scan error: %v", err)
			continue
		}
		jobs = append(jobs, j)
	}
	rows.Close()

	if len(jobs) == 0 {
		log.Println("✅ All files already have file_size_bytes set.")
		return
	}

	total := len(jobs)
	log.Printf("🔍 Fetching sizes for %d files (concurrency=%d)...", total, concurrency)

	jobCh := make(chan job, concurrency)
	resultCh := make(chan result, concurrency)
	failCh := make(chan failure, concurrency)

	var wg sync.WaitGroup
	for i := 0; i < concurrency; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			client := &http.Client{Timeout: 30 * time.Second}
			for j := range jobCh {
				size, err := headWithRetry(client, j.id, j.location)
				if err != nil {
					failCh <- failure{id: j.id, err: err}
					continue
				}
				if size > 0 {
					resultCh <- result{id: j.id, size: size}
				}
			}
		}()
	}

	go func() {
		wg.Wait()
		close(resultCh)
		close(failCh)
	}()

	go func() {
		for _, j := range jobs {
			jobCh <- j
		}
		close(jobCh)
	}()

	// Single writer — pgx.Conn is not goroutine-safe.
	// Merge resultCh and failCh via a fan-in so we can drain both to completion.
	type either struct {
		r *result
		f *failure
	}
	merged := make(chan either, concurrency)
	var fanWg sync.WaitGroup
	fanWg.Add(2)
	go func() {
		defer fanWg.Done()
		for r := range resultCh {
			rCopy := r
			merged <- either{r: &rCopy}
		}
	}()
	go func() {
		defer fanWg.Done()
		for f := range failCh {
			fCopy := f
			merged <- either{f: &fCopy}
		}
	}()
	go func() {
		fanWg.Wait()
		close(merged)
	}()

	var (
		done     int
		failures []failure
		start    = time.Now()
	)

	for e := range merged {
		if e.f != nil {
			failures = append(failures, *e.f)
		} else {
			if _, err := conn.Exec(ctx,
				`UPDATE index_files SET file_size_bytes = $1 WHERE id = $2`,
				e.r.size, e.r.id,
			); err != nil {
				log.Printf("⚠️ DB update failed id=%d: %v", e.r.id, err)
			}
		}
		done++
		if done%100 == 0 || done == total {
			log.Printf("  %s", sizeProgressString(done, total, start))
		}
	}

	log.Printf("✅ Done in %v. Sized %d / %d files.", time.Since(start).Round(time.Second), done-len(failures), total)

	if len(failures) > 0 {
		log.Printf("⚠️ %d file(s) failed after %d retries:", len(failures), sizeMaxRetries)
		for _, f := range failures {
			log.Printf("    id=%-6d  %v", f.id, f.err)
		}
	}
}
