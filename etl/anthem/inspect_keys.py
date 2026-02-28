import json
import ijson
import gzip
import requests
import os
from etl.utils.logger import log

def discover_json_structure(url: str):
    """
    Performs a full structural scan of a JSON file to map every unique object schema.
    
    Logic:
    1. Streams JSON events (start_map, map_key, end_map) to keep memory usage near zero.
    2. Uses a 'key_stack' to track keys for currently open objects (handles deep nesting).
    3. On 'end_map', identifies the "shape" of the finished object (its set of sorted keys).
    4. Records the shape if it's the first time we've seen this specific key combination 
       at this specific JSON path (prefix).
    5. Result: A complete catalog of every distinct JSON structure present in the entire file.
    """
    log(f"🧬 Starting Full Structural Discovery: {url}")
    
    # path -> list of sets (unique key combinations seen at this path)
    discovered_schemas = {}
    
    # Stack of sets to track keys for currently open objects
    key_stack = []
    
    processed_count = 0
    
    try:
        response = requests.get(url, stream=True, timeout=60)
        response.raise_for_status()
        
        with gzip.GzipFile(fileobj=response.raw) as gfile:
            # ijson.parse yields (prefix, event, value)
            parser = ijson.parse(gfile)
            
            for prefix, event, value in parser:
                processed_count += 1
                
                if event == 'start_map':
                    # New object starting at 'prefix'
                    key_stack.append(set())
                    
                elif event == 'map_key':
                    # Add key to the current object's key set
                    if key_stack:
                        key_stack[-1].add(value)
                        
                elif event == 'end_map':
                    # Object finished. Record its "shape" if new for this path.
                    if key_stack:
                        keys = key_stack.pop()
                        # Sort for deterministic comparison
                        shape = sorted(list(keys))
                        
                        if prefix not in discovered_schemas:
                            discovered_schemas[prefix] = []
                            
                        if shape not in discovered_schemas[prefix]:
                            discovered_schemas[prefix].append(shape)
                            log(f"✨ New structure found at path '{prefix}': {shape}")

                # Progress logging
                if processed_count % 100000 == 0:
                    log(f"  Processed {processed_count} events... Discovered {sum(len(v) for v in discovered_schemas.values())} unique shapes across {len(discovered_schemas)} paths.")

    except Exception as e:
        log(f"❌ Discovery failed: {e}")

    # Output results
    output_dir = "data/anthem"
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "mrf_structure.json")
    
    # Formatting for output: dict of lists of lists
    with open(output_path, "w") as f:
        json.dump(discovered_schemas, f, indent=2)
        
    log(f"✅ Full discovery complete! Saved to {output_path}")
    log(f"📊 Summary: {len(discovered_schemas)} unique paths, {sum(len(v) for v in discovered_schemas.values())} unique object shapes.")

# Target URL for structural discovery
TARGET_URL = "https://anthembcca.mrf.bcbs.com/2026-02_266_38B0_in-network-rates_1_of_3.json.gz?&Expires=1774274448&Signature=DxddGSSN34Gd8RVDlIKJmy03URHR1R1RmJ6x6f9etuPlia6Tu0wxfRT3hLQzgLEneIzdY4ZhUPzokYPAFYzuL7RXk9QEstLCyrRq~Mm7-Ah7C4-sedlzhKGQ6~QmNsgxZrBl6ukmIGsEyRSBMUTqdJg7vMYDevqnPmxFbx3IWnTQouKChKdnIAOMWhXrfeIDYS93OVqiV7KBWr8bQP5O8uYr4g9pircsOhw-~QxQmjQ7tFgR3ypJApRKbQvtVyBkjHazTp8qrgjcgRMJWXOpYIyoP~p~O30-Z7uq9rJpXiXAvcpfDSUv2KJX9dK2RWrRQvmGlHXU8wf53YyexU8FOg__&Key-Pair-Id=K27TQMT39R1C8A"

if __name__ == "__main__":
    discover_json_structure(TARGET_URL)
