package main

import (
	"compress/gzip"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
)

// skipValue safely skips a massive JSON value without allocating it into memory.
func skipValue(decoder *json.Decoder) {
	t, err := decoder.Token()
	if err != nil {
		return
	}
	if delim, ok := t.(json.Delim); ok {
		if delim == '{' || delim == '[' {
			depth := 1
			for depth > 0 {
				t, err := decoder.Token()
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

func parseRates(url string, planName string, isFirstFile bool, seenBillingCodes map[string]bool) {
	log.Printf("⚙️ Processing Rate File: %s", url)

	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		log.Fatalf("❌ Failed to create request: %v", err)
	}

	client := &http.Client{}
	resp, err := client.Do(req)
	if err != nil {
		log.Printf("❌ Failed to fetch rate file: %v", err)
		return
	}
	defer resp.Body.Close()

	if resp.StatusCode != 200 {
		log.Printf("❌ Failed to fetch rate file, status: %d", resp.StatusCode)
		return
	}

	pr := NewProgressReader(resp.Body, resp.ContentLength)

	gz, err := gzip.NewReader(pr)
	if err != nil {
		log.Printf("❌ Failed to unzip: %v", err)
		return
	}
	defer gz.Close()

	decoder := json.NewDecoder(gz)
	
	os.MkdirAll(filepath.Dir(ParseOutputPath), os.ModePerm)
	flags := os.O_CREATE | os.O_WRONLY | os.O_APPEND
	
	ratesFile, err := os.OpenFile(ParseOutputPath, flags, os.ModePerm)
	if err != nil {
		log.Fatalf("❌ Failed to open rates CSV file: %v", err)
	}
	defer ratesFile.Close()
	ratesWriter := csv.NewWriter(ratesFile)
	defer ratesWriter.Flush()

	providersFile, err := os.OpenFile(ParseProvidersOutputPath, flags, os.ModePerm)
	if err != nil {
		log.Fatalf("❌ Failed to open providers CSV file: %v", err)
	}
	defer providersFile.Close()
	providersWriter := csv.NewWriter(providersFile)
	defer providersWriter.Flush()

	billingCodesFile, err := os.OpenFile(ParseBillingCodesOutputPath, flags, os.ModePerm)
	if err != nil {
		log.Fatalf("❌ Failed to open billing codes CSV file: %v", err)
	}
	defer billingCodesFile.Close()
	billingCodesWriter := csv.NewWriter(billingCodesFile)
	defer billingCodesWriter.Flush()

	// 1. Write Headers (Added Name and Description to rates)
	if isFirstFile {
		ratesWriter.Write([]string{"provider_group_id", "plan_name", "billing_code_type", "billing_code", "negotiation_arrangement", "negotiated_type", "negotiated_rate", "expiration_date", "service_code"})
		ratesWriter.Flush()
		
		providersWriter.Write([]string{"provider_group_id", "npi", "tin_type", "tin_value"})
		providersWriter.Flush()

		billingCodesWriter.Write([]string{"billing_code_type", "billing_code", "name", "description"})
		billingCodesWriter.Flush()
	}
	
	// ERD Snippet Generator Map
	schemaExample := make(map[string]interface{})

	log.Println("  🔄 Starting Single-Pass Extract...")
	
	t, err := decoder.Token()
	if err != nil {
		log.Printf("❌ Failed to read root token: %v", err)
		return
	}
	if delim, ok := t.(json.Delim); !ok || delim != '{' {
		log.Printf("❌ Expected root JSON object '{', got %v", t)
		return
	}

	for decoder.More() {
		t, err := decoder.Token()
		if err != nil {
			break
		}
		
		key, ok := t.(string)
		if !ok {
			log.Printf("⚠️ Expected string key, got %v", t)
			continue
		}

		if key == "provider_references" {
			log.Println("    🎯 Found 'provider_references' array. Extracting to providers.csv...")
			decoder.Token() // Read '['
			
			count := 0
			for decoder.More() {
				var ref ProviderReference
				
				// Snippet Generator: Capture the very first raw object exactly as it is in the JSON!
				if isFirstFile && schemaExample["provider_references"] == nil {
					var raw map[string]interface{}
					if err := decoder.Decode(&raw); err == nil {
						schemaExample["provider_references"] = []interface{}{raw} // Store exactly 1 item in the array for the example
						b, _ := json.Marshal(raw)
						json.Unmarshal(b, &ref) // Convert back to struct so CSV pipeline continues
					}
				} else {
					if err := decoder.Decode(&ref); err != nil {
						log.Printf("⚠️ Error decoding ProviderReference: %v", err)
						continue
					}
				}
				
				for _, pg := range ref.ProviderGroups {
					tinType := pg.TIN.Type
					tinVal := pg.TIN.Value
					for _, npi := range pg.NPIs {
						providersWriter.Write([]string{
							fmt.Sprintf("%d", ref.ProviderGroupID),
							fmt.Sprintf("%d", npi),
							tinType,
							tinVal,
						})
					}
				}
				count++
				if count > 0 && count%100000 == 0 {
					providersWriter.Flush()
					log.Printf("    Extracted %d provider groups... %s", count, pr.GetProgressString())
				}
			}
			decoder.Token() // Consume ']'
			providersWriter.Flush()
			log.Printf("    ✅ Extracted %d provider reference groups. %s", count, pr.GetProgressString())
			
		} else if key == "in_network" {
			log.Println("    🎯 Found 'in_network' array. Extracting to rates.csv...")
			decoder.Token() // Read '['
			
			rowCount := 0
			itemCount := 0
			for decoder.More() {
				var item InNetworkItem
				
				// Snippet Generator: Capture the very first raw object exactly as it is in the JSON!
				if isFirstFile && schemaExample["in_network"] == nil {
					var raw map[string]interface{}
					if err := decoder.Decode(&raw); err == nil {
						schemaExample["in_network"] = []interface{}{raw} // Store exactly 1 item in the array for the example
						b, _ := json.Marshal(raw)
						json.Unmarshal(b, &item) // Convert back to struct so CSV pipeline continues
					}
				} else {
					if err := decoder.Decode(&item); err != nil {
						log.Printf("⚠️ Error decoding InNetworkItem: %v", err)
						continue
					}
				}
				itemCount++
				
				// Track unique billing codes to normalize descriptions
				if !seenBillingCodes[item.BillingCode] {
					seenBillingCodes[item.BillingCode] = true
					billingCodesWriter.Write([]string{
						item.BillingCodeType,
						item.BillingCode,
						item.Name,
						item.Description,
					})
				}
				
				for _, rate := range item.NegotiatedRates {
					for _, refID := range rate.ProviderReferences {
						refIDStr := fmt.Sprintf("%d", refID)
						
						for _, price := range rate.NegotiatedPrices {
							serviceCodeStr := strings.Join(price.ServiceCode, "|")
							record := []string{
								refIDStr,
								planName,
								item.BillingCodeType,
								item.BillingCode,
								item.NegotiationArrangement,
								price.NegotiatedType,
								fmt.Sprintf("%.2f", price.NegotiatedRate),
								price.ExpirationDate,
								serviceCodeStr,
							}
							ratesWriter.Write(record)
							rowCount++
						}
					}
				}
				
				if rowCount > 0 && rowCount%10000 == 0 {
					ratesWriter.Flush()
					log.Printf("    Extracted %d flattened rate rows... %s", rowCount, pr.GetProgressString())
				}
			}
			decoder.Token() // Consume ']'
			ratesWriter.Flush()
			log.Printf("    ✅ Extracted %d total rate rows. %s", rowCount, pr.GetProgressString())
			
		} else {
			if isFirstFile {
				// We peek at the next token to see if it's an array or object
				tPeek, _ := decoder.Token()
				if delim, ok := tPeek.(json.Delim); ok && (delim == '[' || delim == '{') {
					log.Printf("    🔍 Schema Discovery: Found unexpected root structure: '%s' (skipping value) %s", key, pr.GetProgressString())
					// Re-inject the opening bracket into the skip depth count
					depth := 1
					for depth > 0 {
						tSkip, _ := decoder.Token()
						if dSkip, ok := tSkip.(json.Delim); ok {
							if dSkip == '{' || dSkip == '[' {
								depth++
							} else if dSkip == '}' || dSkip == ']' {
								depth--
							}
						}
					}
				} else {
					// It's a primitive value (string, number, bool)! Let's save it directly to the example!
					schemaExample[key] = tPeek
					log.Printf("    🔍 Schema Discovery: Captured root key '%s' = %v", key, tPeek)
				}
			} else {
				log.Printf("    🔍 Schema Discovery: Found unexpected root key: '%s' (skipping value) %s", key, pr.GetProgressString())
				skipValue(decoder)
			}
		}
	}
	
	decoder.Token() // '}'
	
	// Write the collected schema snippet to mrf_example.json
	if isFirstFile {
		os.MkdirAll(filepath.Dir(ExampleOutputPath), os.ModePerm)
		out, err := os.Create(ExampleOutputPath)
		if err == nil {
			enc := json.NewEncoder(out)
			enc.SetIndent("", "  ")
			enc.Encode(schemaExample)
			out.Close()
			log.Println("    ✅ Successfully wrote complete ERD Snippet to mrf_example.json")
		} else {
			log.Printf("    ⚠️ Failed to write mrf_example.json: %v", err)
		}
	}
	
	log.Println("  ✅ Completed Rate Parsing.")
}
