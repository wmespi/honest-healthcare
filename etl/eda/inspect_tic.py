import requests
import gzip
import json
import ijson
from itertools import islice

# URL of the 13GB in-network rates file
TIC_URL = "https://mrfstorageprod.blob.core.windows.net/public-mrf/2026-02-01/2026-02-01_UMR--Inc-_Third-Party-Administrator_UNITEDHEALTHCARE-CHOICE-PLUS_0L_in-network-rates.json.gz"

def inspect_header():
    """Reads the first few KB to see the top-level structure."""
    print("--- Top Level Metadata ---")
    response = requests.get(TIC_URL, stream=True)
    with gzip.GzipFile(fileobj=response.raw) as f:
        # Read the first 2KB to see the opening of the JSON
        chunk = f.read(2048).decode('utf-8')
        print(chunk + "...")

def inspect_providers(limit=5):
    """Uses ijson to pull out a few provider references (NPIs)."""
    print(f"\n--- Sampling {limit} Provider References ---")
    response = requests.get(TIC_URL, stream=True)
    with gzip.GzipFile(fileobj=response.raw) as f:
        # ijson.items streams the file and looks for the array at 'provider_references'
        providers = ijson.items(f, 'provider_references.item')
        for p in islice(providers, limit):
            print(json.dumps(p, indent=2))

from decimal import Decimal

class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super(DecimalEncoder, self).default(obj)

def inspect_rates(limit=3):
    """Uses ijson to pull out a few negotiated rate objects."""
    print(f"\n--- Sampling {limit} Negotiated Rates ---")
    response = requests.get(TIC_URL, stream=True)
    with gzip.GzipFile(fileobj=response.raw) as f:
        rates = ijson.items(f, 'in_network.item')
        for r in islice(rates, limit):
            # These objects are huge, so we'll just show the top keys and a snippet
            print(f"Billing Code: {r.get('billing_code')} ({r.get('billing_code_type')})")
            print(f"Name: {r.get('name')}")
            print(f"Negotiated Rates Count: {len(r.get('negotiated_rates', []))}")
            if r.get('negotiated_rates'):
                print("First Rate Sample:")
                print(json.dumps(r['negotiated_rates'][0], indent=2, cls=DecimalEncoder))
            print("-" * 20)

if __name__ == "__main__":
    try:
        inspect_header()
        # Note: If ijson is not installed, these will fail. 
        # You can install it in the container via: pip install ijson
        inspect_providers()
        inspect_rates()
    except Exception as e:
        print(f"Error during inspection: {e}")
        print("\nTip: Make sure 'ijson' and 'requests' are installed in your environment.")
