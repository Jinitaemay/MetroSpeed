import fs from '@ohos.file.fs';
import picker from '@ohos.file.picker';
import common from '@ohos.app.ability.common';
import util from '@ohos.util';
import { BusinessError } from '@ohos.base';
import { batteryInfo, deviceInfo, thermal } from '@kit.BasicServicesKit';
import { fileManagerService } from '@kit.FileManagerServiceKit';
import { LocationSnapshot, SatelliteSnapshot } from './LocationController';
import {
  CalibrationState,
  RunStats,
  SensorCallbackSample,
  SensorFrame,
  SpeedSample,
  motionStateText
} from './SpeedTypes';

const ESTIMATOR_RECORD_INTERVAL_MS = 500;
const DEVICE_HEALTH_RECORD_INTERVAL_MS = 10000;
const RECORD_SCHEMA_VERSION = 17;
const APP_VERSION_CODE = 1786008552;
const ALGORITHM_VERSION = 'anchor-delta-20260804-r5';
const LEGACY_TEMP_LOG_FILE_NAME = 'research-current.jsonl';
const SESSION_LOG_PREFIX = 'research-session-';
const SESSION_LOG_SUFFIX = '.jsonl';
const DISCARDED_LOG_MARKER_SUFFIX = '.discarded';
const WRITE_BUFFER_LIMIT = 512;
const WRITE_BUFFER_MAX_AGE_MS = 1000;
const EXPORT_COPY_BUFFER_BYTES = 64 * 1024;
const EXPORT_YIELD_EVERY_CHUNKS = 8;
const UTF8_ENCODER = new util.TextEncoder();

export interface ResearchRecorderStatus {
  running: boolean;
  sessionId: string;
  sampleCount: number;
  hasExportableLog: boolean;
  integrityWarning: boolean;
  lastEvent: string;
  lastExportPath: string;
}

export type ResearchExportPhase = 'choosing' | 'copying' | 'syncing' | 'completed' | 'failed';

export interface ResearchExportProgress {
  phase: ResearchExportPhase;
  copiedBytes: number;
  totalBytes: number;
}

export type ResearchExportProgressCallback = (progress: ResearchExportProgress) => void;

interface ResearchRecord {
  timestampMs: number;
  sessionId: string;
  recordSchemaVersion: number;
  appVersionCode: number;
  algorithmVersion: string;
  recordSeq: number;
  recordType: string;
  event: string;
  measurementRunId: string | null;
  measurementActive: boolean;
  measurementStartMs: number | null;
  measurementElapsedMs: number | null;
  estimatorActive?: boolean;
  locationIntervalMs?: number | null;
  satelliteIntervalMs?: number | null;
  locationLat?: number | null;
  locationLon?: number | null;
  locationAltitude?: number | null;
  locationAccuracy?: number | null;
  locationAltitudeAccuracy?: number | null;
  locationSpeedMps?: number | null;
  locationSpeedAccuracyMps?: number | null;
  locationBearingDeg?: number | null;
  locationBearingAccuracyDeg?: number | null;
  locationTimeMs?: number | null;
  locationTimeSinceBootNs?: number | null;
  locationTimeUncertaintyNs?: number | null;
  locationSourceType?: number | null;
  locationAgeMs?: number | null;
  satelliteCount?: number | null;
  satelliteFixCount?: number | null;
  satelliteCn0Avg?: number | null;
  satelliteCn0Max?: number | null;
  satelliteCn0PositiveCount?: number | null;
  satelliteCn0PositiveAvg?: number | null;
  satelliteCn0UsedAvg?: number | null;
  filteredAccX?: number | null;
  filteredAccY?: number | null;
  filteredAccZ?: number | null;
  gravityX?: number | null;
  gravityY?: number | null;
  gravityZ?: number | null;
  mainAxisX?: number | null;
  mainAxisY?: number | null;
  mainAxisZ?: number | null;
  pureInertialSpeedKmh?: number;
  displaySpeedKmh?: number;
  estimatedSpeedKmh?: number;
  confidence?: number;
  motionState?: string;
  calibrationStatus?: string;
  notes?: string;
}

interface SensorResearchRecord {
  timestampMs: number;
  sessionId: string;
  recordSchemaVersion: number;
  appVersionCode: number;
  algorithmVersion: string;
  recordSeq: number;
  recordType: string;
  measurementRunId: string | null;
  measurementActive: boolean;
  measurementStartMs: number | null;
  measurementElapsedMs: number | null;
  sensorTimestamp: number | null;
  sensorIntervalMs: number | null;
  accX: number;
  accY: number;
  accZ: number;
  gyroX: number | null;
  gyroY: number | null;
  gyroZ: number | null;
  gyroscopeTimestamp: number | null;
  rotVecX: number | null;
  rotVecY: number | null;
  rotVecZ: number | null;
  rotVecW: number | null;
  rotVecTimestamp: number | null;
  magX: number | null;
  magY: number | null;
  magZ: number | null;
  magTimestamp: number | null;
}

interface SensorCallbackResearchRecord {
  timestampMs: number;
  sessionId: string;
  recordSchemaVersion: number;
  appVersionCode: number;
  algorithmVersion: string;
  recordSeq: number;
  recordType: string;
  measurementRunId: string | null;
  measurementActive: boolean;
  measurementStartMs: number | null;
  measurementElapsedMs: number | null;
  sensorType: string;
  sensorTimestamp: number | null;
  sensorIntervalMs: number | null;
  callbackIntervalMs: number | null;
  requestedIntervalNs: number;
  x?: number;
  y?: number;
  z?: number;
  w?: number;
  biasX?: number;
  biasY?: number;
  biasZ?: number;
}

interface DeviceHealthResearchRecord {
  timestampMs: number;
  sessionId: string;
  recordSchemaVersion: number;
  appVersionCode: number;
  algorithmVersion: string;
  recordSeq: number;
  recordType: string;
  measurementRunId: string | null;
  measurementActive: boolean;
  measurementStartMs: number | null;
  measurementElapsedMs: number | null;
  deviceHealthReason: string;
  batteryTemperatureC: number | null;
  thermalLevel: number | null;
}

interface SessionTarget {
  sessionId: string;
  path: string;
}

export class ResearchRecorder {
  private running: boolean = false;
  private sessionId: string = '';
  private recordCount: number = 0;
  private filesDir: string = '';
  private tempLogPath: string = '';
  private tempLogFile?: fs.File;
  private writeBuffer: string[] = [];
  private writeBufferStartMs: number = 0;
  private hasExportableLog: boolean = false;
  private exportInProgress: boolean = false;
  private lastEvent: string = '';
  private lastExportPath: string = '';
  private lastSample?: SpeedSample;
  private lastStats?: RunStats;
  private lastCalibration?: CalibrationState;
  private lastLocation?: LocationSnapshot;
  private lastSatellite?: SatelliteSnapshot;
  private lastEstimatorRecordMs: number = 0;
  private lastDeviceHealthRecordMs: number = 0;
  private recordSeq: number = 0;
  private previousSensorFrameMs: number = 0;
  private previousSensorTimestampNs: number | null = null;
  private previousCallbackMsByType: Map<string, number> = new Map<string, number>();
  private previousSensorTimestampByType: Map<string, number> = new Map<string, number>();
  private previousLocationMs: number = 0;
  private previousSatelliteMs: number = 0;
  private lastSensorIntervalMs: number | null = null;
  private lastLocationIntervalMs: number | null = null;
  private lastSatelliteIntervalMs: number | null = null;
  private measurementRunId: string | null = null;
  private measurementActive: boolean = false;
  private measurementStartMs: number = 0;
  private writeFailed: boolean = false;

  attachContext(context: common.UIAbilityContext): ResearchRecorderStatus {
    this.filesDir = context.filesDir;
    this.selectNewestPendingLog();
    this.refreshExportableLogState();
    this.restoreExportableLogSummary();
    return this.status();
  }

  start(timestampMs: number, context: common.UIAbilityContext): ResearchRecorderStatus {
    if (this.exportInProgress) {
      this.lastExportPath = '正在导出，请稍候';
      return this.status();
    }
    this.closeTempLogFile();
    const preservedMeasurementRunId = this.measurementRunId;
    const preservedMeasurementActive = this.measurementActive;
    const preservedMeasurementStartMs = this.measurementStartMs;
    this.filesDir = context.filesDir;
    this.running = true;
    const sessionTarget = this.newSessionTarget(timestampMs);
    this.sessionId = sessionTarget.sessionId;
    this.tempLogPath = sessionTarget.path;
    this.recordCount = 0;
    this.writeBuffer = [];
    this.writeBufferStartMs = 0;
    this.hasExportableLog = false;
    this.lastEvent = 'start_record';
    this.lastExportPath = '';
    this.lastSample = undefined;
    this.lastStats = undefined;
    this.lastCalibration = undefined;
    this.lastLocation = undefined;
    this.lastSatellite = undefined;
    this.lastEstimatorRecordMs = 0;
    this.lastDeviceHealthRecordMs = 0;
    this.recordSeq = 0;
    this.previousSensorFrameMs = 0;
    this.previousSensorTimestampNs = null;
    this.previousCallbackMsByType.clear();
    this.previousSensorTimestampByType.clear();
    this.previousLocationMs = 0;
    this.previousSatelliteMs = 0;
    this.lastSensorIntervalMs = null;
    this.lastLocationIntervalMs = null;
    this.lastSatelliteIntervalMs = null;
    this.measurementRunId = preservedMeasurementActive ? preservedMeasurementRunId : null;
    this.measurementActive = preservedMeasurementActive;
    this.measurementStartMs = preservedMeasurementActive ? preservedMeasurementStartMs : 0;
    this.writeFailed = false;
    if (!this.openTempLogForNewSession()) {
      return this.rollbackFailedSessionStart(sessionTarget.path, '记录文件创建失败，旧记录已保留');
    }
    this.appendRecord(timestampMs, 'start_record', 'research recording started');
    this.appendDeviceHealthRecord(timestampMs, 'start');
    // Persist the session header once so a crash before the first large-buffer
    // flush is still recoverable and will be marked incomplete on next launch.
    this.flushTempLogFile();
    this.refreshExportableLogState();
    if (!this.running || !this.tempLogFile || this.writeFailed || !this.hasExportableLog) {
      return this.rollbackFailedSessionStart(sessionTarget.path, '记录文件写入失败，旧记录已保留');
    }
    // Only retire the preceding session after the new header is durably stored.
    this.clearSupersededPendingLogs(sessionTarget.path);
    return this.status();
  }

  stop(timestampMs: number): ResearchRecorderStatus {
    if (this.running) {
      this.appendDeviceHealthRecord(timestampMs, 'stop');
      this.appendRecord(timestampMs, 'stop_record', 'research recording stopped');
    }
    this.running = false;
    this.flushTempLogFile();
    this.closeTempLogFile();
    this.refreshExportableLogState();
    if (!this.writeFailed) {
      this.lastEvent = 'stop_record';
    }
    return this.status();
  }

  flush(): ResearchRecorderStatus {
    this.flushTempLogFile();
    this.refreshExportableLogState();
    return this.status();
  }

  beginMeasurement(timestampMs: number, notes: string = 'measurement started'): ResearchRecorderStatus {
    this.measurementActive = true;
    this.measurementRunId = `measurement-${timestampMs}`;
    this.measurementStartMs = timestampMs;
    if (this.running) {
      this.lastEvent = '开始测速';
      this.appendRecord(timestampMs, '开始测速', notes);
    }
    return this.status();
  }

  noteMeasurementAlreadyActive(timestampMs: number, notes: string = 'measurement already active when research recording started'): ResearchRecorderStatus {
    if (!this.measurementActive) {
      this.measurementActive = true;
      this.measurementRunId = `measurement-${timestampMs}`;
      this.measurementStartMs = timestampMs;
    }
    if (this.running) {
      this.lastEvent = '测速已在运行';
      this.appendRecord(timestampMs, '测速已在运行', notes);
    }
    return this.status();
  }

  endMeasurement(timestampMs: number, notes: string = 'measurement stopped'): ResearchRecorderStatus {
    if (this.running) {
      this.lastEvent = '停止测速';
      this.appendRecord(timestampMs, '停止测速', notes);
    }
    this.measurementActive = false;
    this.measurementRunId = null;
    this.measurementStartMs = 0;
    return this.status();
  }

  cancelMeasurement(timestampMs: number, notes: string): ResearchRecorderStatus {
    if (this.running) {
      this.lastEvent = '开始失败';
      this.appendRecord(timestampMs, '开始失败', notes);
    }
    this.measurementActive = false;
    this.measurementRunId = null;
    this.measurementStartMs = 0;
    return this.status();
  }

  clearEstimatorSnapshot(): void {
    this.lastSample = undefined;
    this.lastStats = undefined;
    this.lastCalibration = undefined;
  }

  recordSensorFrame(frame: SensorFrame): void {
    this.updateSensorInterval(frame);
    if (!this.running) {
      return;
    }
    this.maybeAppendDeviceHealthRecord(frame.timestampMs);
    this.appendSensorRecord(frame);
  }

  recordSensorCallback(sample: SensorCallbackSample): void {
    if (!this.running) {
      return;
    }

    const previousCallbackMs = this.previousCallbackMsByType.get(sample.sensorType);
    const callbackIntervalMs = previousCallbackMs === undefined
      ? null
      : Math.max(0, sample.callbackTimestampMs - previousCallbackMs);
    this.previousCallbackMsByType.set(sample.sensorType, sample.callbackTimestampMs);

    const previousSensorTimestamp = this.previousSensorTimestampByType.get(sample.sensorType);
    const sensorIntervalMs = sample.sensorTimestamp === null || previousSensorTimestamp === undefined
      ? null
      : (sample.sensorTimestamp - previousSensorTimestamp) / 1000000;
    if (sample.sensorTimestamp !== null) {
      this.previousSensorTimestampByType.set(sample.sensorType, sample.sensorTimestamp);
    }

    this.appendSensorCallbackRecord(sample, callbackIntervalMs, sensorIntervalMs);
  }

  updateLocation(location: LocationSnapshot): void {
    this.lastLocationIntervalMs = this.previousLocationMs > 0 ? location.timestampMs - this.previousLocationMs : null;
    this.previousLocationMs = location.timestampMs;
    this.lastLocation = location;
    if (this.running) {
      this.appendRecord(location.timestampMs, 'location', '');
    }
  }

  updateSatellite(satellite: SatelliteSnapshot): void {
    this.lastSatelliteIntervalMs = this.previousSatelliteMs > 0 ? satellite.timestampMs - this.previousSatelliteMs : null;
    this.previousSatelliteMs = satellite.timestampMs;
    this.lastSatellite = satellite;
    if (this.running) {
      this.appendRecord(satellite.timestampMs, 'satellite', '');
    }
  }

  updateEstimator(sample: SpeedSample, stats: RunStats, calibration: CalibrationState): void {
    this.lastSample = sample;
    this.lastStats = stats;
    this.lastCalibration = calibration;
    if (!this.running) {
      return;
    }
    if (sample.timestampMs - this.lastEstimatorRecordMs < ESTIMATOR_RECORD_INTERVAL_MS) {
      return;
    }

    this.lastEstimatorRecordMs = sample.timestampMs;
    this.appendRecord(sample.timestampMs, 'estimator', '');
  }

  markEvent(timestampMs: number, event: string, notes: string = ''): ResearchRecorderStatus {
    this.lastEvent = event;
    this.appendRecord(timestampMs, event, notes);
    return this.status();
  }

  markSystemEvent(timestampMs: number, event: string, notes: string = ''): ResearchRecorderStatus {
    if (this.running) {
      this.lastEvent = event;
      this.appendRecord(timestampMs, event, notes);
    }
    return this.status();
  }

  async exportJsonl(
    context: common.UIAbilityContext,
    onProgress?: ResearchExportProgressCallback
  ): Promise<ResearchRecorderStatus> {
    if (this.exportInProgress) {
      this.lastExportPath = '导出正在进行';
      return this.status();
    }
    if (this.running) {
      this.lastExportPath = '请先停止记录再导出';
      return this.status();
    }
    this.filesDir = context.filesDir;
    if (this.tempLogPath.length < 1) {
      this.selectNewestPendingLog();
    }
    this.refreshExportableLogState();
    if (!this.hasExportableLog) {
      this.selectNewestPendingLog();
      this.refreshExportableLogState();
    }
    this.restoreExportableLogSummary();
    if (!this.hasExportableLog) {
      this.lastExportPath = '没有可导出的记录';
      return this.status();
    }

    const sourcePath = this.tempLogPath;
    const sourceSessionId = this.sessionId;
    const sourceIncomplete = this.writeFailed;
    const completenessSuffix = sourceIncomplete ? '-INCOMPLETE' : '';
    const exportAttemptMs = Date.now();
    const fileName = `${sourceSessionId || `session-${exportAttemptMs}`}${completenessSuffix}` +
      `-export-${exportAttemptMs}.jsonl`;
    this.exportInProgress = true;
    let targetUri = '';
    let copiedBytes = 0;
    let totalBytes = 0;
    const copyProgress = (progress: ResearchExportProgress): void => {
      copiedBytes = progress.copiedBytes;
      totalBytes = progress.totalBytes;
      this.emitExportProgress(onProgress, progress);
    };
    try {
      this.emitExportProgress(onProgress, {
        phase: 'choosing',
        copiedBytes: 0,
        totalBytes: 0
      });
      const documentPicker = new picker.DocumentViewPicker(context);
      const saveOptions: picker.DocumentSaveOptions = {
        newFileNames: [fileName]
      };
      if (deviceInfo.sdkApiVersion >= 23) {
        // API 23+ grants temporary create/delete access without pre-creating a
        // zero-byte target. A picker URI cannot be atomically renamed through
        // the sandbox-only fs.rename API, so an abrupt process/device stop can
        // still leave an incomplete target after creation has begun.
        saveOptions.autoCreateEmptyFile = false;
      }
      const uris = await documentPicker.save(saveOptions);
      if (uris.length < 1) {
        this.lastExportPath = '已取消导出';
        return this.status();
      }
      targetUri = uris[0];

      if (this.running || this.tempLogPath !== sourcePath) {
        const targetRemoved = await this.cleanupFailedExportTarget(targetUri, sourcePath);
        this.lastExportPath = this.failedExportMessage('记录状态已变化，请重试导出', targetRemoved);
        this.emitExportProgress(onProgress, {
          phase: 'failed',
          copiedBytes,
          totalBytes
        });
        return this.status();
      }
      this.flushTempLogFile();
      this.refreshExportableLogState();
      if (!this.hasExportableLog) {
        const targetRemoved = await this.cleanupFailedExportTarget(targetUri, sourcePath);
        this.lastExportPath = this.failedExportMessage('没有可导出的记录', targetRemoved);
        this.emitExportProgress(onProgress, {
          phase: 'failed',
          copiedBytes,
          totalBytes
        });
        return this.status();
      }

      if (!await this.copyTempLogToUri(targetUri, sourcePath, copyProgress)) {
        const targetRemoved = await this.cleanupFailedExportTarget(targetUri, sourcePath);
        this.lastExportPath = this.failedExportMessage('导出复制失败', targetRemoved);
        this.emitExportProgress(onProgress, {
          phase: 'failed',
          copiedBytes,
          totalBytes
        });
        return this.status();
      }
      // Export is a copy operation. Keep the latest source log (and its
      // integrity marker, if any) so the user can export it again. A new
      // recording remains the only operation that overwrites the local log.
      this.refreshExportableLogState();
      this.lastExportPath = sourceIncomplete
        ? `已导出：${fileName}（日志不完整，仍可再次导出）`
        : `已导出：${fileName}（仍可再次导出）`;
      this.emitExportProgress(onProgress, {
        phase: 'completed',
        copiedBytes,
        totalBytes
      });
    } catch (error) {
      let targetRemoved = true;
      if (targetUri.length > 0) {
        targetRemoved = await this.cleanupFailedExportTarget(targetUri, sourcePath);
      }
      this.lastExportPath = this.failedExportMessage('导出失败，请重试', targetRemoved);
      this.emitExportProgress(onProgress, {
        phase: 'failed',
        copiedBytes,
        totalBytes
      });
    } finally {
      this.exportInProgress = false;
    }
    return this.status();
  }

  status(): ResearchRecorderStatus {
    return {
      running: this.running,
      sessionId: this.sessionId,
      sampleCount: this.recordCount,
      hasExportableLog: this.hasExportableLog,
      integrityWarning: this.writeFailed,
      lastEvent: this.lastEvent,
      lastExportPath: this.lastExportPath
    };
  }

  private appendRecord(timestampMs: number, event: string, notes: string, includeEstimator: boolean = true): void {
    if (!this.sessionId) {
      this.sessionId = `session-${timestampMs}`;
    }

    const sample = includeEstimator ? this.lastSample : undefined;
    const stats = includeEstimator ? this.lastStats : undefined;
    const calibration = includeEstimator ? this.lastCalibration : undefined;
    const location = this.lastLocation;
    const satellite = this.lastSatellite;
    const measurementElapsedMs = this.measurementActive && this.measurementStartMs > 0
      ? Math.max(0, timestampMs - this.measurementStartMs)
      : null;
    const recordType = this.recordTypeFor(event);
    this.recordSeq += 1;
    const record: ResearchRecord = {
      timestampMs,
      sessionId: this.sessionId,
      recordSchemaVersion: RECORD_SCHEMA_VERSION,
      appVersionCode: APP_VERSION_CODE,
      algorithmVersion: ALGORITHM_VERSION,
      recordSeq: this.recordSeq,
      recordType,
      event,
      measurementRunId: this.measurementRunId,
      measurementActive: this.measurementActive,
      measurementStartMs: this.measurementActive && this.measurementStartMs > 0 ? this.measurementStartMs : null,
      measurementElapsedMs
    };

    if (recordType === 'location' && location) {
      this.appendLocationFields(record, timestampMs, location);
    }
    if (recordType === 'satellite' && satellite) {
      this.appendSatelliteFields(record, satellite);
    }
    if (recordType === 'estimator' && sample && stats && calibration) {
      this.appendEstimatorFields(record, sample, stats, calibration);
    }
    if (recordType === 'event' && stats) {
      this.appendEstimatorSummary(record, stats);
    }
    if (notes.length > 0) {
      record.notes = notes;
    }

    this.writeJsonLine(JSON.stringify(record));
  }

  private appendLocationFields(record: ResearchRecord, timestampMs: number, location: LocationSnapshot): void {
    record.locationIntervalMs = this.lastLocationIntervalMs;
    record.locationLat = location.latitude;
    record.locationLon = location.longitude;
    record.locationAltitude = location.altitude;
    record.locationAccuracy = location.accuracy;
    record.locationAltitudeAccuracy = location.altitudeAccuracy;
    record.locationSpeedMps = location.speedMps;
    record.locationSpeedAccuracyMps = location.speedAccuracyMps;
    record.locationBearingDeg = location.directionDeg;
    record.locationBearingAccuracyDeg = location.directionAccuracyDeg;
    record.locationTimeMs = location.locationTimeMs;
    record.locationTimeSinceBootNs = location.timeSinceBootNs;
    record.locationTimeUncertaintyNs = location.uncertaintyOfTimeSinceBootNs;
    record.locationSourceType = location.locationSourceType;
    record.locationAgeMs = timestampMs - location.locationTimeMs;
    record.satelliteCount = location.satelliteCount;
    record.satelliteFixCount = location.satelliteFixCount;
    record.satelliteCn0Avg = location.satelliteCn0Avg;
    record.satelliteCn0Max = location.satelliteCn0Max;
    record.satelliteCn0PositiveCount = location.satelliteCn0PositiveCount;
    record.satelliteCn0PositiveAvg = location.satelliteCn0PositiveAvg;
    record.satelliteCn0UsedAvg = location.satelliteCn0UsedAvg;
  }

  private appendSatelliteFields(record: ResearchRecord, satellite: SatelliteSnapshot): void {
    record.satelliteIntervalMs = this.lastSatelliteIntervalMs;
    record.satelliteCount = satellite.satelliteCount;
    record.satelliteFixCount = satellite.satelliteFixCount;
    record.satelliteCn0Avg = satellite.satelliteCn0Avg;
    record.satelliteCn0Max = satellite.satelliteCn0Max;
    record.satelliteCn0PositiveCount = satellite.satelliteCn0PositiveCount;
    record.satelliteCn0PositiveAvg = satellite.satelliteCn0PositiveAvg;
    record.satelliteCn0UsedAvg = satellite.satelliteCn0UsedAvg;
  }

  private appendEstimatorFields(
    record: ResearchRecord,
    sample: SpeedSample,
    stats: RunStats,
    calibration: CalibrationState
  ): void {
    this.appendEstimatorSummary(record, stats);
    record.pureInertialSpeedKmh = sample.speedKmh;
    record.displaySpeedKmh = stats.currentSpeedKmh;
    record.estimatorActive = true;
    record.filteredAccX = sample.filteredAcceleration.x;
    record.filteredAccY = sample.filteredAcceleration.y;
    record.filteredAccZ = sample.filteredAcceleration.z;
    record.gravityX = calibration.gravity.x;
    record.gravityY = calibration.gravity.y;
    record.gravityZ = calibration.gravity.z;
    record.mainAxisX = calibration.mainAxis.x;
    record.mainAxisY = calibration.mainAxis.y;
    record.mainAxisZ = calibration.mainAxis.z;
  }

  private appendEstimatorSummary(record: ResearchRecord, stats: RunStats): void {
    record.estimatedSpeedKmh = stats.currentSpeedKmh;
    record.confidence = stats.confidence;
    record.motionState = motionStateText(stats.motionState);
    record.calibrationStatus = stats.statusText;
  }

  private appendSensorRecord(frame: SensorFrame): void {
    if (!this.sessionId) {
      this.sessionId = `session-${frame.timestampMs}`;
    }

    const measurementElapsedMs = this.measurementActive && this.measurementStartMs > 0
      ? Math.max(0, frame.timestampMs - this.measurementStartMs)
      : null;
    this.recordSeq += 1;
    const record: SensorResearchRecord = {
      timestampMs: frame.timestampMs,
      sessionId: this.sessionId,
      recordSchemaVersion: RECORD_SCHEMA_VERSION,
      appVersionCode: APP_VERSION_CODE,
      algorithmVersion: ALGORITHM_VERSION,
      recordSeq: this.recordSeq,
      recordType: 'sensor',
      measurementRunId: this.measurementRunId,
      measurementActive: this.measurementActive,
      measurementStartMs: this.measurementActive && this.measurementStartMs > 0 ? this.measurementStartMs : null,
      measurementElapsedMs,
      sensorTimestamp: frame.sensorTimestamp,
      sensorIntervalMs: this.lastSensorIntervalMs,
      accX: frame.acceleration.x,
      accY: frame.acceleration.y,
      accZ: frame.acceleration.z,
      gyroX: frame.gyroscope ? frame.gyroscope.x : null,
      gyroY: frame.gyroscope ? frame.gyroscope.y : null,
      gyroZ: frame.gyroscope ? frame.gyroscope.z : null,
      gyroscopeTimestamp: frame.gyroscopeTimestamp !== undefined ? frame.gyroscopeTimestamp : null,
      rotVecX: frame.rotationVector ? frame.rotationVector.x : null,
      rotVecY: frame.rotationVector ? frame.rotationVector.y : null,
      rotVecZ: frame.rotationVector ? frame.rotationVector.z : null,
      rotVecW: frame.rotationVector ? frame.rotationVector.w : null,
      rotVecTimestamp: frame.rotationVectorTimestamp !== undefined ? frame.rotationVectorTimestamp : null,
      magX: frame.magneticField ? frame.magneticField.x : null,
      magY: frame.magneticField ? frame.magneticField.y : null,
      magZ: frame.magneticField ? frame.magneticField.z : null,
      magTimestamp: frame.magneticFieldTimestamp !== undefined ? frame.magneticFieldTimestamp : null
    };

    this.writeJsonLine(JSON.stringify(record));
  }

  private appendSensorCallbackRecord(
    sample: SensorCallbackSample,
    callbackIntervalMs: number | null,
    sensorIntervalMs: number | null
  ): void {
    if (!this.sessionId) {
      this.sessionId = `session-${sample.callbackTimestampMs}`;
    }

    const measurementElapsedMs = this.measurementActive && this.measurementStartMs > 0
      ? Math.max(0, sample.callbackTimestampMs - this.measurementStartMs)
      : null;
    this.recordSeq += 1;
    const record: SensorCallbackResearchRecord = {
      timestampMs: sample.callbackTimestampMs,
      sessionId: this.sessionId,
      recordSchemaVersion: RECORD_SCHEMA_VERSION,
      appVersionCode: APP_VERSION_CODE,
      algorithmVersion: ALGORITHM_VERSION,
      recordSeq: this.recordSeq,
      recordType: 'sensor_callback',
      measurementRunId: this.measurementRunId,
      measurementActive: this.measurementActive,
      measurementStartMs: this.measurementActive && this.measurementStartMs > 0 ? this.measurementStartMs : null,
      measurementElapsedMs,
      sensorType: sample.sensorType,
      sensorTimestamp: sample.sensorTimestamp,
      sensorIntervalMs,
      callbackIntervalMs,
      requestedIntervalNs: sample.requestedIntervalNs
    };

    if (sample.x !== undefined) record.x = sample.x;
    if (sample.y !== undefined) record.y = sample.y;
    if (sample.z !== undefined) record.z = sample.z;
    if (sample.w !== undefined) record.w = sample.w;
    if (sample.biasX !== undefined) record.biasX = sample.biasX;
    if (sample.biasY !== undefined) record.biasY = sample.biasY;
    if (sample.biasZ !== undefined) record.biasZ = sample.biasZ;

    this.writeJsonLine(JSON.stringify(record));
  }

  private maybeAppendDeviceHealthRecord(timestampMs: number): void {
    if (timestampMs - this.lastDeviceHealthRecordMs < DEVICE_HEALTH_RECORD_INTERVAL_MS) {
      return;
    }
    this.appendDeviceHealthRecord(timestampMs, 'periodic');
  }

  private appendDeviceHealthRecord(timestampMs: number, reason: string): void {
    if (!this.running) {
      return;
    }
    if (!this.sessionId) {
      this.sessionId = `session-${timestampMs}`;
    }

    let batteryTemperatureC: number | null = null;
    let thermalLevel: number | null = null;
    try {
      const temperatureDeciC = batteryInfo.batteryTemperature;
      if (Number.isFinite(temperatureDeciC)) {
        batteryTemperatureC = temperatureDeciC / 10;
      }
    } catch (error) {
    }
    try {
      const currentThermalLevel = thermal.getLevel();
      if (Number.isFinite(currentThermalLevel)) {
        thermalLevel = currentThermalLevel;
      }
    } catch (error) {
    }

    const measurementElapsedMs = this.measurementActive && this.measurementStartMs > 0
      ? Math.max(0, timestampMs - this.measurementStartMs)
      : null;
    this.recordSeq += 1;
    const record: DeviceHealthResearchRecord = {
      timestampMs,
      sessionId: this.sessionId,
      recordSchemaVersion: RECORD_SCHEMA_VERSION,
      appVersionCode: APP_VERSION_CODE,
      algorithmVersion: ALGORITHM_VERSION,
      recordSeq: this.recordSeq,
      recordType: 'device_health',
      measurementRunId: this.measurementRunId,
      measurementActive: this.measurementActive,
      measurementStartMs: this.measurementActive && this.measurementStartMs > 0 ? this.measurementStartMs : null,
      measurementElapsedMs,
      deviceHealthReason: reason,
      batteryTemperatureC,
      thermalLevel
    };

    this.lastDeviceHealthRecordMs = timestampMs;
    this.writeJsonLine(JSON.stringify(record));
  }

  private writeJsonLine(line: string): void {
    if (!this.running || !this.tempLogFile) {
      return;
    }
    const nowMs = Date.now();
    if (this.writeBuffer.length < 1) {
      this.writeBufferStartMs = nowMs;
    }
    this.writeBuffer.push(line);
    this.recordCount += 1;
    this.hasExportableLog = true;
    const bufferAgeMs = Math.max(0, nowMs - this.writeBufferStartMs);
    if (this.writeBuffer.length < WRITE_BUFFER_LIMIT && bufferAgeMs < WRITE_BUFFER_MAX_AGE_MS) {
      return;
    }
    this.flushWriteBuffer();
  }

  private flushWriteBuffer(): void {
    if (!this.tempLogFile || this.writeBuffer.length < 1) {
      return;
    }
    try {
      const payload = `${this.writeBuffer.join('\n')}\n`;
      const encoded = UTF8_ENCODER.encodeInto(payload);
      const bytesWritten = fs.writeSync(this.tempLogFile.fd, encoded.buffer, { length: encoded.byteLength });
      if (bytesWritten !== encoded.byteLength) {
        throw new Error(`short research log write: ${bytesWritten}/${encoded.byteLength}`);
      }
      this.writeBuffer = [];
      this.writeBufferStartMs = 0;
    } catch (error) {
      this.running = false;
      this.writeFailed = true;
      this.lastEvent = 'write_record_failed';
      this.lastExportPath = 'record file write failed';
      this.persistIntegrityWarning('record file write failed');
      this.writeBuffer = [];
      this.writeBufferStartMs = 0;
      this.closeTempLogFile();
      this.refreshExportableLogState();
    }
  }

  private newSessionTarget(timestampMs: number): SessionTarget {
    let attempt = 0;
    while (attempt < 1000) {
      const uniqueSuffix = attempt === 0 ? `${timestampMs}` : `${timestampMs}-${attempt}`;
      const target: SessionTarget = {
        sessionId: `session-${uniqueSuffix}`,
        path: `${this.filesDir}/${SESSION_LOG_PREFIX}${uniqueSuffix}${SESSION_LOG_SUFFIX}`
      };
      try {
        fs.statSync(target.path);
        attempt += 1;
      } catch (error) {
        return target;
      }
    }
    const fallbackSuffix = `${timestampMs}-${Date.now()}`;
    return {
      sessionId: `session-${fallbackSuffix}`,
      path: `${this.filesDir}/${SESSION_LOG_PREFIX}${fallbackSuffix}${SESSION_LOG_SUFFIX}`
    };
  }

  private clearSupersededPendingLogs(activePath: string): void {
    if (this.filesDir.length < 1) {
      return;
    }
    try {
      const names = fs.listFileSync(this.filesDir);
      for (const name of names) {
        const isCurrentLog = name === LEGACY_TEMP_LOG_FILE_NAME;
        const isSessionLog = name.startsWith(SESSION_LOG_PREFIX) && name.endsWith(SESSION_LOG_SUFFIX);
        if (!isCurrentLog && !isSessionLog) {
          continue;
        }
        const path = `${this.filesDir}/${name}`;
        if (path === activePath) {
          continue;
        }
        try {
          fs.unlinkSync(path);
          this.removeIntegrityMarker(path);
          this.removeDiscardedMarker(path);
        } catch (error) {
          // The new session is already durable. A stale predecessor can be retried
          // on the next successful recording start.
        }
      }
    } catch (error) {
    }
  }

  private rollbackFailedSessionStart(failedPath: string, message: string): ResearchRecorderStatus {
    this.running = false;
    this.closeTempLogFile();
    let failedFileRemoved = false;
    try {
      fs.unlinkSync(failedPath);
      failedFileRemoved = true;
    } catch (error) {
    }
    if (failedFileRemoved) {
      this.removeIntegrityMarker(failedPath);
      this.removeDiscardedMarker(failedPath);
    } else {
      this.quarantineFailedSession(failedPath);
    }
    this.tempLogPath = '';
    this.selectNewestPendingLog(failedPath);
    this.refreshExportableLogState();
    this.restoreExportableLogSummary();
    this.lastEvent = 'start_record_failed';
    this.lastExportPath = message;
    return this.status();
  }

  private integrityMarkerPath(sourcePath: string): string {
    return `${sourcePath}.incomplete`;
  }

  private discardedMarkerPath(sourcePath: string): string {
    return `${sourcePath}${DISCARDED_LOG_MARKER_SUFFIX}`;
  }

  private hasDiscardedMarker(sourcePath: string): boolean {
    try {
      fs.statSync(this.discardedMarkerPath(sourcePath));
      return true;
    } catch (error) {
      return false;
    }
  }

  private quarantineFailedSession(sourcePath: string): void {
    let markerFile: fs.File | undefined = undefined;
    let markerPersisted = false;
    try {
      markerFile = fs.openSync(
        this.discardedMarkerPath(sourcePath),
        fs.OpenMode.WRITE_ONLY | fs.OpenMode.CREATE | fs.OpenMode.TRUNC
      );
      fs.writeSync(markerFile.fd, 'failed session start\n');
      fs.fsyncSync(markerFile.fd);
      markerPersisted = true;
    } catch (error) {
      markerPersisted = false;
    } finally {
      if (markerFile) {
        try {
          fs.closeSync(markerFile);
        } catch (error) {
          markerPersisted = false;
        }
      }
    }
    if (markerPersisted) {
      return;
    }

    let failedFile: fs.File | undefined = undefined;
    try {
      failedFile = fs.openSync(sourcePath, fs.OpenMode.WRITE_ONLY | fs.OpenMode.TRUNC);
      fs.fsyncSync(failedFile.fd);
    } catch (error) {
    } finally {
      if (failedFile) {
        try {
          fs.closeSync(failedFile);
        } catch (error) {
        }
      }
    }
  }

  private hasIntegrityMarker(sourcePath: string): boolean {
    try {
      fs.statSync(this.integrityMarkerPath(sourcePath));
      return true;
    } catch (error) {
      return false;
    }
  }

  private persistIntegrityWarning(reason: string): void {
    if (this.tempLogPath.length < 1) {
      return;
    }
    let markerFile: fs.File | undefined = undefined;
    try {
      markerFile = fs.openSync(
        this.integrityMarkerPath(this.tempLogPath),
        fs.OpenMode.WRITE_ONLY | fs.OpenMode.CREATE | fs.OpenMode.TRUNC
      );
      fs.writeSync(markerFile.fd, `${reason}\n`);
      fs.fsyncSync(markerFile.fd);
    } catch (error) {
      // The in-memory warning remains visible even if the filesystem cannot persist a sidecar.
    } finally {
      if (markerFile) {
        try {
          fs.closeSync(markerFile);
        } catch (error) {
        }
      }
    }
  }

  private removeIntegrityMarker(sourcePath: string): void {
    try {
      fs.unlinkSync(this.integrityMarkerPath(sourcePath));
    } catch (error) {
    }
  }

  private removeDiscardedMarker(sourcePath: string): void {
    try {
      fs.unlinkSync(this.discardedMarkerPath(sourcePath));
    } catch (error) {
    }
  }

  private selectNewestPendingLog(excludedPath: string = ''): void {
    if (this.running || this.filesDir.length < 1) {
      return;
    }

    this.tempLogPath = '';
    this.hasExportableLog = false;
    this.recordCount = 0;
    this.recordSeq = 0;
    this.sessionId = '';
    this.lastEvent = '';
    this.lastExportPath = '';
    this.writeFailed = false;
    try {
      const names = fs.listFileSync(this.filesDir);
      const sessionNames: string[] = [];
      for (const name of names) {
        if (name.startsWith(SESSION_LOG_PREFIX) && name.endsWith(SESSION_LOG_SUFFIX)) {
          sessionNames.push(name);
        }
      }
      sessionNames.sort().reverse();
      for (const name of sessionNames) {
        const path = `${this.filesDir}/${name}`;
        if (path === excludedPath) {
          continue;
        }
        if (this.hasDiscardedMarker(path)) {
          continue;
        }
        if (!this.isNonEmptyLogCandidate(path)) {
          continue;
        }
        this.tempLogPath = path;
        this.writeFailed = this.hasIntegrityMarker(path);
        return;
      }

      const legacyPath = `${this.filesDir}/${LEGACY_TEMP_LOG_FILE_NAME}`;
      if (legacyPath !== excludedPath && this.isNonEmptyLogCandidate(legacyPath)) {
        this.tempLogPath = legacyPath;
        this.writeFailed = this.hasIntegrityMarker(legacyPath);
      }
    } catch (error) {
    }
  }

  private isNonEmptyLogCandidate(path: string): boolean {
    try {
      return fs.statSync(path).size > 0;
    } catch (error) {
      return false;
    }
  }

  private openTempLogForNewSession(): boolean {
    if (this.tempLogPath.length < 1) {
      return false;
    }
    try {
      this.tempLogFile = fs.openSync(
        this.tempLogPath,
        fs.OpenMode.WRITE_ONLY | fs.OpenMode.CREATE | fs.OpenMode.TRUNC
      );
      this.removeIntegrityMarker(this.tempLogPath);
      this.removeDiscardedMarker(this.tempLogPath);
      return true;
    } catch (error) {
      this.tempLogFile = undefined;
      this.hasExportableLog = false;
      return false;
    }
  }

  private flushTempLogFile(): void {
    if (!this.tempLogFile) {
      return;
    }
    try {
      this.flushWriteBuffer();
      fs.fsyncSync(this.tempLogFile.fd);
    } catch (error) {
      this.running = false;
      this.writeFailed = true;
      this.lastEvent = 'write_record_failed';
      this.lastExportPath = 'record file sync failed';
      this.persistIntegrityWarning('record file sync failed');
      this.closeTempLogFile();
    }
  }

  private emitExportProgress(
    onProgress: ResearchExportProgressCallback | undefined,
    progress: ResearchExportProgress
  ): void {
    if (!onProgress) {
      return;
    }
    try {
      onProgress(progress);
    } catch (error) {
      // Export observers must not interrupt or invalidate the file copy.
    }
  }

  private async copyTempLogToUri(
    uri: string,
    sourcePath: string,
    onProgress: ResearchExportProgressCallback
  ): Promise<boolean> {
    let sourceFile: fs.File | undefined = undefined;
    let targetFile: fs.File | undefined = undefined;
    let copySucceeded = false;
    try {
      const expectedBytes = (await fs.stat(sourcePath)).size;
      sourceFile = await fs.open(sourcePath, fs.OpenMode.READ_ONLY);
      targetFile = await fs.open(
        uri,
        fs.OpenMode.WRITE_ONLY | fs.OpenMode.CREATE | fs.OpenMode.TRUNC
      );
      const buffer = new ArrayBuffer(EXPORT_COPY_BUFFER_BYTES);
      let copiedBytes = 0;
      let chunksSinceYield = 0;
      let lastReportedBytes = 0;
      let shortWrite = false;
      onProgress({
        phase: 'copying',
        copiedBytes: 0,
        totalBytes: expectedBytes
      });
      while (true) {
        const bytesRead = await fs.read(sourceFile.fd, buffer);
        if (bytesRead <= 0) {
          break;
        }
        let chunkOffset = 0;
        while (chunkOffset < bytesRead) {
          const remainingBytes = bytesRead - chunkOffset;
          const writeBuffer = chunkOffset === 0 ? buffer : buffer.slice(chunkOffset, bytesRead);
          const bytesWritten = await fs.write(targetFile.fd, writeBuffer, { length: remainingBytes });
          if (bytesWritten <= 0 || bytesWritten > remainingBytes) {
            shortWrite = true;
            break;
          }
          chunkOffset += bytesWritten;
          copiedBytes += bytesWritten;
        }
        if (shortWrite) {
          break;
        }
        chunksSinceYield += 1;
        if (chunksSinceYield >= EXPORT_YIELD_EVERY_CHUNKS) {
          chunksSinceYield = 0;
          lastReportedBytes = copiedBytes;
          onProgress({
            phase: 'copying',
            copiedBytes,
            totalBytes: expectedBytes
          });
          await this.yieldExportLoop();
        }
      }
      if (!shortWrite) {
        if (copiedBytes !== lastReportedBytes) {
          onProgress({
            phase: 'copying',
            copiedBytes,
            totalBytes: expectedBytes
          });
        }
        onProgress({
          phase: 'syncing',
          copiedBytes,
          totalBytes: expectedBytes
        });
        await fs.fsync(targetFile.fd);
        copySucceeded = copiedBytes === expectedBytes;
      }
    } catch (error) {
      copySucceeded = false;
    }
    if (sourceFile) {
      try {
        await fs.close(sourceFile);
      } catch (error) {
        copySucceeded = false;
      }
    }
    if (targetFile) {
      try {
        await fs.close(targetFile);
      } catch (error) {
        copySucceeded = false;
      }
    }
    return copySucceeded;
  }

  private yieldExportLoop(): Promise<void> {
    return new Promise<void>((resolve) => {
      setTimeout(() => {
        resolve();
      }, 0);
    });
  }

  private failedExportMessage(message: string, targetRemoved: boolean): string {
    return targetRemoved ? message : `${message}；保存位置可能残留未完成文件，请手动删除`;
  }

  private async cleanupFailedExportTarget(uri: string, sourcePath: string): Promise<boolean> {
    if (uri.length < 1 || uri === sourcePath) {
      return false;
    }
    try {
      await fileManagerService.deleteToTrash(uri);
      return true;
    } catch (error) {
      const deleteError = error as BusinessError;
      if (deleteError.code === 1014000002) {
        return true;
      }
    }
    // Public picker URIs are outside the application sandbox, so fs.unlink is
    // not a supported fallback. Permission and I/O failures must remain visible
    // to the user as a possible residual target instead of being hidden.
    return false;
  }

  private closeTempLogFile(): void {
    if (!this.tempLogFile) {
      return;
    }
    try {
      this.flushWriteBuffer();
      fs.closeSync(this.tempLogFile);
    } catch (error) {
      this.running = false;
      this.writeFailed = true;
      this.lastEvent = 'write_record_failed';
      this.lastExportPath = 'record file close failed';
      this.persistIntegrityWarning('record file close failed');
    }
    this.tempLogFile = undefined;
    this.writeBuffer = [];
    this.writeBufferStartMs = 0;
  }

  private refreshExportableLogState(): void {
    if (this.tempLogPath.length < 1) {
      this.hasExportableLog = false;
      return;
    }
    try {
      const stat = fs.statSync(this.tempLogPath);
      this.hasExportableLog = stat.size > 0;
    } catch (error) {
      this.hasExportableLog = false;
    }
  }

  private restoreExportableLogSummary(): void {
    if (this.running || !this.hasExportableLog || this.tempLogPath.length < 1) {
      return;
    }

    try {
      const stat = fs.statSync(this.tempLogPath);
      const fileSize = stat.size;
      if (fileSize <= 0) {
        return;
      }

      const TAIL_SIZE = 65536;
      let text: string;
      if (fileSize <= TAIL_SIZE) {
        text = fs.readTextSync(this.tempLogPath);
      } else {
        text = fs.readTextSync(this.tempLogPath, { offset: fileSize - TAIL_SIZE, length: TAIL_SIZE });
        const firstNewline = text.indexOf('\n');
        if (firstNewline >= 0) {
          text = text.substring(firstNewline + 1);
        }
      }

      const lastNewline = text.lastIndexOf('\n');
      let lastLine = '';
      if (lastNewline < 0) {
        lastLine = text.trim();
      } else {
        lastLine = text.substring(lastNewline + 1).trim();
        if (lastLine.length === 0 && lastNewline > 0) {
          const prevNewline = text.lastIndexOf('\n', lastNewline - 1);
          lastLine = prevNewline < 0
            ? text.substring(0, lastNewline).trim()
            : text.substring(prevNewline + 1, lastNewline).trim();
        }
      }

      if (lastLine.length === 0) {
        return;
      }

      const lastRecord = JSON.parse(lastLine) as Record<string, Object>;
      const restoredRecordSeq = lastRecord['recordSeq'];
      if (typeof restoredRecordSeq === 'number') {
        this.recordCount = restoredRecordSeq;
        this.recordSeq = restoredRecordSeq;
      }

      const restoredSessionId = lastRecord['sessionId'];
      if (typeof restoredSessionId === 'string' && restoredSessionId.length > 0) {
        this.sessionId = restoredSessionId;
      }

      const restoredEvent = lastRecord['event'];
      const restoredType = lastRecord['recordType'];
      if (restoredEvent !== 'stop_record') {
        this.writeFailed = true;
        this.persistIntegrityWarning('missing terminal stop_record');
      }
      if (this.writeFailed) {
        this.lastEvent = 'incomplete_log';
        this.lastExportPath = '日志可能不完整，导出文件将标注 INCOMPLETE';
      } else if (typeof restoredEvent === 'string' && restoredEvent.length > 0) {
        this.lastEvent = restoredEvent;
      } else if (typeof restoredType === 'string' && restoredType.length > 0) {
        this.lastEvent = restoredType;
      }
    } catch (error) {
      this.writeFailed = true;
      this.lastEvent = 'incomplete_log';
      this.lastExportPath = '日志尾部损坏，导出文件将标注 INCOMPLETE';
      this.persistIntegrityWarning('unreadable log tail');
    }
  }

  private updateSensorInterval(frame: SensorFrame): void {
    if (!this.running) {
      return;
    }
    const timestampMs = frame.timestampMs;
    if (frame.sensorTimestamp !== null && frame.sensorTimestamp !== undefined &&
      this.previousSensorTimestampNs !== null && frame.sensorTimestamp > this.previousSensorTimestampNs) {
      this.lastSensorIntervalMs = (frame.sensorTimestamp - this.previousSensorTimestampNs) / 1000000;
    } else {
      this.lastSensorIntervalMs = this.previousSensorFrameMs > 0 ? timestampMs - this.previousSensorFrameMs : null;
    }
    this.previousSensorFrameMs = timestampMs;
    this.previousSensorTimestampNs = frame.sensorTimestamp === null || frame.sensorTimestamp === undefined ? null : frame.sensorTimestamp;
  }

  private recordTypeFor(event: string): string {
    if (!event) {
      return 'sensor';
    }
    if (event === 'location') {
      return 'location';
    }
    if (event === 'satellite') {
      return 'satellite';
    }
    if (event === 'estimator') {
      return 'estimator';
    }
    if (event === 'start_record' || event === 'stop_record') {
      return 'lifecycle';
    }
    return 'event';
  }
}
