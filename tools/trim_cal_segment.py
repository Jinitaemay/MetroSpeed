#!/usr/bin/env python3
import json, sys, argparse
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("jsonl", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--duration-sec", type=int, default=300)
    parser.add_argument("--index", type=int, default=0, help="Which parking calibration to slice (0=first real one)")
    parser.add_argument("--max-speed-kmh", type=float, default=1.0, help="Max speed at parking event to consider real")
    args = parser.parse_args()

    rows = []
    with open(args.jsonl, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    candidates = []
    for i, row in enumerate(rows):
        event = str(row.get("event") or row.get("notes") or "")
        if event not in ("停车校准", "到站校准"):
            continue
        speed = row.get("estimatedSpeedKmh") or row.get("locationSpeedMps")
        if speed is not None:
            speed_kmh = float(speed) * (3.6 if row.get("locationSpeedMps") else 1.0)
        else:
            speed_kmh = 999
        candidates.append((i, row, speed_kmh))

    real = [(i, r, s) for i, r, s in candidates if s <= args.max_speed_kmh]
    if args.index >= len(real):
        print(f"Only {len(real)} real parking calibrations found (index {args.index} out of range)")
        print("Candidates:")
        for i, r, s in candidates:
            print(f"  row {i}: speed={s:.1f} km/h  t={r.get('timestampMs')}")
        return 1

    idx, cal_row, speed_kmh = real[args.index]
    start_ms = int(cal_row["timestampMs"])
    end_ms = start_ms + args.duration_sec * 1000

    out_rows = [cal_row]
    for row in rows[idx + 1:]:
        ts = int(row.get("timestampMs", 0))
        if ts >= end_ms:
            break
        out_rows.append(row)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for row in out_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"Trimmed {len(out_rows)} rows from t={start_ms/1000:.0f}s +{args.duration_sec}s (speed at cal={speed_kmh:.1f} km/h)")
    print(f"Written to {args.out}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
