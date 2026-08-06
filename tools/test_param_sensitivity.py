#!/usr/bin/env python3
import contextlib
import io
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

try:
    from . import param_sensitivity
except ImportError:
    import param_sensitivity  # type: ignore


class ParameterSensitivityTests(unittest.TestCase):
    def test_replay_one_uses_live_estimator_defaults_for_baseline(self) -> None:
        defaults = {"curve_positive_scale": 0.47, "calibration_duration_ms": 1600}
        summary = {
            "locationComparison": {
                "moving": {"maeKmh": 1.0, "biasKmh": 0.5, "count": 2}
            }
        }
        with mock.patch.object(
            param_sensitivity,
            "estimator_default_kwargs",
            return_value=defaults,
        ), mock.patch.object(
            param_sensitivity,
            "analyze_replay_rows",
            return_value=(summary, [{"speedKmh": 0.0}]),
        ) as analyze:
            result = param_sensitivity.replay_one([{"timestampMs": 1}])

        self.assertEqual(result["mae"], 1.0)
        self.assertEqual(analyze.call_args.args[1], defaults)
        self.assertFalse(analyze.call_args.kwargs["infer_start_from_sensor"])
        self.assertFalse(analyze.call_args.kwargs["include_lag_scans"])
        self.assertFalse(analyze.call_args.kwargs["include_bucketed_comparison"])
        self.assertFalse(analyze.call_args.kwargs["include_recorded_comparison"])

    def test_main_parses_once_and_perturbs_the_live_default(self) -> None:
        replay_results = [
            {"mae": 1.0, "bias": 0.0, "count": 10},
            {"mae": 1.2, "bias": 0.0, "count": 10},
            {"mae": 0.9, "bias": 0.0, "count": 10},
        ]
        argv = [
            "param_sensitivity.py",
            "sample.jsonl",
            "--param",
            "curve_positive_scale",
            "--min-paired",
            "1",
        ]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(
            param_sensitivity,
            "validate_scan_schema",
            return_value=[],
        ), mock.patch.object(
            param_sensitivity,
            "read_jsonl_with_info",
            return_value=(
                [{"timestampMs": 1}],
                SimpleNamespace(complete=True, status="complete"),
            ),
        ) as read_rows, mock.patch.object(
            param_sensitivity,
            "estimator_default_kwargs",
            return_value={"curve_positive_scale": 0.5},
        ), mock.patch.object(
            param_sensitivity,
            "replay_one",
            side_effect=replay_results,
        ) as replay_one, contextlib.redirect_stdout(io.StringIO()):
            return_code = param_sensitivity.main()

        self.assertEqual(return_code, 0)
        read_rows.assert_called_once()
        self.assertEqual(replay_one.call_count, 3)
        self.assertEqual(replay_one.call_args_list[0].args, ([{"timestampMs": 1}],))
        self.assertEqual(replay_one.call_args_list[1].args[1:3], ("curve_positive_scale", 0.6))
        self.assertEqual(replay_one.call_args_list[2].args[1:3], ("curve_positive_scale", 0.4))

    def test_main_reports_baseline_failure_without_traceback(self) -> None:
        argv = ["param_sensitivity.py", "sample.jsonl", "--min-paired", "1"]
        stderr = io.StringIO()
        with mock.patch.object(sys, "argv", argv), mock.patch.object(
            param_sensitivity,
            "validate_scan_schema",
            return_value=[],
        ), mock.patch.object(
            param_sensitivity,
            "read_jsonl_with_info",
            return_value=(
                [{"timestampMs": 1}],
                SimpleNamespace(complete=True, status="complete"),
            ),
        ), mock.patch.object(
            param_sensitivity,
            "estimator_default_kwargs",
            return_value={"curve_positive_scale": 0.5},
        ), mock.patch.object(
            param_sensitivity,
            "replay_one",
            side_effect=RuntimeError("missing comparison bucket"),
        ), contextlib.redirect_stderr(stderr):
            return_code = param_sensitivity.main()

        self.assertEqual(return_code, 1)
        self.assertIn(
            "failed to compute baseline: missing comparison bucket",
            stderr.getvalue(),
        )

    def test_main_rejects_input_without_confirmed_structural_completeness(self) -> None:
        for complete, status in (
            (False, "incomplete_structure"),
            (None, "unknown_structure"),
        ):
            with self.subTest(complete=complete), mock.patch.object(
                sys,
                "argv",
                ["param_sensitivity.py", "sample.jsonl"],
            ), mock.patch.object(
                param_sensitivity,
                "validate_scan_schema",
                return_value=[],
            ), mock.patch.object(
                param_sensitivity,
                "read_jsonl_with_info",
                return_value=(
                    [{"timestampMs": 1}],
                    SimpleNamespace(complete=complete, status=status),
                ),
            ), mock.patch.object(
                param_sensitivity,
                "replay_one",
            ) as replay_one:
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    return_code = param_sensitivity.main()

            self.assertEqual(return_code, 1)
            self.assertIn(f"status={status}", stderr.getvalue())
            self.assertIn("inputIntegrity.complete=true", stderr.getvalue())
            replay_one.assert_not_called()


if __name__ == "__main__":
    unittest.main()
