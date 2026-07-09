"""One-off helper: print presigned R2 URLs for the first two FORVEN webms found.
Run with: .\venv\Scripts\python.exe gen_presigned.py
"""
import os
from pathlib import Path

# Load .env
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

os.environ["APP_MODE"] = "cloud"

import importlib
import storage
importlib.reload(storage)

keys = storage.list_keys("sessions/")
webm_keys = [k for k in keys if k.endswith(".webm")]
if not webm_keys:
    print("No webm files found in sessions/")
    raise SystemExit(1)

key1 = webm_keys[0]
key2 = webm_keys[1] if len(webm_keys) > 1 else webm_keys[0]
print("Video 1:", key1)
print("URL 1:")
print(storage.presigned_url(key1, expires=3600))
print()
print("Video 2:", key2)
print("URL 2:")
print(storage.presigned_url(key2, expires=3600))
