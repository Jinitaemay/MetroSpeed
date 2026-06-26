"""偏置诊断: cal_0 积分不对称性 + 重力/主轴变化追踪"""
import sys, os, argparse
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from replay_estimator import (
    read_jsonl, SpeedEstimator, make_sensor_frame, event_matches,
    MotionState, v_dot, v_mag,
)

def run_cal0_diag(rows):
    est = SpeedEstimator(
        curve_positive_scale=0.35, curve_negative_scale=0.35,
        low_confidence_positive_scale=0.55, low_confidence_negative_scale=0.55,
        braking_negative_scale=1.0, use_gyro_gravity=False,
    )
    state_names = {v: k for k, v in vars(MotionState).items() if not k.startswith("_")}
    records = []
    running = False
    cal = 0

    for row in rows:
        t = int(row.get("timestampMs", 0))
        ev = str(row.get("event") or "")
        notes = str(row.get("notes") or "")
        if event_matches(ev, "\u5f00\u59cb\u6d4b\u901f") or notes == "measurement started":
            est.start(t); running = True; cal = 0; continue
        if not running: continue
        if event_matches(ev, "\u505c\u8f66\u6821\u51c6") or event_matches(ev, "\u5230\u7ad9\u6821\u51c6"):
            est.calibrate_at_stop(t); cal += 1
            if cal > 1: break
            continue
        if event_matches(ev, "\u505c\u6b62\u6d4b\u901f"): running = False; continue
        if row.get("recordType") != "sensor": continue
        frame = make_sensor_frame(row)
        if frame is None: continue
        o = est.ingest(frame)
        if cal == 0 and est.motion_state != MotionState.CALIBRATING:
            fwd = v_dot(est.filtered_acceleration, est.main_axis)
            st = est.motion_state
            eff = est.effective_forward_acceleration(fwd, st)
            records.append(dict(
                t=t, fwd=fwd, eff=eff,
                state=state_names.get(st, "?"),
                vel=est.velocity_mps * 3.6,
            ))

    if not records:
        print("cal_0: 无帧")
        return

    pos_raw = sum(r["fwd"] for r in records if r["fwd"] > 0)
    neg_raw = sum(r["fwd"] for r in records if r["fwd"] < 0)
    pos_eff = sum(r["eff"] for r in records if r["eff"] > 0)
    neg_eff = sum(r["eff"] for r in records if r["eff"] < 0)
    n_pos = sum(1 for r in records if r["fwd"] > 0)
    n_neg = sum(1 for r in records if r["fwd"] < 0)

    print(f"\n=== cal_0 积分不对称性 ===")
    print(f"frames: {len(records)}  pos={n_pos}  neg={n_neg}")
    print(f"raw fwd:  pos={pos_raw:.1f} neg={neg_raw:.1f} net={pos_raw+neg_raw:.1f}  ratio={abs(pos_raw/(neg_raw or -0.001)):.2f}:1")
    print(f"eff fwd:  pos={pos_eff:.1f} neg={neg_eff:.1f} net={pos_eff+neg_eff:.1f}  ratio={abs(pos_eff/(neg_eff or -0.001)):.2f}:1")

    state_pos = defaultdict(float)
    state_neg = defaultdict(float)
    state_cnt = defaultdict(int)
    for r in records:
        s = r["state"]
        state_cnt[s] += 1
        if r["eff"] > 0: state_pos[s] += r["eff"]
        else: state_neg[s] += r["eff"]

    print(f"\n=== 按状态分 ===")
    for s in sorted(state_cnt, key=lambda x: -state_cnt[x]):
        print(f"  {s:>15}: {state_cnt[s]:>5}f ({state_cnt[s]/len(records)*100:>5.1f}%)  pos={state_pos[s]:>7.1f} neg={state_neg[s]:>7.1f} net={state_pos[s]+state_neg[s]:>7.1f}")

    print(f"\n=== 重力估计 ===")
    print(f"  initial: (0, 0, 9.80665)")
    g = est.gravity_estimate
    print(f"  final:   ({g[0]:.3f}, {g[1]:.3f}, {g[2]:.3f})  |g|={v_mag(g):.4f}")

def run_gravity_diag(rows):
    est = SpeedEstimator(
        curve_positive_scale=0.35, curve_negative_scale=0.35,
        low_confidence_positive_scale=0.55, low_confidence_negative_scale=0.55,
        braking_negative_scale=1.0, use_gyro_gravity=False,
    )
    running = False
    last_gravity = None
    last_axis = None
    changes = []

    for row in rows:
        t = int(row.get("timestampMs", 0))
        ev = str(row.get("event") or "")
        notes = str(row.get("notes") or "")
        if event_matches(ev, "\u5f00\u59cb\u6d4b\u901f") or notes == "measurement started":
            est.start(t); running = True; last_gravity = None; last_axis = None; continue
        if not running: continue
        if event_matches(ev, "\u505c\u8f66\u6821\u51c6") or event_matches(ev, "\u5230\u7ad9\u6821\u51c6"):
            est.calibrate_at_stop(t); continue
        if event_matches(ev, "\u505c\u6b62\u6d4b\u901f"): running = False; continue
        if row.get("recordType") != "sensor": continue
        frame = make_sensor_frame(row)
        if frame is None: continue
        o = est.ingest(frame)

        g = est.gravity_estimate
        m = est.main_axis
        if g != last_gravity or m != last_axis:
            changes.append(dict(
                t_ms=t, gravity=g, axis=m, vel=est.velocity_mps,
                g_changed=g != last_gravity, a_changed=m != last_axis,
            ))
            last_gravity = g
            last_axis = m

    print(f"\n=== 重力/主轴变化 ===")
    print(f"变化次数: {len(changes)}")
    for i, c in enumerate(changes):
        g = c["gravity"]
        m = c["axis"]
        gm = v_mag(g)
        mm = v_mag(m)
        print(f"\n  #{i+1} t={c['t_ms']}ms vel={c['vel']*3.6:.1f}km/h  grav_changed={c['g_changed']} axis_changed={c['a_changed']}")
        print(f"  gravity=({g[0]:.4f},{g[1]:.4f},{g[2]:.4f}) |g|={gm:.4f}")
        print(f"  axis=   ({m[0]:.4f},{m[1]:.4f},{m[2]:.4f}) |m|={mm:.4f}")
        if c["g_changed"] and gm > 0.001:
            import math
            tilt = math.degrees(math.acos(max(-1, min(1, g[2]/gm))))
            proj = v_dot(g, m)
            print(f"  倾斜={tilt:.1f}deg  重力在主轴投影={proj:.4f}m/s2")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("jsonl", type=Path)
    parser.add_argument("--cal0", action="store_true")
    parser.add_argument("--gravity", action="store_true")
    args = parser.parse_args()
    rows = read_jsonl(args.jsonl)

    if args.cal0 or (not args.cal0 and not args.gravity):
        run_cal0_diag(rows)
    if args.gravity or (not args.cal0 and not args.gravity):
        run_gravity_diag(rows)

if __name__ == "__main__":
    main()
