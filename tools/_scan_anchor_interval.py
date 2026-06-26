import subprocess, json, os, sys
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import cpu_count

TOOLS = Path(__file__).resolve().parent
REPLAY = TOOLS / "replay_estimator.py"
DATA_DIR = Path(os.environ.get("METROSPEED_DATA_DIR", ""))
PYTHON = sys.executable

FILES = [
    "地铁_陈翔公路-南翔-桃浦新村_平放在地板上_20260621.jsonl",
    "地铁_虹桥2号航站楼-浦东1号2号航站楼_放置在窗台上_20260624.jsonl",
    "地铁_沈杜公路-汇臻路_放置在窗台上_20260624.jsonl",
    "地铁_港城路-高青路-东方体育中心_放置在座位上-平放在地板上_20260624.jsonl",
    "磁浮_浦东1号2号航站楼-龙阳路_放置在窗台上_20260624.jsonl",
    "公交_申崇五线_平放在坐垫上_20260619.jsonl",
    "公交_北安跨线_平放在硬质表面_20260624.jsonl",
    "驾车_东靖路-周家嘴路隧道-周家嘴路-北横通道-北翟路地道-北翟高架路-沪常高速_放置在充电位_20260620.jsonl",
]

INTERVALS = [0, 2000, 5000, 10000, 15000, 20000, 30000]
CWD = str(TOOLS.parent)

def run_one(args):
    fname, interval = args
    path = DATA_DIR / fname
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

if __name__ == "__main__":
    workers = max(1, cpu_count() - 1)
    tasks = [(f, interval) for f in FILES for interval in INTERVALS]
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
    for fname in FILES:
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
