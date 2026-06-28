"""Speed time series analysis — outputs every N seconds to locate drift segments.

Usage: python tools/_speed_series.py <file.jsonl> [interval_sec=5]
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from replay_estimator import replay

fname = sys.argv[1] if len(sys.argv) > 1 else ""
interval = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0

path = Path(fname)
if not path.is_absolute():
    data_dir = os.environ.get("METROSPEED_DATA_DIR", ".")
    path = Path(data_dir) / fname

# Load rows
rows = []
with open(path) as fh:
    for line in fh:
        rows.append(json.loads(line))

outputs, events = replay(rows, strict_start=False, infer_start_from_sensor=False)

print(f"\n=== {path.name} ===")
print(f"  {len(outputs)} output frames")

if not outputs:
    print("  No outputs")
    sys.exit(0)

# Sample every interval seconds
t0 = outputs[0]["timestampMs"]
last_t = 0
print(f"\n  {'time(s)':>8} {'kmh':>6} {'conf':>5} {'state':>12} {'accMag':>7} {'secCal':>6}")
print("  " + "-" * 55)

peak_kmh = 0
peak_t = 0
for o in outputs:
    t = (o["timestampMs"] - t0) / 1000.0
    if t - last_t >= interval or t == 0:
        kmh = o["speedKmh"]
        conf = o["confidence"]
        state = o["motionState"]
        acc = o["filteredAccMag"]
        sec_cal = o.get("secondsSinceCalibration", 0)
        print(f"  {t:>8.0f} {kmh:>6.1f} {conf:>5.2f} {state:>12} {acc:>7.2f} {sec_cal:>6.0f}")
        last_t = t
    if o["speedKmh"] > peak_kmh:
        peak_kmh = o["speedKmh"]
        peak_t = t

print(f"\n  PEAK: {peak_kmh:.1f} km/h at t={peak_t:.0f}s")

# Find segments > 80 km/h
over80 = [(o, (o["timestampMs"] - t0) / 1000.0) for o in outputs if o["speedKmh"] > 80]
if over80:
    print(f"\n  Segments > 80 km/h ({len(over80)} frames):")
    start_t = over80[0][1]
    end_t = over80[-1][1]
    print(f"    from t={start_t:.0f}s to t={end_t:.0f}s ({end_t - start_t:.0f}s duration)")
    # Sample within
    for o, t in over80[::max(1, len(over80) // 10)]:
        print(f"    t={t:.0f}s v={o['speedKmh']:.1f} conf={o['confidence']:.2f} state={o['motionState']} secCal={o.get('secondsSinceCalibration', 0):.0f}")
