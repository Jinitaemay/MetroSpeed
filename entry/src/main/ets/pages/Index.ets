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
    lastEvent: '',
    lastExportPath: ''
  };
}

@Entry
@Component
struct Index {
  private estimator: SpeedEstimator = new SpeedEstimator();
  private sensorController: SensorController = new SensorController();
  private locationController: LocationController = new LocationController();
  private researchRecorder: ResearchRecorder = new ResearchRecorder();
  private researchSensorActive: boolean = false;

  @State private stats: RunStats = emptyStats();
  @State private isRunning: boolean = false;
  @State private sensorStatus: string = '传感器未启动';
  @State private locationStatus: string = '定位未启动';
  @State private researchStatus: ResearchRecorderStatus = emptyResearchStatus();
  @State private lastMarkedEvent: string = '';
  @State private tunnelSwitchOn: boolean = false;
  @State private pureInertialSpeedKmh: number = 0;
  @State private gnssSpeedKmh: number = 0;
  @State private isHandheld: boolean = false;
  @State private speedStatsHeight: number = 0;
  private wasMeasuringBeforeBackground: boolean = false;
  private backgroundHandlerRegistered: boolean = false;
  private tunnelState: 'unknown' | 'outside' | 'inside' = 'unknown';
  private feedbackUntilMs: number = 0;
  private lastAnchorGnssMps: number = 0;
  private lastAnchorInertialMps: number = 0;
  private hasGnssAnchor: boolean = false;
  private gnssSpeedAccuracyMps: number = 0;
  private measurementLocationActive: boolean = false;
  private inertialSpeedHistory: SpeedHistoryEntry[] = [];
  private readonly INERTIAL_HISTORY_SIZE: number = 5;
  private readonly GNSS_LAG_MS: number = 40;

  private gyroBuf: GyroSample[] = [];
  private readonly GYRO_WINDOW: number = 40;
  private readonly GYRO_RMS_THRESHOLD: number = 0.5;
  private readonly ZCR_THRESHOLD: number = 5.0;
  private readonly HANDHELD_ACTIVATE: number = 40;
  private handheldSuspectStreak: number = 0;

  private onBgStopSensors(): void {
    this.wasMeasuringBeforeBackground = this.isRunning;
    this.sensorController.stop();
    this.locationController.stop();
  }

  private onBgRestoreSensors(): void {
    if (this.wasMeasuringBeforeBackground && this.isRunning) {
      this.sensorController.start((frame: SensorFrame) => {
        this.onSensorFrame(frame);
      }, (message: string) => {
        this.sensorStatus = message;
      });
      if (this.researchSensorActive) {
        this.sensorController.startAuxiliarySensors();
      }
      this.startLocationForAll();
    }
    this.wasMeasuringBeforeBackground = false;
  }

  aboutToAppear(): void {
    if (!this.backgroundHandlerRegistered) {
      this.backgroundHandlerRegistered = true;
      emitter.on({ eventId: 100 }, (data: emitter.EventData) => {
        const actionData: Record<string, string> = data.data as Record<string, string>;
        if (actionData && actionData.action === 'stopSensors') {
          this.onBgStopSensors();
        } else if (actionData && actionData.action === 'restoreSensors') {
          this.onBgRestoreSensors();
        }
      });
    }
    const context = this.getAbilityContext();
    if (context) {
      this.researchStatus = this.researchRecorder.attachContext(context);
    }
  }

  aboutToDisappear(): void {
    this.researchStatus = this.researchRecorder.flush();
    this.sensorController.stop();
    this.locationController.stop();
  }

  private async startMeasurement(): Promise<void> {
    if (this.isRunning) {
      return;
    }

    const now = Date.now();
    this.stats = this.estimator.start(now);
    this.isRunning = true;
    this.hasGnssAnchor = false;
    this.isHandheld = false;
    this.gyroBuf = [];
    this.handheldSuspectStreak = 0;
    this.sensorStatus = '正在启动传感器';
    this.researchStatus = this.researchRecorder.beginMeasurement(now, 'measurement started');
    this.researchSensorActive = false;
    const sensorsStarted = this.sensorController.start((frame: SensorFrame) => {
      this.onSensorFrame(frame);
    }, (message: string) => {
      this.sensorStatus = message;
    });
    if (!sensorsStarted) {
      this.isRunning = false;
      this.sensorStatus = '传感器未启动';
      this.stats = this.estimator.reset(now);
      this.researchStatus = this.researchRecorder.cancelMeasurement(now, 'sensor start failed');
      if (this.researchStatus.running) {
        this.startResearchSensors();
      }
      return;
    }
    if (this.researchStatus.running) {
      this.startResearchSensors();
    }
    await this.requestLocationPermission();
    this.startLocationForMeasurement();
  }

  private startLocationForMeasurement(): void {
    if (this.measurementLocationActive) {
      return;
    }
    this.measurementLocationActive = true;
    this.startLocationForAll();
  }

  private startLocationForAll(forRecording?: boolean): void {
    this.locationController.start(
      (location: LocationSnapshot) => {
        if (this.researchStatus.running) {
          this.researchRecorder.updateLocation(location);
        }
        const satelliteText = location.satelliteCount === null ? '' : ` 星${location.satelliteFixCount}/${location.satelliteCount}`;
        this.updateLocationStatus(`定位 ${location.accuracy.toFixed(0)}m ${location.speedMps.toFixed(1)}m/s${satelliteText}`);
        this.researchStatus = this.researchRecorder.status();
        this.gnssSpeedKmh = Math.max(0, location.speedMps * 3.6);
        this.gnssSpeedAccuracyMps = location.speedAccuracyMps ?? 0;

        if (this.isRunning) {
          const speedAcc = location.speedAccuracyMps;
          const src = location.locationSourceType;
          const state = this.estimator.getMotionState();
          const inVibration = state === MotionState.StrongVibration || state === MotionState.ConductionVibration;
          if (speedAcc !== null && speedAcc > 0 && src !== null && (src === 1 || src === 4) && this.tunnelState !== 'inside' && !inVibration) {
            this.lastAnchorGnssMps = location.speedMps;
            this.lastAnchorInertialMps = this.getInertialSpeedAtLag(this.GNSS_LAG_MS);
            this.hasGnssAnchor = true;
          }
        }
      },
      (satellite: SatelliteSnapshot) => {
        if (this.researchStatus.running) {
          this.researchRecorder.updateSatellite(satellite);
        }
        this.researchStatus = this.researchRecorder.status();
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

    const now = Date.now();
    this.sensorController.stop();
    this.sensorStatus = '传感器已停止';
    this.researchStatus = this.researchRecorder.endMeasurement(now,
      `reason=${reason} speed=${this.stats.currentSpeedKmh.toFixed(2)}km/h`);
    this.stats = this.estimator.stop(now);
    this.isRunning = false;
    this.hasGnssAnchor = false;
    this.isHandheld = false;
    this.researchSensorActive = false;
    this.stopLocationIfUnused();
    if (this.researchStatus.running) {
      this.startResearchSensors();
    }
  }

  private toggleMeasurement(): void {
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
    this.lastAnchorGnssMps = 0;
    this.lastAnchorInertialMps = 0;
    this.hasGnssAnchor = true;
    this.gnssSpeedKmh = 0;
    if (this.researchStatus.running) {
      this.researchStatus = this.researchRecorder.markEvent(
        now,
        '停车校准',
        `beforeSpeed=${speedBeforeCalibration.toFixed(2)}km/h confidence=${confidenceBeforeCalibration.toFixed(2)} state=${stateBeforeCalibration}`
      );
    }
  }

  private onSensorFrame(frame: SensorFrame): void {
    const nextFrame: EstimatorFrame = this.estimator.ingest(frame);
    this.researchRecorder.recordSensorFrame(frame);

    if (this.isInitialCalibrationRejected(nextFrame.stats)) {
      this.abortStartCalibration();
      return;
    }

    if (frame.gyroscope) {
      this.gyroBuf.push({ gx: frame.gyroscope.x, gy: frame.gyroscope.y, gz: frame.gyroscope.z, ts: frame.timestampMs });
      while (this.gyroBuf.length > this.GYRO_WINDOW * 2) {
        this.gyroBuf.shift();
      }
      this.checkHandheld();
      if (this.isHandheld) {
        return;
      }
    }

    this.stats = nextFrame.stats;
    this.pureInertialSpeedKmh = this.stats.currentSpeedKmh;

    const pure = (!this.hasGnssAnchor || this.gnssSpeedKmh < this.gnssSpeedAccuracyMps * 3.6) ? 1 : 0;
    this.estimator.setPureMode(pure);

    if (this.hasGnssAnchor) {
      const pureSpeedKmh = this.stats.currentSpeedKmh;
      const currentInertial = this.estimator.getVelocityMps();
      const delta = currentInertial - this.lastAnchorInertialMps;
      const anchorSpeedKmh = Math.max(0, (this.lastAnchorGnssMps + delta) * 3.6);
      if (this.gnssSpeedKmh < this.gnssSpeedAccuracyMps * 3.6) {
        this.stats.currentSpeedKmh = pureSpeedKmh;
      } else {
        this.stats.currentSpeedKmh = anchorSpeedKmh;
      }
    }
    this.researchRecorder.updateEstimator(nextFrame.sample, nextFrame.stats, nextFrame.calibration);
    this.researchStatus = this.researchRecorder.status();

    const now = Date.now();
    const currentInertialSpeed = this.estimator.getVelocityMps();
    this.inertialSpeedHistory.push({ t: now, v: currentInertialSpeed });
    while (this.inertialSpeedHistory.length > this.INERTIAL_HISTORY_SIZE) {
      this.inertialSpeedHistory.shift();
    }
  }

  private getInertialSpeedAtLag(delayMs: number): number {
    const target = Date.now() - delayMs;
    let best = this.estimator.getVelocityMps();
    let bestDiff = Infinity;
    for (const entry of this.inertialSpeedHistory) {
      const diff = Math.abs(entry.t - target);
      if (diff < bestDiff) {
        best = entry.v;
        bestDiff = diff;
      }
    }
    return best;
  }

  private checkHandheld(): void {
    if (this.gyroBuf.length < this.GYRO_WINDOW) {
      return;
    }

    let ss = 0;
    for (let i = 0; i < this.GYRO_WINDOW; i++) {
      const v = this.gyroBuf[this.gyroBuf.length - this.GYRO_WINDOW + i];
      ss += v.gx * v.gx + v.gy * v.gy + v.gz * v.gz;
    }
    const rms = Math.sqrt(ss / this.GYRO_WINDOW);

    let zc = 0;
    const base = this.gyroBuf.length - this.GYRO_WINDOW;
    let pgx = this.gyroBuf[base].gx;
    let pgy = this.gyroBuf[base].gy;
    let pgz = this.gyroBuf[base].gz;
    for (let i = base + 1; i < this.gyroBuf.length; i++) {
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
    const windowDurationMs = this.gyroBuf[this.gyroBuf.length - 1].ts
      - this.gyroBuf[this.gyroBuf.length - this.GYRO_WINDOW].ts;
    const zcr = zc / (Math.max(windowDurationMs, 1) / 1000);

    if (rms > this.GYRO_RMS_THRESHOLD && zcr > this.ZCR_THRESHOLD) {
      this.handheldSuspectStreak++;
    } else {
      this.handheldSuspectStreak = 0;
    }

    if (this.handheldSuspectStreak >= this.HANDHELD_ACTIVATE && !this.isHandheld) {
      this.stopMeasurement('handheld');
      this.isHandheld = true;
      this.handheldSuspectStreak = 0;
    }
  }

  private isInitialCalibrationRejected(stats: RunStats): boolean {
    return stats.calibrationCount === 1 && stats.calibrationRejected;
  }

  private abortStartCalibration(): void {
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
    this.isRunning = false;
    this.isHandheld = false;
    this.sensorStatus = '开始失败：初始校准不稳';
    this.researchStatus = this.researchRecorder.cancelMeasurement(now, resetStats.statusText);
    if (this.researchStatus.running) {
      this.startResearchSensors();
    }
  }

  private async toggleResearchRecording(): Promise<void> {
    const now = Date.now();
    if (this.researchStatus.running) {
      this.stopResearchSensorsIfIdle();
      if (!this.isRunning) {
        this.sensorStatus = '传感器未启动';
      }
      this.researchStatus = this.researchRecorder.stop(now);
      this.stopLocationIfUnused();
      BackgroundState.recordingActive = false;
      return;
    }

    const context = this.getAbilityContext();
    if (!context) {
      this.locationStatus = 'context unavailable';
      return;
    }

    this.researchStatus = this.researchRecorder.start(now, context);
    if (this.isRunning) {
      this.researchStatus = this.researchRecorder.noteMeasurementAlreadyActive(
        now,
        `recording started while measurement active; elapsed=${this.stats.elapsedMs}ms`
      );
    }
    this.feedbackUntilMs = 0;
    const permissionGranted = await this.requestLocationPermission();
    this.researchStatus = this.researchRecorder.markSystemEvent(
      Date.now(),
      permissionGranted ? '定位权限可用' : '定位权限不可用',
      this.locationStatus
    );
    this.startLocationForAll(true);
    const locationStarted = this.locationController.isRunning;

    if (!locationStarted) {
      this.locationStatus = '定位未启动，仍记录传感器';
      this.researchStatus = this.researchRecorder.markSystemEvent(Date.now(), '定位启动失败', this.locationStatus);
    } else {
      this.researchStatus = this.researchRecorder.markSystemEvent(Date.now(), '定位启动成功', this.locationStatus);
    }

    BackgroundState.recordingActive = true;
    this.startResearchSensors();
  }

  private startResearchSensors(): void {
    if (this.researchSensorActive) {
      return;
    }

    if (this.isRunning) {
      this.sensorController.startAuxiliarySensors();
      this.researchSensorActive = true;
      return;
    }

    this.researchRecorder.clearEstimatorSnapshot();
    const started = this.sensorController.start((frame: SensorFrame) => {
      this.onResearchSensorFrame(frame);
    }, (message: string) => {
      this.sensorStatus = message;
    });
    if (started) {
      this.sensorController.startAuxiliarySensors();
    }
    this.researchSensorActive = started;
    if (!started) {
      this.sensorStatus = '研究记录传感器未启动';
    }
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
  }

  private getAbilityContext(): common.UIAbilityContext | undefined {
    return this.getUIContext().getHostContext() as common.UIAbilityContext | undefined;
  }

  private async requestLocationPermission(): Promise<boolean> {
    const context = this.getAbilityContext();
    if (!context) {
      this.locationStatus = '无法获取应用上下文';
      return false;
    }

    try {
      const atManager = abilityAccessCtrl.createAtManager();
      const result = await atManager.requestPermissionsFromUser(context, [
        'ohos.permission.LOCATION',
        'ohos.permission.APPROXIMATELY_LOCATION'
      ]);
      if (result.authResults.length < 1) {
        this.locationStatus = '定位权限未返回结果';
        return false;
      }
      for (let i = 0; i < result.authResults.length; i++) {
        if (result.authResults[i] !== 0) {
          this.locationStatus = '定位权限未授予';
          return false;
        }
      }
      this.locationStatus = '定位权限可用';
      return true;
    } catch (error) {
      this.locationStatus = '定位权限未授予';
      return false;
    }
  }

  private markResearchEventWithNotes(event: string, notes: string): void {
    this.researchStatus = this.researchRecorder.markEvent(Date.now(), event, notes);
    this.flashMarkedEvent(event);
    this.locationStatus = `已标记：${event}`;
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
    const context = this.getAbilityContext();
    if (!context) {
      this.locationStatus = '无法获取应用上下文';
      return;
    }

    try {
      this.locationStatus = '请选择导出位置';
      this.researchStatus = await this.researchRecorder.exportJsonl(context);
    } catch (error) {
      this.locationStatus = '导出失败';
    }
  }

  private hasResearchLog(): boolean {
    return this.researchStatus.sampleCount > 0 || this.researchStatus.hasExportableLog;
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
      Column() {
        Text('地铁测速')
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
      Button() {
        Text(this.isRunning ? '停止' : '开始')
          .fontSize(16)
          .fontColor(this.isRunning ? '#F8FAFC' : '#0B1020')
      }
        .height(48)
        .width(0)
        .layoutWeight(1)
        .backgroundColor(this.isRunning ? '#EF4444' : '#4FD1C5')
        .borderRadius(8)
        .onClick(() => {
          this.toggleMeasurement();
        });

      Button() {
        Text('停车校准')
          .fontSize(16)
          .fontColor(this.isRunning ? '#0B1020' : '#94A3B8')
      }
        .height(48)
        .width(0)
        .layoutWeight(1)
        .enabled(this.isRunning)
        .backgroundColor(this.isRunning ? '#F59E0B' : '#1E293B')
        .borderRadius(8)
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
        .fontSize(13)
        .fontWeight(FontWeight.Medium)
        .fontColor('#CBD5E1')
        .maxLines(2)
        .textOverflow({ overflow: TextOverflow.Ellipsis });
      Text('此应用不支持手持使用，弯道、滑动和长时间未校准会降低精度，停车时点停车校准。')
        .fontSize(12)
        .fontColor('#94A3B8')
        .lineHeight(18)
        .maxLines(3)
        .margin({ top: 6 });
    }
    .width('100%')
    .padding(12)
    .backgroundColor('#0F172A')
    .borderRadius(8);
  }

  @Builder
  private ResearchPanel() {
    Column({ space: 10 }) {
      Row() {
        Text(this.researchStatus.running ? '记录中' : '研究记录')
          .fontSize(15)
          .fontWeight(FontWeight.Medium)
          .fontColor('#F8FAFC')
          .layoutWeight(1);
        Text(this.researchStatus.sampleCount > 0 ? `${this.researchStatus.sampleCount} 条` : '未记录')
          .fontSize(12)
          .fontColor('#94A3B8');
      }
      .width('100%');

      Row({ space: 8 }) {
        Button() {
          Text(this.researchStatus.running ? '停止记录' : '开始记录')
            .fontSize(14)
            .fontColor(this.researchStatus.running ? '#F8FAFC' : '#0B1020')
        }
          .height(40)
          .width(0)
          .layoutWeight(1)
          .backgroundColor(this.researchStatus.running ? '#EF4444' : '#4FD1C5')
          .borderRadius(8)
          .onClick(() => {
            this.toggleResearchRecording();
          });

        Button() {
          Text('导出')
            .fontSize(14)
            .fontColor(this.hasResearchLog() ? '#0B1020' : '#94A3B8')
        }
          .height(40)
          .width(86)
          .enabled(this.hasResearchLog())
          .backgroundColor(this.hasResearchLog() ? '#F59E0B' : '#1E293B')
          .borderRadius(8)
          .onClick(() => {
            this.exportResearchLog();
          });
      }
      .width('100%');

      Row() {
        Column() {
          Text(this.tunnelSwitchOn ? '隧道中' : '地面/未入隧')
            .fontSize(18)
            .fontWeight(FontWeight.Bold)
            .fontColor('#F8FAFC');
          Text(this.lastMarkedEvent === '入隧' || this.lastMarkedEvent === '出隧' ? `已标记：${this.lastMarkedEvent}` : '切换时写入入隧/出隧事件')
            .fontSize(12)
            .fontColor('#94A3B8')
            .margin({ top: 6 });
        }
        .alignItems(HorizontalAlign.Start)
        .layoutWeight(1);

        Toggle({ type: ToggleType.Switch, isOn: this.tunnelSwitchOn })
          .selectedColor('#F59E0B')
          .scale({ x: 1.25, y: 1.25 })
          .margin({ right: 8 })
          .onChange((isOn: boolean) => {
            this.setTunnelStateFromSwitch(isOn);
          });
      }
      .width('100%')
      .height(78)
      .padding({ left: 14, right: 14 })
      .backgroundColor(this.tunnelSwitchOn ? '#3A2711' : '#1F2937')
      .borderRadius(8)
      .alignItems(VerticalAlign.Center);

      Text(this.locationStatus)
        .fontSize(12)
        .fontColor('#94A3B8')
        .maxLines(1)
        .textOverflow({ overflow: TextOverflow.Ellipsis });
    }
    .width('100%')
    .padding(12)
    .backgroundColor('#0F172A')
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
              if (this.isHandheld) {
              Column() {
                Text('请稳定放置')
                  .fontSize(24)
                  .fontWeight(FontWeight.Bold)
                  .fontColor('#FFFFFF')
                  .letterSpacing(3);
                Text('请将设备稳定放置，\n停车时重新开始测速，\n设备移动会中止测速。')
                  .fontSize(14)
                  .fontColor('#FECACA')
                  .lineHeight(22)
                  .margin({ top: 10 });
              }
              .width('100%')
              .height(this.speedStatsHeight)
              .justifyContent(FlexAlign.Center)
              .backgroundColor('#DC2626')
              .borderRadius(8)
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
