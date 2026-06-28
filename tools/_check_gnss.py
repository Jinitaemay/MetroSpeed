import json
import os
import sys
from pathlib import Path

fname = sys.argv[1]
data_dir = Path(os.environ.get("METROSPEED_DATA_DIR", "."))
f = data_dir / fname

locations = []
with open(f) as fh:
    for line in fh:
        d = json.loads(line)
        if d.get("recordType") == "location":
            locations.append(d)

print(f"File: {fname}")
print(f"Location records: {len(locations)}")
if locations:
    for loc in locations:
        speed = loc.get("speedKmh", 0)
        acc = loc.get("accuracy", 0)
        ts = loc["timestampMs"]
        print(f"  speed={speed:.1f} km/h  acc={acc}  ts={ts}")