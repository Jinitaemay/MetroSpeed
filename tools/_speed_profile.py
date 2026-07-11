"""Pure inertial speed profile analysis (no GNSS needed).

Usage: python tools/_speed_profile.py <file1.jsonl> [file2.jsonl ...]
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPLAY = Path(__file__).resolve().parent / "replay_estimator.py"

files = [a for a in sys.argv[1:] if not a.startswith("--")]
if not files:
    print("Usage: python tools/_speed_profile.py <file1.jsonl> [file2.jsonl ...]", file=sys.stderr)
    raise SystemExit(1)

success_count = 0
failure_count = 0

for fname in files:
    path = Path(fname)
    if not path.is_absolute():
        data_dir = os.environ.get("METROSPEED_DATA_DIR", ".")
        path = Path(data_dir) / fname

    cmd = [sys.executable, str(REPLAY), str(path), "--no-strict-start"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR {path.name}: {result.stderr.strip()[:200]}")
        failure_count += 1
        continue
    try:
        d = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        print(f"ERROR {path.name}: invalid replay JSON: {error}")
        failure_count += 1
        continue

    spd = d.get("speed", {})
    conf = d.get("confidence", {})
    samples = d.get("sensorSamples", 0)
    if not isinstance(samples, int) or samples <= 0:
        print(f"ERROR {path.name}: replay produced no sensor samples")
        failure_count += 1
        continue
    success_count += 1
    events = d.get("events", [])

    print(f"\n=== {path.name} ===")
    print(f"  samples: {samples}")
    print(f"  maxKmh:   {spd.get('maxKmh', 0):.1f}")
    print(f"  p90Kmh:   {spd.get('p90Kmh', 0):.1f}")
    print(f"  medianKmh:{spd.get('medianKmh', 0):.1f}")
    print(f"  minKmh:   {spd.get('minKmh', 0):.1f}")
    print(f"  lastKmh:  {spd.get('lastKmh', 0):.1f}")
    if conf:
        print(f"  confidence: median={conf.get('median', 0):.2f} p10={conf.get('p10', 0):.2f} min={conf.get('min', 0):.2f}")

    # Calibration events
    cal_events = [e for e in events if "calibration" in str(e.get("event", ""))]
    print(f"  calibrations: {len(cal_events)}")
    for e in cal_events:
        ts = e.get("t", 0)
        cal_count = e.get("calibrationCount", "?")
        g = e.get("gravity", [])
        g_str = f"({g[0]:.3f},{g[1]:.3f},{g[2]:.3f})" if isinstance(g, list) and len(g) >= 3 else str(g)
        print(f"    {e['event']}@{ts} cal#{cal_count} g={g_str}")

    # Key events
    key_events = [e for e in events if e.get("event") in ("stop", "tunnel_enter", "tunnel_exit")]
    if key_events:
        print(f"  key events ({len(key_events)}):")
        for e in key_events[:10]:
            ts = e.get("t", 0)
            ev = e.get("event")
            v = e.get("speedKmh", "")
            print(f"    {ev}@{ts} v={v}")

if success_count == 0 or failure_count > 0:
    raise SystemExit(1)
