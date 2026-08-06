from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from textwrap import dedent
from unittest.mock import patch

try:
    from . import aux_sensor_location_diag
except ImportError:
    import aux_sensor_location_diag


SESSION_ID = "session-1000"


def base_row(schema: int, seq: int, timestamp_ms: int, record_type: str) -> dict[str, object]:
    return {
        "timestampMs": timestamp_ms,
        "sessionId": SESSION_ID,
        "recordSchemaVersion": schema,
        "appVersionCode": 1,
        "algorithmVersion": "test",
        "recordSeq": seq,
        "recordType": record_type,
        "measurementRunId": None,
        "measurementActive": False,
        "measurementStartMs": None,
        "measurementElapsedMs": None,
    }


def lifecycle_row(schema: int, seq: int, timestamp_ms: int, event: str) -> dict[str, object]:
    row = base_row(schema, seq, timestamp_ms, "lifecycle")
    row["event"] = event
    return row


def sensor_callback_row(schema: int, seq: int, timestamp_ms: int) -> dict[str, object]:
    row = base_row(schema, seq, timestamp_ms, "sensor_callback")
    row.update(
        {
            "sensorType": "accelerometer",
            "sensorTimestamp": timestamp_ms * 1_000_000,
            "requestedIntervalNs": 10_000_000,
        }
    )
    return row


def device_health_row(
    seq: int,
    timestamp_ms: int,
    reason: str,
    temperature_c: float | None = 32.5,
    thermal_level: int | None = 1,
) -> dict[str, object]:
    row = base_row(17, seq, timestamp_ms, "device_health")
    row.update(
        {
            "deviceHealthReason": reason,
            "batteryTemperatureC": temperature_c,
            "thermalLevel": thermal_level,
        }
    )
    return row


class AuxSensorLocationDiagTest(unittest.TestCase):
    def diagnose_rows(self, rows: list[dict[str, object]]) -> tuple[bool, str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.jsonl"
            path.write_text(
                "".join(f"{json.dumps(row, ensure_ascii=False)}\n" for row in rows),
                encoding="utf-8",
            )
            output = io.StringIO()
            with redirect_stdout(output):
                result = aux_sensor_location_diag.diagnose(str(path), 2.5, 3)
            return result, output.getvalue()

    def normalized_report(self, output: str) -> str:
        lines = output.strip().splitlines()
        self.assertTrue(lines and lines[0].startswith("=== "))
        lines[0] = "=== <source> ==="
        return "\n".join(lines)

    def test_schema16_remains_compatible_without_device_health(self) -> None:
        rows = [
            lifecycle_row(16, 1, 1000, "start_record"),
            sensor_callback_row(16, 2, 1010),
            lifecycle_row(16, 3, 2000, "stop_record"),
        ]

        result, output = self.diagnose_rows(rows)

        self.assertTrue(result)
        self.assertNotIn("Device health:", output)

    def test_schema16_success_report_matches_legacy_summary(self) -> None:
        rows = [lifecycle_row(16, 1, 1000, "start_record")]
        rows.extend(
            sensor_callback_row(16, seq, timestamp_ms)
            for seq, timestamp_ms in enumerate(
                (1010, 1020, 1030, 1130, 1140), start=2
            )
        )
        rows.extend(
            [
                base_row(16, 7, 1200, "location"),
                base_row(16, 8, 1210, "location"),
                base_row(16, 9, 1300, "location"),
                base_row(16, 10, 1310, "satellite"),
                base_row(16, 11, 1320, "satellite"),
                lifecycle_row(16, 12, 1400, "stop_record"),
            ]
        )

        result, output = self.diagnose_rows(rows)

        self.assertTrue(result)
        self.assertEqual(
            self.normalized_report(output),
            dedent(
                """
                === <source> ===
                rows=12, sensor_callback=5, invalid_json=0, schemas={16: 12}
                supported_uniform_schema=yes (supported=[15, 16, 17])
                recordSeq[session-1000]: first=1, last=12, missing=0, reverse=0, duplicate=0
                initial_start_record=yes
                terminal_stop_record=yes
                schema16_plus_sensor_set=yes (unexpected=[])

                Sensor callback cadence:

                accelerometer: records=5, requested_mode=10.00 ms/100.00 Hz (5 rows)
                  sensor timestamp: effective=30.77 Hz, P50=10.00 ms, P95=86.50 ms, max=100.00 ms
                    intervals=4, reverse=0, duplicate=0, gaps(>2.5xP50)=1, estimated_missing~=9
                      gap=100.00 ms at line=5, recordSeq=5, estimated_missing~=9
                  callback clock: effective=30.77 Hz, P50=10.00 ms, P95=86.50 ms, max=100.00 ms
                    intervals=4, reverse=0, duplicate=0, gaps(>2.5xP50)=1, estimated_missing~=9
                      gap=100.00 ms at line=5, recordSeq=5, estimated_missing~=9

                System callback cadence:

                location: records=3
                  callback clock: effective=20.00 Hz, P50=50.00 ms, P95=86.00 ms, max=90.00 ms
                    intervals=2, reverse=0, duplicate=0, gaps(>2.5xP50)=0, estimated_missing~=0

                satellite: records=2
                  callback clock: effective=100.00 Hz, P50=10.00 ms, P95=10.00 ms, max=10.00 ms
                    intervals=1, reverse=0, duplicate=0, gaps(>2.5xP50)=0, estimated_missing~=0
                """
            ).strip(),
        )

    def test_rejects_each_malformed_sensor_callback_field(self) -> None:
        malformed_values = {
            "sensorType": "",
            "timestampMs": "not-a-time",
            "sensorTimestamp": None,
            "requestedIntervalNs": 0,
        }
        for field_name, malformed_value in malformed_values.items():
            with self.subTest(field_name=field_name):
                callback = sensor_callback_row(16, 3, 1020)
                callback[field_name] = malformed_value
                rows = [
                    lifecycle_row(16, 1, 1000, "start_record"),
                    sensor_callback_row(16, 2, 1010),
                    callback,
                    lifecycle_row(16, 4, 2000, "stop_record"),
                ]

                result, output = self.diagnose_rows(rows)

                self.assertFalse(result)
                self.assertIn("valid_sensor_callback=1", output)
                self.assertIn("invalid_sensor_callback=1", output)
                self.assertIn("invalid sensor_callback lines: 3", output)
                self.assertIn(field_name, output)

    def test_rejects_callback_rows_with_no_valid_cadence_points(self) -> None:
        callback = sensor_callback_row(15, 2, 1010)
        callback.update(
            {
                "sensorType": None,
                "timestampMs": float("nan"),
                "sensorTimestamp": "missing",
                "requestedIntervalNs": -1,
            }
        )
        rows = [
            lifecycle_row(15, 1, 1000, "start_record"),
            callback,
            lifecycle_row(15, 3, 2000, "stop_record"),
        ]

        result, output = self.diagnose_rows(rows)

        self.assertFalse(result)
        self.assertIn("sensor_callback=1", output)
        self.assertIn("valid_sensor_callback=0", output)
        self.assertIn("No valid schema-15/16/17 sensor_callback records found.", output)

    def test_huge_numeric_callback_fields_are_reported_as_invalid(self) -> None:
        huge_number = 10**400
        self.assertIsNone(aux_sensor_location_diag.finite_number(huge_number))

        for field_name in (
            "timestampMs",
            "sensorTimestamp",
            "requestedIntervalNs",
        ):
            with self.subTest(field_name=field_name):
                callback = sensor_callback_row(16, 3, 1020)
                callback[field_name] = huge_number
                rows = [
                    lifecycle_row(16, 1, 1000, "start_record"),
                    sensor_callback_row(16, 2, 1010),
                    callback,
                    lifecycle_row(16, 4, 2000, "stop_record"),
                ]

                result, output = self.diagnose_rows(rows)

                self.assertFalse(result)
                self.assertIn("invalid_sensor_callback=1", output)
                self.assertIn(field_name, output)

    def test_invalid_utf8_is_reported_and_returns_nonzero(self) -> None:
        rows = [
            lifecycle_row(16, 1, 1000, "start_record"),
            sensor_callback_row(16, 2, 1010),
            lifecycle_row(16, 3, 2000, "stop_record"),
        ]
        payload = (
            f"{json.dumps(rows[0])}\n".encode()
            + b"\xff\n"
            + f"{json.dumps(rows[1])}\n{json.dumps(rows[2])}\n".encode()
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "invalid-utf8.jsonl"
            path.write_bytes(payload)
            output = io.StringIO()
            with (
                patch.object(sys, "argv", ["aux_sensor_location_diag.py", str(path)]),
                redirect_stdout(output),
            ):
                exit_code = aux_sensor_location_diag.main()

        self.assertEqual(exit_code, 1)
        self.assertIn("invalid_json=1", output.getvalue())
        self.assertIn("invalid JSON lines: 2", output.getvalue())

    def test_two_passes_use_one_stable_snapshot(self) -> None:
        rows = [
            lifecycle_row(16, 1, 900, "start_record"),
            sensor_callback_row(16, 2, 1000),
            sensor_callback_row(16, 3, 1010),
            lifecycle_row(16, 4, 1020, "stop_record"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "changing.jsonl"
            path.write_text(
                "".join(f"{json.dumps(row)}\n" for row in rows),
                encoding="utf-8",
            )
            original_summarize = aux_sensor_location_diag.SeriesAccumulator.summarize
            source_mutated = False

            def summarize_and_mutate(
                accumulator: aux_sensor_location_diag.SeriesAccumulator,
            ) -> aux_sensor_location_diag.SeriesStats:
                nonlocal source_mutated
                stats = original_summarize(accumulator)
                if not source_mutated:
                    with path.open("a", encoding="utf-8") as stream:
                        stream.write(
                            f"{json.dumps(sensor_callback_row(16, 5, 2000))}\n"
                        )
                    source_mutated = True
                return stats

            output = io.StringIO()
            with (
                patch.object(
                    aux_sensor_location_diag.SeriesAccumulator,
                    "summarize",
                    summarize_and_mutate,
                ),
                redirect_stdout(output),
            ):
                result = aux_sensor_location_diag.diagnose(str(path), 2.5, 3)

        self.assertTrue(source_mutated)
        self.assertTrue(result)
        self.assertIn("rows=4, sensor_callback=2", output.getvalue())
        self.assertIn("max=10.00 ms", output.getvalue())
        self.assertNotIn("line=5", output.getvalue())

    def test_stdin_snapshot_closes_when_summary_raises(self) -> None:
        class SummaryFailure(Exception):
            pass

        rows = [
            lifecycle_row(16, 1, 1000, "start_record"),
            sensor_callback_row(16, 2, 1010),
            lifecycle_row(16, 3, 2000, "stop_record"),
        ]
        stdin = io.StringIO("".join(f"{json.dumps(row)}\n" for row in rows))
        snapshot = io.BytesIO()
        with (
            patch.object(sys, "stdin", stdin),
            patch.object(
                aux_sensor_location_diag.tempfile,
                "TemporaryFile",
                return_value=snapshot,
            ),
            patch.object(
                aux_sensor_location_diag.SeriesAccumulator,
                "summarize",
                side_effect=SummaryFailure,
            ),
        ):
            with self.assertRaises(SummaryFailure):
                aux_sensor_location_diag.diagnose("-", 2.5, 3)

        self.assertTrue(snapshot.closed)
        self.assertFalse(stdin.closed)

    def test_large_series_uses_compact_intervals_and_fixed_gap_storage(self) -> None:
        accumulator = aux_sensor_location_diag.SeriesAccumulator()
        timestamps: list[float] = []
        timestamp_ms = 0.0
        for index in range(20_000):
            timestamp_ms += 100.0 if index and index % 1000 == 0 else 10.0
            timestamps.append(timestamp_ms)
            accumulator.add(timestamp_ms)

        self.assertEqual(accumulator.positive_intervals.typecode, "d")
        self.assertEqual(len(accumulator.positive_intervals), len(timestamps) - 1)
        self.assertFalse(hasattr(accumulator, "__dict__"))
        self.assertLess(
            sys.getsizeof(accumulator.positive_intervals),
            len(accumulator.positive_intervals) * 16 + 1024,
        )

        stats = accumulator.summarize()
        scanner = aux_sensor_location_diag.GapScanner(stats.p50_ms, 2.5, 3)
        for line_no, value_ms in enumerate(timestamps, start=1):
            scanner.add(value_ms, line_no, line_no)
        scanner.apply(stats)

        self.assertEqual(stats.p50_ms, 10.0)
        self.assertEqual(stats.p95_ms, 10.0)
        self.assertEqual(stats.large_gap_count, 19)
        self.assertEqual(stats.estimated_missing, 19 * 9)
        self.assertEqual(len(stats.large_gaps), 3)
        self.assertTrue(all(gap.interval_ms == 100.0 for gap in stats.large_gaps))

    def test_schema17_accepts_start_periodic_and_stop_device_health(self) -> None:
        rows = [
            lifecycle_row(17, 1, 1000, "start_record"),
            device_health_row(2, 1000, "start"),
            sensor_callback_row(17, 3, 1010),
            device_health_row(4, 11000, "periodic", 33.2, 2),
            device_health_row(5, 11500, "stop", 33.3, 2),
            lifecycle_row(17, 6, 11500, "stop_record"),
        ]

        result, output = self.diagnose_rows(rows)

        self.assertTrue(result)
        self.assertIn("records=3", output)
        self.assertIn("valid=yes", output)
        self.assertIn("2:warm", output)

    def test_schema17_success_report_matches_legacy_summary(self) -> None:
        rows = [
            lifecycle_row(17, 1, 1000, "start_record"),
            device_health_row(2, 1000, "start", 31.5, 1),
            sensor_callback_row(17, 3, 1010),
            sensor_callback_row(17, 4, 1020),
            sensor_callback_row(17, 5, 1120),
            base_row(17, 6, 1130, "location"),
            base_row(17, 7, 1230, "location"),
            device_health_row(8, 11000, "periodic", None, 2),
            device_health_row(9, 11500, "stop", 33.5, None),
            lifecycle_row(17, 10, 11500, "stop_record"),
        ]

        result, output = self.diagnose_rows(rows)

        self.assertTrue(result)
        self.assertEqual(
            self.normalized_report(output),
            dedent(
                """
                === <source> ===
                rows=10, sensor_callback=3, invalid_json=0, schemas={17: 10}
                supported_uniform_schema=yes (supported=[15, 16, 17])
                recordSeq[session-1000]: first=1, last=10, missing=0, reverse=0, duplicate=0
                initial_start_record=yes
                terminal_stop_record=yes
                schema16_plus_sensor_set=yes (unexpected=[])

                Sensor callback cadence:

                accelerometer: records=3, requested_mode=10.00 ms/100.00 Hz (3 rows)
                  sensor timestamp: effective=18.18 Hz, P50=55.00 ms, P95=95.50 ms, max=100.00 ms
                    intervals=2, reverse=0, duplicate=0, gaps(>2.5xP50)=0, estimated_missing~=0
                  callback clock: effective=18.18 Hz, P50=55.00 ms, P95=95.50 ms, max=100.00 ms
                    intervals=2, reverse=0, duplicate=0, gaps(>2.5xP50)=0, estimated_missing~=0

                System callback cadence:

                location: records=2
                  callback clock: effective=10.00 Hz, P50=100.00 ms, P95=100.00 ms, max=100.00 ms
                    intervals=1, reverse=0, duplicate=0, gaps(>2.5xP50)=0, estimated_missing~=0

                Device health:
                  records=3, reasons={'start': 1, 'periodic': 1, 'stop': 1}, valid=yes
                  sampling clock: effective=0.19 Hz, P50=5250.00 ms, P95=9525.00 ms, max=10000.00 ms
                    intervals=2, reverse=0, duplicate=0, gaps(>2.5xP50)=0, estimated_missing~=0
                  batteryTemperatureC: min=31.5, max=33.5, unavailable=1
                  thermalLevel: {'1:normal': 1, '2:warm': 1}, unavailable=1
                """
            ).strip(),
        )

    def test_schema17_rejects_missing_device_health(self) -> None:
        rows = [
            lifecycle_row(17, 1, 1000, "start_record"),
            sensor_callback_row(17, 2, 1010),
            lifecycle_row(17, 3, 2000, "stop_record"),
        ]

        result, output = self.diagnose_rows(rows)

        self.assertFalse(result)
        self.assertIn("records=0", output)
        self.assertIn("valid=no", output)

    def test_schema17_reports_unavailable_health_values_without_corrupting_log(self) -> None:
        rows = [
            lifecycle_row(17, 1, 1000, "start_record"),
            device_health_row(2, 1000, "start", None, None),
            sensor_callback_row(17, 3, 1010),
            device_health_row(4, 2000, "stop", None, None),
            lifecycle_row(17, 5, 2000, "stop_record"),
        ]

        result, output = self.diagnose_rows(rows)

        self.assertTrue(result)
        self.assertIn("batteryTemperatureC: unavailable", output)
        self.assertIn("thermalLevel: unavailable", output)

    def test_schema17_rejects_invalid_health_values(self) -> None:
        rows = [
            lifecycle_row(17, 1, 1000, "start_record"),
            device_health_row(2, 1000, "start", 32.5, 99),
            sensor_callback_row(17, 3, 1010),
            device_health_row(4, 2000, "stop", 32.6, 1),
            lifecycle_row(17, 5, 2000, "stop_record"),
        ]

        result, output = self.diagnose_rows(rows)

        self.assertFalse(result)
        self.assertIn("invalid device_health lines: 2", output)


if __name__ == "__main__":
    unittest.main()
