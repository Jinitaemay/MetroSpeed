import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

TOOLS_DIR = Path(__file__).resolve().parent
REPLAY = TOOLS_DIR / "replay_estimator.py"

parser = argparse.ArgumentParser(
    description="顺序回放指定目录顶层的多条 MetroSpeed JSONL 记录",
    allow_abbrev=False,
)
parser.add_argument(
    "--dir",
    dest="data_dir",
    type=Path,
    help="数据目录；默认读取 METROSPEED_DATA_DIR",
)
parser.add_argument(
    "--files",
    help="逗号分隔的显式文件子集；相对于数据目录解析",
)
parser.add_argument("--anchor-v2", action="store_true")
parser.add_argument("--pure-zero", action="store_true")
args, extra_args = parser.parse_known_args()

env_data_dir = os.environ.get("METROSPEED_DATA_DIR")
DATA_DIR = args.data_dir or (Path(env_data_dir) if env_data_dir else None)
FILES_OVERRIDE = args.files
ANCHOR_V2 = args.anchor_v2
PURE_ZERO = args.pure_zero


def usage_error(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(2)


def concise_subprocess_error(stderr: str, limit: int = 240) -> str:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if not lines:
        return "unknown replay failure"
    error_lines = [line for line in lines if "error:" in line.casefold()]
    selected = error_lines[-1] if error_lines else lines[-1]
    return selected[-limit:]


if DATA_DIR is None:
    usage_error("请用 --dir <目录> 或设置环境变量 METROSPEED_DATA_DIR")
if not DATA_DIR.exists():
    usage_error(f"数据目录不存在: {DATA_DIR}")
if not DATA_DIR.is_dir():
    usage_error(f"数据路径不是目录: {DATA_DIR}")

GLOBAL_LAG_MS: int | None = None
for i, arg in enumerate(extra_args):
    if arg == "--gnss-lag-ms" and i + 1 < len(extra_args):
        try:
            GLOBAL_LAG_MS = int(extra_args[i+1])
        except ValueError:
            pass
    if "=" in arg and arg.startswith("--gnss-lag-ms="):
        try:
            GLOBAL_LAG_MS = int(arg.split("=", 1)[1])
        except ValueError:
            pass

FILES: list[str] = []

if FILES_OVERRIDE is not None:
    FILES = [f.strip() for f in FILES_OVERRIDE.split(",") if f.strip()]
    if not FILES:
        usage_error("--files 不能为空")
    print(
        "WARNING: --files 仅运行显式子集，不代表完整主回归集。",
        file=sys.stderr,
    )
else:
    ALL_FILES = sorted(DATA_DIR.glob("*.jsonl"))
    ALL_FILES = [f for f in ALL_FILES if "_replay_" not in f.name]
    FILES = [f.name for f in ALL_FILES]

if not FILES:
    usage_error(f"数据目录顶层没有可回放的 JSONL: {DATA_DIR}")

if ANCHOR_V2:
    print("=== ANCHOR V2 MODE (ArkTS: GNSS reliability + tunnel lockout + confidence blend) ===")
else:
    print("=== INERTIAL MODE (pure inertial) ===")
if PURE_ZERO:
    print("=== PURE ZERO (anchor+delta, no blend) ===")
if GLOBAL_LAG_MS is not None:
    print(f"=== GNSS lag compensation: {GLOBAL_LAG_MS}ms ===")
if extra_args:
    print(f"extra: {' '.join(extra_args)}")

success_count = 0
failure_count = 0
gnss_comparison_count = 0
incomplete_count = 0
unknown_integrity_count = 0
for fname in FILES:
    path = DATA_DIR / fname
    if not path.exists():
        print(f"SKIP {fname}: file not found")
        failure_count += 1
        continue
    cmd = [
        sys.executable,
        str(REPLAY),
        str(path),
        "--skip-lag-scans",
        "--skip-bucketed-comparison",
    ]
    if ANCHOR_V2:
        cmd.append("--anchor-v2")
    elif not PURE_ZERO:
        # The default gate needs exact pure-inertial speed/GNSS statistics, not
        # millions of replay-output dictionaries. This path still validates and
        # replays every JSONL row in source order, with disk-backed exact stats.
        cmd.append("--streaming-baseline-summary")
    if PURE_ZERO:
        cmd.append("--pure-zero")
    if extra_args:
        cmd.extend(extra_args)
    result = subprocess.run(
        cmd,
        capture_output=True, text=True, cwd=str(TOOLS_DIR.parent),
    )
    if result.returncode != 0:
        print(f"ERROR {fname}: {concise_subprocess_error(result.stderr)}")
        failure_count += 1
        continue
    try:
        d = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        print(f"ERROR {fname}: invalid replay JSON: {error}")
        failure_count += 1
        continue
    try:
        sensor_samples = int(d.get("sensorSamples", 0))
    except (TypeError, ValueError):
        sensor_samples = 0
    if sensor_samples <= 0:
        print(f"ERROR {fname}: replay produced no sensor samples")
        failure_count += 1
        continue
    integrity = d.get("inputIntegrity")
    integrity_value = integrity.get("complete") if isinstance(integrity, dict) else None
    input_complete = integrity_value is True
    if integrity_value is False:
        incomplete_count += 1
        if isinstance(integrity, dict):
            ignored_tail = integrity.get("ignoredTail", {})
            if not isinstance(ignored_tail, dict):
                ignored_tail = {}
            print(
                f"INCOMPLETE {fname}: status={integrity.get('status', 'unknown')} "
                f"line={ignored_tail.get('lineNumber', 'unknown')} "
                f"bytes={ignored_tail.get('byteCount', 'unknown')}"
            )
        else:
            print(f"INCOMPLETE {fname}: replay did not report input integrity")
    elif integrity_value is not True:
        unknown_integrity_count += 1
        status = integrity.get("status", "unknown") if isinstance(integrity, dict) else "missing"
        print(f"UNKNOWN {fname}: input integrity status={status}")
    if ANCHOR_V2:
        comp = d.get("anchoredComparison", {})
        moving = comp.get("moving", {})
        all_comp = comp.get("all", {})
        pairs = comp.get("pairedLocationRows", 0)
        spd = d.get("anchoredDisplaySpeed", {})
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
    has_gnss_comparison = (
        isinstance(pairs, (int, float))
        and pairs > 0
        and isinstance(all_comp.get("count"), (int, float))
        and all_comp["count"] > 0
    )
    if has_gnss_comparison:
        gnss_comparison_count += 1
    else:
        print(f"ERROR {fname}: replay produced no GNSS comparison metrics")

    if input_complete and has_gnss_comparison:
        success_count += 1
    else:
        failure_count += 1
    print(
        f"moving_mae={moving.get('maeKmh', 'N/A')} "
        f"moving_count={moving.get('count', 0)} "
        f"all_mae={all_comp.get('maeKmh', 'N/A')} "
        f"all_count={all_comp.get('count', 0)} "
        f"pairs={pairs} "
        f"samples={samples} "
        f"input_complete={'true' if input_complete else ('false' if integrity_value is False else 'unknown')} "
        f"max_kmh={max_kmh:.0f} "
        f"median_kmh={median_kmh:.0f} "
        f"file={fname}"
    )

print(
    f"summary: success={success_count} failed_or_missing={failure_count} "
    f"with_gnss_comparison={gnss_comparison_count} incomplete={incomplete_count} "
    f"unknown_integrity={unknown_integrity_count}"
)
if success_count == 0 or failure_count > 0:
    raise SystemExit(1)
