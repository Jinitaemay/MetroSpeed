"""隧道诊断: 分段MAE + 纯隧道信号速度曲线"""
import json, sys, os, argparse
from pathlib import Path
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from replay_estimator import *

def extract_tunnel_segments(rows):
    segments = []
    inside = False
    start = 0
    for row in rows:
        evt = str(row.get("event") or "")
        notes = str(row.get("notes") or "")
        if row.get("recordType") != "event":
            continue
        if "\u5165\u96a7" in evt or "\u5165\u96a7" in notes:
            inside = True
            start = row["timestampMs"]
        elif "\u51fa\u96a7" in evt or "\u51fa\u96a7" in notes:
            if inside:
                segments.append((start, row["timestampMs"]))
                inside = False
    return segments, inside

def run_speed_replay(rows):
    est = SpeedEstimator(
        curve_positive_scale=0.35, curve_negative_scale=0.35,
        low_confidence_positive_scale=0.55, low_confidence_negative_scale=0.55,
        braking_negative_scale=1.0, use_gyro_gravity=False,
    )
    running = False
    state_names = {v: k for k, v in vars(MotionState).items() if not k.startswith("_")}
    records = []
    t0 = 0

    for row in rows:
        t = int(row.get("timestampMs", 0))
        ev = str(row.get("event") or "")
        notes = str(row.get("notes") or "")
        if event_matches(ev, "\u5f00\u59cb\u6d4b\u901f") or notes == "measurement started":
            est.start(t); running = True; t0 = t; continue
        if not running: continue
        if event_matches(ev, "\u505c\u8f66\u6821\u51c6") or event_matches(ev, "\u5230\u7ad9\u6821\u51c6"):
            est.calibrate_at_stop(t); continue
        if event_matches(ev, "\u505c\u6b62\u6d4b\u901f"):
            est.stop(t); running = False; continue
        if row.get("recordType") != "sensor": continue
        f = make_sensor_frame(row)
        if f is None: continue
        o = est.ingest(f)
        if o.motion_state != MotionState.CALIBRATING:
            records.append(dict(
                t=t, sec=(t - t0) / 1000,
                spd=o.speed_kmh, cf=o.confidence,
                st=state_names.get(o.motion_state, "?"),
            ))
    return records

def compare_with_gps(rows, est_by_t, segments):
    inside_pairs = []
    outside_pairs = []

    def in_tunnel(t_ms):
        for s, e in segments:
            if s <= t_ms <= e: return True
        return False

    for row in rows:
        if row.get("recordType") != "location": continue
        t = row["timestampMs"]
        gnss_mps = row.get("locationSpeedMps")
        if gnss_mps is None: continue
        nearest_t = min(est_by_t.keys(), key=lambda k: abs(k - t))
        if abs(t - nearest_t) > 2000: continue
        est_spd, state = est_by_t[nearest_t]
        err = est_spd - gnss_mps * 3.6
        pair = {"t": t, "est": est_spd, "gnss": gnss_mps * 3.6, "err": err}
        if in_tunnel(t): inside_pairs.append(pair)
        else: outside_pairs.append(pair)
    return inside_pairs, outside_pairs

def mae_bias(pairs):
    if not pairs: return 0, 0
    errs = [p["err"] for p in pairs]
    return sum(abs(e) for e in errs) / len(errs), sum(errs) / len(errs)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("jsonl", type=Path)
    parser.add_argument("--tunnel", action="store_true", help="分段隧道对比")
    parser.add_argument("--speed", action="store_true", help="纯隧道速度曲线")
    args = parser.parse_args()

    rows = read_jsonl(args.jsonl)
    segments, still_inside = extract_tunnel_segments(rows)

    print(f"文件: {args.jsonl.name}")
    if segments:
        print("隧道段:")
        for i, (s, e) in enumerate(segments):
            print(f"  段{i+1}: {s} -> {e}  ({((e-s)/1000):.0f}s)")
        if still_inside:
            print(f"  未闭合段 (仍在隧道内)")
    elif not segments:
        print("未检测到隧道事件")

    if args.tunnel:
        if not segments:
            print("无隧道段，跳过隧道的开关分析")
            return
        records = run_speed_replay(rows)
        if not records:
            print("无回放帧")
            return
        est_by_t = {r["t"]: (r["spd"], r["st"]) for r in records}
        inside, outside = compare_with_gps(rows, est_by_t, segments)
        im, ib = mae_bias(inside)
        om, ob = mae_bias(outside)
        print(f"\n{'段':<10} {'点数':>5} {'MAE':>8} {'bias':>8}")
        print(f"{'隧道内':<10} {len(inside):>5} {im:>8.2f} {ib:>8.2f}")
        print(f"{'隧道外':<10} {len(outside):>5} {om:>8.2f} {ob:>8.2f}")
        for i, (s, e) in enumerate(segments):
            seg = [p for p in inside if s <= p["t"] <= e]
            if seg:
                m, b = mae_bias(seg)
                print(f"  段{i+1} ({((e-s)/1000):.0f}s): {len(seg)}点 MAE={m:.2f} bias={b:.2f}")

    if args.speed or not args.tunnel:
        records = run_speed_replay(rows)
        if not records:
            print("无回放帧")
            return
        print(f"\n=== 纯惯性速度 ===")
        spds = [r["spd"] for r in records]
        print(f"帧数={len(records)}  max={max(spds):.1f}km/h  median={sorted(spds)[len(spds)//2]:.1f}")
        st_counts = Counter(r["st"] for r in records)
        print(f"state: {dict(st_counts.most_common())}")
        print(f"\n{'sec':>5} {'spd':>6} {'state':>18} {'cf':>6}")
        last_s = -99
        for r in records:
            s = int(r["sec"])
            if s != last_s and s % 5 == 0:
                last_s = s
                print(f"{s:>5} {r['spd']:>6.1f} {r['st']:>18} {r['cf']:>6.3f}")

if __name__ == "__main__":
    main()
