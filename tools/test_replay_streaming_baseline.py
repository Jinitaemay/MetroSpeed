import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    from .replay_estimator import (
        analyze_replay_path_streaming_baseline,
        analyze_replay_rows,
        read_jsonl_with_info,
        StreamingBaselineStore,
    )
except ImportError:
    from replay_estimator import (
        analyze_replay_path_streaming_baseline,
        analyze_replay_rows,
        read_jsonl_with_info,
        StreamingBaselineStore,
    )


REPO_ROOT = Path(__file__).resolve().parents[1]
REPLAY_SCRIPT = REPO_ROOT / "tools" / "replay_estimator.py"


def sensor_row(timestamp_ms: int, sensor_timestamp: int, run_id: str) -> dict:
    return {
        "recordType": "sensor",
        "timestampMs": timestamp_ms,
        "measurementRunId": run_id,
        "measurementActive": True,
        "sensorTimestamp": sensor_timestamp,
        "accX": 0.12,
        "accY": 0.0,
        "accZ": 9.80665,
        "gyroX": 0.0,
        "gyroY": 0.0,
        "gyroZ": 0.0,
    }


def sample_rows() -> list[dict]:
    rows: list[dict] = [
        {
            "recordType": "event",
            "timestampMs": 1000,
            "notes": "measurement started",
            "measurementRunId": "run-a",
        },
        {
            "recordType": "sensor_callback",
            "timestampMs": 1001,
            "sensorType": "accelerometer",
        },
    ]
    rows.extend(
        sensor_row(1000 + index * 20, index * 20_000_000, "run-a")
        for index in range(130)
    )
    rows.append({
        "recordType": "location",
        "timestampMs": 2500,
        "locationTimeMs": 2480,
        "locationSpeedMps": 4.5,
        "locationSpeedAccuracyMps": 0.5,
        "locationSourceType": 1,
        "measurementRunId": "run-a",
        "measurementActive": True,
    })
    rows.append({
        "recordType": "event",
        "timestampMs": 3600,
        "event": "停止测速",
        "measurementRunId": "run-a",
    })

    # The second run deliberately moves wall time backwards. Source order and
    # run isolation must still match the existing in-memory implementation.
    rows.append({
        "recordType": "event",
        "timestampMs": 500,
        "notes": "measurement started",
        "measurementRunId": "run-b",
    })
    rows.extend(
        sensor_row(500 + index * 20, index * 20_000_000, "run-b")
        for index in range(130)
    )
    rows.append({
        "recordType": "location",
        "timestampMs": 1900,
        "locationTimeMs": 1880,
        "locationSpeedMps": 0.4,
        "locationSpeedAccuracyMps": 0.2,
        "locationSourceType": 4,
        "measurementRunId": "run-b",
        "measurementActive": True,
    })
    rows.append({
        "recordType": "event",
        "timestampMs": 3100,
        "event": "停止测速",
        "measurementRunId": "run-b",
    })
    return rows


class StreamingBaselineReplayTests(unittest.TestCase):
    def write_rows(self, path: Path) -> None:
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for row in sample_rows():
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
                handle.write("\n")

    def assert_stats_close(self, actual: dict, expected: dict) -> None:
        self.assertEqual(set(actual), set(expected))
        for key, expected_value in expected.items():
            actual_value = actual[key]
            if isinstance(expected_value, dict):
                self.assert_stats_close(actual_value, expected_value)
            elif isinstance(expected_value, float):
                self.assertAlmostEqual(actual_value, expected_value, places=12)
            else:
                self.assertEqual(actual_value, expected_value)

    def test_streaming_gate_matches_full_pure_replay_statistics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "two-runs.jsonl"
            self.write_rows(path)
            rows, full_read_info = read_jsonl_with_info(path)
            full_summary, full_outputs = analyze_replay_rows(
                rows,
                strict_start=False,
                include_lag_scans=False,
                include_bucketed_comparison=False,
                include_recorded_comparison=False,
                gnss_lag_ms=-40,
            )
            streaming_summary, streaming_read_info = (
                analyze_replay_path_streaming_baseline(
                    path,
                    strict_start=False,
                    gnss_lag_ms=-40,
                )
            )

        self.assertGreater(len(full_outputs), 0)
        self.assertEqual(streaming_summary["sensorSamples"], len(full_outputs))
        self.assertEqual(streaming_summary["analysisMode"], "streamingBaseline")
        self.assert_stats_close(streaming_summary["speed"], full_summary["speed"])
        self.assert_stats_close(
            streaming_summary["locationComparison"],
            full_summary["locationComparison"],
        )
        self.assertEqual(
            streaming_read_info.summary(False),
            full_read_info.summary(False),
        )

    def test_streaming_cli_rejects_anchor_and_output_modes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "input.jsonl"
            output = Path(temp_dir) / "output.jsonl"
            self.write_rows(path)
            common = [
                sys.executable,
                str(REPLAY_SCRIPT),
                str(path),
                "--streaming-baseline-summary",
                "--skip-lag-scans",
                "--skip-bucketed-comparison",
            ]
            anchor = subprocess.run(
                [*common, "--anchor-v2"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            write_output = subprocess.run(
                [*common, "--out", str(output)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )

        self.assertEqual(anchor.returncode, 2)
        self.assertIn("pure-inertial mode only", anchor.stderr)
        self.assertEqual(write_output.returncode, 2)
        self.assertIn("cannot be combined with --out", write_output.stderr)

    def test_disk_store_is_removed_on_success_and_failure(self) -> None:
        successful = StreamingBaselineStore()
        successful_path = successful.path
        with successful:
            self.assertTrue(successful_path.exists())
        self.assertFalse(successful_path.exists())

        failed = StreamingBaselineStore()
        failed_path = failed.path
        with self.assertRaisesRegex(RuntimeError, "injected failure"):
            with failed:
                self.assertTrue(failed_path.exists())
                raise RuntimeError("injected failure")
        self.assertFalse(failed_path.exists())

    def test_default_baseline_command_enables_streaming_only_without_anchor(self) -> None:
        source = (REPO_ROOT / "tools" / "_baseline_all.py").read_text(encoding="utf-8")
        anchor_branch = source.index('if ANCHOR_V2:')
        streaming_flag = source.index('cmd.append("--streaming-baseline-summary")')
        pure_zero_branch = source.index('if PURE_ZERO:', streaming_flag)
        self.assertLess(anchor_branch, streaming_flag)
        self.assertLess(streaming_flag, pure_zero_branch)
        self.assertIn("elif not PURE_ZERO:", source[anchor_branch:streaming_flag])


if __name__ == "__main__":
    unittest.main()
