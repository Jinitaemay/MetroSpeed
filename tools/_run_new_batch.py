"""
批量跑新记录的多组参数对比

用法：
    python tools/_run_new_batch.py --dir <数据目录> [--files <file1,file2,...>]

不指定 --files 时跑目录下所有 *.jsonl（排除 _replay_ 文件）。
数据目录也可通过环境变量 METROSPEED_DATA_DIR 指定（--dir 优先）。
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
REPLAY = TOOLS_DIR / "replay_estimator.py"

# 参数组合：(标签, 参数列表)
CONFIGS = [
    ("pure", []),
    ("anchor-v2", ["--anchor-v2", "--pure-zero"]),
    ("anchor-v2 -40ms", ["--anchor-v2", "--pure-zero", "--gnss-lag-ms=-40"]),
    ("anchor 5s", ["--anchor-v2", "--pure-zero", "--gnss-lag-ms=-40", "--anchor-interval-ms=5000"]),
    ("anchor 10s", ["--anchor-v2", "--pure-zero", "--gnss-lag-ms=-40", "--anchor-interval-ms=10000"]),
    ("anchor 30s", ["--anchor-v2", "--pure-zero", "--gnss-lag-ms=-40", "--anchor-interval-ms=30000"]),
    ("anchor 60s", ["--anchor-v2", "--pure-zero", "--gnss-lag-ms=-40", "--anchor-interval-ms=60000"]),
]


def resolve_files(data_dir: Path, files_arg: str | None) -> list[Path]:
    if files_arg:
        files = [f.strip() for f in files_arg.split(",") if f.strip()]
        return [Path(f) if Path(f).is_absolute() else data_dir / f for f in files]
    all_files = sorted(data_dir.glob("*.jsonl"))
    return [f for f in all_files if "_replay_" not in f.name]


def run_replay(filepath, extra_args):
    cmd = [sys.executable, str(REPLAY), str(filepath)] + extra_args
    result = subprocess.run(
        cmd,
        capture_output=True, text=True, cwd=str(TOOLS_DIR.parent),
        timeout=120,
    )
    if result.returncode != 0:
        return None, result.stderr.strip()[:200]
    try:
        d = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None, "JSON parse error"
    return d, None


def extract_metrics(d, is_anchor):
    if is_anchor:
        comp = d.get("anchoredComparison", {})
        spd = d.get("anchorSpeed", {})
    else:
        comp = d.get("locationComparison", {})
        spd = d.get("speed", {})

    moving = comp.get("moving", {})
    all_comp = comp.get("all", {})

    return {
        "moving_mae": moving.get("maeKmh"),
        "moving_count": moving.get("count", 0),
        "all_mae": all_comp.get("maeKmh"),
        "all_count": all_comp.get("count", 0),
        "pairs": comp.get("pairedLocationRows", 0),
        "max_kmh": spd.get("maxKmh", 0),
        "samples": d.get("sensorSamples", 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", type=Path,
                        default=Path(os.environ.get("METROSPEED_DATA_DIR", "")),
                        help="数据目录（默认读环境变量 METROSPEED_DATA_DIR）")
    parser.add_argument("--files", type=str, default=None,
                        help="逗号分隔的文件名列表；不指定则跑目录下所有 *.jsonl（排除 _replay_）")
    args = parser.parse_args()

    if not args.dir or not args.dir.exists():
        print(f"错误：数据目录不存在或未指定：{args.dir}", file=sys.stderr)
        print("请用 --dir <目录> 或设置环境变量 METROSPEED_DATA_DIR", file=sys.stderr)
        return 1

    files = resolve_files(args.dir, args.files)
    if not files:
        print(f"错误：目录 {args.dir} 下没有可处理的 jsonl 文件", file=sys.stderr)
        return 1

    # 表头
    config_labels = [c[0] for c in CONFIGS]
    print(f"{'文件':<45}", end="")
    for label in config_labels:
        print(f" {label:>12}", end="")
    print()
    print("-" * (45 + 13 * len(config_labels)))

    results = {}
    successful_runs = 0
    metric_runs = 0
    failed_runs = 0
    missing_metric_runs = 0

    for filepath in files:
        result_key = str(filepath.resolve())
        if not filepath.exists():
            print(f"{filepath.name[:43]:<45}  FILE NOT FOUND")
            failed_runs += 1
            continue

        print(f"{filepath.name[:43]:<45}", end="", flush=True)

        results[result_key] = {}

        for label, extra_args in CONFIGS:
            is_anchor = "anchor" in label
            d, err = run_replay(filepath, extra_args)
            if d is None:
                print(f" {'ERR':>12}", end="", flush=True)
                results[result_key][label] = None
                failed_runs += 1
                continue

            metrics = extract_metrics(d, is_anchor)
            if metrics["samples"] <= 0:
                print(f" {'EMPTY':>12}", end="", flush=True)
                results[result_key][label] = None
                failed_runs += 1
                continue
            mae = metrics["moving_mae"]
            if mae is not None:
                successful_runs += 1
                metric_runs += 1
                print(f" {mae:>11.2f}", end="", flush=True)
            else:
                print(f" {'N/A':>12}", end="", flush=True)
                failed_runs += 1
                missing_metric_runs += 1

            results[result_key][label] = metrics

        print()

    print()
    print("=== 详细数据 (moving MAE, km/h) ===")
    print()

    # 详细表格
    print(f"{'文件':<45} {'模式':<15} {'MAE':>8} {'count':>6} {'max':>6} {'samples':>8}")
    print("-" * 90)

    for filepath in files:
        result_key = str(filepath.resolve())
        fname = filepath.name
        if result_key not in results:
            continue
        first = True
        for label, _ in CONFIGS:
            m = results[result_key].get(label)
            if m is None:
                continue
            mae = m["moving_mae"]
            mae_text = f"{mae:.2f}" if mae is not None else "N/A"
            if first:
                print(f"{fname[:43]:<45} {label:<15} {mae_text:>8} {m['moving_count']:>6} {m['max_kmh']:>6.0f} {m['samples']:>8}")
                first = False
            else:
                print(f"{'':<45} {label:<15} {mae_text:>8} {m['moving_count']:>6}")
        print()

    print(
        f"summary: success={successful_runs} failed={failed_runs} "
        f"with_moving_mae={metric_runs} missing_moving_mae={missing_metric_runs}"
    )
    return 0 if successful_runs > 0 and metric_runs > 0 and failed_runs == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
