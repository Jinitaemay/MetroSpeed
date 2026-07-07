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
  timestampMs: number;
  forward: number;
  lateral: number;
  gyroMagnitude: number;
  accStep: number;
  gravityDeviation: number;
}

interface PreCalFrame {
  acceleration: Vector3;
  gyroMagnitude: number;
  velocityMps: number;
}

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
  private mainAxisLastUpdateMs: number = 0;
  private calibrationCount: number = 0;
  private calibrationUntilMs: number = 0;
  private calibrationSum: Vector3 = emptyVector();
  private calibrationSquareSum: number = 0;
  private calibrationGyroSum: number = 0;
  private calibrationGyroMax: number = 0;
  private calibrationMaxStep: number = 0;
  private calibrationLastAcceleration?: Vector3;
  private calibrationSamples: number = 0;
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
  private lastRawAcceleration?: Vector3;

  private initialCalibrationDone: boolean = false;

  getVelocityMps(): number {
    return this.velocityMps;
  }

  getMotionState(): MotionState {
    return this.motionState;
  }

  setPureMode(pure: number): void {
    this.pureMode = pure;
  }

  start(timestampMs: number): RunStats {
    this.running = true;
    this.startMs = timestampMs;
    this.lastTimestampMs = timestampMs;
    this.lastSensorTimestampNs = null;
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
    this.mainAxisLastUpdateMs = 0;
    this.calibrationCount = 0;
    this.confidence = 0;
    this.windowFrames = [];
    this.preCalBuffer = [];
    this.calibrationRejectedUntilMs = 0;
    this.parkingCalibrationPending = false;
    this.parkingCalibrationRejectedUntilMs = 0;
    this.parkingCalibrationSuccessUntilMs = 0;
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
    this.parkingCalibrationPending = false;
    this.parkingCalibrationRejectedUntilMs = 0;
    this.parkingCalibrationSuccessUntilMs = 0;
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
    this.mainAxisLastUpdateMs = 0;
    this.calibrationCount = 0;
    this.confidence = 0;
    this.windowFrames = [];
    this.calibrationSamples = 0;
    this.calibrationSum = emptyVector();
    this.calibrationSquareSum = 0;
    this.calibrationGyroSum = 0;
    this.calibrationGyroMax = 0;
    this.calibrationMaxStep = 0;
    this.calibrationLastAcceleration = undefined;
    this.calibrationRejectedUntilMs = 0;
    this.parkingCalibrationPending = false;
    this.parkingCalibrationRejectedUntilMs = 0;
    this.parkingCalibrationSuccessUntilMs = 0;
    this.lastRawAcceleration = undefined;
    this.preCalBuffer = [];
    this.initialCalibrationDone = false;
    return this.currentStats(timestampMs, '等待开始');
  }

  calibrateAtStop(timestampMs: number): RunStats {
    if (!this.initialCalibrationDone) {
      return this.currentStats(timestampMs, '请等待初始校准完成');
    }
    this.lastTimestampMs = timestampMs;
    this.lastSensorTimestampNs = null;
    this.parkingCalibrationPending = true;
    this.parkingCalibrationRejectedUntilMs = 0;
    this.parkingCalibrationSuccessUntilMs = 0;
    this.beginCalibration(timestampMs);
    return this.currentStats(timestampMs, '停车校准中，请保持停稳');
  }

  refreshGravityAtEntrance(timestampMs: number): boolean {
    const windowFrames = 75;
    if (this.preCalBuffer.length < windowFrames) {
      return false;
    }

    let bestStart = 0;
    let bestRms = Infinity;
    for (let i = 0; i <= this.preCalBuffer.length - windowFrames; i++) {
      let s = emptyVector();
      let sq = 0;
      for (let j = i; j < i + windowFrames; j++) {
        const f = this.preCalBuffer[j];
        s = add(s, f.acceleration);
        sq += dot(f.acceleration, f.acceleration);
      }
      const mean = scale(s, 1 / windowFrames);
      const meanSq = sq / windowFrames;
      const meanDot = dot(mean, mean);
      const rms = Math.sqrt(Math.max(0, meanSq - meanDot));
      if (rms < bestRms) {
        bestRms = rms;
        bestStart = i;
      }
    }

    let calSum = emptyVector();
    let calSqSum = 0;
    let calGyroSum = 0;
    let calGyroMax = 0;
    let calMaxStep = 0;
    let lastAcc: Vector3 | undefined = undefined;
    for (let j = bestStart; j < bestStart + windowFrames; j++) {
      const f = this.preCalBuffer[j];
      calSum = add(calSum, f.acceleration);
      calSqSum += dot(f.acceleration, f.acceleration);
      calGyroSum += f.gyroMagnitude;
      calGyroMax = Math.max(calGyroMax, f.gyroMagnitude);
      if (lastAcc) {
        calMaxStep = Math.max(calMaxStep, magnitude(subtract(f.acceleration, lastAcc)));
      }
      lastAcc = f.acceleration;
    }

    const candidate = scale(calSum, 1 / windowFrames);
    const meanSquare = calSqSum / windowFrames;
    const candidateDot = dot(candidate, candidate);
    const rmsDeviation = Math.sqrt(Math.max(0, meanSquare - candidateDot));
    const gravMag = magnitude(candidate);
    const rawGravityError = Math.abs(gravMag - 9.80665);
    const gyroAverage = calGyroSum / windowFrames;
    const motion = gyroAverage > 0.08 || calGyroMax > 0.25 || calMaxStep > 0.65;
    const stable = rmsDeviation < 0.25 && rawGravityError < 0.25 && !motion;

    if (stable) {
      this.gravityEstimate = candidate;
      return true;
    }
    return false;
  }

  ingest(frame: SensorFrame): EstimatorFrame {
    if (!this.running) {
      return this.makeFrame(frame, this.currentStats(frame.timestampMs, '等待开始'));
    }

    const dt = this.computeDeltaSeconds(frame);
    const rawAcceleration = frame.acceleration;
    const accStep = this.computeAccStep(rawAcceleration);

    const preCalGyro = frame.gyroscope ? magnitude(frame.gyroscope) : 0;
    if (this.preCalBuffer.length >= 180) {
      this.preCalBuffer.shift();
    }
    this.preCalBuffer.push({ acceleration: rawAcceleration, gyroMagnitude: preCalGyro, velocityMps: this.velocityMps });

    if (frame.timestampMs <= this.calibrationUntilMs) {
      const calibrationGyroMagnitude = frame.gyroscope ? magnitude(frame.gyroscope) : 0;
      this.collectCalibration(rawAcceleration, calibrationGyroMagnitude);
      if (!this.parkingCalibrationPending) {
        return {
          sample: this.makeSample(frame.timestampMs, rawAcceleration, emptyVector(), MotionState.Calibrating, 0.35),
          stats: this.currentStats(frame.timestampMs, '校准中，请保持设备静止'),
          calibration: this.currentCalibration()
        };
      }
    }

    this.finishCalibrationIfNeeded(frame.timestampMs);

    const motionAcceleration = subtract(rawAcceleration, this.gravityEstimate);
    const clipped = clipMagnitude(motionAcceleration, 3.5);
    const filtered = this.lowPass(clipped, 0.22);
    this.filteredAcceleration = filtered;

    const gyroMagnitude = frame.gyroscope ? magnitude(frame.gyroscope) : 0;
    this.updateMainAxisLock(frame.timestampMs);
    const axisAllowed = this.shouldUpdateMainAxis(filtered, gyroMagnitude, frame.timestampMs);
    if (axisAllowed) {
      this.updateMainAxis(filtered, frame.timestampMs);
    }

    const forwardAcceleration = dot(filtered, this.mainAxis);
    const lateralVector = subtract(filtered, scale(this.mainAxis, forwardAcceleration));
    const lateralAcceleration = magnitude(lateralVector);
    this.pushWindowFrame(
      frame.timestampMs,
      forwardAcceleration,
      lateralAcceleration,
      gyroMagnitude,
      accStep,
      Math.abs(magnitude(rawAcceleration) - 9.80665)
    );

    const state = this.detectMotionState(forwardAcceleration, lateralAcceleration, gyroMagnitude, frame.timestampMs);
    const effectiveAcceleration = this.effectiveForwardAcceleration(forwardAcceleration, state);
    const previousVelocityMps = this.velocityMps;
    this.velocityMps = Math.max(0, this.velocityMps + effectiveAcceleration * dt);
    this.distanceM += ((previousVelocityMps + this.velocityMps) / 2) * dt;

    const speedKmh = this.velocityMps * 3.6;
    this.maxSpeedKmh = Math.max(this.maxSpeedKmh, speedKmh);
    this.confidence = this.computeConfidence(state, gyroMagnitude, frame.timestampMs);
    this.motionState = state;

    const sample = this.makeSample(frame.timestampMs, rawAcceleration, filtered, state, this.confidence);

    return {
      sample,
      stats: this.currentStats(frame.timestampMs, this.statusFor(state, frame.timestampMs)),
      calibration: this.currentCalibration()
    };
  }

  private beginCalibration(timestampMs: number): void {
    this.calibrationUntilMs = timestampMs + 1500;
    this.calibrationSum = emptyVector();
    this.calibrationSquareSum = 0;
    this.calibrationGyroSum = 0;
    this.calibrationGyroMax = 0;
    this.calibrationMaxStep = 0;
    this.calibrationLastAcceleration = undefined;
    this.calibrationSamples = 0;
    this.calibrationCount += 1;
    this.lastCalibrationMs = timestampMs;
    this.motionState = MotionState.Calibrating;
  }

  private collectCalibration(acceleration: Vector3, gyroMagnitude: number): void {
    if (this.calibrationLastAcceleration) {
      this.calibrationMaxStep = Math.max(this.calibrationMaxStep, magnitude(subtract(acceleration, this.calibrationLastAcceleration)));
    }
    this.calibrationLastAcceleration = acceleration;
    this.calibrationSum = add(this.calibrationSum, acceleration);
    this.calibrationSquareSum += dot(acceleration, acceleration);
    this.calibrationGyroSum += gyroMagnitude;
    this.calibrationGyroMax = Math.max(this.calibrationGyroMax, gyroMagnitude);
    this.calibrationSamples += 1;
  }

  private finishCalibrationIfNeeded(timestampMs: number): void {
    if (this.calibrationSamples > 0 && timestampMs > this.calibrationUntilMs) {
      let stableResult = false;
      let gravityCandidate: Vector3 = emptyVector();
      let parkingWindowEndVelocityMps: number = 0;

      if (this.parkingCalibrationPending && this.preCalBuffer.length >= 75) {
        const windowFrames = 75;
        let bestStart = 0;
        let bestRms = Infinity;
        for (let i = 0; i <= this.preCalBuffer.length - windowFrames; i++) {
          let sum = emptyVector();
          let sqSum = 0;
          for (let j = i; j < i + windowFrames; j++) {
            const f = this.preCalBuffer[j];
            sum = add(sum, f.acceleration);
            sqSum += dot(f.acceleration, f.acceleration);
          }
          const mean = scale(sum, 1 / windowFrames);
          const meanSq = sqSum / windowFrames;
          const meanDot = dot(mean, mean);
          const rms = Math.sqrt(Math.max(0, meanSq - meanDot));
          if (rms < bestRms) {
            bestRms = rms;
            bestStart = i;
          }
        }

        let calSum = emptyVector();
        let calSqSum = 0;
        let calGyroSum = 0;
        let calGyroMax = 0;
        let calMaxStep = 0;
        let lastAcc: Vector3 | undefined = undefined;
        for (let j = bestStart; j < bestStart + windowFrames; j++) {
          const f = this.preCalBuffer[j];
          calSum = add(calSum, f.acceleration);
          calSqSum += dot(f.acceleration, f.acceleration);
          calGyroSum += f.gyroMagnitude;
          calGyroMax = Math.max(calGyroMax, f.gyroMagnitude);
          if (lastAcc) {
            calMaxStep = Math.max(calMaxStep, magnitude(subtract(f.acceleration, lastAcc)));
          }
          lastAcc = f.acceleration;
        }

        gravityCandidate = scale(calSum, 1 / windowFrames);
        const meanSquare = calSqSum / windowFrames;
        const gravitySquare = dot(gravityCandidate, gravityCandidate);
        const rmsDeviation = Math.sqrt(Math.max(0, meanSquare - gravitySquare));
        const gravityMagnitude = magnitude(gravityCandidate);
        const rawGravityError = Math.abs(gravityMagnitude - 9.80665);
        const gyroAverage = calGyroSum / windowFrames;
        const motionDuringCalibration = gyroAverage > 0.08 || calGyroMax > 0.25 || calMaxStep > 0.65;
        stableResult = rmsDeviation < 0.25 && rawGravityError < 0.25 && !motionDuringCalibration;
        parkingWindowEndVelocityMps = this.preCalBuffer[bestStart + windowFrames - 1].velocityMps;
      } else {
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

      if (stableResult) {
        this.gravityEstimate = gravityCandidate;
        this.calibrationRejectedUntilMs = 0;
        if (this.parkingCalibrationPending) {
          this.applyParkingZero(timestampMs, parkingWindowEndVelocityMps);
          this.parkingCalibrationSuccessUntilMs = timestampMs + 5000;
        }
      } else {
        this.calibrationRejectedUntilMs = timestampMs + 10000;
        if (this.parkingCalibrationPending) {
          this.parkingCalibrationRejectedUntilMs = timestampMs + 5000;
        }
      }
      this.parkingCalibrationPending = false;
      this.initialCalibrationDone = true;
      this.filteredAcceleration = emptyVector();
      this.calibrationSamples = 0;
      this.calibrationSum = emptyVector();
      this.calibrationSquareSum = 0;
      this.calibrationGyroSum = 0;
      this.calibrationGyroMax = 0;
      this.calibrationMaxStep = 0;
      this.calibrationLastAcceleration = undefined;
    }
  }

  private lowPass(acceleration: Vector3, alpha: number): Vector3 {
    return {
      x: this.filteredAcceleration.x + alpha * (acceleration.x - this.filteredAcceleration.x),
      y: this.filteredAcceleration.y + alpha * (acceleration.y - this.filteredAcceleration.y),
      z: this.filteredAcceleration.z + alpha * (acceleration.z - this.filteredAcceleration.z)
    };
  }

  private computeAccStep(rawAcceleration: Vector3): number {
    if (!this.lastRawAcceleration) {
      this.lastRawAcceleration = rawAcceleration;
      return 0;
    }
    const value = magnitude(subtract(rawAcceleration, this.lastRawAcceleration));
    this.lastRawAcceleration = rawAcceleration;
    return value;
  }

  private computeDeltaSeconds(frame: SensorFrame): number {
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
    if (rawDt <= 0) {
      return 0.02;
    }
    return clamp(rawDt, 0.005, 0.08);
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

  private shouldUpdateMainAxis(filtered: Vector3, gyroMagnitude: number, timestampMs: number): boolean {
    const accelerationMagnitude = magnitude(filtered);
    if (!this.mainAxisInitialized) {
      return accelerationMagnitude > 0.18 && gyroMagnitude < 0.18;
    }
    if (this.motionState === MotionState.Curve || this.motionState === MotionState.LowConfidence) {
      return false;
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

  private updateMainAxis(filtered: Vector3, timestampMs: number): void {
    const candidateMagnitude = magnitude(filtered);
    let candidate = normalize(filtered);
    if (!this.mainAxisInitialized) {
      this.mainAxis = candidate;
      this.mainAxisInitialized = true;
      this.mainAxisSeedMagnitude = candidateMagnitude;
      this.mainAxisUpdateCount = 1;
      this.mainAxisLastUpdateMs = timestampMs;
      this.orthogonalizeAxis();
      return;
    }

    if (this.mainAxisLocked) {
      if (dot(candidate, this.mainAxis) < 0) {
        candidate = scale(candidate, -1);
      }
      this.mainAxis = normalize(mix(this.mainAxis, candidate, 0.003));
      this.orthogonalizeAxis();
      return;
    }

    const alignment = dot(candidate, this.mainAxis);
    if (Math.abs(alignment) < 0.35 && this.velocityMps < 2 && candidateMagnitude > Math.max(0.18, this.mainAxisSeedMagnitude * 1.8)) {
      this.mainAxis = candidate;
      this.mainAxisSeedMagnitude = candidateMagnitude;
      this.mainAxisUpdateCount += 1;
      this.mainAxisLastUpdateMs = timestampMs;
      this.orthogonalizeAxis();
      return;
    }

    if (alignment < 0) {
      candidate = scale(candidate, -1);
    }
    this.mainAxis = normalize(mix(this.mainAxis, candidate, 0.025));
    this.mainAxisSeedMagnitude = Math.max(this.mainAxisSeedMagnitude, candidateMagnitude);
    this.mainAxisUpdateCount += 1;
    this.mainAxisLastUpdateMs = timestampMs;
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
    if (this.windowFrames.length < 20) {
      return false;
    }
    return averageAccStep(this.windowFrames) < 0.35 &&
      averageGravityDeviation(this.windowFrames) < 0.45 &&
      averageGyro(this.windowFrames) < 0.12 &&
      varianceForward(this.windowFrames) < 0.08;
  }

  private axisLockWindowUnstable(): boolean {
    if (this.windowFrames.length < 20) {
      return false;
    }
    return averageAccStep(this.windowFrames) > 0.85 || averageGravityDeviation(this.windowFrames) > 1.2;
  }

  private pushWindowFrame(
    timestampMs: number,
    forward: number,
    lateral: number,
    gyroMagnitude: number,
    accStep: number,
    gravityDeviation: number
  ): void {
    this.windowFrames.push({ timestampMs, forward, lateral, gyroMagnitude, accStep, gravityDeviation });
    while (this.windowFrames.length > 80 || (this.windowFrames.length > 0 && timestampMs - this.windowFrames[0].timestampMs > 1800)) {
      this.windowFrames.shift();
    }
  }

  private detectMotionState(forward: number, lateral: number, gyroMagnitude: number, timestampMs: number): MotionState {
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

  private applyParkingZero(timestampMs: number, offsetVelocityMps: number): void {
    this.velocityMps = Math.max(0, this.velocityMps - offsetVelocityMps);
    this.lastTimestampMs = timestampMs;
    this.filteredAcceleration = emptyVector();
    this.windowFrames = [];
    this.mainAxisInitialized = false;
    this.mainAxisLocked = false;
    this.mainAxisSeedMagnitude = 0;
    this.mainAxisUpdateCount = 0;
    this.mainAxisLastUpdateMs = 0;
    this.lastRawAcceleration = undefined;
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

  private makeSample(
    timestampMs: number,
    raw: Vector3,
    filtered: Vector3,
    state: MotionState,
    confidence: number
  ): SpeedSample {
    const speedKmh = this.velocityMps * 3.6;
    return {
      timestampMs,
      rawAcceleration: raw,
      filteredAcceleration: filtered,
      speedMps: this.velocityMps,
      speedKmh,
      confidence,
      motionState: state
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
      frame.acceleration,
      this.filteredAcceleration,
      stats.motionState,
      stats.confidence
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
      return '停车校准成功，速度已归零';
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
