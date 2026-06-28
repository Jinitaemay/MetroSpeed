"""锚点间隔多进程并行扫描

用法：
    python tools/_scan_anchor_interval.py --dir <数据目录> [--files <file1,file2,...>]

不指定 --files 时跑目录下所有 *.jsonl（排除 _replay_ 文件）。
数据目录也可通过环境变量 METROSPEED_DATA_DIR 指定（--dir 优先）。
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import cpu_count

TOOLS = Path(__file__).resolve().parent
REPLAY = TOOLS / "replay_estimator.py"
PYTHON = sys.executable

INTERVALS = [0, 2000, 5000, 10000, 15000, 20000, 30000]
CWD = str(TOOLS.parent)


def resolve_files(data_dir: Path, files_arg: str | None) -> list[Path]:
    if files_arg:
        files = [f.strip() for f in files_arg.split(",") if f.strip()]
        return [Path(f) if Path(f).is_absolute() else data_dir / f for f in files]
    all_files = sorted(data_dir.glob("*.jsonl"))
    return [f for f in all_files if "_replay_" not in f.name]


def run_one(args):
    fname, interval, data_dir = args
    path = data_dir / fname if not Path(fname).is_absolute() else Path(fname)
    r = subprocess.run(
        [PYTHON, str(REPLAY), str(path),
         "--anchor-v2", "--pure-zero", "--gnss-lag-ms=-40",
         f"--anchor-interval-ms={interval}"],
        capture_output=True, text=True, cwd=CWD
    )
    if r.returncode != 0:
        return (fname, interval, None)
    d = json.loads(r.stdout)
    mae = d["anchoredComparison"]["moving"]["maeKmh"]
    pairs = d["anchoredComparison"]["pairedLocationRows"]
    return (fname, interval, mae, pairs)


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

    file_names = [f.name for f in files]

    workers = max(1, cpu_count() - 1)
    tasks = [(name, interval, args.dir) for name in file_names for interval in INTERVALS]
    total = len(tasks)
    print(f"Running {total} jobs on {workers} workers ...")

    n = 0
    results = {}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(run_one, task): task for task in tasks}
        for fut in as_completed(futures):
            res = fut.result()
            task = futures[fut]
            results[(res[0], res[1])] = res
            n += 1
            if n % 8 == 0 or n == total:
                print(f"  {n}/{total} done")

    print(f"\n{'file':<42} {'int_s':>5} {'mae':>8} {'pairs':>6}  ratio")
    print("-" * 72)
    for fname in file_names:
        base_mae = None
        for interval in INTERVALS:
            r = results.get((fname, interval))
            if r is None or r[2] is None:
                print(f"{fname[:40]:<42} {interval/1000:>4.0f}s  ERROR")
                continue
            mae = r[2]
            pairs = r[3]
            if interval == 0:
                base_mae = mae
                ratio = "1.00x"
            elif base_mae is not None and base_mae > 0.001:
                ratio = f"{mae/base_mae:.2f}x"
            else:
                ratio = ""
            print(f"{fname[:40]:<42} {interval/1000:>4.0f}s  {mae:>8.2f} {pairs:>6}  {ratio}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
