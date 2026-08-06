import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def section(source: str, begin: str, end: str) -> str:
    start = source.index(begin)
    finish = source.index(end, start)
    return source[start:finish]


class CoreSensorAxisGuardContractTests(unittest.TestCase):
    def test_current_state_gates_axis_learning_before_final_projection(self) -> None:
        estimator = read("entry/src/main/ets/model/SpeedEstimator.ets")
        ingest = section(estimator, "  ingest(frame: SensorFrame)", "  private beginCalibration")

        push = ingest.index("this.pushWindowFrame(")
        candidate = ingest.index("const axisCandidateState = this.detectMotionState")
        axis_gate = ingest.index("this.shouldUpdateMainAxis(filtered, gyroMagnitude, axisCandidateState)")
        replacement = ingest.index("this.replaceLatestWindowProjection")
        final_state = ingest.index("const state = this.detectMotionState", candidate + 1)
        self.assertLess(push, candidate)
        self.assertLess(candidate, axis_gate)
        self.assertLess(axis_gate, replacement)
        self.assertLess(replacement, final_state)

        gate = section(estimator, "  private shouldUpdateMainAxis", "  private updateMainAxis(")
        initialization = gate.index("if (!this.mainAxisInitialized)")
        for state in (
            "MotionState.Curve",
            "MotionState.LowConfidence",
            "MotionState.StrongVibration",
            "MotionState.ConductionVibration",
        ):
            self.assertLess(gate.index(state), initialization)

    def test_required_gyro_never_reaches_estimator_as_implicit_zero(self) -> None:
        controller = read("entry/src/main/ets/model/SensorController.ets")
        emit = section(controller, "  private emitFrame", "  private observeAccelerationContinuity")
        missing = emit.index("this.handleMissingGyroscope")
        required = emit.index("if (this.requireGyroscope)", missing)
        blocked = emit.index("return;", required)
        delivered = emit.index("this.frameCallback(frame)")
        self.assertLess(missing, required)
        self.assertLess(required, blocked)
        self.assertLess(blocked, delivered)

        self.assertIn("fatalCallback?: CoreSensorFatalCallback", controller)
        self.assertIn("sensor: CoreSensorFailureSensor", controller)
        self.assertIn("phase: CoreSensorFailurePhase", controller)
        self.assertIn("this.gyroscopeFreshFrameSeen ? 'stale' : 'initial'", controller)

    def test_watchdog_is_generation_guarded_confirmed_and_cancelled(self) -> None:
        controller = read("entry/src/main/ets/model/SensorController.ets")
        watchdog = section(controller, "  private scheduleCoreSensorWatchdog", "  private failCoreSensor(")
        fatal = section(controller, "  private failCoreSensor(", "  private emitStatus")
        stop = section(controller, "  stop(): boolean", "  private subscribeAcceleration")

        self.assertIn("this.isCoreSubscriptionActive(generation)", watchdog)
        self.assertIn("ACCELEROMETER_INITIAL_TIMEOUT_MS", watchdog)
        self.assertIn("ACCELEROMETER_STALE_TIMEOUT_MS", watchdog)
        self.assertIn("ACCELEROMETER_STALL_CONFIRM_MS", watchdog)
        self.assertIn("this.stopCoreSensorWatchdog();", stop)
        self.assertIn("if (!this.running || this.coreFatalReported)", fatal)
        self.assertIn("failedGeneration !== this.subscriptionGeneration", fatal)
        self.assertIn("this.stopCoreSensorWatchdog();", fatal)

    def test_watchdog_uses_monotonic_uptime_but_frames_keep_wall_time(self) -> None:
        controller = read("entry/src/main/ets/model/SensorController.ets")
        emit = section(controller, "  private emitFrame", "  private observeAccelerationContinuity")
        watchdog = section(
            controller,
            "  private checkAccelerometerWatchdog",
            "  private failCoreSensor(",
        )
        monotonic = section(
            controller,
            "  private readMonotonicNowMs",
            "  private scheduleCoreSensorWatchdog",
        )

        self.assertIn("systemDateTime.getUptime(systemDateTime.TimeType.STARTUP)", monotonic)
        self.assertIn("Math.min(wallDeltaMs, CORE_SENSOR_WATCHDOG_INTERVAL_MS * 2)", monotonic)
        self.assertIn("const wallNowMs = Date.now();", emit)
        self.assertIn("const monotonicNowMs = this.readMonotonicNowMs();", emit)
        self.assertIn("timestampMs: wallNowMs", emit)
        self.assertIn("this.observeAccelerationContinuity(sensorTimestamp, monotonicNowMs)", emit)
        self.assertIn("const now = this.readMonotonicNowMs();", watchdog)
        self.assertNotIn("Date.now()", watchdog)
        self.assertIn("coreStartMonotonicMs", controller)
        self.assertIn("lastAccelerationMonotonicMs", controller)
        self.assertIn("gyroscopeMissingSinceMonotonicMs", controller)

    def test_non_finite_sensor_callbacks_are_dropped_before_recording_or_state(self) -> None:
        controller = read("entry/src/main/ets/model/SensorController.ets")
        subscriptions = (
            ("  private subscribeAcceleration", "  private subscribeGyroscope"),
            ("  private subscribeGyroscope", "  private subscribeRotationVector"),
            ("  private subscribeRotationVector", "  private subscribeMagneticField"),
            ("  private subscribeMagneticField", "  private subscribeUncalibratedGyroscope"),
            ("  private subscribeUncalibratedGyroscope", "  private subscribeUncalibratedMagneticField"),
            ("  private subscribeUncalibratedMagneticField", "  private emitSensorSample"),
        )
        for begin, end in subscriptions:
            body = section(controller, begin, end)
            finite_guard = body.index("isFiniteSensorTimestamp(data.timestamp)")
            recorded = body.index("this.emitSensorSample({")
            self.assertLess(finite_guard, recorded)

        acceleration = section(
            controller,
            "  private subscribeAcceleration",
            "  private subscribeGyroscope",
        )
        self.assertLess(
            acceleration.index("isFiniteVector3(data.x, data.y, data.z)"),
            acceleration.index("this.emitFrame("),
        )
        stop = section(controller, "  stop(): boolean", "  private subscribeAcceleration")
        self.assertIn(
            "return coreCleanupSucceeded && auxiliaryCleanupSucceeded;", stop
        )

    def test_optional_gyro_degrades_and_can_recover(self) -> None:
        controller = read("entry/src/main/ets/model/SensorController.ets")
        missing = section(controller, "  private handleMissingGyroscope", "  private clearGyroscopeMissingWindow")
        emit = section(controller, "  private emitFrame", "  private observeAccelerationContinuity")
        self.assertIn("if (this.requireGyroscope)", missing)
        self.assertIn("this.optionalGyroscopeDegraded = true", missing)
        self.assertIn("this.gyroscopeAvailable = false", missing)
        self.assertIn("this.optionalGyroscopeDegraded = false", emit)
        self.assertIn("研究记录恢复完整传感器采集", emit)

    def test_page_turns_fatal_sensor_events_into_terminal_ui_state(self) -> None:
        page = read("entry/src/main/ets/pages/InertialSpeed.ets")
        measurement_start = section(
            page,
            "  private async startMeasurement",
            "  private startLocationForMeasurement",
        )
        handlers = section(
            page,
            "  private handleMeasurementCoreSensorFatal",
            "  private toggleMeasurement",
        )
        research_start = section(
            page,
            "  private startResearchSensors",
            "  private ensureResearchSensorsOrStop",
        )

        self.assertIn("}, true, (error: CoreSensorFatalError)", measurement_start)
        self.assertIn("this.handleMeasurementCoreSensorFatal(error, operationId)", measurement_start)
        self.assertIn("setTimeout(() =>", handlers)
        self.assertIn("this.stopMeasurement(reason)", handlers)
        self.assertIn("测速已中止", handlers)
        self.assertIn("this.researchRecorder.markSystemEvent", handlers)
        self.assertIn("BackgroundState.recordingActive = false", handlers)
        self.assertIn("}, false, (error: CoreSensorFatalError)", research_start)
        self.assertIn("this.handleResearchCoreSensorFatal(error, sensorOperationId)", research_start)


if __name__ == "__main__":
    unittest.main()
