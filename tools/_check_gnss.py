import json
import os
import sys
from pathlib import Path

fname = sys.argv[1]
data_dir = Path(os.environ.get("METROSPEED_DATA_DIR", "."))
f = data_dir / fname

locations = []
with open(f, encoding="utf-8") as fh:
    for line in fh:
        d = json.loads(line)
        if d.get("recordType") == "location":
            locations.append(d)

print(f"File: {fname}")
print(f"Location records: {len(locations)}")
if locations:
    for loc in locations:
        speed = (loc.get("locationSpeedMps") or 0) * 3.6
        speed_acc = loc.get("locationSpeedAccuracyMps")
        location_acc = loc.get("locationAccuracy")
        ts = loc["timestampMs"]
        speed_acc_text = f"{speed_acc:.2f}" if speed_acc is not None else "N/A"
        location_acc_text = f"{location_acc:.1f}" if location_acc is not None else "N/A"
        print(
            f"  speed={speed:.1f} km/h  speedAcc={speed_acc_text} m/s "
            f"locationAcc={location_acc_text} m  ts={ts}"
        )
