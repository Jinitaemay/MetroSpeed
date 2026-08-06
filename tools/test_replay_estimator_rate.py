#!/usr/bin/env python3
import math
import unittest
from unittest.mock import patch
from typing import Dict, List, Tuple

try:
    from . import replay_estimator as replay_module
    from .replay_estimator import (
        DEPARTURE_EVIDENCE_SECONDS,
        PARKING_WINDOW_SECONDS,
        PRE_CAL_BUFFER_SECONDS,
        REFERENCE_DT_SECONDS,
        MotionState,
        PreCalFrame,
        SensorFrame,
        SpeedEstimator,
        alpha_for_delta,
        build_anchored_outputs_v2,
        interpolate_phone_inertial_history,
        window_duration_seconds,
    )
except ImportError:
    import replay_estimator as replay_module
    from replay_estimator import (
        DEPARTURE_EVIDENCE_SECONDS,
        PARKING_WINDOW_SECONDS,
        PRE_CAL_BUFFER_SECONDS,
        REFERENCE_DT_SECONDS,
        MotionState,
        PreCalFrame,
        SensorFrame,
        SpeedEstimator,
        alpha_for_delta,
        build_anchored_outputs_v2,
        interpolate_phone_inertial_history,
        window_duration_seconds,
    )


GRAVITY = 9.80665


def sensor_frame(timestamp_ms: int, forward_acceleration: float = 0.0) -> SensorFrame:
    return SensorFrame(
        timestamp_ms=timestamp_ms,
        sensor_timestamp=float(timestamp_ms) * 1_000_000.0,
        acceleration=(forward_acceleration, 0.0, GRAVITY),
        gyroscope=(0.0, 0.0, 0.0),
        gyroscope_timestamp=None,
    )


def prepared_estimator() -> SpeedEstimator:
    estimator = SpeedEstimator()
    estimator.start(0)
    estimator.initial_calibration_done = True
    estimator.calibration_until_ms = -1
    estimator.main_axis = (1.0, 0.0, 0.0)
    estimator.main_axis_initialized = True
    estimator.main_axis_locked = True
    return estimator


def run_trajectory(rate_hz: int) -> Tuple[SpeedEstimator, Dict[int, float]]:
    estimator = prepared_estimator()
    step_ms = 1000 // rate_hz
    common_speeds: Dict[int, float] = {}
    for timestamp_ms in range(0, 8000 + step_ms, step_ms):
        if timestamp_ms < 1000:
            acceleration = 0.0
        elif timestamp_ms < 3000:
            acceleration = 0.30
        elif timestamp_ms < 4000:
            acceleration = 0.0
        elif timestamp_ms < 6000:
            acceleration = -0.30
        else:
            acceleration = 0.0
        output = estimator.ingest(sensor_frame(timestamp_ms, acceleration))
        if timestamp_ms % 20 == 0:
            common_speeds[timestamp_ms] = output.speed_kmh
    return estimator, common_speeds


def run_parking_calibration(
    rate_hz: int,
    depart_after_request: bool,
) -> Tuple[SpeedEstimator, int]:
    estimator = prepared_estimator()
    step_ms = 1000 // rate_hz
    request_ms = 4000

    for timestamp_ms in range(0, request_ms + step_ms, step_ms):
        estimator.ingest(sensor_frame(timestamp_ms, 0.04))

    if not estimator.calibrate_at_stop(request_ms):
        raise AssertionError("synthetic parking calibration request was rejected")

    result = 0
    for timestamp_ms in range(
        request_ms + step_ms,
        request_ms + 1800 + step_ms,
        step_ms,
    ):
        acceleration = 0.04
        if depart_after_request and timestamp_ms >= request_ms + 300:
            acceleration = 0.24
        estimator.ingest(sensor_frame(timestamp_ms, acceleration))
        result = estimator.consume_parking_calibration_result()
        if result != 0:
            break
    return estimator, result


def replay_frames(
    rate_hz: int,
    evidence_seconds: float,
    stationary_tail_seconds: float = 0.0,
) -> Tuple[SpeedEstimator, List[PreCalFrame]]:
    delta_seconds = 1.0 / rate_hz
    step_ms = 1000 // rate_hz
    stationary_count = round(PARKING_WINDOW_SECONDS * rate_hz)
    evidence_count = round(evidence_seconds * rate_hz)
    stationary_tail_count = round(stationary_tail_seconds * rate_hz)
    frames: List[PreCalFrame] = []

    for index in range(stationary_count):
        frames.append(
            PreCalFrame(
                timestamp_ms=index * step_ms,
                acceleration=(0.0, 0.0, GRAVITY),
                gyro_magnitude=0.0,
                acc_step=0.0,
                delta_seconds=delta_seconds,
                elapsed_seconds=delta_seconds,
                continuous=True,
                distance_before_m=0.0,
                integrated=False,
            )
        )

    for offset in range(evidence_count):
        frames.append(
            PreCalFrame(
                timestamp_ms=(stationary_count + offset) * step_ms,
                acceleration=(1.0, 0.0, GRAVITY),
                gyro_magnitude=0.0,
                acc_step=0.0,
                delta_seconds=delta_seconds,
                elapsed_seconds=delta_seconds,
                continuous=True,
                distance_before_m=0.0,
                integrated=True,
            )
        )

    for offset in range(stationary_tail_count):
        frames.append(
            PreCalFrame(
                timestamp_ms=(stationary_count + evidence_count + offset) * step_ms,
                acceleration=(0.0, 0.0, GRAVITY),
                gyro_magnitude=0.0,
                acc_step=0.0,
                delta_seconds=delta_seconds,
                elapsed_seconds=delta_seconds,
                continuous=True,
                distance_before_m=0.0,
                integrated=True,
            )
        )

    # The live ingest path owns the final, not-yet-integrated frame.
    frames.append(
        PreCalFrame(
            timestamp_ms=(
                stationary_count + evidence_count + stationary_tail_count
            ) * step_ms,
            acceleration=(0.0, 0.0, GRAVITY),
            gyro_magnitude=0.0,
            acc_step=0.0,
            delta_seconds=delta_seconds,
            elapsed_seconds=delta_seconds,
            continuous=True,
            distance_before_m=0.0,
            integrated=False,
        )
    )

    estimator = prepared_estimator()
    estimator.apply_parking_replay(frames, 0, stationary_count - 1)
    return estimator, frames


class RateIndependentEstimatorTests(unittest.TestCase):
    def test_reference_alpha_has_same_20_ms_response(self) -> None:
        alpha_50 = alpha_for_delta(0.22, REFERENCE_DT_SECONDS)
        alpha_100 = alpha_for_delta(0.22, REFERENCE_DT_SECONDS / 2.0)
        response_50 = alpha_50
        response_100 = alpha_100 + alpha_100 * (1.0 - alpha_100)
        self.assertAlmostEqual(alpha_50, 0.22, places=12)
        self.assertAlmostEqual(response_100, response_50, places=12)

    def test_acc_step_is_normalized_to_20_ms(self) -> None:
        estimator_50 = SpeedEstimator()
        estimator_100 = SpeedEstimator()
        estimator_50.compute_acc_step((0.0, 0.0, GRAVITY), 0.02)
        estimator_100.compute_acc_step((0.0, 0.0, GRAVITY), 0.01)
        step_50 = estimator_50.compute_acc_step((0.02, 0.0, GRAVITY), 0.02)
        step_100 = estimator_100.compute_acc_step((0.01, 0.0, GRAVITY), 0.01)
        self.assertAlmostEqual(step_50, step_100, places=12)

    def test_axis_mix_and_update_count_keep_50_hz_time_semantics(self) -> None:
        axes = {}
        for rate_hz in (50, 100):
            estimator = SpeedEstimator()
            estimator.main_axis_initialized = True
            estimator.main_axis = (1.0, 0.0, 0.0)
            delta_seconds = 1.0 / rate_hz
            for index in range(round(1.2 * rate_hz)):
                estimator.update_main_axis(
                    (1.0, 0.2, 0.0),
                    round(index * delta_seconds * 1000),
                    delta_seconds,
                )
            self.assertAlmostEqual(estimator.main_axis_update_count, 60.0, places=12)
            axes[rate_hz] = estimator.main_axis
        for component_50, component_100 in zip(axes[50], axes[100]):
            self.assertAlmostEqual(component_50, component_100, delta=0.0001)

    def test_invalid_delta_reuses_last_valid_rate(self) -> None:
        estimator = SpeedEstimator()
        estimator.start(0)
        first = estimator.compute_delta_seconds(sensor_frame(10))
        second = estimator.compute_delta_seconds(sensor_frame(20))
        duplicate = estimator.compute_delta_seconds(sensor_frame(20))
        non_finite = estimator.compute_delta_seconds(
            SensorFrame(
                timestamp_ms=math.nan,  # type: ignore[arg-type]
                sensor_timestamp=None,
                acceleration=(0.0, 0.0, GRAVITY),
                gyroscope=(0.0, 0.0, 0.0),
                gyroscope_timestamp=None,
            )
        )
        self.assertAlmostEqual(first, 0.01)
        self.assertAlmostEqual(second, 0.01)
        self.assertAlmostEqual(duplicate, 0.01)
        self.assertAlmostEqual(non_finite, 0.01)

    def test_long_gap_clamps_integration_but_restarts_evidence_time(self) -> None:
        estimator = SpeedEstimator()
        estimator.start(0)
        estimator.compute_delta_seconds(sensor_frame(10))
        estimator.compute_delta_seconds(sensor_frame(20))
        gap_delta = estimator.compute_delta_seconds(sensor_frame(1000))
        self.assertAlmostEqual(gap_delta, 0.08)
        self.assertAlmostEqual(estimator.last_elapsed_delta_seconds, 0.01)
        self.assertFalse(estimator.last_sample_continuous)
        self.assertAlmostEqual(estimator.last_valid_delta_seconds, 0.01)

    def test_phone_gnss_anchor_interpolates_40_ms_history(self) -> None:
        history = [
            {"timestampMs": 940, "speedKmh": 5.0 * 3.6},
            {"timestampMs": 950, "speedKmh": 5.5 * 3.6},
            {"timestampMs": 960, "speedKmh": 6.0 * 3.6},
            {"timestampMs": 970, "speedKmh": 7.0 * 3.6},
            {"timestampMs": 980, "speedKmh": 8.0 * 3.6},
        ]
        # App target is callback 1005 ms - 40 ms = 965 ms.
        self.assertAlmostEqual(
            interpolate_phone_inertial_history(history, 1005),
            6.5,
            places=12,
        )

    def test_tunnel_freezes_anchor_usability_until_exit(self) -> None:
        rows = [
            {
                "timestampMs": 0,
                "recordType": "event",
                "event": "开始测速",
                "measurementRunId": "run-1",
                "measurementActive": True,
            },
            {
                "timestampMs": 100,
                "recordType": "location",
                "event": "location",
                "measurementRunId": "run-1",
                "measurementActive": True,
                "locationSpeedMps": 20.0,
                "locationSpeedAccuracyMps": 1.0,
                "locationSourceType": 1,
            },
            {
                "timestampMs": 200,
                "recordType": "event",
                "event": "入隧",
                "measurementRunId": "run-1",
                "measurementActive": True,
            },
            {
                "timestampMs": 300,
                "recordType": "location",
                "event": "location",
                "measurementRunId": "run-1",
                "measurementActive": True,
                "locationSpeedMps": 5.0,
                "locationSpeedAccuracyMps": 0.5,
                "locationSourceType": 4,
            },
            {
                "timestampMs": 400,
                "recordType": "event",
                "event": "出隧",
                "measurementRunId": "run-1",
                "measurementActive": True,
            },
            {
                "timestampMs": 500,
                "recordType": "location",
                "event": "location",
                "measurementRunId": "run-1",
                "measurementActive": True,
                "locationSpeedMps": 30.0,
                "locationSpeedAccuracyMps": 1.0,
                "locationSourceType": 4,
            },
            {
                "timestampMs": 600,
                "recordType": "location",
                "event": "location",
                "measurementRunId": "run-1",
                "measurementActive": True,
                "locationSpeedMps": 0.5,
                "locationSpeedAccuracyMps": 1.0,
                "locationSourceType": 1,
            },
        ]
        outputs = [
            {
                "timestampMs": 150,
                "sourceRowIndex": 1,
                "sensorTimestamp": 150_000_000,
                "measurementRunId": "run-1",
                "speedKmh": 0.0,
                "confidence": 0.5,
                "motionState": "cruise",
            },
            {
                "timestampMs": 350,
                "sourceRowIndex": 3,
                "sensorTimestamp": 350_000_000,
                "measurementRunId": "run-1",
                "speedKmh": 3.6,
                "confidence": 0.5,
                "motionState": "cruise",
            },
            {
                "timestampMs": 450,
                "sourceRowIndex": 4,
                "sensorTimestamp": 450_000_000,
                "measurementRunId": "run-1",
                "speedKmh": 7.2,
                "confidence": 0.5,
                "motionState": "cruise",
            },
            {
                "timestampMs": 550,
                "sourceRowIndex": 5,
                "sensorTimestamp": 550_000_000,
                "measurementRunId": "run-1",
                "speedKmh": 10.8,
                "confidence": 0.5,
                "motionState": "strong_vibration",
            },
            {
                "timestampMs": 650,
                "sourceRowIndex": 6,
                "sensorTimestamp": 650_000_000,
                "measurementRunId": "run-1",
                "speedKmh": 14.4,
                "confidence": 0.5,
                "motionState": "strong_vibration",
            },
        ]

        anchored = build_anchored_outputs_v2(rows, outputs, pure_zero=True)

        self.assertEqual(len(anchored), 5)
        self.assertTrue(anchored[0]["anchorApplied"])
        self.assertAlmostEqual(anchored[0]["anchoredSpeedKmh"], 72.0)
        self.assertTrue(anchored[1]["anchorApplied"])
        self.assertAlmostEqual(anchored[1]["anchoredSpeedKmh"], 75.6)
        self.assertAlmostEqual(anchored[1]["anchorSpeedKmh"], 72.0)
        self.assertFalse(anchored[2]["anchorApplied"])
        self.assertAlmostEqual(anchored[2]["anchoredSpeedKmh"], 7.2)
        self.assertTrue(anchored[3]["anchorApplied"])
        self.assertAlmostEqual(anchored[3]["anchorSpeedKmh"], 108.0)
        self.assertFalse(anchored[4]["anchorApplied"])
        self.assertAlmostEqual(anchored[4]["anchoredSpeedKmh"], 14.4)
        self.assertAlmostEqual(anchored[1]["displayMaxSpeedKmh"], 75.6)

    def test_reliable_gnss_updates_anchor_during_both_vibration_states(self) -> None:
        rows = [
            {
                "timestampMs": 0,
                "recordType": "event",
                "event": "开始测速",
                "measurementRunId": "run-1",
                "measurementActive": True,
            },
            {
                "timestampMs": 90,
                "recordType": "sensor",
                "measurementRunId": "run-1",
                "measurementActive": True,
            },
            {
                "timestampMs": 100,
                "recordType": "location",
                "measurementRunId": "run-1",
                "measurementActive": True,
                "locationSpeedMps": 10.0,
                "locationSpeedAccuracyMps": 1.0,
                "locationSourceType": 1,
            },
            {
                "timestampMs": 190,
                "recordType": "sensor",
                "measurementRunId": "run-1",
                "measurementActive": True,
            },
            {
                "timestampMs": 200,
                "recordType": "location",
                "measurementRunId": "run-1",
                "measurementActive": True,
                "locationSpeedMps": 20.0,
                "locationSpeedAccuracyMps": 1.0,
                "locationSourceType": 4,
            },
        ]
        outputs = [
            {
                "timestampMs": 90,
                "sourceRowIndex": 1,
                "sensorTimestamp": 90_000_000,
                "measurementRunId": "run-1",
                "speedKmh": 3.6,
                "confidence": 0.5,
                "motionState": "conduction_vibration",
            },
            {
                "timestampMs": 190,
                "sourceRowIndex": 3,
                "sensorTimestamp": 190_000_000,
                "measurementRunId": "run-1",
                "speedKmh": 7.2,
                "confidence": 0.5,
                "motionState": "strong_vibration",
            },
            {
                "timestampMs": 210,
                "sourceRowIndex": 4,
                "sensorTimestamp": 210_000_000,
                "measurementRunId": "run-1",
                "speedKmh": 10.8,
                "confidence": 0.5,
                "motionState": "strong_vibration",
            },
        ]

        anchored = build_anchored_outputs_v2(rows, outputs, pure_zero=True)

        self.assertFalse(anchored[0]["anchorApplied"])
        self.assertTrue(anchored[1]["anchorApplied"])
        self.assertAlmostEqual(anchored[1]["anchorSpeedKmh"], 36.0)
        self.assertTrue(anchored[2]["anchorApplied"])
        self.assertAlmostEqual(anchored[2]["anchorSpeedKmh"], 72.0)

    def test_anchor_history_uses_rolling_window_and_reports_raw_speed(self) -> None:
        rows = [
            {
                "timestampMs": 0,
                "recordType": "event",
                "event": "开始测速",
                "measurementRunId": "run-1",
                "measurementActive": True,
            }
        ]
        outputs = []
        expected_anchor_count = 0
        for sample_index in range(1, 501):
            timestamp_ms = sample_index * 10
            source_row_index = len(rows)
            rows.append({
                "timestampMs": timestamp_ms,
                "recordType": "sensor",
                "measurementRunId": "run-1",
                "measurementActive": True,
            })
            outputs.append({
                "timestampMs": timestamp_ms,
                "sourceRowIndex": source_row_index,
                "sensorTimestamp": timestamp_ms * 1_000_000,
                "measurementRunId": "run-1",
                "speedKmh": sample_index * 0.01,
                "confidence": 0.5,
                "motionState": "cruise",
            })
            if sample_index % 10 == 0:
                rows.append({
                    "timestampMs": timestamp_ms + 1,
                    "recordType": "location",
                    "measurementRunId": "run-1",
                    "measurementActive": True,
                    "locationSpeedMps": 10.0,
                    "locationSpeedAccuracyMps": 1.0,
                    "locationSourceType": 1,
                })
                expected_anchor_count += 1

        observed_history_lengths = []
        original_interpolate = replay_module.interpolate_phone_inertial_history

        def track_history(history, callback_ms):
            observed_history_lengths.append(len(history))
            return original_interpolate(history, callback_ms)

        diagnostics = {}
        with patch.object(
            replay_module,
            "interpolate_phone_inertial_history",
            side_effect=track_history,
        ):
            anchored = build_anchored_outputs_v2(
                rows,
                outputs,
                pure_zero=True,
                diagnostics=diagnostics,
            )

        self.assertEqual(len(anchored), len(outputs))
        self.assertEqual(
            len(diagnostics["rawEligibleAnchorSpeedsKmh"]),
            expected_anchor_count,
        )
        self.assertTrue(observed_history_lengths)
        self.assertLessEqual(max(observed_history_lengths), 22)

    def test_buffers_keep_equal_time_at_50_and_100_hz(self) -> None:
        results = {}
        for rate_hz in (50, 100):
            estimator = prepared_estimator()
            step_ms = 1000 // rate_hz
            for timestamp_ms in range(0, 4000 + step_ms, step_ms):
                estimator.ingest(sensor_frame(timestamp_ms))
            results[rate_hz] = (
                len(estimator.pre_cal_buffer),
                sum(frame.elapsed_seconds for frame in estimator.pre_cal_buffer),
                len(estimator.window_frames),
                window_duration_seconds(estimator.window_frames),
            )

        self.assertEqual(results[50][0], 180)
        self.assertEqual(results[100][0], 360)
        self.assertAlmostEqual(results[50][1], PRE_CAL_BUFFER_SECONDS, places=12)
        self.assertAlmostEqual(results[100][1], PRE_CAL_BUFFER_SECONDS, places=12)
        self.assertEqual(results[50][2], 80)
        self.assertEqual(results[100][2], 160)
        self.assertAlmostEqual(results[50][3], 1.6, places=12)
        self.assertAlmostEqual(results[100][3], 1.6, places=12)

    def test_50_and_100_hz_trajectories_remain_close(self) -> None:
        estimator_50, speeds_50 = run_trajectory(50)
        estimator_100, speeds_100 = run_trajectory(100)
        self.assertEqual(speeds_50.keys(), speeds_100.keys())
        max_speed_difference = max(
            abs(speeds_50[timestamp_ms] - speeds_100[timestamp_ms])
            for timestamp_ms in speeds_50
        )
        self.assertLess(max_speed_difference, 0.025)
        self.assertAlmostEqual(
            estimator_50.max_speed_kmh,
            estimator_100.max_speed_kmh,
            delta=0.002,
        )
        self.assertAlmostEqual(
            estimator_50.distance_m,
            estimator_100.distance_m,
            delta=0.002,
        )

    def test_conduction_spikes_cannot_claim_axis_before_real_departure(self) -> None:
        estimator = SpeedEstimator()
        estimator.start(0)

        for timestamp_ms in range(0, 1500 + 10, 10):
            estimator.ingest(
                SensorFrame(
                    timestamp_ms=timestamp_ms,
                    sensor_timestamp=float(timestamp_ms) * 1_000_000.0,
                    acceleration=(0.0, 0.0, GRAVITY),
                    gyroscope=(0.0, 0.0, 0.0),
                    gyroscope_timestamp=None,
                )
            )

        spike_states = []
        for timestamp_ms, spike_x in ((1510, 1.6), (1520, -1.6)):
            output = estimator.ingest(
                SensorFrame(
                    timestamp_ms=timestamp_ms,
                    sensor_timestamp=float(timestamp_ms) * 1_000_000.0,
                    acceleration=(spike_x, 0.0, GRAVITY),
                    gyroscope=(0.0, 0.0, 0.0),
                    gyroscope_timestamp=None,
                )
            )
            spike_states.append(output.motion_state)

        self.assertEqual(
            spike_states,
            [MotionState.CONDUCTION_VIBRATION, MotionState.CONDUCTION_VIBRATION],
        )
        self.assertFalse(estimator.main_axis_initialized)

        final_output = None
        for timestamp_ms in range(1530, 6530, 10):
            final_output = estimator.ingest(
                SensorFrame(
                    timestamp_ms=timestamp_ms,
                    sensor_timestamp=float(timestamp_ms) * 1_000_000.0,
                    acceleration=(0.0, 0.6, GRAVITY),
                    gyroscope=(0.0, 0.0, 0.0),
                    gyroscope_timestamp=None,
                )
            )

        self.assertIsNotNone(final_output)
        assert final_output is not None
        self.assertTrue(estimator.main_axis_initialized)
        self.assertGreater(abs(estimator.main_axis[1]), 0.98)
        self.assertAlmostEqual(final_output.speed_kmh, 10.0, delta=0.75)

    def test_parking_calibration_resets_stationary_drift_at_both_rates(self) -> None:
        estimator_50, result_50 = run_parking_calibration(50, False)
        estimator_100, result_100 = run_parking_calibration(100, False)
        self.assertEqual(result_50, 1)
        self.assertEqual(result_100, 1)
        self.assertEqual(estimator_50.velocity_mps, 0.0)
        self.assertEqual(estimator_100.velocity_mps, 0.0)
        self.assertAlmostEqual(
            estimator_50.distance_m,
            estimator_100.distance_m,
            delta=0.002,
        )

    def test_parking_replay_preserves_sustained_departure_at_both_rates(self) -> None:
        estimator_50, result_50 = run_parking_calibration(50, True)
        estimator_100, result_100 = run_parking_calibration(100, True)
        self.assertEqual(result_50, 1)
        self.assertEqual(result_100, 1)
        self.assertGreater(estimator_50.velocity_mps, 0.20)
        self.assertGreater(estimator_100.velocity_mps, 0.20)
        self.assertAlmostEqual(
            estimator_50.velocity_mps,
            estimator_100.velocity_mps,
            delta=0.01,
        )

    def test_departure_evidence_is_60_ms_not_a_frame_count(self) -> None:
        for rate_hz in (50, 100):
            short, _ = replay_frames(rate_hz, DEPARTURE_EVIDENCE_SECONDS - 0.02)
            sustained, _ = replay_frames(rate_hz, DEPARTURE_EVIDENCE_SECONDS)
            self.assertEqual(short.velocity_mps, 0.0)
            self.assertEqual(short.distance_m, 0.0)
            self.assertGreater(sustained.velocity_mps, 0.0)
            self.assertGreater(sustained.distance_m, 0.0)

    def test_20_ms_raw_spike_cannot_use_filter_tail_as_departure(self) -> None:
        for rate_hz in (50, 100):
            estimator, _ = replay_frames(
                rate_hz,
                evidence_seconds=0.02,
                stationary_tail_seconds=0.20,
            )
            self.assertEqual(estimator.velocity_mps, 0.0)
            self.assertEqual(estimator.distance_m, 0.0)

    def test_long_gap_discards_old_parking_and_motion_windows(self) -> None:
        estimator = prepared_estimator()
        for timestamp_ms in range(0, 1500, 20):
            estimator.ingest(sensor_frame(timestamp_ms))

        estimator.ingest(sensor_frame(10000))
        self.assertEqual(len(estimator.pre_cal_buffer), 1)
        self.assertEqual(len(estimator.window_frames), 1)
        self.assertFalse(estimator.pre_cal_buffer[0].continuous)

        self.assertTrue(estimator.calibrate_at_stop(10000))
        result = 0
        for timestamp_ms in range(10020, 11600, 20):
            estimator.ingest(sensor_frame(timestamp_ms))
            result = estimator.consume_parking_calibration_result()
            if result != 0:
                break
        self.assertEqual(result, -1)

    def test_parking_action_delay_only_controls_evidence_freshness(self) -> None:
        base_wall_ms = 1_780_000_000_000

        def run_request(action_delay_ms: float, next_sensor_delay_ms: int) -> Tuple[SpeedEstimator, int]:
            estimator = prepared_estimator()
            estimator.last_timestamp_ms = base_wall_ms
            for elapsed_ms in range(0, 2000 + 20, 20):
                estimator.ingest(SensorFrame(
                    timestamp_ms=base_wall_ms + elapsed_ms,
                    sensor_timestamp=float(elapsed_ms) * 1_000_000.0,
                    acceleration=(0.0, 0.0, GRAVITY),
                    gyroscope=(0.0, 0.0, 0.0),
                    gyroscope_timestamp=None,
                ))

            logical_before_click = estimator.logical_timestamp_ms
            action_timestamp_ms = base_wall_ms + 2000 + action_delay_ms
            self.assertTrue(estimator.calibrate_at_stop(action_timestamp_ms))
            self.assertEqual(estimator.logical_timestamp_ms, logical_before_click)
            self.assertEqual(estimator.parking_calibration_request_ms, logical_before_click)

            result = 0
            first_elapsed_ms = 2000 + next_sensor_delay_ms + 20
            for elapsed_ms in range(first_elapsed_ms, 3800, 20):
                estimator.ingest(SensorFrame(
                    timestamp_ms=base_wall_ms + elapsed_ms,
                    sensor_timestamp=float(elapsed_ms) * 1_000_000.0,
                    acceleration=(0.0, 0.0, GRAVITY),
                    gyroscope=(0.0, 0.0, 0.0),
                    gyroscope_timestamp=None,
                ))
                result = estimator.consume_parking_calibration_result()
                if result != 0:
                    break
            return estimator, result

        accepted, accepted_result = run_request(200.0, 200)
        self.assertEqual(accepted_result, 1)
        self.assertEqual(accepted.last_calibration_ms, 2000.0)

        for action_delay_ms, next_sensor_delay_ms in (
            (301.0, 301),
            (500.0, 500),
            (86_400_000.0, 500),
            (-100.0, 0),
            (float("nan"), 0),
            (float("inf"), 0),
        ):
            with self.subTest(action_delay_ms=action_delay_ms):
                rejected, rejected_result = run_request(
                    action_delay_ms,
                    next_sensor_delay_ms,
                )
                self.assertEqual(rejected_result, -1)
                self.assertEqual(rejected.last_calibration_ms, 0)

    def test_gap_drops_pre_gap_filter_tail_during_parking_replay(self) -> None:
        for rate_hz in (50, 100):
            estimator = prepared_estimator()
            step_ms = 1000 // rate_hz
            request_ms = 4000
            for timestamp_ms in range(0, request_ms + step_ms, step_ms):
                estimator.ingest(sensor_frame(timestamp_ms))

            self.assertTrue(estimator.calibrate_at_stop(request_ms))
            for timestamp_ms in range(
                request_ms + step_ms,
                request_ms + 40 + step_ms,
                step_ms,
            ):
                estimator.ingest(sensor_frame(timestamp_ms, 1.0))

            result = 0
            for timestamp_ms in range(
                request_ms + 1040,
                request_ms + 1800 + step_ms,
                step_ms,
            ):
                estimator.ingest(sensor_frame(timestamp_ms))
                result = estimator.consume_parking_calibration_result()
                if result != 0:
                    break

            self.assertEqual(result, 1)
            self.assertEqual(estimator.velocity_mps, 0.0)
            self.assertEqual(estimator.distance_m, 0.0)

    def test_long_gap_restarts_initial_calibration_without_incrementing_count(self) -> None:
        estimator = SpeedEstimator()
        estimator.start(0)
        for timestamp_ms in range(0, 580, 20):
            estimator.ingest(sensor_frame(timestamp_ms))
        self.assertEqual(estimator.calibration_samples, 29)

        estimator.ingest(sensor_frame(10000))
        self.assertEqual(estimator.calibration_samples, 1)
        self.assertEqual(estimator.calibration_until_ms, 11500)
        self.assertEqual(estimator.calibration_count, 1)

        for timestamp_ms in range(10020, 11540, 20):
            estimator.ingest(sensor_frame(timestamp_ms))
        self.assertTrue(estimator.initial_calibration_done)
        self.assertEqual(estimator.calibration_count, 1)

    def test_wall_clock_jump_does_not_advance_estimator_clock(self) -> None:
        reference = prepared_estimator()
        jumped = prepared_estimator()

        for sample_index in range(200):
            sensor_timestamp = float(sample_index * 20) * 1_000_000.0
            reference.ingest(SensorFrame(
                timestamp_ms=sample_index * 20,
                sensor_timestamp=sensor_timestamp,
                acceleration=(0.30, 0.0, GRAVITY),
                gyroscope=(0.0, 0.0, 0.0),
                gyroscope_timestamp=None,
            ))
            jumped.ingest(SensorFrame(
                timestamp_ms=sample_index * 20 + (86_400_000 if sample_index >= 100 else 0),
                sensor_timestamp=sensor_timestamp,
                acceleration=(0.30, 0.0, GRAVITY),
                gyroscope=(0.0, 0.0, 0.0),
                gyroscope_timestamp=None,
            ))

        self.assertAlmostEqual(jumped.logical_timestamp_ms, 3980.0)
        self.assertAlmostEqual(jumped.logical_timestamp_ms, reference.logical_timestamp_ms)
        self.assertAlmostEqual(jumped.velocity_mps, reference.velocity_mps, places=12)
        self.assertAlmostEqual(jumped.confidence, reference.confidence, places=12)

    def test_wall_clock_only_fallback_clamps_discontinuous_jump(self) -> None:
        estimator = prepared_estimator()
        estimator.ingest(SensorFrame(
            timestamp_ms=0,
            sensor_timestamp=None,
            acceleration=(0.30, 0.0, GRAVITY),
            gyroscope=(0.0, 0.0, 0.0),
            gyroscope_timestamp=None,
        ))
        estimator.ingest(SensorFrame(
            timestamp_ms=86_400_000,
            sensor_timestamp=None,
            acceleration=(0.30, 0.0, GRAVITY),
            gyroscope=(0.0, 0.0, 0.0),
            gyroscope_timestamp=None,
        ))

        self.assertAlmostEqual(estimator.logical_timestamp_ms, 20.0)
        self.assertFalse(estimator.last_sample_continuous)

    def test_replay_seconds_since_calibration_uses_logical_clock(self) -> None:
        rows = [
            {"timestampMs": 0, "event": "\u5f00\u59cb\u6d4b\u901f"},
            {
                "timestampMs": 0,
                "recordType": "sensor",
                "sensorTimestamp": 0.0,
                "accX": 0.0,
                "accY": 0.0,
                "accZ": GRAVITY,
            },
            {
                "timestampMs": 86_400_020,
                "recordType": "sensor",
                "sensorTimestamp": 20_000_000.0,
                "accX": 0.0,
                "accY": 0.0,
                "accZ": GRAVITY,
            },
        ]

        outputs, _ = replay_module.replay(
            rows,
            strict_start=False,
            infer_start_from_sensor=False,
        )

        self.assertEqual(len(outputs), 2)
        self.assertAlmostEqual(outputs[-1]["secondsSinceCalibration"], 0.02)


if __name__ == "__main__":
    unittest.main()
