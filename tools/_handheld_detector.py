import json
import math
import sys
from pathlib import Path
from collections import deque

GYRO_WINDOW = 40
GYRO_RMS_THRESHOLD = 0.3
ZCR_THRESHOLD = 5.0
ACTIVATE_FRAMES = 40


def detect_handheld(jsonl_path):
    gyro_buffer: deque[dict] = deque(maxlen=GYRO_WINDOW)
    suspected_streak = 0
    activated = False
    results = []

    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("recordType") != "sensor":
                continue

            gx = row.get("gyroX")
            gy = row.get("gyroY")
            gz = row.get("gyroZ")
            if gx is None or gy is None or gz is None:
                continue

            ts = row.get("timestampMs", 0)
            gyro_buffer.append({"gx": gx, "gy": gy, "gz": gz, "ts": ts})

            if len(gyro_buffer) < GYRO_WINDOW:
                continue

            rms = math.sqrt(
                sum(v["gx"] ** 2 + v["gy"] ** 2 + v["gz"] ** 2 for v in gyro_buffer)
                / GYRO_WINDOW
            )

            zc = 0
            prev = gyro_buffer[0]
            for v in list(gyro_buffer)[1:]:
                if v["gx"] * prev["gx"] <= 0 and v["gx"] != prev["gx"]:
                    zc += 1
                if v["gy"] * prev["gy"] <= 0 and v["gy"] != prev["gy"]:
                    zc += 1
                if v["gz"] * prev["gz"] <= 0 and v["gz"] != prev["gz"]:
                    zc += 1
                prev = v

            window_duration = (gyro_buffer[-1]["ts"] - gyro_buffer[0]["ts"]) / 1000.0
            if window_duration <= 0:
                window_duration = GYRO_WINDOW / 40.0
            zcr = zc / window_duration

            suspected = rms > GYRO_RMS_THRESHOLD and zcr > ZCR_THRESHOLD

            if suspected:
                suspected_streak += 1
            else:
                suspected_streak = 0

            if suspected_streak >= ACTIVATE_FRAMES and not activated:
                activated = True
                results.append({
                    "event": "activated",
                    "timestampMs": ts,
                    "gyroRms": rms,
                    "zcr": zcr,
                })

    return results


def main():
    files = []
    for i, arg in enumerate(sys.argv):
        if arg == "--dir" and i + 1 < len(sys.argv):
            data_dir = Path(sys.argv[i + 1])
            if data_dir.exists():
                files = sorted(data_dir.glob("*.jsonl"))
                files = [str(f) for f in files if "_replay_" not in f.name]
            break

    if not files:
        print("Usage: python _handheld_detector.py --dir <data_dir>")
        sys.exit(1)

    print(f"gyroRms threshold > {GYRO_RMS_THRESHOLD}")
    print(f"zeroCrossingRate threshold > {ZCR_THRESHOLD}/s")
    print(f"activation streak = {ACTIVATE_FRAMES} frames")
    print()

    total_activations = 0
    for fpath in files:
        fname = Path(fpath).name
        results = detect_handheld(fpath)

        if results:
            for r in results:
                sec = r["timestampMs"] / 1000.0
                print(
                    f"{r['event']}  {fname}  "
                    f"t={sec:.1f}s  "
                    f"rms={r['gyroRms']:.3f}  "
                    f"zcr={r['zcr']:.1f}/s"
                )
            total_activations += 1
        else:
            print(f"OK     {fname}")

    print()
    print(f"Files with activations: {total_activations}/{len(files)}")


if __name__ == "__main__":
    main()
