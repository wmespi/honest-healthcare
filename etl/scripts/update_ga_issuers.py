import requests
import csv
import json
import os
import io

PUF_URL = "https://georgiaaccess.gov/wp-content/uploads/2025/12/PLAN-PUF_20251216063410_2026.csv"
OUTPUT_PATH = "data/hospitals/ga_issuers.json"

def update_issuers():
    print(f"🌐 Fetching GA Access Plan-PUF from: {PUF_URL}")
    
    try:
        # Disabling verify for gov site with custom CA issues in container
        response = requests.get(PUF_URL, timeout=30, verify=False)
        response.raise_for_status()
        
        # Use StringIO to read the CSV content from the response text
        f = io.StringIO(response.text)
        reader = csv.DictReader(f)
        
        issuers = {} # Using a dict for de-duplication: {id: name}
        
        for row in reader:
            issuer_id = row.get('IssuerId')
            marketing_name = row.get('IssuerMarketPlaceMarketingName')
            if issuer_id and marketing_name:
                # Keep the shortest/cleanest name if there are multiple for one ID
                if issuer_id not in issuers or len(marketing_name) < len(issuers[issuer_id]):
                    issuers[issuer_id] = marketing_name
        
        # Format for saving
        output_data = [
            {"issuer_id": i_id, "name": name} 
            for i_id, name in sorted(issuers.items(), key=lambda x: x[0])
        ]
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
        
        with open(OUTPUT_PATH, 'w', encoding='utf-8') as out_f:
            json.dump(output_data, out_f, indent=2)
            
        print(f"✅ Successfully saved {len(output_data)} unique GA issuers to {OUTPUT_PATH}")
        
        # Specifically highlight Anthem for current task
        anthem_ids = [i['issuer_id'] for i in output_data if 'Anthem' in i['name']]
        print(f"📍 Verified Anthem IDs: {', '.join(anthem_ids)}")

    except Exception as e:
        print(f"❌ Failed to update issuers: {e}")

if __name__ == "__main__":
    update_issuers()
