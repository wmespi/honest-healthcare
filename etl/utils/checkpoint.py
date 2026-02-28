import json
import os
from typing import Dict, Any, Optional
from etl.utils.logger import log

class CheckpointManager:
    """
    Manages progress tracking for URL-based ETL tasks.
    State is persisted to a JSON file.
    """
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.state: Dict[str, Dict[str, Any]] = {}
        self.load()

    def load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r') as f:
                    self.state = json.load(f)
            except Exception as e:
                log(f"⚠️ Failed to load checkpoint file {self.filepath}: {e}")
                self.state = {}
        else:
            os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
            self.state = {}

    def save(self):
        try:
            with open(self.filepath, 'w') as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            log(f"❌ Failed to save checkpoint file {self.filepath}: {e}")

    def is_completed(self, url: str) -> bool:
        return self.state.get(url, {}).get("status") == "completed"

    def get_progress(self, url: str) -> Dict[str, Any]:
        return self.state.get(url, {"status": "pending", "items_processed": 0})

    def mark_progress(self, url: str, items_processed: int, rows_loaded: Optional[int] = None):
        if url not in self.state:
            self.state[url] = {}
        
        self.state[url].update({
            "status": "in_progress",
            "items_processed": items_processed,
            "rows_loaded": rows_loaded if rows_loaded is not None else self.state[url].get("rows_loaded", 0)
        })
        self.save()

    def mark_completed(self, url: str, rows_loaded: Optional[int] = None):
        if url not in self.state:
            self.state[url] = {}
        
        self.state[url].update({
            "status": "completed",
            "rows_loaded": rows_loaded if rows_loaded is not None else self.state[url].get("rows_loaded", 0)
        })
        self.save()

    def mark_error(self, url: str, error_msg: str):
        if url not in self.state:
            self.state[url] = {}
            
        self.state[url].update({
            "status": "error",
            "error": error_msg
        })
        self.save()
