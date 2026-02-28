import requests
import ijson
import gzip
from typing import Generator, Any
from etl.utils.logger import log

def stream_gzip_json(url: str, ijson_path: str, timeout: int = 60) -> Generator[Any, None, None]:
    """
    Opens a gzipped JSON stream from a URL and yields items matching the ijson path.
    Memory-efficient for very large files.
    """
    log(f"📡 Streaming from {url}")
    try:
        response = requests.get(url, stream=True, timeout=timeout)
        response.raise_for_status()
        
        # Decompress on the fly
        with gzip.GzipFile(fileobj=response.raw) as gfile:
            # ijson.items yields objects at the specified path
            for item in ijson.items(gfile, ijson_path):
                yield item
                
    except requests.exceptions.RequestException as e:
        log(f"❌ Network error streaming {url}: {e}")
        raise
    except Exception as e:
        log(f"❌ Unexpected error streaming {url}: {e}")
        raise
