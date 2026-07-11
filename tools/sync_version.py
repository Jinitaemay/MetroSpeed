#!/usr/bin/env python3
import argparse
import datetime as dt
import os
import pathlib
import re
import sys
import tempfile
import time


ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_JSON = ROOT / "AppScope" / "app.json5"
RECORDER = ROOT / "entry" / "src" / "main" / "ets" / "model" / "ResearchRecorder.ets"
REPLAY_ESTIMATOR = ROOT / "tools" / "replay_estimator.py"
README = ROOT / "README.md"
LOCK_FILE = ROOT / ".sync-version.lock"
LOCK_STALE_SECONDS = 300
ALGO_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def read_text_exact(path: pathlib.Path, encoding: str = "utf-8") -> str:
    """Read text without Python's universal-newline normalization."""
    with path.open("r", encoding=encoding, newline="") as handle:
        return handle.read()


def replace_once(text: str, pattern: str, replacement: str, label: str) -> str:
    matches = list(re.finditer(pattern, text))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {label}, found {len(matches)}")
    return re.sub(pattern, lambda _match: replacement, text, count=1)


def unique_match(pattern: str, text: str, label: str) -> re.Match[str]:
    matches = list(re.finditer(pattern, text))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {label}, found {len(matches)}")
    return matches[0]


def validate_inputs(version_code: int, algo_version: str | None) -> None:
    if not 1 <= version_code <= 2_147_483_647:
        raise ValueError("versionCode must be between 1 and 2147483647")
    if algo_version is not None and ALGO_PATTERN.fullmatch(algo_version) is None:
        raise ValueError(
            "ALGORITHM_VERSION may contain only letters, digits, '.', '_' and '-'"
        )


def atomic_write(path: pathlib.Path, text: str, expected_text: str | None = None) -> None:
    if expected_text is not None and read_text_exact(path) != expected_text:
        raise RuntimeError(f"Concurrent change detected before writing {path.relative_to(ROOT)}")
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if expected_text is not None and read_text_exact(path) != expected_text:
            raise RuntimeError(f"Concurrent change detected while writing {path.relative_to(ROOT)}")
        os.replace(temp_name, path)
    finally:
        try:
            pathlib.Path(temp_name).unlink()
        except FileNotFoundError:
            pass


def parse_versions(
    app_text: str, recorder_text: str, replay_text: str, readme_text: str
) -> tuple[int, str, int, str, str, str]:
    app_code_match = unique_match(
        r'"versionCode"\s*:\s*(\d+)', app_text, "versionCode in app.json5"
    )
    app_name_match = unique_match(
        r'"versionName"\s*:\s*"([^"]+)"', app_text, "versionName in app.json5"
    )
    recorder_code_match = unique_match(
        r"const APP_VERSION_CODE\s*=\s*(\d+);",
        recorder_text,
        "APP_VERSION_CODE in ResearchRecorder.ets",
    )
    recorder_algo_match = unique_match(
        r"const ALGORITHM_VERSION\s*=\s*'([^']+)';",
        recorder_text,
        "ALGORITHM_VERSION in ResearchRecorder.ets",
    )
    replay_algo_match = unique_match(
        r'ALGORITHM_VERSION\s*=\s*"([^"]+)"',
        replay_text,
        "ALGORITHM_VERSION in replay_estimator.py",
    )
    readme_algo_match = unique_match(
        r"\*\*算法版本\*\*[：:]\s*`([^`]+)`",
        readme_text,
        "ALGORITHM_VERSION in README.md",
    )
    return (
        int(app_code_match.group(1)),
        app_name_match.group(1),
        int(recorder_code_match.group(1)),
        recorder_algo_match.group(1),
        replay_algo_match.group(1),
        readme_algo_match.group(1),
    )


def read_sources() -> tuple[str, str, str, str]:
    return (
        read_text_exact(APP_JSON),
        read_text_exact(RECORDER),
        read_text_exact(REPLAY_ESTIMATOR),
        read_text_exact(README),
    )


def _sync_unlocked(
    version_code: int | None,
    algo_version: str | None,
    check: bool,
    allow_downgrade: bool = False,
) -> int:
    app_text, recorder_text, replay_text, readme_text = read_sources()
    app_code, app_name, recorder_code, recorder_algo, replay_algo, readme_algo = (
        parse_versions(app_text, recorder_text, replay_text, readme_text)
    )

    if check:
        errors: list[str] = []
        for label, code in (
            ("app.json5", app_code),
            ("ResearchRecorder.ets", recorder_code),
        ):
            try:
                validate_inputs(code, None)
            except ValueError as error:
                errors.append(f"{label}: {error}")
        for label, algorithm in (
            ("ResearchRecorder.ets", recorder_algo),
            ("replay_estimator.py", replay_algo),
            ("README.md", readme_algo),
        ):
            if ALGO_PATTERN.fullmatch(algorithm) is None:
                errors.append(
                    f"{label}: ALGORITHM_VERSION may contain only letters, digits, "
                    "'.', '_' and '-'"
                )
        if app_code != recorder_code:
            errors.append(
                f"app versionCode={app_code}, recorder APP_VERSION_CODE={recorder_code}"
            )
        if not (recorder_algo == replay_algo == readme_algo):
            errors.append(
                "ALGORITHM_VERSION: "
                f"ArkTS='{recorder_algo}', Python='{replay_algo}', README='{readme_algo}'"
            )
        if errors:
            for error in errors:
                print(f"version mismatch: {error}", file=sys.stderr)
            return 1
        print(f"version ok: {app_code} / {app_name} / algo={recorder_algo}")
        return 0

    if version_code is None:
        version_code = max(
            int(dt.datetime.now().timestamp()),
            app_code + 1,
            recorder_code + 1,
        )
    validate_inputs(version_code, algo_version)
    if not allow_downgrade and version_code < max(app_code, recorder_code):
        raise ValueError(
            "refusing to lower versionCode; pass --allow-downgrade only for an "
            "intentional local rollback"
        )

    staged_app = replace_once(
        app_text,
        r'"versionCode"\s*:\s*\d+',
        f'"versionCode": {version_code}',
        "versionCode",
    )
    staged_recorder = replace_once(
        recorder_text,
        r"const APP_VERSION_CODE\s*=\s*\d+;",
        f"const APP_VERSION_CODE = {version_code};",
        "APP_VERSION_CODE",
    )
    staged_replay = replay_text
    staged_readme = readme_text

    if algo_version is not None:
        staged_recorder = replace_once(
            staged_recorder,
            r"const ALGORITHM_VERSION\s*=\s*'[^']+';",
            f"const ALGORITHM_VERSION = '{algo_version}';",
            "ALGORITHM_VERSION in ResearchRecorder.ets",
        )
        staged_replay = replace_once(
            staged_replay,
            r'ALGORITHM_VERSION\s*=\s*"[^"]+"',
            f'ALGORITHM_VERSION = "{algo_version}"',
            "ALGORITHM_VERSION in replay_estimator.py",
        )
        staged_readme = replace_once(
            staged_readme,
            r"(?m)^>\s*\*\*算法版本\*\*[：:]\s*`[^`\r\n]+`\s*$",
            f"> **算法版本**：`{algo_version}`",
            "ALGORITHM_VERSION in README.md",
        )

    staged_versions = parse_versions(
        staged_app, staged_recorder, staged_replay, staged_readme
    )
    expected_algo = algo_version if algo_version is not None else recorder_algo
    if staged_versions[0] != version_code or staged_versions[2] != version_code:
        raise RuntimeError("Staged versionCode validation failed")
    if not (staged_versions[3] == staged_versions[4] == staged_versions[5] == expected_algo):
        raise RuntimeError("Staged ALGORITHM_VERSION validation failed")

    originals = {
        APP_JSON: app_text,
        RECORDER: recorder_text,
        REPLAY_ESTIMATOR: replay_text,
        README: readme_text,
    }
    for path, original in originals.items():
        if read_text_exact(path) != original:
            raise RuntimeError(f"Concurrent change detected in {path.relative_to(ROOT)}")

    write_plan = [
        (APP_JSON, staged_app, app_text),
        (RECORDER, staged_recorder, recorder_text),
    ]
    if algo_version is not None:
        write_plan.extend([
            (REPLAY_ESTIMATOR, staged_replay, replay_text),
            (README, staged_readme, readme_text),
        ])

    written: list[tuple[pathlib.Path, str, str]] = []
    try:
        for path, staged, original in write_plan:
            if staged == original:
                continue
            atomic_write(path, staged, expected_text=original)
            written.append((path, staged, original))
    except (OSError, RuntimeError) as error:
        rollback_errors: list[str] = []
        for path, staged, original in reversed(written):
            try:
                atomic_write(path, original, expected_text=staged)
            except (OSError, RuntimeError) as rollback_error:
                rollback_errors.append(f"{path.relative_to(ROOT)}: {rollback_error}")
        if rollback_errors:
            raise RuntimeError(
                f"Version sync failed ({error}); rollback incomplete: {'; '.join(rollback_errors)}"
            ) from error
        raise RuntimeError(f"Version sync failed and changes were rolled back: {error}") from error

    parts = [f"version: {version_code} / {app_name}"]
    if algo_version is not None:
        parts.append(f"algo: {algo_version}")
    print(f"synced {' '.join(parts)}")
    return 0


def acquire_version_lock() -> tuple[int, str]:
    token = f"pid={os.getpid()} time={time.time_ns()}\n"
    for _attempt in range(2):
        try:
            lock_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            try:
                age_seconds = time.time() - LOCK_FILE.stat().st_mtime
            except FileNotFoundError:
                continue
            if age_seconds > LOCK_STALE_SECONDS:
                try:
                    LOCK_FILE.unlink()
                except FileNotFoundError:
                    pass
                continue
            raise RuntimeError(
                f"Another build/version sync is running: {LOCK_FILE.name}"
            ) from error
        os.write(lock_fd, token.encode("ascii"))
        os.fsync(lock_fd)
        return lock_fd, token
    raise RuntimeError(f"Could not acquire version lock: {LOCK_FILE.name}")


def release_version_lock(lock_fd: int, token: str) -> None:
    os.close(lock_fd)
    try:
        if LOCK_FILE.read_text(encoding="ascii") == token:
            LOCK_FILE.unlink()
    except FileNotFoundError:
        pass


def sync(
    version_code: int | None,
    algo_version: str | None,
    check: bool,
    allow_downgrade: bool = False,
) -> int:
    lock_fd, token = acquire_version_lock()
    try:
        return _sync_unlocked(version_code, algo_version, check, allow_downgrade)
    finally:
        try:
            release_version_lock(lock_fd, token)
        except OSError as error:
            print(f"warning: could not release version lock: {error}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", type=int, default=None)
    parser.add_argument(
        "--algo", type=str, default=None,
        help="ALGORITHM_VERSION string; omit to keep current",
    )
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--allow-downgrade",
        action="store_true",
        help="permit an explicit --code below the current version (local rollback only)",
    )
    args = parser.parse_args()
    try:
        if args.check and (
            args.code is not None or args.algo is not None or args.allow_downgrade
        ):
            raise ValueError("--check cannot be combined with update options")
        if args.allow_downgrade and args.code is None:
            raise ValueError("--allow-downgrade requires an explicit --code")
        return sync(args.code, args.algo, args.check, args.allow_downgrade)
    except (RuntimeError, ValueError) as error:
        print(f"version sync failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
