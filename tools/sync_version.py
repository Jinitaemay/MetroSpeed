#!/usr/bin/env python3
import argparse
import datetime as dt
import pathlib
import re
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_JSON = ROOT / "AppScope" / "app.json5"
RECORDER = ROOT / "entry" / "src" / "main" / "ets" / "model" / "ResearchRecorder.ets"
REPLAY_ESTIMATOR = ROOT / "tools" / "replay_estimator.py"


def version_name_from_code(version_code: int) -> str:
    timestamp = dt.datetime.fromtimestamp(version_code)
    return timestamp.strftime("%Y%m%d.%H%M%S")


def replace_once(text: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1)
    if count != 1:
        raise RuntimeError(f"Expected one {label}, found {count}")
    return updated


def read_versions() -> tuple[int, str, int, str, str]:
    app_text = APP_JSON.read_text(encoding="utf-8")
    recorder_text = RECORDER.read_text(encoding="utf-8")
    replay_text = REPLAY_ESTIMATOR.read_text(encoding="utf-8")
    app_code_match = re.search(r'"versionCode"\s*:\s*(\d+)', app_text)
    app_name_match = re.search(r'"versionName"\s*:\s*"([^"]+)"', app_text)
    recorder_code_match = re.search(r"const APP_VERSION_CODE\s*=\s*(\d+);", recorder_text)
    recorder_algo_match = re.search(r"const ALGORITHM_VERSION\s*=\s*'([^']+)';", recorder_text)
    replay_algo_match = re.search(r'ALGORITHM_VERSION\s*=\s*"([^"]+)"', replay_text)
    if not app_code_match or not app_name_match or not recorder_code_match:
        raise RuntimeError("Could not read all version fields")
    if not recorder_algo_match or not replay_algo_match:
        raise RuntimeError("Could not read ALGORITHM_VERSION from both files")
    return (
        int(app_code_match.group(1)),
        app_name_match.group(1),
        int(recorder_code_match.group(1)),
        recorder_algo_match.group(1),
        replay_algo_match.group(1),
    )


def sync(version_code: int, algo_version: str, check: bool) -> int:
    app_code, app_name, recorder_code, recorder_algo, replay_algo = read_versions()

    if check:
        errors = []
        if app_code != recorder_code:
            errors.append(f"app versionCode={app_code}, recorder APP_VERSION_CODE={recorder_code}")
        if recorder_algo != replay_algo:
            errors.append(f"ALGORITHM_VERSION: ArkTS='{recorder_algo}', Python='{replay_algo}'")
        if errors:
            for e in errors:
                print(f"version mismatch: {e}", file=sys.stderr)
            return 1
        print(f"version ok: {app_code} / {app_name} / algo={recorder_algo}")
        return 0

    app_text = APP_JSON.read_text(encoding="utf-8")
    app_name_match = re.search(r'"versionName"\s*:\s*"([^"]+)"', app_text)
    app_name = app_name_match.group(1) if app_name_match else "unknown"
    app_text = replace_once(app_text, r'"versionCode"\s*:\s*\d+', f'"versionCode": {version_code}', "versionCode")
    APP_JSON.write_text(app_text, encoding="utf-8")

    recorder_text = RECORDER.read_text(encoding="utf-8")
    recorder_text = replace_once(
        recorder_text,
        r"const APP_VERSION_CODE\s*=\s*\d+;",
        f"const APP_VERSION_CODE = {version_code};",
        "APP_VERSION_CODE",
    )
    RECORDER.write_text(recorder_text, encoding="utf-8")

    if algo_version:
        recorder_text = RECORDER.read_text(encoding="utf-8")
        recorder_text = replace_once(
            recorder_text,
            r"const ALGORITHM_VERSION\s*=\s*'[^']+';",
            f"const ALGORITHM_VERSION = '{algo_version}';",
            "ALGORITHM_VERSION in ResearchRecorder.ets",
        )
        RECORDER.write_text(recorder_text, encoding="utf-8")

        replay_text = REPLAY_ESTIMATOR.read_text(encoding="utf-8")
        replay_text = replace_once(
            replay_text,
            r'ALGORITHM_VERSION\s*=\s*"[^"]+"',
            f'ALGORITHM_VERSION = "{algo_version}"',
            "ALGORITHM_VERSION in replay_estimator.py",
        )
        REPLAY_ESTIMATOR.write_text(replay_text, encoding="utf-8")

    parts = [f"version: {version_code} / {app_name}"]
    if algo_version:
        parts.append(f"algo: {algo_version}")
    print(f"synced {' '.join(parts)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", type=int, default=int(dt.datetime.now().timestamp()))
    parser.add_argument("--algo", type=str, default=None, help="ALGORITHM_VERSION string; omit to keep current")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    return sync(args.code, args.algo, args.check)


if __name__ == "__main__":
    raise SystemExit(main())
