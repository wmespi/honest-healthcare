package main

import (
	"compress/gzip"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

func getFileSize(url string) int64 {
	client := &http.Client{Timeout: 5 * time.Second}
	req, err := http.NewRequest("HEAD", url, nil)
	if err != nil {
		return 0
	}
	resp, err := client.Do(req)
	if err != nil || resp.StatusCode != 200 {
		return 0
	}
	return resp.ContentLength
}

func discoverLinks(limit int) {
	log.Println("🚀 Starting Anthem Bronze Layer: Index Discovery (Go)...")

	req, err := http.NewRequest("GET", IndexURL, nil)
	if err != nil {
		log.Fatalf("❌ Failed to create request: %v", err)
	}

	client := &http.Client{} // No timeout for massive streams
	resp, err := client.Do(req)
	if err != nil {
		log.Fatalf("❌ Failed to fetch index: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		log.Fatalf("❌ Failed to fetch index, status code: %d", resp.StatusCode)
	}

	pr := NewProgressReader(resp.Body, resp.ContentLength)

	gz, err := gzip.NewReader(pr)
	if err != nil {
		log.Fatalf("❌ Failed to unzip: %v", err)
	}
	defer gz.Close()

	decoder := json.NewDecoder(gz)
	var candidates []CandidateFile
	seenUrls := make(map[string]bool)
	count := 0

	// Navigate to "reporting_structure" array
	for {
		t, err := decoder.Token()
		if err != nil {
			log.Fatalf("❌ Error scanning index JSON: %v", err)
		}
		if key, ok := t.(string); ok && key == "reporting_structure" {
			break
		}
	}

	// Read open bracket
	decoder.Token()

	for decoder.More() {
		count++
		var rs ReportingStructure
		if err := decoder.Decode(&rs); err != nil {
			log.Printf("⚠️ Decode error: %v", err)
			continue
		}

		var plans []string
		for _, p := range rs.ReportingPlans {
			plans = append(plans, p.PlanName)
		}

		for _, f := range rs.InNetworkFiles {
			if f.Location == "" || seenUrls[f.Location] {
				continue
			}
			if strings.Contains(f.Location, "in-network-rates") || strings.Contains(f.Location, "negotiated-rates") {
				candidates = append(candidates, CandidateFile{
					Description: f.Description,
					Location:    f.Location,
					PlanNames:   plans,
				})
				seenUrls[f.Location] = true
			}
		}

		if count%1000 == 0 {
			log.Printf("  Scanned %d reporting structures... Found %d candidates. %s", count, len(candidates), pr.GetProgressString())
		}
		if limit > 0 && count >= limit {
			log.Printf("🛑 Limit of %d reached.", limit)
			break
		}
	}

	log.Printf("📡 Fetching file sizes for %d URLs (Concurrent)...", len(candidates))

	var wg sync.WaitGroup
	sem := make(chan struct{}, 20) // 20 concurrent workers
	
	for i := range candidates {
		wg.Add(1)
		sem <- struct{}{}
		go func(idx int) {
			defer wg.Done()
			defer func() { <-sem }()
			candidates[idx].FileSizeBytes = getFileSize(candidates[idx].Location)
		}(i)
	}
	wg.Wait()

	os.MkdirAll(filepath.Dir(OutputPath), os.ModePerm)
	file, _ := os.Create(OutputPath)
	defer file.Close()
	json.NewEncoder(file).Encode(candidates)

	log.Printf("✅ Discovery complete! Found %d unique rate file URLs.", len(candidates))
	log.Printf("📂 Saved to %s", OutputPath)
}
