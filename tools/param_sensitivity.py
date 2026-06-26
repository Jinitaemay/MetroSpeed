#!/usr/bin/env python3
"""参数敏感度扫描 — subprocess 运行 replay_estimator.py 改 CLI 参数"""
import argparse, json, subprocess, sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
REPLAY = ROOT / "tools" / "replay_estimator.py"

SCANS: List[Dict[str, Any]] = [
    # effective acceleration (7 existing CLI)
    {"name": "curve_positive_scale", "flag": "--curve-positive-scale", "default": 0.35, "fmt": ".2f"},
    {"name": "curve_negative_scale", "flag": "--curve-negative-scale", "default": 0.35, "fmt": ".2f"},
    {"name": "low_confidence_positive_scale", "flag": "--low-confidence-positive-scale", "default": 0.55, "fmt": ".2f"},
    {"name": "low_confidence_negative_scale", "flag": "--low-confidence-negative-scale", "default": 0.55, "fmt": ".2f"},
    {"name": "braking_negative_scale", "flag": "--braking-negative-scale", "default": 1.0, "fmt": ".2f"},
    {"name": "vibration_threshold", "flag": "--vibration-threshold", "default": 0.85, "fmt": ".2f"},
    {"name": "vibration_scale", "flag": "--vibration-scale", "default": 0.18, "fmt": ".2f"},
    # signal preprocessing
    {"name": "low_pass_alpha", "flag": "--low-pass-alpha", "default": 0.22, "fmt": ".2f"},
    {"name": "accel_clip_ceiling", "flag": "--accel-clip-ceiling", "default": 3.5, "fmt": ".1f"},
    {"name": "dt_fallback", "flag": "--dt-fallback", "default": 0.02, "fmt": ".3f"},
    {"name": "dt_clamp_lo", "flag": "--dt-clamp-lo", "default": 0.005, "fmt": ".3f"},
    {"name": "dt_clamp_hi", "flag": "--dt-clamp-hi", "default": 0.08, "fmt": ".3f"},
    # calibration
    {"name": "calibration_duration_ms", "flag": "--calibration-duration-ms", "default": 1500, "fmt": ".0f"},
    {"name": "calibration_rms_threshold", "flag": "--calibration-rms-threshold", "default": 0.12, "fmt": ".2f"},
    {"name": "calibration_gravity_error", "flag": "--calibration-gravity-error", "default": 0.25, "fmt": ".2f"},
    {"name": "calibration_motion_gyro_mean", "flag": "--calibration-motion-gyro-mean", "default": 0.08, "fmt": ".2f"},
    {"name": "calibration_motion_gyro_max", "flag": "--calibration-motion-gyro-max", "default": 0.25, "fmt": ".2f"},
    {"name": "calibration_motion_acc_step", "flag": "--calibration-motion-acc-step", "default": 0.65, "fmt": ".2f"},
    {"name": "calibration_reject_cooldown_ms", "flag": "--calibration-reject-cooldown-ms", "default": 10000, "fmt": ".0f"},
    {"name": "calibration_parking_success_ms", "flag": "--calibration-parking-success-ms", "default": 5000, "fmt": ".0f"},
    {"name": "calibration_parking_reject_ms", "flag": "--calibration-parking-reject-ms", "default": 5000, "fmt": ".0f"},
    # axis init + tracking
    {"name": "axis_init_acc_threshold", "flag": "--axis-init-acc-threshold", "default": 0.18, "fmt": ".2f"},
    {"name": "axis_init_gyro_threshold", "flag": "--axis-init-gyro-threshold", "default": 0.18, "fmt": ".2f"},
    {"name": "axis_locked_lateral", "flag": "--axis-locked-lateral", "default": 0.08, "fmt": ".2f"},
    {"name": "axis_locked_gyro_instant", "flag": "--axis-locked-gyro-instant", "default": 0.10, "fmt": ".2f"},
    {"name": "axis_locked_gyro_mean", "flag": "--axis-locked-gyro-mean", "default": 0.08, "fmt": ".2f"},
    {"name": "axis_locked_acc", "flag": "--axis-locked-acc", "default": 0.25, "fmt": ".2f"},
    {"name": "axis_locked_speed", "flag": "--axis-locked-speed", "default": 5.0, "fmt": ".1f"},
    {"name": "axis_unlocked_lateral", "flag": "--axis-unlocked-lateral", "default": 0.18, "fmt": ".2f"},
    {"name": "axis_unlocked_gyro_instant", "flag": "--axis-unlocked-gyro-instant", "default": 0.16, "fmt": ".2f"},
    {"name": "axis_unlocked_gyro_mean", "flag": "--axis-unlocked-gyro-mean", "default": 0.14, "fmt": ".2f"},
    {"name": "axis_stop_update_speed", "flag": "--axis-stop-update-speed", "default": 8.0, "fmt": ".1f"},
    {"name": "axis_speed_threshold", "flag": "--axis-speed-threshold", "default": 3.0, "fmt": ".1f"},
    {"name": "axis_acc_high_speed", "flag": "--axis-acc-high-speed", "default": 0.16, "fmt": ".2f"},
    {"name": "axis_acc_low_speed", "flag": "--axis-acc-low-speed", "default": 0.10, "fmt": ".2f"},
    {"name": "axis_mix_locked", "flag": "--axis-mix-locked", "default": 0.003, "fmt": ".3f"},
    {"name": "axis_mix_unlocked", "flag": "--axis-mix-unlocked", "default": 0.025, "fmt": ".3f"},
    {"name": "axis_ortho_threshold", "flag": "--axis-ortho-threshold", "default": 0.15, "fmt": ".2f"},
    {"name": "axis_reset_alignment", "flag": "--axis-reset-alignment", "default": 0.35, "fmt": ".2f"},
    {"name": "axis_reset_speed", "flag": "--axis-reset-speed", "default": 2.0, "fmt": ".1f"},
    {"name": "axis_reset_acc", "flag": "--axis-reset-acc", "default": 0.18, "fmt": ".2f"},
    {"name": "axis_reset_seed_ratio", "flag": "--axis-reset-seed-ratio", "default": 1.8, "fmt": ".1f"},
    # axis lock trigger + stability
    {"name": "axis_lock_speed", "flag": "--axis-lock-speed", "default": 5.0, "fmt": ".1f"},
    {"name": "axis_lock_time_ms", "flag": "--axis-lock-time-ms", "default": 30000, "fmt": ".0f"},
    {"name": "axis_lock_update_count", "flag": "--axis-lock-update-count", "default": 60, "fmt": ".0f"},
    {"name": "axis_window_min_frames", "flag": "--axis-window-min-frames", "default": 20, "fmt": ".0f"},
    {"name": "axis_window_max_frames", "flag": "--axis-window-max-frames", "default": 80, "fmt": ".0f"},
    {"name": "axis_window_max_ms", "flag": "--axis-window-max-ms", "default": 1800, "fmt": ".0f"},
    {"name": "axis_stable_acc_step", "flag": "--axis-stable-acc-step", "default": 0.35, "fmt": ".2f"},
    {"name": "axis_stable_gravity_dev", "flag": "--axis-stable-gravity-dev", "default": 0.45, "fmt": ".2f"},
    {"name": "axis_stable_gyro", "flag": "--axis-stable-gyro", "default": 0.12, "fmt": ".2f"},
    {"name": "axis_stable_forward_var", "flag": "--axis-stable-forward-var", "default": 0.08, "fmt": ".2f"},
    {"name": "axis_unstable_acc_step", "flag": "--axis-unstable-acc-step", "default": 0.85, "fmt": ".2f"},
    {"name": "axis_unstable_gravity_dev", "flag": "--axis-unstable-gravity-dev", "default": 1.2, "fmt": ".2f"},
    # motion state detection
    {"name": "longcal_timeout_ms", "flag": "--longcal-timeout-ms", "default": 360000, "fmt": ".0f"},
    {"name": "idle_forward", "flag": "--idle-forward", "default": 0.035, "fmt": ".3f"},
    {"name": "idle_speed", "flag": "--idle-speed", "default": 0.25, "fmt": ".2f"},
    {"name": "accel_forward", "flag": "--accel-forward", "default": 0.055, "fmt": ".3f"},
    {"name": "brake_forward", "flag": "--brake-forward", "default": -0.055, "fmt": ".3f"},
    {"name": "low_conf_forward_var", "flag": "--low-conf-forward-var", "default": 0.55, "fmt": ".2f"},
    {"name": "low_conf_gyro", "flag": "--low-conf-gyro", "default": 1.8, "fmt": ".1f"},
    {"name": "vibration_conduction_gyro", "flag": "--vibration-conduction-gyro", "default": 0.06, "fmt": ".2f"},
    {"name": "vibration_strong_gyro", "flag": "--vibration-strong-gyro", "default": 0.06, "fmt": ".2f"},
    {"name": "vibration_strong_gravity_dev", "flag": "--vibration-strong-gravity-dev", "default": 1.2, "fmt": ".1f"},
    {"name": "curve_a_lateral", "flag": "--curve-a-lateral", "default": 0.18, "fmt": ".2f"},
    {"name": "curve_a_gyro", "flag": "--curve-a-gyro", "default": 0.045, "fmt": ".3f"},
    {"name": "curve_a_ratio", "flag": "--curve-a-ratio", "default": 1.15, "fmt": ".2f"},
    {"name": "curve_b_gyro", "flag": "--curve-b-gyro", "default": 0.09, "fmt": ".2f"},
    {"name": "curve_b_gyro_var", "flag": "--curve-b-gyro-var", "default": 0.0018, "fmt": ".4f"},
    {"name": "curve_b_lateral", "flag": "--curve-b-lateral", "default": 0.10, "fmt": ".2f"},
    # effective acceleration misc
    {"name": "dead_zone", "flag": "--dead-zone", "default": 0.025, "fmt": ".3f"},
    {"name": "conduction_scale", "flag": "--conduction-scale", "default": 0.45, "fmt": ".2f"},
    # confidence
    {"name": "confidence_base", "flag": "--confidence-base", "default": 0.86, "fmt": ".2f"},
    {"name": "confidence_decay_divisor", "flag": "--confidence-decay-divisor", "default": 240000.0, "fmt": ".0f"},
    {"name": "confidence_decay_max", "flag": "--confidence-decay-max", "default": 0.35, "fmt": ".2f"},
    {"name": "confidence_gyro_divisor", "flag": "--confidence-gyro-divisor", "default": 3.0, "fmt": ".1f"},
    {"name": "confidence_gyro_max", "flag": "--confidence-gyro-max", "default": 0.2, "fmt": ".1f"},
    {"name": "confidence_penalty_curve", "flag": "--confidence-penalty-curve", "default": 0.28, "fmt": ".2f"},
    {"name": "confidence_penalty_low", "flag": "--confidence-penalty-low", "default": 0.35, "fmt": ".2f"},
    {"name": "confidence_penalty_strong", "flag": "--confidence-penalty-strong", "default": 0.48, "fmt": ".2f"},
    {"name": "confidence_penalty_conduction", "flag": "--confidence-penalty-conduction", "default": 0.20, "fmt": ".2f"},
    {"name": "confidence_calibrating", "flag": "--confidence-calibrating", "default": 0.35, "fmt": ".2f"},
    {"name": "confidence_clamp_lo", "flag": "--confidence-clamp-lo", "default": 0.08, "fmt": ".2f"},
    {"name": "confidence_clamp_hi", "flag": "--confidence-clamp-hi", "default": 0.95, "fmt": ".2f"},
]


def replay_one(jsonl: Path, flag: str, value_str: str, bucket: str = None) -> Dict[str, Any]:
    cmd = [sys.executable, str(REPLAY), str(jsonl), "--no-infer-start", flag, value_str]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200])
    d = json.loads(r.stdout)
    if bucket:
        decay = d.get("calibrationDecay", {})
        b = decay.get(bucket)
        if b is None:
            available = sorted(decay.keys())
            raise RuntimeError(f"bucket '{bucket}' not found; available: {available}")
        moving = b.get("moving", {})
    else:
        loc = d.get("locationComparison", {})
        moving = loc.get("moving", {})
    return {
        "mae": moving.get("maeKmh"),
        "bias": moving.get("biasKmh"),
        "count": moving.get("count", 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Parameter sensitivity scan")
    parser.add_argument("jsonl", type=Path)
    parser.add_argument("--perturbation", type=float, default=0.2)
    parser.add_argument("--min-paired", type=int, default=20)
    parser.add_argument("--param", type=str, default=None, help="Scan only one parameter")
    parser.add_argument("--bucket", type=str, default=None, help="Use calibrationDecay bucket instead of global MAE")
    args = parser.parse_args()
    pert = args.perturbation
    bucket = args.bucket

    baseline = replay_one(args.jsonl, "--curve-positive-scale", "0.35", bucket)
    if not baseline["count"] or baseline["count"] < args.min_paired:
        print(f"too few pairs ({baseline.get('count', 0)}), skip")
        return 1

    label = f"bucket {bucket} MAE" if bucket else "MAE"
    print(f"file: {args.jsonl.name}")
    print(f"perturbation: +/-{pert*100:.0f}%")
    print(f"baseline: {label}={baseline['mae']:.2f}  count={baseline['count']}")
    print()

    scans = SCANS
    if args.param:
        scans = [s for s in SCANS if s["name"] == args.param]
        if not scans:
            print(f"unknown param: {args.param}")
            return 1

    results: List[Dict[str, Any]] = []
    for s in scans:
        name = s["name"]
        flag = s["flag"]
        default = s["default"]
        fmt = s["fmt"]
        up_val = default * (1 + pert)
        dn_val = default * (1 - pert)
        try:
            up = replay_one(args.jsonl, flag, f"{up_val:{fmt}}", bucket)
            dn = replay_one(args.jsonl, flag, f"{dn_val:{fmt}}", bucket)
        except Exception as e:
            print(f"  SKIP {name}: {e}")
            continue

        up_mae = up.get("mae")
        dn_mae = dn.get("mae")
        if up_mae is None or dn_mae is None:
            print(f"  SKIP {name}: mae missing up={up_mae} dn={dn_mae}")
            continue
        up_d = up_mae - baseline["mae"]
        dn_d = dn_mae - baseline["mae"]
        impact = abs(up_d) + abs(dn_d)
        results.append({
            "param": name, "default": default,
            "up_delta": up_d, "dn_delta": dn_d,
            "impact": impact,
        })

    results.sort(key=lambda r: r["impact"], reverse=True)
    if not results:
        print("no results")
        return 0

    print(f"{'param':<38} {'default':>8} {'+':>8} {'-':>8} {'impact':>7}")
    print("-" * 72)
    for r in results:
        print(
            f"{r['param']:<38} {r['default']:>8.4f} "
            f"{r['up_delta']:>+8.2f} {r['dn_delta']:>+8.2f} "
            f"{r['impact']:>7.2f}"
        )

    sensitive = [r for r in results if r["impact"] > 0.5]
    if sensitive:
        print(f"\n=== {len(sensitive)} sensitive (impact > 0.5) ===")
        for r in sensitive:
            print(f"  {r['param']}: impact={r['impact']:.2f}  up={r['up_delta']:+.2f}  dn={r['dn_delta']:+.2f}")
    else:
        print("\nno parameters exceeded impact 0.5 threshold")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
