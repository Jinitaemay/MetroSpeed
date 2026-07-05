import json
import os
import subprocess
import sys
from pathlib import Path

DATA_DIR = Path(os.environ.get("METROSPEED_DATA_DIR", ""))
TOOLS_DIR = Path(__file__).resolve().parent
REPLAY = TOOLS_DIR / "replay_estimator.py"

dir_override = None
files_override: str | None = None
extra_args: list = []
for i, arg in enumerate(sys.argv):
    if arg == "--dir" and i + 1 < len(sys.argv):
        dir_override = Path(sys.argv[i + 1])
        sys.argv = sys.argv[:i] + sys.argv[i+2:]
        break
for i, arg in enumerate(sys.argv):
    if arg == "--files" and i + 1 < len(sys.argv):
        files_override = sys.argv[i + 1]
        sys.argv = sys.argv[:i] + sys.argv[i+2:]
        break
for i, arg in enumerate(sys.argv):
    if arg.startswith("--") and arg != "--anchor-v2" and arg != "--pure-zero":
        if "=" in arg:
            extra_args.append(arg)
        elif i + 1 < len(sys.argv) and not sys.argv[i+1].startswith("--"):
            extra_args.extend([arg, sys.argv[i+1]])
        else:
            extra_args.append(arg)

ANCHOR_V2 = "--anchor-v2" in sys.argv
PURE_ZERO = "--pure-zero" in sys.argv
GLOBAL_LAG_MS: int | None = None
for i, arg in enumerate(sys.argv):
    if arg == "--gnss-lag-ms" and i + 1 < len(sys.argv):
        try:
            GLOBAL_LAG_MS = int(sys.argv[i+1])
        except ValueError:
            pass
    if "=" in arg and arg.startswith("--gnss-lag-ms="):
        try:
            GLOBAL_LAG_MS = int(arg.split("=", 1)[1])
        except ValueError:
            pass

if dir_override:
    DATA_DIR = dir_override

FILES: list[str] = []

if files_override:
    FILES = [f.strip() for f in files_override.split(",") if f.strip()]
elif DATA_DIR and DATA_DIR.exists():
    ALL_FILES = sorted(DATA_DIR.glob("*.jsonl"))
    ALL_FILES = [f for f in ALL_FILES if "_replay_" not in f.name]
    FILES = [f.name for f in ALL_FILES]

if not FILES:
    FILES = [
        "地铁_航津路-外高桥保税区北_靠在车窗上_20260619.jsonl",
        "地铁_上海赛车场-嘉定新城-马陆_平放在地板上_20260621.jsonl",
        "地铁_陈翔公路-南翔-桃浦新村_平放在地板上_20260621.jsonl",
        "地铁_双江路-国帆路-新江湾城_平放在驾驶台上_20260619.jsonl",
        "地铁_浦东大道-杨树浦路-大连路_20260618.jsonl",
        "地铁_港城路-高青路-东方体育中心_放置在座位上-平放在地板上_20260624.jsonl",
        "地铁_沈杜公路-汇臻路_放置在窗台上_20260624.jsonl",
        "地铁_虹桥2号航站楼-浦东1号2号航站楼_放置在窗台上_20260624.jsonl",
        "磁浮_浦东1号2号航站楼-龙阳路_放置在窗台上_20260624.jsonl",
        "驾车_东靖路_放置在充电位_20260620.jsonl",
        "驾车_中山南路-外滩隧道-东长治路_20260618.jsonl",
        "驾车_东靖路-周家嘴路隧道-周家嘴路-北横通道-北翟路地道-北翟高架路-沪常高速_放置在充电位_20260620.jsonl",
        "公交_申崇五线_平放在坐垫上_20260619.jsonl",
        "公交_新乐路_放置在坐垫上_20260621.jsonl",
        "公交_许昌路_平放在硬质表面_20260624.jsonl",
        "公交_北安跨线_平放在硬质表面_20260624.jsonl",
        "公交_奉浦快线_平放在硬质表面_20260624.jsonl",
    ]

if ANCHOR_V2:
    print("=== ANCHOR V2 MODE (ArkTS: vibration freeze + tunnel lockout + confidence blend) ===")
else:
    print("=== INERTIAL MODE (pure inertial) ===")
if PURE_ZERO:
    print("=== PURE ZERO (anchor+delta, no blend) ===")
if GLOBAL_LAG_MS is not None:
    print(f"=== GNSS lag compensation: {GLOBAL_LAG_MS}ms ===")
if extra_args:
    print(f"extra: {' '.join(extra_args)}")

for fname in FILES:
    path = DATA_DIR / fname
    if not path.exists():
        print(f"SKIP {fname}: file not found")
        continue
    cmd = [sys.executable, str(REPLAY), str(path)]
    if ANCHOR_V2:
        cmd.append("--anchor-v2")
    if PURE_ZERO:
        cmd.append("--pure-zero")
    if extra_args:
        cmd.extend(extra_args)
    result = subprocess.run(
        cmd,
        capture_output=True, text=True, cwd=str(TOOLS_DIR.parent),
    )
    if result.returncode != 0:
        print(f"ERROR {fname}: {result.stderr.strip()[:120]}")
        continue
    d = json.loads(result.stdout)

    if ANCHOR_V2:
        comp = d.get("anchoredComparison", {})
        moving = comp.get("moving", {})
        all_comp = comp.get("all", {})
        pairs = comp.get("pairedLocationRows", 0)
        spd = d.get("anchorSpeed", {})
        max_kmh = spd.get("maxKmh", 0)
        median_kmh = spd.get("medianKmh", 0)
    else:
        comp = d.get("locationComparison", {})
        moving = comp.get("moving", {})
        all_comp = comp.get("all", {})
        pairs = comp.get("pairedLocationRows", 0)
        spd = d.get("speed", {})
        max_kmh = spd.get("maxKmh", 0)
        median_kmh = spd.get("medianKmh", 0)

    samples = d.get("sensorSamples", 0)
    print(
        f"moving_mae={moving.get('maeKmh', 'N/A')} "
        f"moving_count={moving.get('count', 0)} "
        f"all_mae={all_comp.get('maeKmh', 'N/A')} "
        f"all_count={all_comp.get('count', 0)} "
        f"pairs={pairs} "
        f"samples={samples} "
        f"max_kmh={max_kmh:.0f} "
        f"median_kmh={median_kmh:.0f} "
        f"file={fname}"
    )
