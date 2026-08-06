import {
  CalibrationState,
  EstimatorFrame,
  MotionState,
  RunStats,
  SensorFrame,
  SpeedSample,
  Vector3,
  emptyVector,
  motionStateText
} from './SpeedTypes';

interface AccelWindowFrame {
  forward: number;
  lateral: number;
  gyroMagnitude: number;
  accStep: number;
  gravityDeviation: number;
  elapsedSeconds: number;
}

interface PreCalFrame {
  timestampMs: number;
  acceleration: Vector3;
  gyroMagnitude: number;
  accStep: number;
  deltaSeconds: number;
  elapsedSeconds: number;
  continuous: boolean;
  distanceBeforeM: number;
  integrated: boolean;
}

interface FrameTiming {
  deltaSeconds: number;
  elapsedSeconds: number;
  continuous: boolean;
}

const REFERENCE_SAMPLE_SECONDS = 0.02;
const MAX_CONTINUOUS_SAMPLE_SECONDS = 0.08;
const PRE_CAL_BUFFER_SECONDS = 3.6;
const PRE_CAL_MAX_FRAMES = 800;
const PARKING_WINDOW_SECONDS = 1.5;
const PARKING_WINDOW_MIN_SAMPLES = 30;
const MOTION_WINDOW_MIN_SECONDS = 0.4;
const MOTION_WINDOW_MAX_SECONDS = 1.6;
const MOTION_WINDOW_MAX_FRAMES = 400;
const DEPARTURE_EVIDENCE_SECONDS = 0.06;

export class SpeedEstimator {
  private running: boolean = false;
  private startMs: number = 0;
  private lastTimestampMs: number = 0;
  private lastSensorTimestampNs: number | null = null;
  private velocityMps: number = 0;
  private distanceM: number = 0;
  private maxSpeedKmh: number = 0;
  private filteredAcceleration: Vector3 = emptyVector();
  private gravityEstimate: Vector3 = { x: 0, y: 0, z: 9.80665 };
  private mainAxis: Vector3 = { x: 1, y: 0, z: 0 };
  private mainAxisInitialized: boolean = false;
  private mainAxisLocked: boolean = false;
  private mainAxisSeedMagnitude: number = 0;
  private mainAxisUpdateCount: number = 0;
  private calibrationCount: number = 0;
  private calibrationUntilMs: number = 0;
  private calibrationSum: Vector3 = emptyVector();
  private calibrationSquareSum: number = 0;
  private calibrationGyroSum: number = 0;
  private calibrationGyroMax: number = 0;
  private calibrationMaxStep: number = 0;
  private calibrationSamples: number = 0;
  private calibrationFirstSampleMs: number = 0;
  private calibrationLastSampleMs: number = 0;
  private calibrationRejectedUntilMs: number = 0;
  private lastCalibrationMs: number = 0;
  private motionState: MotionState = MotionState.Idle;
  private confidence: number = 0;
  private pureMode: number = 0;
  private windowFrames: AccelWindowFrame[] = [];
  private preCalBuffer: PreCalFrame[] = [];
  private parkingCalibrationPending: boolean = false;
  private parkingCalibrationRejectedUntilMs: number = 0;
  private parkingCalibrationSuccessUntilMs: number = 0;
  private parkingCalibrationResult: number = 0;
  private parkingCalibrationRequestMs: number = 0;
  private parkingCalibrationPreviousMs: number = 0;
  private parkingCalibrationSnapshot: PreCalFrame[] = [];
  private parkingPostRequestFrames: PreCalFrame[] = [];
  private lastRawAcceleration?: Vector3;
  private lastValidDeltaSeconds: number = REFERENCE_SAMPLE_SECONDS;

  private initialCalibrationDone: boolean = false;

  getVelocityMps(): number {
    return this.velocityMps;
  }

  setPureMode(pure: number): void {
    this.pureMode = pure;
  }

  consumeParkingCalibrationResult(): number {
    const result = this.parkingCalibrationResult;
    this.parkingCalibrationResult = 0;
    return result;
  }

  start(timestampMs: number): RunStats {
    this.running = true;
    this.startMs = timestampMs;
    this.lastTimestampMs = timestampMs;
    this.lastSensorTimestampNs = null;
    this.lastValidDeltaSeconds = REFERENCE_SAMPLE_SECONDS;
    this.velocityMps = 0;
    this.distanceM = 0;
    this.maxSpeedKmh = 0;
    this.filteredAcceleration = emptyVector();
    this.gravityEstimate = { x: 0, y: 0, z: 9.80665 };
    this.mainAxis = { x: 1, y: 0, z: 0 };
    this.mainAxisInitialized = false;
    this.mainAxisLocked = false;
    this.mainAxisSeedMagnitude = 0;
    this.mainAxisUpdateCount = 0;
    this.calibrationCount = 0;
    this.confidence = 0;
    this.windowFrames = [];
    this.preCalBuffer = [];
    this.calibrationRejectedUntilMs = 0;
    this.parkingCalibrationPending = false;
    this.parkingCalibrationRejectedUntilMs = 0;
    this.parkingCalibrationSuccessUntilMs = 0;
    this.parkingCalibrationResult = 0;
    this.parkingCalibrationRequestMs = 0;
    this.parkingCalibrationPreviousMs = 0;
    this.parkingCalibrationSnapshot = [];
    this.parkingPostRequestFrames = [];
    this.lastRawAcceleration = undefined;
    this.initialCalibrationDone = false;
    this.beginCalibration(timestampMs);
    return this.currentStats(timestampMs, '校准中，请保持设备静止');
  }

  stop(timestampMs: number): RunStats {
    const stats = this.currentStats(timestampMs, '已停止');
    this.running = false;
    this.motionState = MotionState.Stopped;
    this.velocityMps = 0;
    this.lastTimestampMs = timestampMs;
    this.lastSensorTimestampNs = null;
    this.lastValidDeltaSeconds = REFERENCE_SAMPLE_SECONDS;
    this.parkingCalibrationPending = false;
    this.parkingCalibrationRejectedUntilMs = 0;
    this.parkingCalibrationSuccessUntilMs = 0;
    this.parkingCalibrationResult = 0;
    this.parkingCalibrationRequestMs = 0;
    this.parkingCalibrationPreviousMs = 0;
    this.parkingCalibrationSnapshot = [];
    this.parkingPostRequestFrames = [];
    this.lastRawAcceleration = undefined;
    this.preCalBuffer = [];
    return {
      currentSpeedKmh: 0,
      maxSpeedKmh: stats.maxSpeedKmh,
      averageSpeedKmh: stats.averageSpeedKmh,
      elapsedMs: stats.elapsedMs,
      calibrationCount: stats.calibrationCount,
      confidence: stats.confidence,
      motionState: MotionState.Stopped,
      statusText: '已停止',
      calibrationRejected: false
    };
  }

  reset(timestampMs: number): RunStats {
    this.running = false;
    this.startMs = timestampMs;
    this.lastTimestampMs = timestampMs;
    this.lastSensorTimestampNs = null;
    this.lastValidDeltaSeconds = REFERENCE_SAMPLE_SECONDS;
    this.velocityMps = 0;
    this.distanceM = 0;
    this.maxSpeedKmh = 0;
    this.confidence = 0;
    this.motionState = MotionState.Idle;
    this.filteredAcceleration = emptyVector();
    this.gravityEstimate = { x: 0, y: 0, z: 9.80665 };
    this.mainAxis = { x: 1, y: 0, z: 0 };
    this.mainAxisInitialized = false;
    this.mainAxisLocked = false;
    this.mainAxisSeedMagnitude = 0;
    this.mainAxisUpdateCount = 0;
    this.calibrationCount = 0;
    this.confidence = 0;
    this.windowFrames = [];
    this.calibrationSamples = 0;
    this.calibrationSum = emptyVector();
    this.calibrationSquareSum = 0;
    this.calibrationGyroSum = 0;
    this.calibrationGyroMax = 0;
    this.calibrationMaxStep = 0;
    this.calibrationFirstSampleMs = 0;
    this.calibrationLastSampleMs = 0;
    this.calibrationRejectedUntilMs = 0;
    this.parkingCalibrationPending = false;
    this.parkingCalibrationRejectedUntilMs = 0;
    this.parkingCalibrationSuccessUntilMs = 0;
    this.parkingCalibrationResult = 0;
    this.parkingCalibrationRequestMs = 0;
    this.parkingCalibrationPreviousMs = 0;
    this.parkingCalibrationSnapshot = [];
    this.parkingPostRequestFrames = [];
    this.lastRawAcceleration = undefined;
    this.preCalBuffer = [];
    this.initialCalibrationDone = false;
    return this.currentStats(timestampMs, '等待开始');
  }

  calibrateAtStop(timestampMs: number): RunStats {
    if (!this.initialCalibrationDone) {
      return this.currentStats(timestampMs, '请等待初始校准完成');
    }
    if (this.parkingCalibrationPending) {
      return this.currentStats(timestampMs, '停车校准中，请保持停稳');
    }
    this.parkingCalibrationPending = true;
    this.parkingCalibrationRejectedUntilMs = 0;
    this.parkingCalibrationSuccessUntilMs = 0;
    this.parkingCalibrationResult = 0;
    this.parkingCalibrationRequestMs = timestampMs;
    this.parkingCalibrationPreviousMs = this.lastCalibrationMs;
    // Freeze the evidence available at click time. Waiting for the 1.5s result must
    // not let high-rate post-click callbacks evict the stationary window.
    this.parkingCalibrationSnapshot = this.preCalBuffer.slice();
    this.parkingPostRequestFrames = [];
    this.beginCalibration(timestampMs);
    // A request is not a successful calibration. Keep confidence decay anchored to
    // the previous success until this attempt is actually accepted.
    this.lastCalibrationMs = this.parkingCalibrationPreviousMs;
    return this.currentStats(timestampMs, '停车校准中，请保持停稳');
  }

  ingest(frame: SensorFrame): EstimatorFrame {
    if (!this.running) {
      return this.makeFrame(frame, this.currentStats(frame.timestampMs, '等待开始'));
    }

    const timing = this.computeFrameTiming(frame);
    const dt = timing.deltaSeconds;
    const rawAcceleration = frame.acceleration;
    if (!timing.continuous) {
      // Integration is deliberately clamped across callback stalls, but short
      // evidence windows must restart instead of spanning an unobserved gap.
      this.preCalBuffer = [];
      this.windowFrames = [];
      this.filteredAcceleration = emptyVector();
      if (!this.initialCalibrationDone && !this.parkingCalibrationPending) {
        // A partially collected initial calibration is not continuous evidence.
        // Restart its timer and statistics without counting a second calibration.
        this.resetCalibrationEvidence(frame.timestampMs);
      }
    }
    const accStep = this.computeAccStep(rawAcceleration, timing.elapsedSeconds, timing.continuous);

    // Initial calibration duration is measured from the first real sensor sample.
    // This keeps calibrationCount at one and also handles a delayed first callback.
    if (!this.initialCalibrationDone && !this.parkingCalibrationPending && this.calibrationSamples === 0) {
      this.calibrationUntilMs = frame.timestampMs + 1500;
      this.lastCalibrationMs = frame.timestampMs;
    }

    const preCalGyro = frame.gyroscope ? magnitude(frame.gyroscope) : 0;
    const preCalFrame: PreCalFrame = {
      timestampMs: frame.timestampMs,
      acceleration: rawAcceleration,
      gyroMagnitude: preCalGyro,
      accStep,
      deltaSeconds: dt,
      elapsedSeconds: timing.elapsedSeconds,
      continuous: timing.continuous,
      distanceBeforeM: this.distanceM,
      integrated: false
    };
    this.preCalBuffer.push(preCalFrame);
    this.trimPreCalBuffer();
    if (this.parkingCalibrationPending) {
      this.parkingPostRequestFrames.push(preCalFrame);
    }

    if (frame.timestampMs <= this.calibrationUntilMs) {
      const calibrationGyroMagnitude = frame.gyroscope ? magnitude(frame.gyroscope) : 0;
      this.collectCalibration(frame.timestampMs, rawAcceleration, calibrationGyroMagnitude, accStep);
      if (!this.parkingCalibrationPending) {
        return {
          sample: this.makeSample(frame.timestampMs, emptyVector()),
          stats: this.currentStats(frame.timestampMs, '校准中，请保持设备静止'),
          calibration: this.currentCalibration()
        };
      }
    }

    this.finishCalibrationIfNeeded(frame.timestampMs);

    const motionAcceleration = subtract(rawAcceleration, this.gravityEstimate);
    const clipped = clipMagnitude(motionAcceleration, 3.5);
    const filtered = this.lowPass(clipped, 0.22, dt);
    this.filteredAcceleration = filtered;

    const gyroMagnitude = frame.gyroscope ? magnitude(frame.gyroscope) : 0;
    this.updateMainAxisLock(frame.timestampMs);

    // Classify the current sample before allowing it to teach the vehicle axis.
    // The vibration evidence (acceleration step, gyro and gravity deviation) is
    // axis-independent, while the provisional projection lets the existing curve
    // and low-confidence guards apply to this sample instead of one frame late.
    let forwardAcceleration = dot(filtered, this.mainAxis);
    let lateralVector = subtract(filtered, scale(this.mainAxis, forwardAcceleration));
    let lateralAcceleration = magnitude(lateralVector);
    this.pushWindowFrame(
      forwardAcceleration,
      lateralAcceleration,
      gyroMagnitude,
      accStep,
      Math.abs(magnitude(rawAcceleration) - 9.80665),
      timing.elapsedSeconds,
      timing.continuous
    );

    const axisCandidateState = this.detectMotionState(forwardAcceleration, gyroMagnitude, frame.timestampMs);
    const axisAllowed = this.shouldUpdateMainAxis(filtered, gyroMagnitude, axisCandidateState);
    if (axisAllowed) {
      this.updateMainAxis(filtered, dt);
      forwardAcceleration = dot(filtered, this.mainAxis);
      lateralVector = subtract(filtered, scale(this.mainAxis, forwardAcceleration));
      lateralAcceleration = magnitude(lateralVector);
      this.replaceLatestWindowProjection(forwardAcceleration, lateralAcceleration);
    }

    const state = this.detectMotionState(forwardAcceleration, gyroMagnitude, frame.timestampMs);
    const effectiveAcceleration = this.effectiveForwardAcceleration(forwardAcceleration, state);
    const previousVelocityMps = this.velocityMps;
    this.velocityMps = Math.max(0, this.velocityMps + effectiveAcceleration * dt);
    this.distanceM += ((previousVelocityMps + this.velocityMps) / 2) * dt;

    const speedKmh = this.velocityMps * 3.6;
    this.maxSpeedKmh = Math.max(this.maxSpeedKmh, speedKmh);
    this.confidence = this.computeConfidence(state, gyroMagnitude, frame.timestampMs);
    this.motionState = state;
    preCalFrame.integrated = true;

    const sample = this.makeSample(frame.timestampMs, filtered);

    return {
      sample,
      stats: this.currentStats(frame.timestampMs, this.statusFor(state, frame.timestampMs)),
      calibration: this.currentCalibration()
    };
  }

  private beginCalibration(timestampMs: number): void {
    this.resetCalibrationEvidence(timestampMs);
    this.calibrationCount += 1;
  }

  private resetCalibrationEvidence(timestampMs: number): void {
    this.calibrationUntilMs = timestampMs + 1500;
    this.calibrationSum = emptyVector();
    this.calibrationSquareSum = 0;
    this.calibrationGyroSum = 0;
    this.calibrationGyroMax = 0;
    this.calibrationMaxStep = 0;
    this.calibrationSamples = 0;
    this.calibrationFirstSampleMs = 0;
    this.calibrationLastSampleMs = 0;
    this.lastCalibrationMs = timestampMs;
    this.motionState = MotionState.Calibrating;
  }

  private collectCalibration(timestampMs: number, acceleration: Vector3, gyroMagnitude: number, accStep: number): void {
    if (this.calibrationSamples === 0) {
      this.calibrationFirstSampleMs = timestampMs;
    }
    this.calibrationLastSampleMs = timestampMs;
    this.calibrationMaxStep = Math.max(this.calibrationMaxStep, accStep);
    this.calibrationSum = add(this.calibrationSum, acceleration);
    this.calibrationSquareSum += dot(acceleration, acceleration);
    this.calibrationGyroSum += gyroMagnitude;
    this.calibrationGyroMax = Math.max(this.calibrationGyroMax, gyroMagnitude);
    this.calibrationSamples += 1;
  }

  private trimPreCalBuffer(): void {
    let durationSeconds = 0;
    for (let i = 0; i < this.preCalBuffer.length; i++) {
      durationSeconds += this.preCalBuffer[i].elapsedSeconds;
    }
    while (this.preCalBuffer.length > 1 && durationSeconds > PRE_CAL_BUFFER_SECONDS) {
      durationSeconds -= this.preCalBuffer[0].elapsedSeconds;
      this.preCalBuffer.shift();
    }
    while (this.preCalBuffer.length > PRE_CAL_MAX_FRAMES) {
      this.preCalBuffer.shift();
    }
  }

  private finishCalibrationIfNeeded(timestampMs: number): void {
    if ((this.parkingCalibrationPending || this.calibrationSamples > 0) && timestampMs > this.calibrationUntilMs) {
      const wasParkingCalibration = this.parkingCalibrationPending;
      let stableResult = false;
      let gravityCandidate: Vector3 = emptyVector();
      let parkingWindowStart = -1;
      let parkingWindowEnd = -1;

      const parkingFrames = wasParkingCalibration
        ? this.parkingCalibrationSnapshot.concat(this.parkingPostRequestFrames)
        : this.preCalBuffer;
      const historicalFrameCount = wasParkingCalibration
        ? this.parkingCalibrationSnapshot.length
        : Math.max(0, this.preCalBuffer.length - 1);
      let eligibleParkingFrameCount = 0;
      for (let n = 0; n < historicalFrameCount; n++) {
        if (parkingFrames[n].timestampMs <= this.parkingCalibrationRequestMs) {
          eligibleParkingFrameCount = n + 1;
        } else {
          break;
        }
      }
      if (wasParkingCalibration && eligibleParkingFrameCount >= PARKING_WINDOW_MIN_SAMPLES) {
        let bestStart = -1;
        let bestEnd = -1;
        let bestRms = Infinity;
        for (let i = 0; i < eligibleParkingFrameCount; i++) {
          let windowDurationSeconds = 0;
          let windowEnd = i;
          while (windowEnd < eligibleParkingFrameCount && windowDurationSeconds < PARKING_WINDOW_SECONDS) {
            windowDurationSeconds += parkingFrames[windowEnd].elapsedSeconds;
            windowEnd += 1;
          }
          if (windowDurationSeconds < PARKING_WINDOW_SECONDS) {
            break;
          }
          const endIndex = windowEnd - 1;
          const windowFrameCount = endIndex - i + 1;
          if (windowFrameCount < PARKING_WINDOW_MIN_SAMPLES) {
            continue;
          }
          const windowEndTimestampMs = parkingFrames[endIndex].timestampMs;
          const windowAgeMs = this.parkingCalibrationRequestMs - windowEndTimestampMs;
          if (windowAgeMs < 0 || windowAgeMs > 300) {
            continue;
          }
          let sum = emptyVector();
          let sqSum = 0;
          for (let j = i; j <= endIndex; j++) {
            const f = parkingFrames[j];
            sum = add(sum, f.acceleration);
            sqSum += dot(f.acceleration, f.acceleration);
          }
          const mean = scale(sum, 1 / windowFrameCount);
          const meanSq = sqSum / windowFrameCount;
          const meanDot = dot(mean, mean);
          const rms = Math.sqrt(Math.max(0, meanSq - meanDot));
          if (rms < bestRms) {
            bestRms = rms;
            bestStart = i;
            bestEnd = endIndex;
          }
        }

        if (bestStart >= 0 && bestEnd >= bestStart) {
          const windowFrameCount = bestEnd - bestStart + 1;
          let calSum = emptyVector();
          let calSqSum = 0;
          let calGyroSum = 0;
          let calGyroMax = 0;
          let calMaxStep = 0;
          for (let j = bestStart; j <= bestEnd; j++) {
            const f = parkingFrames[j];
            calSum = add(calSum, f.acceleration);
            calSqSum += dot(f.acceleration, f.acceleration);
            calGyroSum += f.gyroMagnitude;
            calGyroMax = Math.max(calGyroMax, f.gyroMagnitude);
            if (j > bestStart) {
              calMaxStep = Math.max(calMaxStep, f.accStep);
            }
          }

          gravityCandidate = scale(calSum, 1 / windowFrameCount);
          const meanSquare = calSqSum / windowFrameCount;
          const gravitySquare = dot(gravityCandidate, gravityCandidate);
          const rmsDeviation = Math.sqrt(Math.max(0, meanSquare - gravitySquare));
          const gravityMagnitude = magnitude(gravityCandidate);
          const rawGravityError = Math.abs(gravityMagnitude - 9.80665);
          const gyroAverage = calGyroSum / windowFrameCount;
          const motionDuringCalibration = gyroAverage > 0.08 || calGyroMax > 0.25 || calMaxStep > 0.65;
          stableResult = rmsDeviation < 0.25 && rawGravityError < 0.25 && !motionDuringCalibration;
          parkingWindowStart = bestStart;
          parkingWindowEnd = bestEnd;
        }
      } else if (!wasParkingCalibration) {
        const sampleCoverageMs = this.calibrationLastSampleMs - this.calibrationFirstSampleMs;
        const hasEnoughSamples = this.calibrationSamples >= 30 && sampleCoverageMs >= 1000;
        if (hasEnoughSamples) {
          gravityCandidate = scale(this.calibrationSum, 1 / this.calibrationSamples);
          const meanSquare = this.calibrationSquareSum / this.calibrationSamples;
          const gravitySquare = dot(gravityCandidate, gravityCandidate);
          const rmsDeviation = Math.sqrt(Math.max(0, meanSquare - gravitySquare));
          const gravityMagnitude = magnitude(gravityCandidate);
          const rawGravityError = Math.abs(gravityMagnitude - 9.80665);
          const gyroAverage = this.calibrationGyroSum / this.calibrationSamples;
          const motionDuringCalibration = gyroAverage > 0.08 || this.calibrationGyroMax > 0.25 || this.calibrationMaxStep > 0.65;
          stableResult = rmsDeviation < 0.25 && rawGravityError < 0.25 && !motionDuringCalibration;
        }
      }

      if (stableResult) {
        this.gravityEstimate = gravityCandidate;
        this.calibrationRejectedUntilMs = 0;
        if (wasParkingCalibration) {
          this.lastCalibrationMs = this.parkingCalibrationRequestMs;
          this.applyParkingReplay(parkingFrames, parkingWindowStart, parkingWindowEnd);
          this.parkingCalibrationSuccessUntilMs = timestampMs + 5000;
          this.parkingCalibrationResult = 1;
        } else {
          this.filteredAcceleration = emptyVector();
        }
      } else {
        this.calibrationRejectedUntilMs = timestampMs + 10000;
        if (wasParkingCalibration) {
          this.parkingCalibrationRejectedUntilMs = timestampMs + 5000;
          this.parkingCalibrationResult = -1;
        } else {
          this.filteredAcceleration = emptyVector();
        }
      }
      this.parkingCalibrationPending = false;
      this.parkingCalibrationRequestMs = 0;
      this.parkingCalibrationPreviousMs = 0;
      this.parkingCalibrationSnapshot = [];
      this.parkingPostRequestFrames = [];
      this.initialCalibrationDone = true;
      this.calibrationSamples = 0;
      this.calibrationSum = emptyVector();
      this.calibrationSquareSum = 0;
      this.calibrationGyroSum = 0;
      this.calibrationGyroMax = 0;
      this.calibrationMaxStep = 0;
      this.calibrationFirstSampleMs = 0;
      this.calibrationLastSampleMs = 0;
    }
  }

  private lowPass(acceleration: Vector3, referenceAlpha: number, deltaSeconds: number): Vector3 {
    const alpha = alphaForDelta(referenceAlpha, deltaSeconds);
    return {
      x: this.filteredAcceleration.x + alpha * (acceleration.x - this.filteredAcceleration.x),
      y: this.filteredAcceleration.y + alpha * (acceleration.y - this.filteredAcceleration.y),
      z: this.filteredAcceleration.z + alpha * (acceleration.z - this.filteredAcceleration.z)
    };
  }

  private computeAccStep(rawAcceleration: Vector3, elapsedSeconds: number, continuous: boolean): number {
    if (!continuous || !this.lastRawAcceleration) {
      this.lastRawAcceleration = rawAcceleration;
      return 0;
    }
    const rawStep = magnitude(subtract(rawAcceleration, this.lastRawAcceleration));
    this.lastRawAcceleration = rawAcceleration;
    return rawStep * (REFERENCE_SAMPLE_SECONDS / Math.max(elapsedSeconds, 0.005));
  }

  private computeFrameTiming(frame: SensorFrame): FrameTiming {
    const timestampMs = frame.timestampMs;
    let rawDt = 0;
    if (frame.sensorTimestamp !== null && frame.sensorTimestamp !== undefined) {
      if (this.lastSensorTimestampNs !== null && frame.sensorTimestamp > this.lastSensorTimestampNs) {
        rawDt = (frame.sensorTimestamp - this.lastSensorTimestampNs) / 1000000000;
      } else {
        rawDt = (timestampMs - this.lastTimestampMs) / 1000;
      }
      this.lastSensorTimestampNs = frame.sensorTimestamp;
    } else {
      rawDt = (timestampMs - this.lastTimestampMs) / 1000;
      this.lastSensorTimestampNs = null;
    }
    this.lastTimestampMs = timestampMs;
    if (!Number.isFinite(rawDt) || rawDt <= 0) {
      return {
        deltaSeconds: this.lastValidDeltaSeconds,
        elapsedSeconds: this.lastValidDeltaSeconds,
        continuous: true
      };
    }
    const deltaSeconds = clamp(rawDt, 0.005, 0.08);
    const continuous = rawDt <= MAX_CONTINUOUS_SAMPLE_SECONDS;
    const elapsedSeconds = continuous ? rawDt : this.lastValidDeltaSeconds;
    if (continuous) {
      this.lastValidDeltaSeconds = deltaSeconds;
    }
    return { deltaSeconds, elapsedSeconds, continuous };
  }

  private updateMainAxisLock(timestampMs: number): void {
    if (!this.mainAxisInitialized) {
      return;
    }
    if (this.mainAxisLocked && this.axisLockWindowUnstable()) {
      this.mainAxisLocked = false;
    }
    if (this.mainAxisLocked) {
      return;
    }
    if (!this.axisLockWindowStable()) {
      return;
    }
    if (this.velocityMps > 5 || timestampMs - this.startMs > 30000 || this.mainAxisUpdateCount >= 60) {
      this.mainAxisLocked = true;
    }
  }

  private shouldUpdateMainAxis(filtered: Vector3, gyroMagnitude: number, candidateState: MotionState): boolean {
    const accelerationMagnitude = magnitude(filtered);
    if (candidateState === MotionState.Curve ||
      candidateState === MotionState.LowConfidence ||
      candidateState === MotionState.StrongVibration ||
      candidateState === MotionState.ConductionVibration) {
      return false;
    }
    if (!this.mainAxisInitialized) {
      return accelerationMagnitude > 0.18 && gyroMagnitude < 0.18;
    }

    const lateralAverage = averageLateral(this.windowFrames);
    const gyroAverage = averageGyro(this.windowFrames);

    if (this.mainAxisLocked) {
      const lockedStraight = lateralAverage < 0.08 && gyroMagnitude < 0.10 && gyroAverage < 0.08;
      return accelerationMagnitude > 0.25 && lockedStraight && this.velocityMps < 5;
    }

    if (this.velocityMps > 8) {
      return false;
    }
    const axisUpdateLikelyStraight = lateralAverage < 0.18 && gyroMagnitude < 0.16 && gyroAverage < 0.14;
    const speedAdjustedThreshold = this.velocityMps > 3 ? 0.16 : 0.10;
    return accelerationMagnitude > speedAdjustedThreshold && axisUpdateLikelyStraight;
  }

  private updateMainAxis(filtered: Vector3, deltaSeconds: number): void {
    const candidateMagnitude = magnitude(filtered);
    const updateWeight = deltaSeconds / REFERENCE_SAMPLE_SECONDS;
    let candidate = normalize(filtered);
    if (!this.mainAxisInitialized) {
      this.mainAxis = candidate;
      this.mainAxisInitialized = true;
      this.mainAxisSeedMagnitude = candidateMagnitude;
      this.mainAxisUpdateCount = updateWeight;
      this.orthogonalizeAxis();
      return;
    }

    if (this.mainAxisLocked) {
      if (dot(candidate, this.mainAxis) < 0) {
        candidate = scale(candidate, -1);
      }
      this.mainAxis = normalize(mix(this.mainAxis, candidate, alphaForDelta(0.003, deltaSeconds)));
      this.orthogonalizeAxis();
      return;
    }

    const alignment = dot(candidate, this.mainAxis);
    if (Math.abs(alignment) < 0.35 && this.velocityMps < 2 && candidateMagnitude > Math.max(0.18, this.mainAxisSeedMagnitude * 1.8)) {
      this.mainAxis = candidate;
      this.mainAxisSeedMagnitude = candidateMagnitude;
      this.mainAxisUpdateCount += updateWeight;
      this.orthogonalizeAxis();
      return;
    }

    if (alignment < 0) {
      candidate = scale(candidate, -1);
    }
    this.mainAxis = normalize(mix(this.mainAxis, candidate, alphaForDelta(0.025, deltaSeconds)));
    this.mainAxisSeedMagnitude = Math.max(this.mainAxisSeedMagnitude, candidateMagnitude);
    this.mainAxisUpdateCount += updateWeight;
    this.orthogonalizeAxis();
  }

  private orthogonalizeAxis(): void {
    const gravDir = normalize(this.gravityEstimate);
    const proj = dot(this.mainAxis, gravDir);
    if (Math.abs(proj) > 0.15) {
      this.mainAxis = normalize(subtract(this.mainAxis, scale(gravDir, proj)));
    }
  }

  private axisLockWindowStable(): boolean {
    if (windowDurationSeconds(this.windowFrames) < MOTION_WINDOW_MIN_SECONDS) {
      return false;
    }
    return averageAccStep(this.windowFrames) < 0.35 &&
      averageGravityDeviation(this.windowFrames) < 0.45 &&
      averageGyro(this.windowFrames) < 0.12 &&
      varianceForward(this.windowFrames) < 0.08;
  }

  private axisLockWindowUnstable(): boolean {
    if (windowDurationSeconds(this.windowFrames) < MOTION_WINDOW_MIN_SECONDS) {
      return false;
    }
    return averageAccStep(this.windowFrames) > 0.85 || averageGravityDeviation(this.windowFrames) > 1.2;
  }

  private pushWindowFrame(
    forward: number,
    lateral: number,
    gyroMagnitude: number,
    accStep: number,
    gravityDeviation: number,
    elapsedSeconds: number,
    continuous: boolean
  ): void {
    if (!continuous) {
      this.windowFrames = [];
    }
    this.windowFrames.push({
      forward,
      lateral,
      gyroMagnitude,
      accStep,
      gravityDeviation,
      elapsedSeconds
    });
    while (
      this.windowFrames.length > 1 &&
      (this.windowFrames.length > MOTION_WINDOW_MAX_FRAMES ||
        windowDurationSeconds(this.windowFrames) > MOTION_WINDOW_MAX_SECONDS)
    ) {
      this.windowFrames.shift();
    }
  }

  private replaceLatestWindowProjection(forward: number, lateral: number): void {
    if (this.windowFrames.length < 1) {
      return;
    }
    const latest = this.windowFrames[this.windowFrames.length - 1];
    latest.forward = forward;
    latest.lateral = lateral;
  }

  private detectMotionState(forward: number, gyroMagnitude: number, timestampMs: number): MotionState {
    const lateralAverage = averageLateral(this.windowFrames);
    const gyroAverage = averageGyro(this.windowFrames);
    const gyroVarianceValue = varianceGyro(this.windowFrames);
    const forwardVariance = varianceForward(this.windowFrames);
    const vibrationAverage = averageAccStep(this.windowFrames);
    const gravityDeviationAverage = averageGravityDeviation(this.windowFrames);
    const longSinceCalibration = timestampMs - this.lastCalibrationMs > 360000;
    const conductionVibration = vibrationAverage > 0.85 && gyroAverage <= 0.06;
    const strongVibration = (vibrationAverage > 0.85 && gyroAverage > 0.06) || gravityDeviationAverage > 1.2;
    const curveLikely =
      (lateralAverage > 0.18 && gyroAverage > 0.045
        && Math.abs(forward) < lateralAverage * 1.15) ||
      (gyroAverage > 0.09 && gyroVarianceValue > 0.0018
        && lateralAverage > 0.10);

    if (curveLikely) {
      return MotionState.Curve;
    }
    if (conductionVibration) {
      return MotionState.ConductionVibration;
    }
    if (strongVibration) {
      return MotionState.StrongVibration;
    }
    if (longSinceCalibration || forwardVariance > 0.55 || gyroMagnitude > 1.8) {
      return MotionState.LowConfidence;
    }
    if (Math.abs(forward) < 0.035 && this.velocityMps < 0.25) {
      return MotionState.Idle;
    }
    if (forward > 0.055) {
      return MotionState.StraightAcceleration;
    }
    if (forward < -0.055) {
      return MotionState.Braking;
    }
    return MotionState.Cruise;
  }

  private effectiveForwardAcceleration(forward: number, state: MotionState): number {
    if (Math.abs(forward) < 0.025) {
      return 0;
    }
    if (state === MotionState.Curve) {
      return forward * 0.35;
    }
    if (state === MotionState.StrongVibration) {
      return forward * 0.18;
    }
    if (state === MotionState.ConductionVibration) {
      return forward * 0.45;
    }
    if (state === MotionState.LowConfidence) {
      return forward * 0.55;
    }
    return forward;
  }

  private applyParkingReplay(frames: PreCalFrame[], windowStart: number, windowEnd: number): void {
    const anchorDistanceM = frames[windowStart].distanceBeforeM;
    this.velocityMps = 0;
    this.distanceM = anchorDistanceM;
    this.filteredAcceleration = emptyVector();
    this.windowFrames = [];
    this.motionState = MotionState.Idle;

    // Keep the learned vehicle axis. Only make it tangent to the newly calibrated
    // gravity vector; clearing the axis here would discard an immediate departure.
    if (this.mainAxisInitialized) {
      this.orthogonalizeAxis();
    }

    // Warm the low-pass filter and short motion window from the stationary anchor.
    // The anchor itself is known to be at zero speed and therefore is not integrated.
    for (let i = windowStart; i <= windowEnd; i++) {
      const frame = frames[i];
      frame.distanceBeforeM = anchorDistanceM;
      if (!frame.continuous) {
        this.filteredAcceleration = emptyVector();
      }
      const motionAcceleration = subtract(frame.acceleration, this.gravityEstimate);
      const clipped = clipMagnitude(motionAcceleration, 3.5);
      const filtered = this.lowPass(clipped, 0.22, frame.deltaSeconds);
      this.filteredAcceleration = filtered;
      const forward = dot(filtered, this.mainAxis);
      const lateralVector = subtract(filtered, scale(this.mainAxis, forward));
      this.pushWindowFrame(
        forward,
        magnitude(lateralVector),
        frame.gyroMagnitude,
        frame.accStep,
        Math.abs(magnitude(frame.acceleration) - 9.80665),
        frame.elapsedSeconds,
        frame.continuous
      );
    }

    let positiveAccelerationSeconds = 0;
    let positiveAccelerationSamples = 0;
    let departureDetected = false;
    // The final buffer element is the current, not-yet-integrated sensor frame.
    // Leave it to the normal ingest path so every frame contributes exactly once.
    const replayFrameLimit = Math.max(windowEnd + 1, frames.length - 1);
    for (let j = windowEnd + 1; j < replayFrameLimit; j++) {
      const frame = frames[j];
      frame.distanceBeforeM = this.distanceM;
      if (!frame.continuous) {
        // Do not let a pre-gap low-pass tail turn post-gap stationary samples
        // into false sustained departure evidence.
        this.filteredAcceleration = emptyVector();
      }
      const motionAcceleration = subtract(frame.acceleration, this.gravityEstimate);
      const clipped = clipMagnitude(motionAcceleration, 3.5);
      const rawForward = dot(clipped, this.mainAxis);
      const filtered = this.lowPass(clipped, 0.22, frame.deltaSeconds);
      this.filteredAcceleration = filtered;
      const forward = dot(filtered, this.mainAxis);
      const lateralVector = subtract(filtered, scale(this.mainAxis, forward));
      const lateral = magnitude(lateralVector);
      this.pushWindowFrame(
        forward,
        lateral,
        frame.gyroMagnitude,
        frame.accStep,
        Math.abs(magnitude(frame.acceleration) - 9.80665),
        frame.elapsedSeconds,
        frame.continuous
      );
      const state = this.detectMotionState(forward, frame.gyroMagnitude, frame.timestampMs);
      this.motionState = state;

      if (!frame.integrated) {
        continue;
      }

      const effectiveAcceleration = this.effectiveForwardAcceleration(forward, state);
      const previousVelocityMps = this.velocityMps;
      this.velocityMps = Math.max(0, this.velocityMps + effectiveAcceleration * frame.deltaSeconds);
      this.distanceM += ((previousVelocityMps + this.velocityMps) / 2) * frame.deltaSeconds;

      if (!frame.continuous) {
        positiveAccelerationSeconds = 0;
        positiveAccelerationSamples = 0;
      }
      if (forward > 0.055 && rawForward > 0.055 && frame.gyroMagnitude < 0.25) {
        positiveAccelerationSeconds += frame.elapsedSeconds;
        positiveAccelerationSamples += 1;
      } else {
        positiveAccelerationSeconds = 0;
        positiveAccelerationSamples = 0;
      }
      if (positiveAccelerationSeconds >= DEPARTURE_EVIDENCE_SECONDS && positiveAccelerationSamples >= 3) {
        departureDetected = true;
      }
    }

    // Non-negative live integration rectifies stationary noise into a small positive
    // drift. A replay with no sustained departure evidence is the calibrated stop.
    if (!departureDetected) {
      this.velocityMps = 0;
      this.distanceM = anchorDistanceM;
      this.motionState = MotionState.Idle;
      for (let k = windowEnd + 1; k < replayFrameLimit; k++) {
        frames[k].distanceBeforeM = anchorDistanceM;
      }
    }
    if (frames.length > 0) {
      frames[frames.length - 1].distanceBeforeM = this.distanceM;
    }
  }

  private computeConfidence(state: MotionState, gyroMagnitude: number, timestampMs: number): number {
    let value = 1.0;
    const sinceCalibration = timestampMs - this.lastCalibrationMs;

    let decayRate = this.pureMode > 0 ? (0.85 / 120000) : (0.85 / 180000);
    if (state === MotionState.Curve) {
      decayRate *= 3.0;
    }
    if (state === MotionState.StraightAcceleration) {
      decayRate *= 2.0;
    }
    if (state === MotionState.LowConfidence) {
      decayRate *= 2.5;
    }
    if (state === MotionState.StrongVibration) {
      decayRate *= 4.0;
    }
    if (state === MotionState.ConductionVibration) {
      decayRate *= 1.5;
    }
    value -= clamp(sinceCalibration * decayRate, 0, 0.85);
    value -= clamp(gyroMagnitude / 3, 0, 0.2);
    return clamp(value, 0.05, 0.95);
  }

  private makeSample(timestampMs: number, filtered: Vector3): SpeedSample {
    const speedKmh = this.velocityMps * 3.6;
    return {
      timestampMs,
      filteredAcceleration: filtered,
      speedKmh
    };
  }

  private currentStats(timestampMs: number, statusText: string): RunStats {
    const elapsedMs = this.running ? Math.max(0, timestampMs - this.startMs) : 0;
    const averageSpeedKmh = elapsedMs > 0 ? (this.distanceM / (elapsedMs / 1000)) * 3.6 : 0;
    return {
      currentSpeedKmh: this.velocityMps * 3.6,
      maxSpeedKmh: this.maxSpeedKmh,
      averageSpeedKmh,
      elapsedMs,
      calibrationCount: this.calibrationCount,
      confidence: this.confidence,
      motionState: this.motionState,
      statusText,
      calibrationRejected: timestampMs < this.calibrationRejectedUntilMs || timestampMs < this.parkingCalibrationRejectedUntilMs
    };
  }

  private currentCalibration(): CalibrationState {
    return {
      gravity: this.gravityEstimate,
      mainAxis: this.mainAxis
    };
  }

  private makeFrame(frame: SensorFrame, stats: RunStats): EstimatorFrame {
    const sample = this.makeSample(
      frame.timestampMs,
      this.filteredAcceleration
    );
    return {
      sample,
      stats,
      calibration: this.currentCalibration()
    };
  }

  private statusFor(state: MotionState, timestampMs: number): string {
    if (this.parkingCalibrationPending && timestampMs <= this.calibrationUntilMs) {
      return '停车校准中，请保持停稳';
    }
    if (!this.parkingCalibrationPending && timestampMs <= this.calibrationUntilMs) {
      return '校准中，请保持设备静止';
    }
    if (timestampMs < this.parkingCalibrationSuccessUntilMs) {
      return '停车校准成功，速度已校正';
    }
    if (timestampMs < this.parkingCalibrationRejectedUntilMs) {
      return '未停稳，未归零';
    }
    if (timestampMs < this.calibrationRejectedUntilMs) {
      return '校准不稳，已跳过偏置';
    }
    if (state === MotionState.Curve) {
      return '弯道中，速度可能漂移';
    }
    if (state === MotionState.StrongVibration) {
      return '强振动，不适合惯性测速';
    }
    if (state === MotionState.ConductionVibration) {
      return '传导振动，信号有噪声';
    }
    if (state === MotionState.LowConfidence) {
      return '估算可能漂移，请停车校准';
    }
    if (state === MotionState.Calibrating) {
      return '校准中';
    }
    return motionStateText(state);
  }
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function alphaForDelta(referenceAlpha: number, deltaSeconds: number): number {
  const referenceRemainder = clamp(1 - referenceAlpha, 0, 1);
  return 1 - Math.pow(referenceRemainder, deltaSeconds / REFERENCE_SAMPLE_SECONDS);
}

function windowDurationSeconds(frames: AccelWindowFrame[]): number {
  let durationSeconds = 0;
  for (let i = 0; i < frames.length; i++) {
    durationSeconds += frames[i].elapsedSeconds;
  }
  return durationSeconds;
}

function add(a: Vector3, b: Vector3): Vector3 {
  return { x: a.x + b.x, y: a.y + b.y, z: a.z + b.z };
}

function subtract(a: Vector3, b: Vector3): Vector3 {
  return { x: a.x - b.x, y: a.y - b.y, z: a.z - b.z };
}

function scale(a: Vector3, factor: number): Vector3 {
  return { x: a.x * factor, y: a.y * factor, z: a.z * factor };
}

function dot(a: Vector3, b: Vector3): number {
  return a.x * b.x + a.y * b.y + a.z * b.z;
}

function magnitude(a: Vector3): number {
  return Math.sqrt(a.x * a.x + a.y * a.y + a.z * a.z);
}

function normalize(a: Vector3): Vector3 {
  const value = magnitude(a);
  if (value < 0.0001) {
    return { x: 1, y: 0, z: 0 };
  }
  return scale(a, 1 / value);
}

function mix(a: Vector3, b: Vector3, alpha: number): Vector3 {
  return {
    x: a.x + alpha * (b.x - a.x),
    y: a.y + alpha * (b.y - a.y),
    z: a.z + alpha * (b.z - a.z)
  };
}

function clipMagnitude(a: Vector3, maxMagnitude: number): Vector3 {
  const value = magnitude(a);
  if (value <= maxMagnitude || value < 0.0001) {
    return a;
  }
  return scale(a, maxMagnitude / value);
}

function averageLateral(frames: AccelWindowFrame[]): number {
  if (frames.length === 0) {
    return 0;
  }
  let sum = 0;
  for (let i = 0; i < frames.length; i++) {
    sum += frames[i].lateral;
  }
  return sum / frames.length;
}

function averageGyro(frames: AccelWindowFrame[]): number {
  if (frames.length === 0) {
    return 0;
  }
  let sum = 0;
  for (let i = 0; i < frames.length; i++) {
    sum += frames[i].gyroMagnitude;
  }
  return sum / frames.length;
}

function averageAccStep(frames: AccelWindowFrame[]): number {
  if (frames.length === 0) {
    return 0;
  }
  let sum = 0;
  for (let i = 0; i < frames.length; i++) {
    sum += frames[i].accStep;
  }
  return sum / frames.length;
}

function averageGravityDeviation(frames: AccelWindowFrame[]): number {
  if (frames.length === 0) {
    return 0;
  }
  let sum = 0;
  for (let i = 0; i < frames.length; i++) {
    sum += frames[i].gravityDeviation;
  }
  return sum / frames.length;
}

function varianceGyro(frames: AccelWindowFrame[]): number {
  if (frames.length < 2) {
    return 0;
  }
  let sum = 0;
  for (let i = 0; i < frames.length; i++) {
    sum += frames[i].gyroMagnitude;
  }
  const average = sum / frames.length;
  let variance = 0;
  for (let j = 0; j < frames.length; j++) {
    const diff = frames[j].gyroMagnitude - average;
    variance += diff * diff;
  }
  return variance / frames.length;
}

function varianceForward(frames: AccelWindowFrame[]): number {
  if (frames.length < 2) {
    return 0;
  }
  let sum = 0;
  for (let i = 0; i < frames.length; i++) {
    sum += frames[i].forward;
  }
  const average = sum / frames.length;
  let variance = 0;
  for (let j = 0; j < frames.length; j++) {
    const diff = frames[j].forward - average;
    variance += diff * diff;
  }
  return variance / frames.length;
}
