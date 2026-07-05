import json
import sys
from pathlib import Path
from collections import defaultdict

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


def scan_lag_for_states(outputs: list[dict], locations: list[tuple[int, float]],
                        lag_steps: list[int]) -> dict[str, dict[int, float]]:
    """Scan lag per motion state (accel / cruise / braking only), |acc|>=0.04 filter."""
    state_errors: dict[str, dict[int, list[float]]] = {
        "straight_acceleration": {lag: [] for lag in lag_steps},
        "braking": {lag: [] for lag in lag_steps},
        "cruise": {lag: [] for lag in lag_steps},
    }

    for frame in outputs:
        ts = int(frame["timestampMs"])
        inertial = frame["speedKmh"]
        if abs(frame.get("filteredAccMag", 0)) < 0.04:
            continue
        state = frame["motionState"]
        if state not in state_errors:
            continue

        for lag in lag_steps:
            gnss_mps = nearest_gnss(ts + lag, locations, max_delta_ms=5000)
            if gnss_mps is None:
                continue
            gnss = gnss_mps * 3.6
            if inertial < 1.0 and gnss < 1.0:
                continue
            state_errors[state][lag].append(abs(inertial - gnss))

    result: dict[str, dict[int, float]] = {}
    for state in state_errors:
        result[state] = {}
        for lag in lag_steps:
            errs = state_errors[state][lag]
            if len(errs) < 10:
                result[state][lag] = float('inf')
            else:
                result[state][lag] = sum(errs) / len(errs)
    return result


def main():
    if len(sys.argv) < 2:
        print("Usage: python _confidence_analysis.py <path.jsonl>")
        sys.exit(1)

    jsonl_path = sys.argv[1]
    locations = load_location_timeline(jsonl_path)
    rows = read_jsonl(Path(jsonl_path))
    outputs, _events = replay(rows, strict_start=False, infer_start_from_sensor=True)

    lag_steps = list(range(0, 5001, 100))
    results = scan_lag_for_states(outputs, locations, lag_steps)

    for state in ["straight_acceleration", "cruise", "braking"]:
        data = results[state]
        best_lag = min(data, key=lambda k: data[k])
        print(f"\n=== {state} ===")
        for lag in lag_steps:
            marker = " <-- best" if lag == best_lag else ""
            if data[lag] == float('inf'):
                continue
            print(f"  lag={lag:+5d}ms  mean_error={data[lag]:.2f} km/h{marker}")
        print(f"  最佳延迟: {best_lag}ms, min_error={data[best_lag]:.2f} km/h")


if __name__ == "__main__":
    main()
