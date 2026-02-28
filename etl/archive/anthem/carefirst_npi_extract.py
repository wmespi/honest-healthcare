import requests
import ijson
import gzip
import json
import gc

TARGET_NPI = "1235123977"
URL = (
    "https://anthembcbsga.mrf.bcbs.com/2026-02_690_08D0_in-network-rates_20_of_35.json.gz"
    "?&Expires=1774274448"
    "&Signature=eooC5Xw1-xsuS9yRD4vhh0RzBZ4qQyLJPchq6nfnztRdvUvzIOnt8YQ4uCTcc8TwSi5i1ahUGK300Yt1mqptP9OitgF6tgm-SqgzUE1HsspUxVMEnnZ7iepcmrfGUZ9PxtPTn9Jg9g44AqcclsyJZizG~dAPpwKsVyvltyepZnripAGK-PMp65wqOMwug-DWP-1FGJ7jMTrS5lLgv6tsuMwE9qivnv~Gxkv2B9AhMk1nEzSpDGxrWn-4ldHOPxdppY2uBQ0HojP4-E5zAl9veoiM4buHNBC2nd~G7ATNG9MKTq4oclTGbvP5DOZKXNodFsMC5DVy2uWLsgjt59OZ1A__"
    "&Key-Pair-Id=K27TQMT39R1C8A"
)
OUTPUT_PATH = "data/anthem/carefirst_npi_1235123977.json"


def extract():
    print(f"Streaming CareFirst file for NPI {TARGET_NPI}...")

    # Pass 1: Find provider_group_ids containing the target NPI
    # Only keep the group_id, not the full ref object, to save memory
    print("Pass 1: Scanning provider_references for NPI...")
    response = requests.get(URL, stream=True, timeout=120)
    response.raise_for_status()
    stream = gzip.GzipFile(fileobj=response.raw)
    parser = ijson.items(stream, "provider_references.item")

    matched_group_ids = set()
    scan_count = 0

    for ref in parser:
        scan_count += 1
        for g in ref.get("provider_groups", []):
            if any(str(n) == TARGET_NPI for n in g.get("npi", [])):
                matched_group_ids.add(ref.get("provider_group_id"))
                break
        # Don't hold ref in memory
        del ref
        if scan_count % 10000 == 0:
            print(f"  Scanned {scan_count} provider refs...")
            gc.collect()

    response.close()
    gc.collect()

    print(f"Pass 1 done: scanned {scan_count} refs, found {len(matched_group_ids)} group IDs")
    print(f"Matched provider_group_ids: {matched_group_ids}")

    if not matched_group_ids:
        print("No matching provider references found. Exiting.")
        return

    # Pass 2: Re-stream and write matching in_network entries incrementally
    print("Pass 2: Extracting in_network rates (streaming to disk)...")
    response = requests.get(URL, stream=True, timeout=120)
    response.raise_for_status()
    stream = gzip.GzipFile(fileobj=response.raw)
    rate_parser = ijson.items(stream, "in_network.item")

    entry_count = 0
    match_count = 0

    with open(OUTPUT_PATH, "w") as out:
        out.write('{\n')
        out.write(f'  "npi_filter": "{TARGET_NPI}",\n')
        out.write(f'  "matched_provider_group_ids": {json.dumps(list(matched_group_ids))},\n')
        out.write('  "in_network": [\n')

        first = True
        for record in rate_parser:
            entry_count += 1
            matched = False
            for rate in record.get("negotiated_rates", []):
                if any(r in matched_group_ids for r in rate.get("provider_references", [])):
                    matched = True
                    break

            if matched:
                if not first:
                    out.write(",\n")
                json.dump({
                    "billing_code": record.get("billing_code"),
                    "billing_code_type": record.get("billing_code_type"),
                    "name": record.get("name"),
                    "negotiated_rates": record.get("negotiated_rates"),
                }, out, indent=2, default=str)
                first = False
                match_count += 1

            del record
            if entry_count % 1000 == 0:
                print(f"  Scanned {entry_count} in_network entries, {match_count} matched...")
                gc.collect()

        out.write("\n  ]\n}")

    response.close()
    print(f"Pass 2 done: scanned {entry_count} entries, {match_count} matched")
    print(f"Written to {OUTPUT_PATH}")


if __name__ == "__main__":
    extract()
