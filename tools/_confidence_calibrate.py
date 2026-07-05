"""Run confidence calibration across all old records (parallel)."""
import json
import sys
from pathlib import Path
from collections import defaultdict
from multiprocessing import Pool, cpu_count

sys.path.insert(0, str(Path(__file__).parent))
from replay_estimator import read_jsonl, replay


def load_location_timeline(jsonl_path: str) -> list[tuple[int, float]]:
    rows = read_jsonl(Path(jsonl_path))
    locations: list[tuple[int, float]] = []
    for row in rows:
        if row.get("recordType") != "location":
            continue
        if row.get("locationSourceType") not in (1, 4):
            continue
        ts = row.get("timestampMs", 0)
        spd = row.get("locationSpeedMps")
        if ts is None or spd is None:
            continue
        locations.append((int(ts), float(spd)))
    return locations


def nearest_gnss(target_ms: int, locations: list[tuple[int, float]], max_delta_ms: int = 1000) -> float | None:
    best = None
    best_dist = max_delta_ms + 1
    for ts, spd in locations:
        d = abs(ts - target_ms)
        if d < best_dist:
            best_dist = d
            best = spd
    return best if best_dist <= max_delta_ms else None


def process_one(fpath: Path) -> list[tuple[float, float, str, str]]:
    fname = fpath.name
    locations = load_location_timeline(str(fpath))
    if len(locations) < 5:
        print(f"  SKIP  {fname} (too few GNSS: {len(locations)})")
        return []
    rows = read_jsonl(fpath)
    outputs, _ = replay(rows, strict_start=False, infer_start_from_sensor=True)
    if not outputs:
        print(f"  SKIP  {fname} (no output)")
        return []

    frames: list[tuple[float, float, str, str]] = []
    for frame in outputs:
        ts = int(frame["timestampMs"])
        inertial = frame["speedKmh"]
        gnss_mps = nearest_gnss(ts, locations, max_delta_ms=1000)
        if gnss_mps is None:
            continue
        gnss = gnss_mps * 3.6
        if inertial < 1.0 and gnss < 1.0:
            continue
        error = abs(inertial - gnss)
        conf = frame.get("confidence", 0.0)
        state = frame.get("motionState", "unknown")
        frames.append((conf, error, state, fname))
    print(f"  OK    {fname}  GNSS={len(locations):>3d}  frames={len(frames):>5d}")
    return frames


def quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * q))]


def main():
    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"C:\Users\18918\Documents\研究记录\旧记录")
    files = sorted(data_dir.glob("*.jsonl"))
    files = [f for f in files if "_replay_" not in f.name]
    if not files:
        print(f"No jsonl files in {data_dir}")
        sys.exit(1)

    n_workers = min(len(files), cpu_count())
    print(f"Processing {len(files)} records with {n_workers} workers...")
    print()

    with Pool(processes=n_workers) as pool:
        results = pool.map(process_one, files)

    all_frames: list[tuple[float, float, str, str]] = []
    for r in results:
        all_frames.extend(r)

    print()
    print(f"Total matched frames: {len(all_frames)}")
    print()

    conf_buckets: dict[int, list[float]] = defaultdict(list)
    for conf, err, _, _ in all_frames:
        bucket = int(conf * 100) // 5 * 5
        if bucket < 5:
            bucket = 5
        conf_buckets[bucket].append(err)

    print("=== 置信度分桶 vs 误差 (每5%) ===")
    print(f"{'置信度':<10s} {'帧数':>6s} {'均值':>8s} {'中位':>8s} {'P90':>8s}")
    for bucket in sorted(conf_buckets.keys()):
        errs = conf_buckets[bucket]
        if len(errs) < 50:
            continue
        mean = sum(errs) / len(errs)
        print(f"{bucket:>3d}%     {len(errs):>6d}  {mean:>6.2f}  {quantile(errs, 0.5):>6.2f}  {quantile(errs, 0.9):>6.2f} km/h")

    print()
    print("=== 运动状态 vs 误差 ===")
    state_errs: dict[str, list[float]] = defaultdict(list)
    for conf, err, state, _ in all_frames:
        state_errs[state].append(err)
    for state in sorted(state_errs.keys()):
        errs = state_errs[state]
        print(f"  {state:<22s}  n={len(errs):>6d}  mean={sum(errs)/len(errs):.2f}  p50={quantile(errs,0.5):.2f}  p90={quantile(errs,0.9):.2f} km/h")


if __name__ == "__main__":
    main()
