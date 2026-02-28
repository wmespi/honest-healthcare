import json
import os
import gzip
import requests
import ijson
import psycopg2
from io import StringIO
from etl.utils.logger import log
from etl.utils.checkpoint import CheckpointManager
from backend.models import Base
from backend.database import engine

# Database Configuration
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@db:5432/honest_healthcare")
INDEX_PATH = "data/anthem/index_urls.json"
CHECKPOINT_PATH = "data/anthem/checkpoint.json"
FAILED_LOG_PATH = "data/anthem/failed_normalizations.json"
DB_SIZE_LIMIT_BYTES = 5 * 1024 * 1024 * 1024  # 5GB

# Filtering Logic (Outpatient Focus)
EXCLUDED_HCPCS_PREFIXES = ('A', 'B', 'E', 'K', 'L', 'V')
# We'll allow CPT (mostly digits) and J, P, G, M, Q, R, S for clinical HCPCS

def is_target_code(code: str, code_type: str) -> bool:
    """Returns True if the code is a clinical outpatient target."""
    if not code: return False
    
    # CMS standard: HCPCS types often include CPT
    # We prioritize CPT (mostly numeric) and specific HCPCS Level II clinical ranges
    if code[0].isdigit():
        return True # CPT
    
    if code.startswith(EXCLUDED_HCPCS_PREFIXES):
        return False
        
    return True # Allow everything else (J, P, G, etc.) for wide clinical coverage

def get_db_size():
    """Returns current database size in bytes."""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        with conn.cursor() as cur:
            cur.execute("SELECT pg_database_size(current_database())")
            size = cur.fetchone()[0]
            return size
    except Exception as e:
        log(f"⚠️ Could not check DB size: {e}")
        return 0
    finally:
        if 'conn' in locals(): conn.close()

def log_failure(url, error_type, message):
    """Logs normalization failures to a dead-letter file."""
    failures = []
    if os.path.exists(FAILED_LOG_PATH):
        try:
            with open(FAILED_LOG_PATH, 'r') as f:
                failures = json.load(f)
        except: pass
    
    failures.append({
        "url": url,
        "type": error_type,
        "error": message
    })
    
    with open(FAILED_LOG_PATH, 'w') as f:
        json.dump(failures, f, indent=2)

def bulk_load(records):
    """Fast bulk load into PostgreSQL using COPY FROM STDIN."""
    if not records: return
    
    f = StringIO()
    for r in records:
        # Columns: payor, npi, billing_code, billing_code_type, procedure_name, 
        # negotiated_rate, negotiated_type, billing_class, service_codes, 
        # expiration_date, network_name, plan_name, source_file
        row = "\t".join([str(val).replace('\t', ' ').replace('\n', ' ') if val is not None else '' for val in r])
        f.write(row + "\n")
    
    f.seek(0)
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.copy_from(f, 'mrf_rates', columns=(
                'payor', 'npi', 'billing_code', 'billing_code_type', 'procedure_name',
                'negotiated_rate', 'negotiated_type', 'billing_class', 'service_codes',
                'expiration_date', 'network_name', 'plan_name', 'source_file'
            ))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def process_file(url: str, plan_names: list, skip_count: int = 0):
    """
    Two-phase normalization of an Anthem MRF.
    Phase A: Map Provider References
    Phase B: Stream Rates with resolve + filter
    """
    log(f"⚙️ Processing: {url}")
    
    provider_map = {} # id -> {network_name, npis}
    plan_name = plan_names[0] if plan_names else "Unknown"
    
    try:
        response = requests.get(url, stream=True, timeout=60)
        response.raise_for_status()
        
        # Phase A: Build provider map
        # We need to re-open or buffer if not careful. Anthem files are large.
        # ijson can't easily jump back, so we use multiple sub-parsers if possible.
        
        with gzip.GzipFile(fileobj=response.raw) as gfile:
            # Phase A: Get provider_references
            log("  Phase A: Mapping Provider References...")
            pr_parser = ijson.items(gfile, 'provider_references.item')
            for item in pr_parser:
                pr_id = item.get('provider_group_id')
                if pr_id:
                    npis = []
                    for pg in item.get('provider_groups', []):
                        npis.append(str(pg.get('npi')))
                    
                    provider_map[pr_id] = {
                        "network_name": item.get('network_name'),
                        "npis": npis
                    }
            
            # Since requests-stream is consumed, we typically have to re-request 
            # OR use a more advanced approach. For MVP, we'll re-request for Phase B.
            # (Note: For massive files, we'd ideally use a single pass with ijson.parse)
            
        # Phase B: Rates
        log("  Phase B: Normalizing Rates...")
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
                # Note: Anthem rates can have multiple prices per item
                for rate_entry in item.get('negotiated_rates', []):
                    ref_ids = rate_entry.get('provider_references', [])
                    if isinstance(ref_ids, int): ref_ids = [ref_ids]
                    
                    price_entries = rate_entry.get('negotiated_prices', [])
                    for price in price_entries:
                        for pr_id in ref_ids:
                            ref_data = provider_map.get(pr_id, {})
                            npis = ref_data.get('npis', ["Unknown"])
                            
                            for npi in npis:
                                records_buffer.append((
                                    "anthem",
                                    npi,
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
                                    url
                                ))
                
                if len(records_buffer) >= 20000:
                    bulk_load(records_buffer)
                    loaded_count += len(records_buffer)
                    records_buffer = []
                    log(f"    Loaded {loaded_count} rates... (Item {it_count})")
                    # Update checkpoint
                    checkpoint.mark_progress(url, it_count, loaded_count)
            
            # Final flush
            if records_buffer:
                bulk_load(records_buffer)
                loaded_count += len(records_buffer)
                checkpoint.mark_progress(url, it_count, loaded_count)
            
            log(f"  ✅ Completed: {loaded_count} records from {it_count} items.")
            checkpoint.mark_completed(url)

    except ijson.common.JSONError as e:
        log(f"❌ Structural Mismatch: {e}")
        log_failure(url, "STRUCTURAL_MISMATCH", str(e))
    except Exception as e:
        log(f"❌ Environmental Failure: {e}")
        log_failure(url, "ENVIRONMENTAL_FAILURE", str(e))

if __name__ == "__main__":
    if not os.path.exists(INDEX_PATH):
        log(f"❌ Index not found: {INDEX_PATH}")
        exit(1)
        
    # Ensure tables exist
    log("🏗️ Initializing database schema...")
    Base.metadata.create_all(bind=engine)
    
    checkpoint = CheckpointManager(CHECKPOINT_PATH)
    
    with open(INDEX_PATH, 'r') as f:
        urls_data = json.load(f)
        
    # Sorting: Smallest-to-Largest
    sorted_urls = sorted(urls_data, key=lambda x: x.get('file_size_bytes', float('inf')))
    
    log(f"🚀 Starting Anthem Normalization Phase (Limit: 5GB)")
    
    for entry in sorted_urls:
        url = entry.get('location')
        if checkpoint.is_completed(url):
            continue
            
        current_size = get_db_size()
        if current_size >= DB_SIZE_LIMIT_BYTES:
            log(f"🛑 5GB Database Limit reached ({current_size / 1024**3:.2f} GB). Stopping.")
            break
            
        skip = checkpoint.get_progress(url).get('items_processed', 0)
        process_file(url, entry.get('plan_names', []), skip_count=skip)
        checkpoint.save()
