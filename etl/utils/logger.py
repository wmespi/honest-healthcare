import sys
from datetime import datetime

def log(msg):
    """Log to both current process stdout and main container stdout for Docker UI visibility"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_msg = f"[{timestamp}] {msg}"
    
    # 1. Standard stdout
    print(full_msg, flush=True)
    
    # 2. Attempt to write to container's PID 1 stdout for Docker Log View visibility
    # This is useful when running via 'docker exec' to ensure the main log view shows progress
    try:
        with open('/proc/1/fd/1', 'w') as f:
            f.write(f"{full_msg}\n")
    except (PermissionError, FileNotFoundError, OSError):
        # Fallback if not in a container or lack permissions
        pass
