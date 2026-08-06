#!/usr/bin/env python3
import unittest
from unittest import mock

try:
    from . import replay_estimator
except ImportError:
    import replay_estimator  # type: ignore


def output_sample(
    run_id: str,
    timestamp_ms: int,
    source_row_index: int,
    speed_kmh: float,
) -> dict[str, object]:
    return {
        "measurementRunId": run_id,
        "timestampMs": timestamp_ms,
        "sourceRowIndex": source_row_index,
        "speedKmh": speed_kmh,
        "secondsSinceCalibration": 1.0,
    }


def location_row(run_id: str, timestamp_ms: int) -> dict[str, object]:
    return {
        "recordType": "location",
        "timestampMs": timestamp_ms,
        "locationTimeMs": timestamp_ms,
        "locationSpeedMps": 0.0,
        "locationSourceType": 1,
        "locationSpeedAccuracyMps": 1.0,
        "measurementRunId": run_id,
    }


class ReplayAnalysisTests(unittest.TestCase):
    def test_bucket_ids_follow_processing_order_when_wall_clock_moves_backward(self) -> None:
        rows = [location_row("run-a", 10_050), location_row("run-b", 5_050)]
        outputs = [
            output_sample("run-a", 10_000, 0, 10.0),
            output_sample("run-a", 10_100, 0, 10.0),
            output_sample("run-b", 5_000, 1, 20.0),
            output_sample("run-b", 5_100, 1, 20.0),
        ]

        result = replay_estimator.compare_bucketed(rows, outputs)

        self.assertEqual(result["cal_0"]["all"]["biasKmh"], 10.0)
        self.assertEqual(result["cal_1"]["all"]["biasKmh"], 20.0)

    def test_lag_scan_indexes_location_rows_once(self) -> None:
        rows = [
            location_row("run-a", 1_050),
            {"recordType": "event", "timestampMs": 1_060},
            location_row("run-a", 1_070),
        ]
        outputs = [
            output_sample("run-a", 1_000, 0, 10.0),
            output_sample("run-a", 1_100, 2, 10.0),
        ]
        original = replay_estimator.location_row_is_comparable
        with mock.patch.object(
            replay_estimator,
            "location_row_is_comparable",
            wraps=original,
        ) as comparable:
            replay_estimator.scan_location_lag(
                rows,
                outputs,
                min_lag_ms=-20,
                max_lag_ms=20,
                step_ms=20,
            )

        self.assertEqual(comparable.call_count, len(rows))

    def test_fast_summary_path_omits_only_expensive_optional_analyses(self) -> None:
        with mock.patch.object(
            replay_estimator,
            "recorded_estimator_abs_diffs",
            side_effect=AssertionError("recorded comparison must be skipped"),
        ) as recorded_comparison:
            summary = replay_estimator.summarize(
                [],
                [],
                [],
                dict(replay_estimator.APP_PARITY_CONFIG),
                include_lag_scans=False,
                include_bucketed_comparison=False,
                include_recorded_comparison=False,
            )

        recorded_comparison.assert_not_called()
        self.assertIn("locationComparison", summary)
        self.assertNotIn("locationLagScan", summary)
        self.assertNotIn("locationLagScanFine", summary)
        self.assertNotIn("calibrationDecay", summary)

    def test_default_summary_reuses_one_output_run_index(self) -> None:
        original = replay_estimator.build_output_runs
        with (
            mock.patch.object(
                replay_estimator,
                "build_output_runs",
                wraps=original,
            ) as build_runs,
            mock.patch.object(
                replay_estimator,
                "recorded_estimator_abs_diffs",
                return_value=([1.25], "test"),
            ) as recorded_comparison,
        ):
            summary = replay_estimator.summarize(
                [],
                [],
                [],
                dict(replay_estimator.APP_PARITY_CONFIG),
                include_lag_scans=False,
                include_bucketed_comparison=False,
            )

        self.assertEqual(build_runs.call_count, 1)
        recorded_comparison.assert_called_once()
        self.assertEqual(summary["recordedEstimatorDiff"]["maeKmh"], 1.25)

    def test_invalid_location_time_is_a_controlled_value_error(self) -> None:
        rows = [location_row("run-a", 1_050)]
        rows[0]["locationTimeMs"] = {"invalid": True}
        outputs = [
            output_sample("run-a", 1_000, 0, 10.0),
            output_sample("run-a", 1_100, 0, 10.0),
        ]

        with self.assertRaisesRegex(ValueError, "locationTimeMs must be a finite integer"):
            replay_estimator.compare_with_location(rows, outputs)

    def test_zero_location_time_keeps_timestamp_fallback_compatibility(self) -> None:
        rows = [location_row("run-a", 1_050)]
        rows[0]["locationTimeMs"] = 0
        outputs = [
            output_sample("run-a", 1_000, 0, 10.0),
            output_sample("run-a", 1_100, 0, 10.0),
        ]

        result = replay_estimator.compare_with_location(rows, outputs)

        self.assertEqual(result["pairedLocationRows"], 1)

    def test_app_parity_compatibility_key_is_explicitly_config_only(self) -> None:
        summary = replay_estimator.summarize(
            [],
            [],
            [],
            dict(replay_estimator.APP_PARITY_CONFIG),
            use_anchor_v2=True,
            pure_zero=True,
            include_lag_scans=False,
            include_bucketed_comparison=False,
        )
        config = summary["replayConfig"]

        self.assertTrue(config["appParity"])
        self.assertTrue(config["appParityDeprecated"])
        self.assertEqual(config["appParity"], config["appConfigParity"])
        self.assertEqual(config["crossRuntimeParity"], "not_verified")


if __name__ == "__main__":
    unittest.main()
