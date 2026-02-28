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
    1. Streams JSON events to keep memory usage near zero.
    2. Uses a 'stack' to track keys and data for the current nesting level.
    3. Maintains 'seen_shapes_at_path' to deduplicate at every level.
    4. Builds a single 'representative_tree' that mirrors the MRF format but 
       only contains one instance of each unique object shape found at any path.
    5. Logs the full JSON of any newly discovered structure.
    """
    log(f"🧬 Starting Full Structural Discovery: {url}")
    
    # path -> list of unique key combinations (sorted lists of keys)
    discovered_schemas = {}
    # Stores the final pruned representative object
    representative_tree = None
    
    # Sets to track seen shapes per path for deduplication
    seen_shapes_at_path = {}
    
    # stack of [key_set, data_dict, current_key, prefix]
    stack = []
    
    processed_count = 0
    
    try:
        response = requests.get(url, stream=True, timeout=60)
        response.raise_for_status()
        
        with gzip.GzipFile(fileobj=response.raw) as gfile:
            parser = ijson.parse(gfile)
            
            for prefix, event, value in parser:
                processed_count += 1
                
                if event == 'start_map':
                    stack.append([set(), {}, None, prefix])
                    
                elif event == 'map_key':
                    if stack:
                        stack[-1][0].add(value)
                        stack[-1][2] = value
                        
                elif event in ('string', 'number', 'boolean', 'null'):
                    if stack:
                        k = stack[-1][2]
                        if k: stack[-1][1][k] = value
                
                elif event == 'start_array':
                    if stack:
                        k = stack[-1][2]
                        if k: stack[-1][1][k] = []
                        
                elif event == 'end_map':
                    if stack:
                        keys_set, data_dict, _, this_prefix = stack.pop()
                        shape = sorted(list(keys_set))
                        
                        if this_prefix not in discovered_schemas:
                            discovered_schemas[this_prefix] = []
                            seen_shapes_at_path[this_prefix] = set()
                            
                        # Unique shape detection
                        shape_tuple = tuple(shape)
                        is_new_shape = shape_tuple not in seen_shapes_at_path[this_prefix]
                        
                        if is_new_shape:
                            seen_shapes_at_path[this_prefix].add(shape_tuple)
                            discovered_schemas[this_prefix].append(shape)
                            log(f"✨ New structure found at '{this_prefix}':\n{json.dumps(data_dict, indent=2)}")

                        # Propagation: Build the representative tree
                        if not stack:
                            # We just popped the root object
                            representative_tree = data_dict
                        else:
                            parent_key = stack[-1][2]
                            if parent_key:
                                parent_sample = stack[-1][1]
                                # If it's an array, only add if this shape hasn't been added to THIS parent yet
                                if isinstance(parent_sample.get(parent_key), list):
                                    # For arrays, we want to keep one example of each UNIQUE shape seen AT THIS PATH
                                    # We use the global is_new_shape to decide if we add it to the example array
                                    if is_new_shape:
                                        parent_sample[parent_key].append(data_dict)
                                else:
                                    # For objects, simply set/overwrite with the latest version
                                    parent_sample[parent_key] = data_dict

                # Progress logging
                if processed_count % 10000000 == 0:
                    log(f"  Processed {processed_count} events... Discovered {sum(len(v) for v in discovered_schemas.values())} unique shapes.")

    except Exception as e:
        log(f"❌ Discovery failed: {e}")

    # Sort results top-down by path depth (number of dots)
    sorted_paths = sorted(discovered_schemas.keys(), key=lambda p: (p.count('.'), p))
    sorted_map = {p: discovered_schemas[p] for p in sorted_paths}

    # Output results
    output_dir = "data/anthem"
    os.makedirs(output_dir, exist_ok=True)
    
    # 1. Save structural map
    map_path = os.path.join(output_dir, "mrf_structure.json")
    with open(map_path, "w") as f:
        json.dump(sorted_map, f, indent=2)
        
    # 2. Save human-readable example (Full Pruned Tree)
    example_path = os.path.join(output_dir, "mrf_example.json")
    with open(example_path, "w") as f:
        json.dump(representative_tree, f, indent=2)
        
    log(f"✅ Discovery complete!")
    log(f"📂 Structural Map: {map_path}")
    log(f"📂 Human Example: {example_path}")

if __name__ == "__main__":
    # Select the smallest file from discovery for the fastest structural scan
    index_path = "data/anthem/index_urls.json"
    target_url = None
    
    if os.path.exists(index_path):
        with open(index_path, "r") as f:
            urls = json.load(f)
            # Sort by size (ascending), skip zero-length if possible
            valid_urls = [u for u in urls if u.get("file_size_bytes", 0) > 0]
            if valid_urls:
                smallest = min(valid_urls, key=lambda x: x["file_size_bytes"])
                target_url = smallest["location"]
                log(f"🎯 Auto-selected smallest file for discovery: {smallest['file_size_bytes'] / 1024 / 1024:.2f} MB")
            elif urls:
                target_url = urls[0]["location"]
    
    if not target_url:
        log("⚠️ No index_urls.json found, using hardcoded fallback.")
        target_url = "https://anthembcca.mrf.bcbs.com/2026-02_266_38B0_in-network-rates_1_of_3.json.gz?&Expires=1774274448&Signature=DxddGSSN34Gd8RVDlIKJmy03URHR1R1RmJ6x6f9etuPlia6Tu0wxfRT3hLQzgLEneIzdY4ZhUPzokYPAFYzuL7RXk9QEstLCyrRq~Mm7-Ah7C4-sedlzhKGQ6~QmNsgxZrBl6ukmIGsEyRSBMUTqdJg7vMYDevqnPmxFbx3IWnTQouKChKdnIAOMWhXrfeIDYS93OVqiV7KBWr8bQP5O8uYr4g9pircsOhw-~QxQmjQ7tFgR3ypJApRKbQvtVyBkjHazTp8qrgjcgRMJWXOpYIyoP~p~O30-Z7uq9rJpXiXAvcpfDSUv2KJX9dK2RWrRQvmGlHXU8wf53YyexU8FOg__&Key-Pair-Id=K27TQMT39R1C8A"

    discover_json_structure(target_url)
