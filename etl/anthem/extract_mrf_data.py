import json
import os
import gzip
import requests
import ijson
import psycopg2
import argparse
from io import StringIO
from sqlalchemy import text
from etl.utils.logger import log
from etl.utils.checkpoint import CheckpointManager
from backend.models import Base
from backend.database import engine

# Database Configuration
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@db:5432/honest_healthcare")
INDEX_PATH = "data/anthem/index_urls.json"

# Production Paths
PROD_CHECKPOINT = "data/anthem/checkpoint.json"
PROD_FAILED_LOG = "data/anthem/failed_normalizations.json"
PROD_LIMIT = 5 * 1024 * 1024 * 1024  # 5GB

# Test Paths
TEST_DIR = "data/anthem/test"
TEST_CHECKPOINT = os.path.join(TEST_DIR, "checkpoint_test.json")
TEST_FAILED_LOG = os.path.join(TEST_DIR, "failed_test.json")
TEST_LIMIT = 250 * 1024 * 1024  # 250MB

def is_target_code(code: str, code_type: str) -> bool:
    """
    Returns True ONLY if the code is a clinical outpatient target (CPT/HCPCS).
    Explicitly excludes MS-DRG, Revenue Codes, etc.
    """
    if not code or not code_type: return False
    
    ct_upper = code_type.upper()
    
    # Strictly allow only HCPCS or CPT
    # Some files use "HCPCS" as the type for CPT codes
    if ct_upper not in ["HCPCS", "CPT"]:
        return False

    # Guard against MS-DRG or other numeric types that might bleed into HCPCS/CPT labels
    # CPT is usually 5 digits. HCPCS is usually Alpha + 4 digits.
    # MS-DRG is 3 digits.
    if len(code) < 5:
        return False
        
    return True

def get_table_size(table_name: str):
    """Returns the total size of a specific table in bytes."""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        with conn.cursor() as cur:
            # pg_total_relation_size includes indexes
            cur.execute(f"SELECT pg_total_relation_size('{table_name}')")
            return cur.fetchone()[0]
    except Exception as e:
        log(f"⚠️ Could not check table size for '{table_name}': {e}")
        return 0
    finally:
        if 'conn' in locals(): conn.close()

def log_failure(url, error_type, message, failed_path):
    """Logs failures to the appropriate log (prod or test)."""
    failures = []
    if os.path.exists(failed_path):
        try:
            with open(failed_path, 'r') as f:
                failures = json.load(f)
        except: pass
    
    failures.append({
        "url": url,
        "type": error_type,
        "error": message
    })
    
    with open(failed_path, 'w') as f:
        json.dump(failures, f, indent=2)

def bulk_load(records, table_name):
    """Fast bulk load into target table (mrf_rates or test_mrf_rates)."""
    if not records: return
    
    f = StringIO()
    for r in records:
        # Columns: payor, npi, billing_code, billing_code_type, procedure_name, 
        # negotiated_rate, negotiated_type, billing_class, service_codes, 
        # expiration_date, network_name, plan_name, business_name, tin_value, source_file
        row = "\t".join([str(val).replace('\t', ' ').replace('\n', ' ') if val is not None else '' for val in r])
        f.write(row + "\n")
    
    f.seek(0)
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.copy_from(f, table_name, columns=(
                'payor', 'npi', 'billing_code', 'billing_code_type', 'procedure_name',
                'negotiated_rate', 'negotiated_type', 'billing_class', 'service_codes',
                'expiration_date', 'network_name', 'plan_name', 'business_name', 'tin_value', 'source_file'
            ))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def process_file(url: str, plan_names: list, checkpoint, table_name, failed_path, skip_count: int = 0):
    """
    Two-phase normalization of an Anthem MRF.
    Phase A: Map Facility Metadata (Business Name, TIN)
    Phase B: Stream Rates with resolve + filter + Row Explosion
    """
    log(f"⚙️ Processing: {url}")
    
    provider_map = {} # id -> {network_name, facilities: [{npi, business_name, tin}]}
    plan_name = plan_names[0] if plan_names else "Unknown"
    
    try:
        response = requests.get(url, stream=True, timeout=60)
        response.raise_for_status()
        
        with gzip.GzipFile(fileobj=response.raw) as gfile:
            # Phase A: Get provider_references
            log("  Phase A: Mapping Facility Metadata (Business Names/TINs)...")
            pr_parser = ijson.items(gfile, 'provider_references.item')
            for item in pr_parser:
                pr_id = item.get('provider_group_id')
                if pr_id:
                    facility_data = [] # List of {npi, business_name, tin}
                    for pg in item.get('provider_groups', []):
                        npi_input = pg.get('npi')
                        # NPI can be a single int or a list of ints
                        npi_list = npi_input if isinstance(npi_input, list) else [npi_input]
                        
                        tin_obj = pg.get('tin', {})
                        for npi in npi_list:
                            if npi:
                                facility_data.append({
                                    "npi": str(npi),
                                    "business_name": tin_obj.get('business_name', 'Unknown Facility'),
                                    "tin": tin_obj.get('value', 'Unknown TIN')
                                })
                    
                    provider_map[pr_id] = {
                        "network_name": item.get('network_name'),
                        "facilities": facility_data
                    }
            log(f"    ✅ Phase A complete. Mapped {len(provider_map)} provider reference groups.")
            
        # Phase B: Rates
        log("  Phase B: Normalizing Rates (Row Explosion)...")
        response = requests.get(url, stream=True, timeout=60)
        with gzip.GzipFile(fileobj=response.raw) as gfile:
            rates_parser = ijson.items(gfile, 'in_network.item')
            
            records_buffer = []
            it_count = 0
            loaded_count = 0
            
            for item in rates_parser:
                it_count += 1
                if it_count <= skip_count:
                    continue
                
                code = item.get('billing_code')
                code_type = item.get('billing_code_type')
                
                if not is_target_code(code, code_type):
                    continue
                
                # Resolve Metadata
                for rate_entry in item.get('negotiated_rates', []):
                    ref_ids = rate_entry.get('provider_references', [])
                    if isinstance(ref_ids, int): ref_ids = [ref_ids]
                    
                    price_entries = rate_entry.get('negotiated_prices', [])
                    for price in price_entries:
                        for pr_id in ref_ids:
                            ref_data = provider_map.get(pr_id, {})
                            facilities = ref_data.get('facilities', [{"npi": "Unknown", "business_name": "Unknown", "tin": "Unknown"}])
                            
                            # Row Explosion: Each NPI/Facility gets its own row
                            for fac in facilities:
                                records_buffer.append((
                                    "anthem",
                                    fac['npi'],
                                    code,
                                    code_type,
                                    item.get('name'),
                                    price.get('negotiated_rate'),
                                    price.get('negotiated_type'),
                                    price.get('billing_class'),
                                    price.get('service_code'),
                                    price.get('expiration_date'),
                                    ref_data.get('network_name'),
                                    plan_name,
                                    fac['business_name'],
                                    fac['tin'],
                                    url
                                ))
                
                if len(records_buffer) >= 20000:
                    bulk_load(records_buffer, table_name)
                    loaded_count += len(records_buffer)
                    records_buffer = []
                    log(f"    Loaded {loaded_count} rates... (Item {it_count})")
                    # Update checkpoint
                    checkpoint.mark_progress(url, it_count, loaded_count)
            
            # Final flush
            if records_buffer:
                bulk_load(records_buffer, table_name)
                loaded_count += len(records_buffer)
                checkpoint.mark_progress(url, it_count, loaded_count)
            
            log(f"  ✅ Completed: {loaded_count} records.")
            checkpoint.mark_completed(url)

    except ijson.common.JSONError as e:
        log_failure(url, "STRUCTURAL_MISMATCH", str(e), failed_path)
        raise e
    except Exception as e:
        log_failure(url, "ENVIRONMENTAL_FAILURE", str(e), failed_path)
        raise e

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="Run in isolated test mode")
    args = parser.parse_args()

    if args.test:
        log("🧪 TEST MODE: Isolated Environment (250MB Limit)")
        os.makedirs(TEST_DIR, exist_ok=True)
        checkpoint_path, failed_path = TEST_CHECKPOINT, TEST_FAILED_LOG
        table_name, limit = "test_mrf_rates", TEST_LIMIT
    else:
        log("🚀 PRODUCTION MODE: Main Pipeline (5GB Limit)")
        checkpoint_path, failed_path = PROD_CHECKPOINT, PROD_FAILED_LOG
        table_name, limit = "mrf_rates", PROD_LIMIT

    if not os.path.exists(INDEX_PATH):
        log(f"❌ Index not found: {INDEX_PATH}")
        exit(1)
        
    # 1. Initialize DB Schema
    log("🏗️ Ensuring database schema is ready...")
    Base.metadata.create_all(bind=engine)
    
    # 2. Handle Test Table Creation (Isolated from Prod)
    if args.test:
        log(f"🛠️ Ensuring test table '{table_name}' exists (cloned from prod schema)...")
        with engine.begin() as conn:
            conn.execute(text(f"CREATE TABLE IF NOT EXISTS {table_name} (LIKE mrf_rates INCLUDING ALL)"))
    
    # 3. Unified Ingestion Loop
    checkpoint = CheckpointManager(checkpoint_path)
    with open(INDEX_PATH, 'r') as f:
        urls_data = json.load(f)
        
    # Sorting: Smallest-to-Largest
    sorted_urls = sorted(urls_data, key=lambda x: x.get('file_size_bytes', float('inf')))
    
    log(f"🚀 Starting Anthem Normalization Phase")
    
    for entry in sorted_urls:
        url = entry.get('location')
        if checkpoint.is_completed(url):
            continue
            
        current_size = get_table_size(table_name)
        if current_size >= limit:
            log(f"🛑 Table size limit reached ({current_size / 1024**2:.2f} MB). Stopping.")
            break
            
        skip = checkpoint.get_progress(url).get('items_processed', 0)
        try:
            process_file(url, entry.get('plan_names', []), checkpoint, table_name, failed_path, skip_count=skip)
            checkpoint.save()
        except Exception as e:
            log(f"❌ CRITICAL FAILURE during {url}: {e}")
            log("🛑 Terminating ingestion to prevent resource exhaustion.")
            exit(1)
