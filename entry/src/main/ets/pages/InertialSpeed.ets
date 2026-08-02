import abilityAccessCtrl from '@ohos.abilityAccessCtrl';
import common from '@ohos.app.ability.common';
import { SpeedEstimator } from '../model/SpeedEstimator';
import { SensorController } from '../model/SensorController';
import { LocationController, LocationSnapshot, SatelliteSnapshot } from '../model/LocationController';
import { ResearchRecorder, ResearchRecorderStatus } from '../model/ResearchRecorder';
import { BackgroundState } from '../model/BackgroundState';
import { emitter } from '@kit.BasicServicesKit';
import {
  EstimatorFrame,
  MotionState,
  RunStats,
  SensorCallbackSample,
  SensorFrame,
  SpeedHistoryEntry,
  motionStateText
} from '../model/SpeedTypes';

interface GyroSample {
  gx: number;
  gy: number;
  gz: number;
  ts: number;
}

interface LocationPermissionResult {
  granted: boolean;
  message: string;
}

function emptyStats(): RunStats {
  return {
    currentSpeedKmh: 0,
    maxSpeedKmh: 0,
    averageSpeedKmh: 0,
    elapsedMs: 0,
    calibrationCount: 0,
    confidence: 0,
    motionState: MotionState.Idle,
    statusText: '等待开始',
    calibrationRejected: false
  };
}

function emptyResearchStatus(): ResearchRecorderStatus {
  return {
    running: false,
    sessionId: '',
    sampleCount: 0,
    hasExportableLog: false,
    integrityWarning: false,
    lastEvent: '',
    lastExportPath: ''
  };
}

@Entry
@Component
struct InertialSpeed {
  private estimator: SpeedEstimator = new SpeedEstimator();
  private sensorController: SensorController = new SensorController();
  private locationController: LocationController = new LocationController();
  private researchRecorder: ResearchRecorder = new ResearchRecorder();
  private researchSensorActive: boolean = false;

  @State private stats: RunStats = emptyStats();
  @State private isRunning: boolean = false;
  @State private measurementStartPending: boolean = false;
  @State private sensorStatus: string = '传感器未启动';
  @State private locationStatus: string = '定位未启动';
  @State private researchStatus: ResearchRecorderStatus = emptyResearchStatus();
  @State private lastMarkedEvent: string = '';
  @State private tunnelSwitchOn: boolean = false;
  @State private pureInertialSpeedKmh: number = 0;
  @State private gnssSpeedKmh: number = 0;
  @State private showPlacementWarning: boolean = false;
  @State private speedStatsHeight: number = 0;
  @State private researchExportInProgress: boolean = false;
  @State private navigationInProgress: boolean = false;
  private backgroundSensorsPaused: boolean = false;
  private backgroundHandlerRegistered: boolean = false;
  private measurementOperationId: number = 0;
  private researchOperationId: number = 0;
  private locationPermissionRequest: Promise<LocationPermissionResult> | null = null;
  private tunnelState: 'unknown' | 'outside' | 'inside' = 'unknown';
  private feedbackUntilMs: number = 0;
  private lastAnchorGnssMps: number = 0;
  private lastAnchorInertialMps: number = 0;
  private hasGnssAnchor: boolean = false;
  private gnssAnchorUsable: boolean = false;
  private measurementLocationActive: boolean = false;
  private inertialSpeedHistory: SpeedHistoryEntry[] = [];
  private readonly INERTIAL_HISTORY_RETENTION_MS: number = 200;
  private readonly GNSS_LAG_MS: number = 40;
  private displayDistanceM: number = 0;
  private displayElapsedMs: number = 0;
  private displayMaxSpeedKmh: number = 0;
  private lastDisplaySpeedMps: number = 0;
  private lastDisplayTimestampMs: number = 0;
  private readonly DISPLAY_STATS_MAX_CONTINUOUS_MS: number = 80;
  private readonly backgroundEventHandler: (data: emitter.EventData) => void = (data: emitter.EventData): void => {
    const actionData: Record<string, string> = data.data as Record<string, string>;
    if (actionData && actionData.action === 'stopSensors') {
      this.onBgStopSensors();
    } else if (actionData && actionData.action === 'restoreSensors') {
      this.onBgRestoreSensors();
    }
  };

  private gyroBuf: GyroSample[] = [];
  private readonly GYRO_WINDOW_MS: number = 780;
  private readonly GYRO_RMS_THRESHOLD: number = 0.5;
  private readonly ZCR_THRESHOLD: number = 5.0;
  private readonly HANDHELD_ACTIVATE_MS: number = 800;
  private handheldSuspectSinceMs: number | null = null;
  private lastGyroscopeSensorTimestamp: number | null = null;
  private placementWarningShownThisRun: boolean = false;

  private onBgStopSensors(): void {
    if (this.backgroundSensorsPaused) {
      return;
    }
    this.backgroundSensorsPaused = true;
    this.measurementOperationId += 1;
    this.measurementStartPending = false;
    this.researchOperationId += 1;
    const now = Date.now();
    if (this.researchStatus.running) {
      this.researchStatus = this.researchRecorder.markSystemEvent(
        now,
        '后台连续采集中止',
        'LOCATION long task unavailable or cancelled'
      );
      if (this.isRunning) {
        this.researchStatus = this.researchRecorder.endMeasurement(
          now,
          'reason=background_collection_unavailable'
        );
      }
      this.researchStatus = this.researchRecorder.stop(now);
    } else {
      this.researchStatus = this.researchRecorder.flush();
    }
    if (this.isRunning) {
      const finalStats = this.estimator.stop(now);
      this.applyDisplayStatsSummary(finalStats);
      this.stats = finalStats;
    }
    this.isRunning = false;
    this.hasGnssAnchor = false;
    this.gnssAnchorUsable = false;
    this.pureInertialSpeedKmh = 0;
    this.gnssSpeedKmh = 0;
    this.sensorController.stop();
    this.locationController.stop();
    this.researchSensorActive = false;
    this.measurementLocationActive = false;
    this.inertialSpeedHistory = [];
    this.gyroBuf = [];
    this.handheldSuspectSinceMs = null;
    this.lastGyroscopeSensorTimestamp = null;
    BackgroundState.measurementActive = false;
    BackgroundState.recordingActive = false;
    this.notifyCollectionStateChanged();
    this.sensorStatus = '后台连续采集已中止，请回到前台重新开始';
    this.locationStatus = '后台长时任务不可用或已取消';
  }

  private onBgRestoreSensors(): void {
    if (!this.backgroundSensorsPaused) {
      return;
    }
    this.backgroundSensorsPaused = false;
    this.sensorStatus = '后台连续采集曾中止，请重新开始测速或记录';
  }

  aboutToAppear(): void {
    if (!this.backgroundHandlerRegistered) {
      this.backgroundHandlerRegistered = true;
      emitter.on({ eventId: 100 }, this.backgroundEventHandler);
    }
    const context = this.getAbilityContext();
    if (context) {
      this.researchStatus = this.researchRecorder.attachContext(context);
    }
  }

  aboutToDisappear(): void {
    this.measurementOperationId += 1;
    this.measurementStartPending = false;
    this.researchOperationId += 1;
    BackgroundState.measurementActive = false;
    BackgroundState.recordingActive = false;
    this.notifyCollectionStateChanged();
    const now = Date.now();
    if (this.isRunning && this.researchStatus.running) {
      this.researchStatus = this.researchRecorder.endMeasurement(now, 'reason=page_disappear');
    }
    if (this.isRunning) {
      const finalStats = this.estimator.stop(now);
      this.applyDisplayStatsSummary(finalStats);
      this.stats = finalStats;
      this.isRunning = false;
    }
    this.researchStatus = this.researchStatus.running
      ? this.researchRecorder.stop(now)
      : this.researchRecorder.flush();
    this.sensorController.stop();
    this.locationController.stop();
    if (this.backgroundHandlerRegistered) {
      emitter.off(100, this.backgroundEventHandler);
      this.backgroundHandlerRegistered = false;
    }
  }

  private notifyCollectionStateChanged(): void {
    emitter.emit({ eventId: 100 }, {
      data: { action: 'collectionStateChanged' }
    });
  }

  private async startMeasurement(): Promise<void> {
    if (this.isRunning || this.measurementStartPending) {
      return;
    }

    const operationId = ++this.measurementOperationId;
    this.measurementStartPending = true;
    this.locationStatus = '正在确认定位权限';
    const permissionResult = await this.requestLocationPermission();
    if (operationId !== this.measurementOperationId) {
      return;
    }
    this.measurementStartPending = false;
    this.locationStatus = permissionResult.message;
    const now = Date.now();
    this.stats = this.estimator.start(now);
    this.resetDisplayStats();
    this.isRunning = true;
    this.hasGnssAnchor = false;
    this.gnssAnchorUsable = false;
    this.pureInertialSpeedKmh = 0;
    this.gnssSpeedKmh = 0;
    this.inertialSpeedHistory = [];
    this.showPlacementWarning = false;
    this.placementWarningShownThisRun = false;
    this.gyroBuf = [];
    this.handheldSuspectSinceMs = null;
    this.lastGyroscopeSensorTimestamp = null;
    this.sensorStatus = '正在启动传感器';
    this.researchStatus = this.researchRecorder.beginMeasurement(now, 'measurement started');
    this.syncResearchRuntimeState();
    this.researchSensorActive = false;
    const sensorsStarted = this.sensorController.start((frame: SensorFrame) => {
      this.onSensorFrame(frame);
    }, (message: string) => {
      this.sensorStatus = message;
    }, (sample: SensorCallbackSample) => {
      this.researchRecorder.recordSensorCallback(sample);
    });
    if (!sensorsStarted) {
      const measurementSensorError = this.sensorStatus;
      this.isRunning = false;
      BackgroundState.measurementActive = false;
      this.notifyCollectionStateChanged();
      this.stats = this.estimator.reset(now);
      this.researchStatus = this.researchRecorder.cancelMeasurement(now, 'sensor start failed');
      this.syncResearchRuntimeState();
      let researchRestored = true;
      if (this.researchStatus.running) {
        researchRestored = this.ensureResearchSensorsOrStop();
      }
      if (researchRestored) {
        this.sensorStatus = measurementSensorError === '正在启动传感器'
          ? '传感器未启动'
          : measurementSensorError;
      }
      return;
    }
    BackgroundState.measurementActive = true;
    this.notifyCollectionStateChanged();
    if (this.researchStatus.running) {
      this.ensureResearchSensorsOrStop();
    }
    if (permissionResult.granted) {
      this.startLocationForMeasurement();
    }
  }

  private startLocationForMeasurement(): void {
    if (this.measurementLocationActive || this.locationController.isRunning) {
      this.measurementLocationActive = true;
      return;
    }
    this.measurementLocationActive = this.startLocationForAll();
  }

  private startLocationForAll(forRecording?: boolean): boolean {
    return this.locationController.start(
      (location: LocationSnapshot) => {
        if (this.researchStatus.running) {
          this.researchRecorder.updateLocation(location);
        }
        const satelliteText = location.satelliteCount === null ? '' : ` 星${location.satelliteFixCount}/${location.satelliteCount}`;
        this.updateLocationStatus(`定位 ${location.accuracy.toFixed(0)}m ${location.speedMps.toFixed(1)}m/s${satelliteText}`);
        this.researchStatus = this.researchRecorder.status();
        this.gnssSpeedKmh = Math.max(0, location.speedMps * 3.6);
        this.syncResearchRuntimeState();

        if (this.isRunning) {
          const speedAcc = location.speedAccuracyMps;
          const src = location.locationSourceType;
          if (this.tunnelState !== 'inside') {
            const reliableAnchor = speedAcc !== null &&
              speedAcc > 0 &&
              location.speedMps >= speedAcc &&
              src !== null &&
              (src === 1 || src === 4);
            this.gnssAnchorUsable = reliableAnchor;
            if (reliableAnchor) {
              this.lastAnchorGnssMps = location.speedMps;
              this.lastAnchorInertialMps = this.getInertialSpeedAtLag(this.GNSS_LAG_MS, location.timestampMs);
              this.hasGnssAnchor = true;
            }
          }
        }
      },
      (satellite: SatelliteSnapshot) => {
        if (this.researchStatus.running) {
          this.researchRecorder.updateSatellite(satellite);
        }
        this.researchStatus = this.researchRecorder.status();
        this.syncResearchRuntimeState();
      },
      (message: string) => {
        this.locationStatus = message;
      },
      forRecording
    );
  }

  private stopLocationIfUnused(): void {
    if (this.isRunning || this.researchStatus.running) {
      return;
    }
    this.measurementLocationActive = false;
    this.locationController.stop();
    this.locationStatus = '定位已停止';
  }

  private stopMeasurement(reason: string = 'manual'): void {
    if (!this.isRunning) {
      return;
    }

    this.measurementOperationId += 1;
    BackgroundState.measurementActive = false;
    this.notifyCollectionStateChanged();
    const now = Date.now();
    this.sensorController.stop();
    this.sensorStatus = '传感器已停止';
    this.researchStatus = this.researchRecorder.endMeasurement(now,
      `reason=${reason} speed=${this.stats.currentSpeedKmh.toFixed(2)}km/h`);
    this.syncResearchRuntimeState();
    const finalStats = this.estimator.stop(now);
    this.applyDisplayStatsSummary(finalStats);
    this.stats = finalStats;
    this.isRunning = false;
    this.hasGnssAnchor = false;
    this.gnssAnchorUsable = false;
    this.pureInertialSpeedKmh = 0;
    this.inertialSpeedHistory = [];
    this.showPlacementWarning = false;
    this.researchSensorActive = false;
    this.stopLocationIfUnused();
    if (!this.locationController.isRunning) {
      this.gnssSpeedKmh = 0;
    }
    if (this.researchStatus.running) {
      this.ensureResearchSensorsOrStop();
    }
  }

  private toggleMeasurement(): void {
    if (this.measurementStartPending) {
      return;
    }
    if (this.isRunning) {
      this.stopMeasurement();
      return;
    }

    this.startMeasurement();
  }

  private calibrateAtStop(): void {
    if (!this.isRunning) {
      return;
    }

    const now = Date.now();
    const speedBeforeCalibration = this.stats.currentSpeedKmh;
    const confidenceBeforeCalibration = this.stats.confidence;
    const stateBeforeCalibration = motionStateText(this.stats.motionState);
    this.stats = this.estimator.calibrateAtStop(now);
    this.applyDisplayStatsSummary(this.stats);
    if (this.researchStatus.running) {
      this.researchStatus = this.researchRecorder.markEvent(
        now,
        '停车校准请求',
        `beforeSpeed=${speedBeforeCalibration.toFixed(2)}km/h confidence=${confidenceBeforeCalibration.toFixed(2)} state=${stateBeforeCalibration}`
      );
    }
    this.syncResearchRuntimeState();
  }

  private onSensorFrame(frame: SensorFrame): void {
    const nextFrame: EstimatorFrame = this.estimator.ingest(frame);
    this.researchRecorder.recordSensorFrame(frame);

    if (this.isInitialCalibrationRejected(nextFrame.stats)) {
      this.abortStartCalibration();
      return;
    }

    const parkingCalibrationResult = this.estimator.consumeParkingCalibrationResult();
    if (parkingCalibrationResult > 0) {
      this.lastAnchorGnssMps = 0;
      this.lastAnchorInertialMps = 0;
      this.hasGnssAnchor = true;
      this.gnssAnchorUsable = true;
      this.gnssSpeedKmh = 0;
      this.inertialSpeedHistory = [];
    }

    this.stats = nextFrame.stats;
    this.pureInertialSpeedKmh = this.stats.currentSpeedKmh;

    const anchorApplied = this.hasGnssAnchor && this.gnssAnchorUsable;
    const pure = anchorApplied ? 0 : 1;
    this.estimator.setPureMode(pure);

    if (anchorApplied) {
      const currentInertial = this.estimator.getVelocityMps();
      const delta = currentInertial - this.lastAnchorInertialMps;
      const anchorSpeedKmh = Math.max(0, (this.lastAnchorGnssMps + delta) * 3.6);
      this.stats.currentSpeedKmh = anchorSpeedKmh;
    }
    const displayTimestampMs = frame.sensorTimestamp === null || frame.sensorTimestamp === undefined
      ? frame.timestampMs
      : frame.sensorTimestamp / 1000000;
    this.updateDisplayStats(this.stats.currentSpeedKmh, displayTimestampMs);
    this.researchRecorder.updateEstimator(nextFrame.sample, nextFrame.stats, nextFrame.calibration);
    this.researchStatus = this.researchRecorder.status();
    if (parkingCalibrationResult > 0 && this.researchStatus.running) {
      this.researchStatus = this.researchRecorder.markEvent(
        frame.timestampMs,
        '停车校准成功',
        `speed=${this.stats.currentSpeedKmh.toFixed(2)}km/h`
      );
    } else if (parkingCalibrationResult < 0 && this.researchStatus.running) {
      this.researchStatus = this.researchRecorder.markEvent(
        frame.timestampMs,
        '停车校准拒绝',
        `speed=${this.stats.currentSpeedKmh.toFixed(2)}km/h`
      );
    }
    this.syncResearchRuntimeState();

    const currentInertialSpeed = this.estimator.getVelocityMps();
    this.inertialSpeedHistory.push({ t: frame.timestampMs, v: currentInertialSpeed });
    const historyCutoffMs = frame.timestampMs - this.INERTIAL_HISTORY_RETENTION_MS;
    while (this.inertialSpeedHistory.length > 1 && this.inertialSpeedHistory[1].t <= historyCutoffMs) {
      this.inertialSpeedHistory.shift();
    }

    if (frame.gyroscope) {
      const gyroscopeSensorTimestamp = frame.gyroscopeTimestamp ?? null;
      const isNewGyroscopeSample = gyroscopeSensorTimestamp === null ||
        gyroscopeSensorTimestamp !== this.lastGyroscopeSensorTimestamp;
      if (isNewGyroscopeSample) {
        if (gyroscopeSensorTimestamp !== null) {
          this.lastGyroscopeSensorTimestamp = gyroscopeSensorTimestamp;
        }
        const currentGyroscopeTimestampMs = gyroscopeSensorTimestamp === null
          ? frame.timestampMs
          : gyroscopeSensorTimestamp / 1000000;
        this.gyroBuf.push({
          gx: frame.gyroscope.x,
          gy: frame.gyroscope.y,
          gz: frame.gyroscope.z,
          ts: currentGyroscopeTimestampMs
        });
        const gyroCutoffMs = currentGyroscopeTimestampMs - this.GYRO_WINDOW_MS;
        while (this.gyroBuf.length > 1 && this.gyroBuf[1].ts <= gyroCutoffMs) {
          this.gyroBuf.shift();
        }
        this.checkHandheld();
      }
    }
  }

  private resetDisplayStats(): void {
    this.displayDistanceM = 0;
    this.displayElapsedMs = 0;
    this.displayMaxSpeedKmh = 0;
    this.lastDisplaySpeedMps = 0;
    this.lastDisplayTimestampMs = 0;
  }

  private updateDisplayStats(currentSpeedKmh: number, timestampMs: number): void {
    const currentSpeedMps = Math.max(0, currentSpeedKmh / 3.6);
    if (this.lastDisplayTimestampMs > 0 && timestampMs > this.lastDisplayTimestampMs) {
      const elapsedMs = timestampMs - this.lastDisplayTimestampMs;
      if (elapsedMs <= this.DISPLAY_STATS_MAX_CONTINUOUS_MS) {
        const elapsedSeconds = elapsedMs / 1000;
        this.displayDistanceM += ((this.lastDisplaySpeedMps + currentSpeedMps) / 2) * elapsedSeconds;
        this.displayElapsedMs += elapsedMs;
      }
    }
    this.lastDisplayTimestampMs = timestampMs;
    this.lastDisplaySpeedMps = currentSpeedMps;
    this.displayMaxSpeedKmh = Math.max(this.displayMaxSpeedKmh, currentSpeedKmh);
    this.applyDisplayStatsSummary(this.stats);
  }

  private applyDisplayStatsSummary(stats: RunStats): void {
    stats.maxSpeedKmh = this.displayMaxSpeedKmh;
    stats.averageSpeedKmh = this.displayElapsedMs > 0
      ? (this.displayDistanceM / (this.displayElapsedMs / 1000)) * 3.6
      : 0;
  }

  private getInertialSpeedAtLag(delayMs: number, referenceTimestampMs: number): number {
    const target = referenceTimestampMs - delayMs;
    if (this.inertialSpeedHistory.length === 0) {
      return this.estimator.getVelocityMps();
    }
    if (target <= this.inertialSpeedHistory[0].t) {
      return this.inertialSpeedHistory[0].v;
    }

    for (let i = 1; i < this.inertialSpeedHistory.length; i++) {
      const previous = this.inertialSpeedHistory[i - 1];
      const current = this.inertialSpeedHistory[i];
      if (target <= current.t) {
        const durationMs = current.t - previous.t;
        if (durationMs <= 0) {
          return current.v;
        }
        const ratio = (target - previous.t) / durationMs;
        return previous.v + (current.v - previous.v) * ratio;
      }
    }

    return this.inertialSpeedHistory[this.inertialSpeedHistory.length - 1].v;
  }

  private checkHandheld(): void {
    if (!this.isRunning || this.placementWarningShownThisRun) {
      return;
    }
    if (this.gyroBuf.length < 2) {
      return;
    }
    const latestTimestampMs = this.gyroBuf[this.gyroBuf.length - 1].ts;
    const windowDurationMs = latestTimestampMs - this.gyroBuf[0].ts;
    if (windowDurationMs < this.GYRO_WINDOW_MS) {
      return;
    }

    let ss = 0;
    for (let i = 0; i < this.gyroBuf.length; i++) {
      const v = this.gyroBuf[i];
      ss += v.gx * v.gx + v.gy * v.gy + v.gz * v.gz;
    }
    const rms = Math.sqrt(ss / this.gyroBuf.length);

    let zc = 0;
    let pgx = this.gyroBuf[0].gx;
    let pgy = this.gyroBuf[0].gy;
    let pgz = this.gyroBuf[0].gz;
    for (let i = 1; i < this.gyroBuf.length; i++) {
      const v = this.gyroBuf[i];
      if (v.gx * pgx <= 0 && v.gx !== pgx) {
        zc++;
      }
      if (v.gy * pgy <= 0 && v.gy !== pgy) {
        zc++;
      }
      if (v.gz * pgz <= 0 && v.gz !== pgz) {
        zc++;
      }
      pgx = v.gx;
      pgy = v.gy;
      pgz = v.gz;
    }
    const zcr = zc / (Math.max(windowDurationMs, 1) / 1000);

    if (rms > this.GYRO_RMS_THRESHOLD && zcr > this.ZCR_THRESHOLD) {
      if (this.handheldSuspectSinceMs === null) {
        this.handheldSuspectSinceMs = latestTimestampMs;
      }
    } else {
      this.handheldSuspectSinceMs = null;
    }

    if (this.handheldSuspectSinceMs !== null &&
      latestTimestampMs - this.handheldSuspectSinceMs >= this.HANDHELD_ACTIVATE_MS &&
      !this.placementWarningShownThisRun) {
      this.placementWarningShownThisRun = true;
      this.handheldSuspectSinceMs = null;
      if (this.researchStatus.running) {
        this.researchStatus = this.researchRecorder.markSystemEvent(
          Date.now(),
          '持续晃动自动停止',
          `gyroRms=${rms.toFixed(4)} zcr=${zcr.toFixed(2)}`
        );
        this.syncResearchRuntimeState();
      }
      this.stopMeasurement('handheld');
      this.showPlacementWarning = true;
    }
  }

  private isInitialCalibrationRejected(stats: RunStats): boolean {
    return stats.calibrationCount === 1 && stats.calibrationRejected;
  }

  private abortStartCalibration(): void {
    this.measurementOperationId += 1;
    BackgroundState.measurementActive = false;
    this.notifyCollectionStateChanged();
    const now = Date.now();
    this.sensorController.stop();
    this.researchSensorActive = false;
    const resetStats = this.estimator.reset(now);
    this.stats = {
      currentSpeedKmh: 0,
      maxSpeedKmh: 0,
      averageSpeedKmh: 0,
      elapsedMs: 0,
      calibrationCount: 0,
      confidence: 0,
      motionState: MotionState.Idle,
      statusText: '开始失败，请停稳后再开始',
      calibrationRejected: false
    };
    this.resetDisplayStats();
    this.isRunning = false;
    this.hasGnssAnchor = false;
    this.gnssAnchorUsable = false;
    this.pureInertialSpeedKmh = 0;
    this.gnssSpeedKmh = 0;
    this.showPlacementWarning = false;
    this.sensorStatus = '开始失败：初始校准不稳';
    this.researchStatus = this.researchRecorder.cancelMeasurement(now, resetStats.statusText);
    this.syncResearchRuntimeState();
    this.stopLocationIfUnused();
    if (this.researchStatus.running) {
      this.ensureResearchSensorsOrStop();
    }
  }

  private async toggleResearchRecording(): Promise<void> {
    if (this.researchExportInProgress) {
      return;
    }
    const operationId = ++this.researchOperationId;
    const now = Date.now();
    if (this.researchStatus.running) {
      this.stopResearchSensorsIfIdle();
      if (!this.isRunning) {
        this.sensorStatus = '传感器未启动';
      }
      this.researchStatus = this.researchRecorder.stop(now);
      this.stopLocationIfUnused();
      BackgroundState.recordingActive = false;
      this.notifyCollectionStateChanged();
      return;
    }

    const context = this.getAbilityContext();
    if (!context) {
      this.locationStatus = 'context unavailable';
      return;
    }

    if (!this.startResearchSensors()) {
      BackgroundState.recordingActive = false;
      this.notifyCollectionStateChanged();
      return;
    }
    this.researchStatus = this.researchRecorder.start(now, context);
    if (!this.researchStatus.running) {
      this.stopResearchSensorsIfIdle();
      this.locationStatus = this.researchStatus.lastExportPath || '研究记录文件启动失败';
      BackgroundState.recordingActive = false;
      this.notifyCollectionStateChanged();
      this.stopLocationIfUnused();
      return;
    }
    if (!this.sensorController.isGyroscopeAvailable) {
      this.researchStatus = this.researchRecorder.markSystemEvent(
        now,
        '研究记录传感器降级',
        'gyroscope unavailable; accelerometer recording continues'
      );
    }
    BackgroundState.recordingActive = true;
    this.notifyCollectionStateChanged();
    if (this.isRunning) {
      this.researchStatus = this.researchRecorder.noteMeasurementAlreadyActive(
        now,
        `recording started while measurement active; elapsed=${this.stats.elapsedMs}ms`
      );
      this.syncResearchRuntimeState();
      if (!this.researchStatus.running) {
        return;
      }
    }
    this.feedbackUntilMs = 0;
    const permissionResult = await this.requestLocationPermission();
    if (operationId !== this.researchOperationId || !this.researchStatus.running) {
      return;
    }
    this.locationStatus = permissionResult.message;
    this.researchStatus = this.researchRecorder.markSystemEvent(
      Date.now(),
      permissionResult.granted ? '定位权限可用' : '定位权限不可用',
      this.locationStatus
    );
    this.syncResearchRuntimeState();
    if (!this.researchStatus.running) {
      return;
    }
    if (!permissionResult.granted) {
      return;
    }
    if (!this.locationController.isRunning) {
      this.startLocationForAll(true);
    }
    const locationStarted = this.locationController.isRunning;

    if (!locationStarted) {
      this.locationStatus = '定位未启动，仍记录传感器';
      this.researchStatus = this.researchRecorder.markSystemEvent(Date.now(), '定位启动失败', this.locationStatus);
    } else {
      this.researchStatus = this.researchRecorder.markSystemEvent(Date.now(), '定位启动成功', this.locationStatus);
    }
    this.syncResearchRuntimeState();

  }

  private startResearchSensors(): boolean {
    if (this.researchSensorActive) {
      return true;
    }

    if (this.isRunning) {
      this.sensorController.startAuxiliarySensors();
      this.researchSensorActive = true;
      return true;
    }

    this.researchRecorder.clearEstimatorSnapshot();
    this.sensorStatus = '正在启动研究记录传感器';
    const started = this.sensorController.start((frame: SensorFrame) => {
      this.onResearchSensorFrame(frame);
    }, (message: string) => {
      this.sensorStatus = message;
    }, (sample: SensorCallbackSample) => {
      this.researchRecorder.recordSensorCallback(sample);
    }, false);
    if (started) {
      this.sensorController.startAuxiliarySensors();
      if (!this.sensorController.isGyroscopeAvailable) {
        this.sensorStatus = '研究记录已降级：陀螺仪不可用';
        if (this.researchStatus.running) {
          this.researchStatus = this.researchRecorder.markSystemEvent(
            Date.now(),
            '研究记录传感器降级',
            'gyroscope unavailable; accelerometer recording continues'
          );
        }
      }
    }
    this.researchSensorActive = started;
    if (!started && this.sensorStatus === '正在启动研究记录传感器') {
      this.sensorStatus = '研究记录传感器未启动';
    }
    return started;
  }

  private ensureResearchSensorsOrStop(): boolean {
    if (!this.researchStatus.running || this.startResearchSensors()) {
      return true;
    }
    this.researchStatus = this.researchRecorder.stop(Date.now());
    BackgroundState.recordingActive = false;
    this.notifyCollectionStateChanged();
    this.researchSensorActive = false;
    this.stopLocationIfUnused();
    return false;
  }

  private stopResearchSensorsIfIdle(): void {
    if (!this.researchSensorActive) {
      return;
    }

    if (this.isRunning) {
      this.sensorController.stopAuxiliarySensors();
      this.researchSensorActive = false;
      return;
    }

    this.sensorController.stop();
    this.researchSensorActive = false;
  }

  private onResearchSensorFrame(frame: SensorFrame): void {
    this.researchRecorder.recordSensorFrame(frame);
    this.researchStatus = this.researchRecorder.status();
    this.syncResearchRuntimeState();
  }

  private syncResearchRuntimeState(): void {
    if (this.researchStatus.running || !BackgroundState.recordingActive) {
      return;
    }
    if (this.researchStatus.integrityWarning) {
      this.sensorStatus = '研究日志写入失败，记录已停止';
      this.locationStatus = this.researchStatus.lastExportPath || '日志可能不完整';
    }
    BackgroundState.recordingActive = false;
    this.notifyCollectionStateChanged();
    this.stopResearchSensorsIfIdle();
    this.stopLocationIfUnused();
  }

  private getAbilityContext(): common.UIAbilityContext | undefined {
    return this.getUIContext().getHostContext() as common.UIAbilityContext | undefined;
  }

  private async requestLocationPermission(): Promise<LocationPermissionResult> {
    if (this.locationPermissionRequest !== null) {
      return await this.locationPermissionRequest;
    }

    const request = this.performLocationPermissionRequest();
    this.locationPermissionRequest = request;
    try {
      return await request;
    } catch (error) {
      return { granted: false, message: '定位权限未授予' };
    } finally {
      if (this.locationPermissionRequest === request) {
        this.locationPermissionRequest = null;
      }
    }
  }

  private async performLocationPermissionRequest(): Promise<LocationPermissionResult> {
    const context = this.getAbilityContext();
    if (!context) {
      return { granted: false, message: '无法获取应用上下文' };
    }

    try {
      const atManager = abilityAccessCtrl.createAtManager();
      const result = await atManager.requestPermissionsFromUser(context, [
        'ohos.permission.LOCATION',
        'ohos.permission.APPROXIMATELY_LOCATION'
      ]);
      if (result.authResults.length < 2) {
        return { granted: false, message: '定位权限未返回结果' };
      }
      for (let i = 0; i < result.authResults.length; i++) {
        if (result.authResults[i] !== 0) {
          return { granted: false, message: '定位权限未授予' };
        }
      }
      return { granted: true, message: '定位权限可用' };
    } catch (error) {
      return { granted: false, message: '定位权限未授予' };
    }
  }

  private markResearchEventWithNotes(event: string, notes: string): void {
    this.researchStatus = this.researchRecorder.markEvent(Date.now(), event, notes);
    this.syncResearchRuntimeState();
    this.flashMarkedEvent(event);
  }

  private setTunnelStateFromSwitch(isInside: boolean): void {
    if (isInside && this.tunnelState === 'inside') {
      this.tunnelSwitchOn = true;
      return;
    }
    if (!isInside && this.tunnelState !== 'inside') {
      this.tunnelSwitchOn = false;
      return;
    }

    const event = isInside ? '入隧' : '出隧';
    this.tunnelState = isInside ? 'inside' : 'outside';
    this.tunnelSwitchOn = isInside;
    if (!isInside) {
      this.gnssAnchorUsable = false;
    }
    if (this.researchStatus.running) {
      this.markResearchEventWithNotes(event, 'manual_switch');
    }
  }

  private flashMarkedEvent(event: string): void {
    this.lastMarkedEvent = event;
    this.feedbackUntilMs = Date.now() + 1600;
    setTimeout(() => {
      if (this.lastMarkedEvent === event) {
        this.lastMarkedEvent = '';
      }
    }, 1600);
  }

  private updateLocationStatus(status: string): void {
    if (Date.now() < this.feedbackUntilMs) {
      return;
    }
    this.locationStatus = status;
  }

  private async exportResearchLog(): Promise<void> {
    if (!this.hasResearchLog() || this.researchStatus.running || this.researchExportInProgress) {
      return;
    }
    const context = this.getAbilityContext();
    if (!context) {
      this.locationStatus = '无法获取应用上下文';
      return;
    }

    this.researchExportInProgress = true;
    try {
      this.locationStatus = '请选择导出位置';
      this.researchStatus = await this.researchRecorder.exportJsonl(context);
      this.locationStatus = this.researchStatus.lastExportPath || '导出完成';
    } catch (error) {
      this.locationStatus = '导出失败';
    } finally {
      this.researchExportInProgress = false;
    }
  }

  private hasResearchLog(): boolean {
    return this.researchStatus.sampleCount > 0 || this.researchStatus.hasExportableLog;
  }

  private returnToModeSelection(): void {
    try {
      this.getUIContext().getRouter().back();
    } catch {
      this.locationStatus = '无法返回测速方式首页';
    }
  }

  private switchToTunnelLightSpeed(): void {
    if (this.navigationInProgress) {
      return;
    }
    this.navigationInProgress = true;
    try {
      this.getUIContext().getRouter().replaceUrl({ url: 'pages/TunnelLight' }).then(() => {
        this.navigationInProgress = false;
      }).catch(() => {
        this.navigationInProgress = false;
        this.locationStatus = '无法切换到灯光打点测速';
      });
    } catch {
      this.navigationInProgress = false;
      this.locationStatus = '无法切换到灯光打点测速';
    }
  }

  private confidenceText(): string {
    return `${Math.round(this.stats.confidence * 100)}%`;
  }

  private statusColor(): string {
    if (this.stats.motionState === MotionState.Curve) {
      return '#F59E0B';
    }
    if (this.stats.motionState === MotionState.LowConfidence || this.stats.motionState === MotionState.StrongVibration || this.stats.motionState === MotionState.ConductionVibration) {
      return '#EF4444';
    }
    if (this.stats.motionState === MotionState.Calibrating) {
      return '#60A5FA';
    }
    return '#22C55E';
  }

  private formatDuration(ms: number): string {
    const totalSeconds = Math.floor(ms / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    const secondText = seconds < 10 ? `0${seconds}` : `${seconds}`;
    return `${minutes}:${secondText}`;
  }

  @Builder
  private Header() {
    Row() {
      Button('‹', { type: ButtonType.Normal, stateEffect: true })
        .width(44)
        .height(40)
        .fontSize(28)
        .fontColor('#F8FAFC')
        .backgroundColor('#1E293B')
        .borderRadius(8)
        .accessibilityText('返回测速方式首页')
        .margin({ right: 12 })
        .onClick(() => {
          this.returnToModeSelection();
        });
      Column() {
        Text('惯性测速')
          .fontSize(24)
          .fontWeight(FontWeight.Bold)
          .fontColor('#F8FAFC');
        Text('稳定放置 · 惯性估算')
          .fontSize(13)
          .fontColor('#94A3B8')
          .margin({ top: 4 });
      }
      .alignItems(HorizontalAlign.Start)
      .layoutWeight(1);

      Text(motionStateText(this.stats.motionState))
        .fontSize(13)
        .fontColor('#0B1020')
        .fontWeight(FontWeight.Medium)
        .padding({ left: 10, right: 10, top: 6, bottom: 6 })
        .backgroundColor(this.statusColor())
        .borderRadius(8);
    }
    .width('100%')
    .alignItems(VerticalAlign.Center);
  }

  @Builder
  private SpeedPanel() {
    Column() {
      Text(this.stats.currentSpeedKmh.toFixed(1))
        .fontSize(64)
        .fontWeight(FontWeight.Bold)
        .fontColor('#F8FAFC')
        .maxLines(1);
      Text('km/h')
        .fontSize(18)
        .fontColor('#94A3B8')
        .margin({ top: -4 });

      Row() {
        Text(this.stats.statusText)
          .fontSize(14)
          .fontColor('#CBD5E1')
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
          .layoutWeight(1);
        Text(`置信度 ${this.confidenceText()}`)
          .fontSize(13)
          .fontColor(this.statusColor());
      }
      .width('100%')
      .margin({ top: 14 });

      Row({ space: 14 }) {
        Column() {
          Row() {
            Text(this.pureInertialSpeedKmh.toFixed(1))
              .fontSize(15)
              .fontWeight(FontWeight.Medium)
              .fontColor('#94A3B8')
              .maxLines(1);
            Text(' km/h')
              .fontSize(11)
              .fontColor('#94A3B8');
          }
          Text('纯惯性')
            .fontSize(11)
            .fontColor('#94A3B8')
            .margin({ top: 2 });
        }
        .alignItems(HorizontalAlign.Center)
        .layoutWeight(1);

        Column() {
          Row() {
            Text(this.gnssSpeedKmh.toFixed(1))
              .fontSize(15)
              .fontWeight(FontWeight.Medium)
              .fontColor('#60A5FA')
              .maxLines(1);
            Text(' km/h')
              .fontSize(11)
              .fontColor('#94A3B8');
          }
          Text('GNSS')
            .fontSize(11)
            .fontColor('#94A3B8')
            .margin({ top: 2 });
        }
        .alignItems(HorizontalAlign.Center)
        .layoutWeight(1);
      }
      .width('100%')
      .margin({ top: 10 });
    }
    .width('100%')
    .padding(14)
    .backgroundColor('#111827')
    .borderRadius(8);
  }

  @Builder
  private ControlPanel() {
    Row({ space: 10 }) {
      Button({ type: ButtonType.Capsule, stateEffect: !this.measurementStartPending }) {
        Text(this.measurementStartPending ? '准备中' : (this.isRunning ? '停止' : '开始'))
          .fontSize(16)
          .fontColor(this.measurementStartPending ? '#CBD5E1' : (this.isRunning ? '#F8FAFC' : '#0B1020'))
      }
        .height(48)
        .width(0)
        .layoutWeight(1)
        .focusable(!this.measurementStartPending)
        .hitTestBehavior(this.measurementStartPending ? HitTestMode.BLOCK_DESCENDANTS : HitTestMode.Default)
        .accessibilityText(this.measurementStartPending ? '准备中，不可用' : (this.isRunning ? '停止' : '开始'))
        .backgroundColor(this.measurementStartPending ? '#334155' : (this.isRunning ? '#DC2626' : '#4FD1C5'))
        .onClick(() => {
          this.toggleMeasurement();
        });

      Button({ type: ButtonType.Capsule, stateEffect: this.isRunning }) {
        Text('停车校准')
          .fontSize(16)
          .fontColor(this.isRunning ? '#0B1020' : '#94A3B8')
      }
        .height(48)
        .width(0)
        .layoutWeight(1)
        .focusable(this.isRunning)
        .hitTestBehavior(this.isRunning ? HitTestMode.Default : HitTestMode.BLOCK_DESCENDANTS)
        .accessibilityText(this.isRunning ? '停车校准' : '停车校准，不可用')
        .backgroundColor(this.isRunning ? '#F59E0B' : '#1E293B')
        .onClick(() => {
          this.calibrateAtStop();
        });
    }
    .width('100%');
  }

  @Builder
  private StatsGrid() {
    Column({ space: 10 }) {
      Row({ space: 10 }) {
        Column() {
          Text(`${this.stats.maxSpeedKmh.toFixed(1)} km/h`)
            .fontSize(18)
            .fontWeight(FontWeight.Bold)
            .fontColor('#F8FAFC')
            .maxLines(1)
            .textOverflow({ overflow: TextOverflow.Ellipsis });
          Text('最高')
            .fontSize(12)
            .fontColor('#94A3B8')
            .margin({ top: 5 });
        }
        .width(0)
        .height(64)
        .layoutWeight(1)
        .justifyContent(FlexAlign.Center)
        .backgroundColor('#111827')
        .borderRadius(8);

        Column() {
          Text(`${this.stats.averageSpeedKmh.toFixed(1)} km/h`)
            .fontSize(18)
            .fontWeight(FontWeight.Bold)
            .fontColor('#F8FAFC')
            .maxLines(1)
            .textOverflow({ overflow: TextOverflow.Ellipsis });
          Text('平均')
            .fontSize(12)
            .fontColor('#94A3B8')
            .margin({ top: 5 });
        }
        .width(0)
        .height(64)
        .layoutWeight(1)
        .justifyContent(FlexAlign.Center)
        .backgroundColor('#111827')
        .borderRadius(8);
      }
      .width('100%');

      Row({ space: 10 }) {
        Column() {
          Text(this.formatDuration(this.stats.elapsedMs))
            .fontSize(18)
            .fontWeight(FontWeight.Bold)
            .fontColor('#F8FAFC')
            .maxLines(1)
            .textOverflow({ overflow: TextOverflow.Ellipsis });
          Text('时长')
            .fontSize(12)
            .fontColor('#94A3B8')
            .margin({ top: 5 });
        }
        .width(0)
        .height(64)
        .layoutWeight(1)
        .justifyContent(FlexAlign.Center)
        .backgroundColor('#111827')
        .borderRadius(8);

        Column() {
          Text(`${this.stats.calibrationCount} 次`)
            .fontSize(18)
            .fontWeight(FontWeight.Bold)
            .fontColor('#F8FAFC')
            .maxLines(1)
            .textOverflow({ overflow: TextOverflow.Ellipsis });
          Text('校准')
            .fontSize(12)
            .fontColor('#94A3B8')
            .margin({ top: 5 });
        }
        .width(0)
        .height(64)
        .layoutWeight(1)
        .justifyContent(FlexAlign.Center)
        .backgroundColor('#111827')
        .borderRadius(8);
      }
      .width('100%');
    }
    .width('100%');
  }

  @Builder
  private SensorNotice() {
    Column() {
      Text(this.sensorStatus)
        .width('100%')
        .fontSize(13)
        .fontWeight(FontWeight.Medium)
        .fontColor('#CBD5E1')
        .lineHeight(20);
      Text('需稳定放置；手持、弯道、滑动、明显上下坡或长时间未校准会降低精度。结果仅供参考，停车后请点“停车校准”。')
        .width('100%')
        .fontSize(12)
        .fontColor('#94A3B8')
        .lineHeight(18)
        .margin({ top: 6 });
    }
    .width('100%')
    .padding(12)
    .backgroundColor('#0F172A')
    .borderRadius(8);
  }

  @Builder
  private AnchorFreezeControl() {
    Row() {
      Column() {
        Text('GNSS 锚点冻结')
          .fontSize(15)
          .fontWeight(FontWeight.Medium)
          .fontColor('#E2E8F0');
        Text(this.lastMarkedEvent === '入隧' ? '已冻结：不保证提高精度' :
          (this.lastMarkedEvent === '出隧' ? '已恢复 GNSS 锚点刷新' :
            (this.tunnelSwitchOn ? '已冻结：不再刷新锚点，可能增加漂移' :
              '仅供对照实验\n停止刷新锚点，可能增加漂移')))
          .fontSize(11)
          .fontColor(this.tunnelSwitchOn ? '#B8C2D0' : '#94A3B8')
          .lineHeight(16)
          .maxLines(2)
          .margin({ top: 6 });
      }
      .alignItems(HorizontalAlign.Start)
      .layoutWeight(1);

      Toggle({ type: ToggleType.Switch, isOn: this.tunnelSwitchOn })
        .selectedColor('#F59E0B')
        .accessibilityText(this.tunnelSwitchOn ? '关闭 GNSS 锚点冻结，恢复刷新' :
          '开启 GNSS 锚点冻结，仅供对照实验')
        .scale({ x: 1.25, y: 1.25 })
        .margin({ right: 8 })
        .onChange((isOn: boolean) => {
          this.setTunnelStateFromSwitch(isOn);
        });
    }
    .width('100%')
    .height(78)
    .padding({ left: 4, right: 4 })
    .alignItems(VerticalAlign.Center);
  }

  @Builder
  private ResearchPanel() {
    Column({ space: 10 }) {
      Row() {
        Text(this.researchStatus.running ? '记录中' : '研究记录')
          .fontSize(16)
          .fontWeight(FontWeight.Bold)
          .fontColor('#F8FAFC')
          .layoutWeight(1);
        Text(this.researchStatus.sampleCount > 0 ? `${this.researchStatus.sampleCount} 条` : '未记录')
          .fontSize(12)
          .fontColor(this.tunnelSwitchOn ? '#B8C2D0' : '#CBD5E1');
      }
      .width('100%');

      Row({ space: 8 }) {
        Button({ type: ButtonType.Capsule, stateEffect: !this.researchExportInProgress }) {
          Text(this.researchStatus.running ? '停止记录' : '开始记录')
            .fontSize(14)
            .fontColor(this.researchStatus.running ? '#F8FAFC' : '#0B1020')
        }
          .height(40)
          .width(0)
          .layoutWeight(1)
          .focusable(!this.researchExportInProgress)
          .hitTestBehavior(this.researchExportInProgress ? HitTestMode.BLOCK_DESCENDANTS : HitTestMode.Default)
          .accessibilityText(this.researchExportInProgress ? '研究记录，不可用' :
            (this.researchStatus.running ? '停止记录' : '开始记录'))
          .backgroundColor(this.researchStatus.running ? '#DC2626' : '#4FD1C5')
          .onClick(() => {
            this.toggleResearchRecording();
          });

        Button({
          type: ButtonType.Capsule,
          stateEffect: this.hasResearchLog() && !this.researchStatus.running && !this.researchExportInProgress
        }) {
          Text(this.researchExportInProgress ? '导出中' : '导出')
            .fontSize(14)
          .fontColor(this.hasResearchLog() && !this.researchStatus.running && !this.researchExportInProgress ? '#0B1020' : '#94A3B8')
        }
          .height(40)
          .width(86)
          .focusable(this.hasResearchLog() && !this.researchStatus.running && !this.researchExportInProgress)
          .hitTestBehavior(this.hasResearchLog() && !this.researchStatus.running && !this.researchExportInProgress ?
            HitTestMode.Default : HitTestMode.BLOCK_DESCENDANTS)
          .accessibilityText(this.hasResearchLog() && !this.researchStatus.running && !this.researchExportInProgress ?
            '导出' : '导出，不可用')
          .backgroundColor(this.hasResearchLog() && !this.researchStatus.running && !this.researchExportInProgress ? '#F59E0B' : '#1E293B')
          .onClick(() => {
            this.exportResearchLog();
          });
      }
      .width('100%');

      if (this.researchStatus.integrityWarning) {
        Text(this.researchStatus.lastExportPath || '日志可能不完整，导出时将标注 INCOMPLETE')
          .fontSize(12)
          .fontColor('#F87171')
          .maxLines(2)
          .width('100%');
      }

      Divider()
        .strokeWidth(1)
        .color(this.tunnelSwitchOn ? '#8E6A35' : '#475569');

      this.AnchorFreezeControl();

      Row() {
        Text(this.locationStatus)
          .fontSize(12)
          .fontWeight(FontWeight.Medium)
          .fontColor(this.tunnelSwitchOn ? '#B8C2D0' : '#94A3B8')
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis });
      }
      .width('100%')
      .height(32)
      .justifyContent(FlexAlign.Center);
    }
    .width('100%')
    .padding(12)
    .backgroundColor(this.tunnelSwitchOn ? '#251E16' : '#0F172A')
    .border({ width: 1, color: this.tunnelSwitchOn ? '#A36D22' : '#1E293B' })
    .borderRadius(8);
  }

  build() {
    Stack() {
      Scroll() {
        Column({ space: 12 }) {
          this.Header();
          Stack() {
            Column({ space: 12 }) {
              this.SpeedPanel();
              this.StatsGrid();
            }
            .width('100%')
            .onAreaChange((_: Area, newArea: Area) => {
              this.speedStatsHeight = newArea.height as number;
            });
            if (this.showPlacementWarning) {
              Column() {
                Column() {
                  Text('请稳定放置')
                    .fontSize(26)
                    .fontWeight(FontWeight.Bold)
                    .fontColor('#FFFFFF')
                    .letterSpacing(3);
                  Text('请将设备稳定放置，\n停车时重新开始测速，\n设备移动会中止测速。')
                    .fontSize(15)
                    .fontColor('#FECACA')
                    .lineHeight(26)
                    .margin({ top: 14 });
                }
                .width('100%')
                .layoutWeight(1)
                .justifyContent(FlexAlign.Center);
                Button({ type: ButtonType.Normal, stateEffect: !this.navigationInProgress }) {
                  Text('改用灯光打点测速')
                    .fontSize(14)
                    .fontWeight(FontWeight.Medium)
                    .fontColor('#FFFFFF')
                    .maxLines(1)
                    .textOverflow({ overflow: TextOverflow.Ellipsis });
                }
                  .width('58%')
                  .height(42)
                  .backgroundColor('#DC2626')
                  .border({ width: 1, color: '#FECACA' })
                  .borderRadius(8)
                  .margin({ bottom: 47 })
                  .focusable(!this.navigationInProgress)
                  .hitTestBehavior(this.navigationInProgress ? HitTestMode.BLOCK_DESCENDANTS : HitTestMode.Default)
                  .accessibilityText(this.navigationInProgress ? '改用灯光打点测速，不可用' : '改用灯光打点测速')
                  .onClick(() => {
                    this.switchToTunnelLightSpeed();
                  });
              }
              .width('100%')
              .height(this.speedStatsHeight)
              .padding({ top: 16, bottom: 16 })
              .alignItems(HorizontalAlign.Center)
              .backgroundColor('#DC2626')
              .borderRadius(8);
            }
          }
          .width('100%');
          this.ControlPanel();
          this.ResearchPanel();
          this.SensorNotice();
        }
        .width('100%')
        .padding({ left: 16, right: 16, top: 18, bottom: 8 });
      }
      .width('100%')
      .height('100%')
      .backgroundColor('#0B1020')
      .edgeEffect(EdgeEffect.Spring)
      .scrollBar(BarState.Off);
    }
    .width('100%')
    .height('100%')
    .backgroundColor('#0B1020')
    .expandSafeArea([SafeAreaType.SYSTEM], [SafeAreaEdge.TOP, SafeAreaEdge.BOTTOM]);
  }
}
