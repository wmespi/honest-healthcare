import requests
import ijson
import json
import argparse
from ijson.common import JSONError

def parse_index(url, search_keywords):
    """
    Streams a UHC Index JSON file and find plans matching keywords.
    """
    print(f"Streaming and parsing Index JSON from: {url}")
    print(f"Searching for keywords: {search_keywords}")
    
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()
        
        # ijson.items streams through the 'reporting_structure' array
        # Each item in reporting_structure contains 'reporting_plans' and 'in_network_files'
        structures = ijson.items(response.raw, 'reporting_structure.item')
        
        found_matches = []
        
        for struct in structures:
            plans = struct.get('reporting_plans', [])
            matched_plan = None
            
            # Check if any plan in this group matches our keywords
            for plan in plans:
                name = plan.get('plan_name', '').upper()
                sponsor = plan.get('plan_sponser_name', '').upper()
                
                if any(kw.upper() in name or kw.upper() in sponsor for kw in search_keywords):
                    matched_plan = plan
                    break
            
            if matched_plan:
                # If we have a match, collect the in-network file locations
                files = struct.get('in_network_files', [])
                if files:
                    for f in files:
                        match_info = {
                            "plan_name": matched_plan.get('plan_name'),
                            "sponsor": matched_plan.get('plan_sponser_name'),
                            "description": f.get('description'),
                            "location": f.get('location')
                        }
                        found_matches.append(match_info)
                        print(f"Found Match: {matched_plan.get('plan_name')} -> {f.get('location')[:80]}...")

        return found_matches

    except JSONError as e:
        print(f"JSON Parsing Error: {e}")
    except requests.exceptions.RequestException as e:
        print(f"HTTP Request Error: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    
    return []

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parse UHC Index JSON for specific plans.")
    parser.add_argument("--url", required=True, help="URL of the UHC Index JSON file")
    parser.add_argument("--keywords", nargs="+", default=["GEORGIA", "ATLANTA", "EMORY"], help="Keywords to search for in plan/sponsor names")
    parser.add_argument("--output", default="matched_files.json", help="Output file for found URLs")
    
    args = parser.parse_args()
    
    matches = parse_index(args.url, args.keywords)
    
    if matches:
        print(f"\nTotal matches found: {len(matches)}")
        with open(args.output, 'w') as f:
            json.dump(matches, f, indent=2)
        print(f"Results saved to {args.output}")
    else:
        print("\nNo matches found for the specified keywords.")
