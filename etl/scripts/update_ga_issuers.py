import requests
import csv
import json
import os
import io
from etl.utils.logger import log

PUF_URL = "https://georgiaaccess.gov/wp-content/uploads/2025/12/PLAN-PUF_20251216063410_2026.csv"
OUTPUT_PATH = "data/hospitals/ga_issuers.json"

def update_issuers():
    log(f"🌐 Fetching GA Access Plan-PUF from: {PUF_URL}")
    
    try:
        response = requests.get(PUF_URL, timeout=30, verify=False)
        response.raise_for_status()
        
        f = io.StringIO(response.text)
        reader = csv.DictReader(f)
        
        # issuers_data[issuer_id] = { "name": ..., "plan_ids": set() }
        issuers_data = {}
        
        for row in reader:
            issuer_id = row.get('IssuerId')
            marketing_name = row.get('IssuerMarketPlaceMarketingName')
            # Use full StandardComponentId for exact mapping
            plan_id = row.get('StandardComponentId')
            
            if issuer_id and marketing_name:
                if issuer_id not in issuers_data:
                    issuers_data[issuer_id] = {
                        "name": marketing_name,
                        "plan_ids": set()
                    }
                
                if plan_id:
                    issuers_data[issuer_id]["plan_ids"].add(plan_id)
        
        # Format for saving
        output_data = []
        for i_id, info in sorted(issuers_data.items(), key=lambda x: x[0]):
            output_data.append({
                "issuer_id": i_id,
                "name": info["name"],
                "plan_ids": sorted(list(info["plan_ids"]))
            })
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
        
        with open(OUTPUT_PATH, 'w', encoding='utf-8') as out_f:
            json.dump(output_data, out_f, indent=2)
            
        log(f"✅ Successfully saved {len(output_data)} issuers with Product IDs to {OUTPUT_PATH}")
        
        # Specifically highlight Anthem for current task
        anthem_plans = []
        for i in output_data:
            if 'Anthem' in i['name']:
                anthem_plans.extend(i['plan_ids'])
        log(f"📍 Anthem GA Plan IDs found: {len(anthem_plans)}")

    except Exception as e:
        log(f"❌ Failed to update issuers: {e}")

def log(msg):
    print(msg, flush=True)

if __name__ == "__main__":
    update_issuers()
