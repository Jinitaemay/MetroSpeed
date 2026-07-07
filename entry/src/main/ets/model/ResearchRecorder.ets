import fs from '@ohos.file.fs';
import picker from '@ohos.file.picker';
import common from '@ohos.app.ability.common';
import { LocationSnapshot, SatelliteSnapshot } from './LocationController';
import {
  CalibrationState,
  RunStats,
  SensorFrame,
  SpeedSample,
  motionStateText
} from './SpeedTypes';

const SENSOR_RECORD_INTERVAL_MS = 20;
const ESTIMATOR_RECORD_INTERVAL_MS = 500;
const RECORD_SCHEMA_VERSION = 13;
const APP_VERSION_CODE = 1783497867;
const ALGORITHM_VERSION = 'anchor-delta-20260707-r2';
const TEMP_LOG_FILE_NAME = 'research-current.jsonl';
const WRITE_BUFFER_LIMIT = 16;
const EXPORT_COPY_BUFFER_BYTES = 64 * 1024;

export interface ResearchRecorderStatus {
  running: boolean;
  sessionId: string;
  sampleCount: number;
  hasExportableLog: boolean;
  lastEvent: string;
  lastExportPath: string;
}

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
  estimatedSpeedKmh?: number;
  confidence?: number;
  motionState?: string;
  calibrationStatus?: string;
  notes?: string;
}

interface SensorResearchRecord {
  timestampMs: number;
  recordSeq: number;
  recordType: string;
  measurementActive: boolean;
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
  sysGravityX: number | null;
  sysGravityY: number | null;
  sysGravityZ: number | null;
  sysGravityTimestamp: number | null;
  linearAccX: number | null;
  linearAccY: number | null;
  linearAccZ: number | null;
  linearAccTimestamp: number | null;
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

export class ResearchRecorder {
  private running: boolean = false;
  private sessionId: string = '';
  private recordCount: number = 0;
  private tempLogPath: string = '';
  private tempLogFile?: fs.File;
  private writeBuffer: string[] = [];
  private hasExportableLog: boolean = false;
  private lastEvent: string = '';
  private lastExportPath: string = '';
  private lastSample?: SpeedSample;
  private lastStats?: RunStats;
  private lastCalibration?: CalibrationState;
  private lastLocation?: LocationSnapshot;
  private lastSatellite?: SatelliteSnapshot;
  private lastSensorRecordMs: number = 0;
  private lastEstimatorRecordMs: number = 0;
  private recordSeq: number = 0;
  private previousSensorFrameMs: number = 0;
  private previousSensorTimestampNs: number | null = null;
  private previousLocationMs: number = 0;
  private previousSatelliteMs: number = 0;
  private lastSensorIntervalMs: number | null = null;
  private lastLocationIntervalMs: number | null = null;
  private lastSatelliteIntervalMs: number | null = null;
  private measurementRunId: string | null = null;
  private measurementActive: boolean = false;
  private measurementStartMs: number = 0;

  attachContext(context: common.UIAbilityContext): ResearchRecorderStatus {
    this.ensureTempLogPath(context);
    this.refreshExportableLogState();
    this.restoreExportableLogSummary();
    return this.status();
  }

  start(timestampMs: number, context: common.UIAbilityContext): ResearchRecorderStatus {
    this.closeTempLogFile();
    this.ensureTempLogPath(context);
    this.running = true;
    this.sessionId = `session-${timestampMs}`;
    this.recordCount = 0;
    this.writeBuffer = [];
    this.hasExportableLog = false;
    this.lastEvent = 'start_record';
    this.lastExportPath = '';
    this.lastSample = undefined;
    this.lastStats = undefined;
    this.lastCalibration = undefined;
    this.lastLocation = undefined;
    this.lastSatellite = undefined;
    this.lastSensorRecordMs = 0;
    this.lastEstimatorRecordMs = 0;
    this.recordSeq = 0;
    this.previousSensorFrameMs = 0;
    this.previousSensorTimestampNs = null;
    this.previousLocationMs = 0;
    this.previousSatelliteMs = 0;
    this.lastSensorIntervalMs = null;
    this.lastLocationIntervalMs = null;
    this.lastSatelliteIntervalMs = null;
    this.measurementRunId = null;
    this.measurementActive = false;
    this.measurementStartMs = 0;
    if (!this.openTempLogForNewSession()) {
      this.running = false;
      this.lastEvent = 'start_record_failed';
      this.lastExportPath = 'record file open failed';
      return this.status();
    }
    this.appendRecord(timestampMs, 'start_record', 'research recording started');
    return this.status();
  }

  stop(timestampMs: number): ResearchRecorderStatus {
    if (this.running) {
      this.appendRecord(timestampMs, 'stop_record', 'research recording stopped');
    }
    this.running = false;
    this.flushTempLogFile();
    this.closeTempLogFile();
    this.refreshExportableLogState();
    this.lastEvent = 'stop_record';
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
    if (frame.timestampMs - this.lastSensorRecordMs < SENSOR_RECORD_INTERVAL_MS) {
      return;
    }

    this.lastSensorRecordMs = frame.timestampMs;
    this.appendSensorRecord(frame);
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

  async exportJsonl(context: common.UIAbilityContext): Promise<ResearchRecorderStatus> {
    const fileName = `${this.sessionId || `session-${Date.now()}`}.jsonl`;
    try {
      const documentPicker = new picker.DocumentViewPicker(context);
      const uris = await documentPicker.save({
        newFileNames: [fileName]
      });
      if (uris.length < 1) {
        this.lastExportPath = '已取消导出';
        return this.status();
      }

      this.ensureTempLogPath(context);
      this.flushTempLogFile();
      this.refreshExportableLogState();
      if (!this.hasExportableLog) {
        this.lastExportPath = 'no exportable log';
        return this.status();
      }

      if (!this.copyTempLogToUri(uris[0])) {
        this.lastExportPath = '导出复制失败';
        return this.status();
      }
      this.lastExportPath = `已导出：${fileName}`;
    } catch (error) {
      this.lastExportPath = '导出失败，请重试';
    }
    return this.status();
  }

  status(): ResearchRecorderStatus {
    return {
      running: this.running,
      sessionId: this.sessionId,
      sampleCount: this.recordCount,
      hasExportableLog: this.hasExportableLog,
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
      recordSeq: this.recordSeq,
      recordType: 'sensor',
      measurementActive: this.measurementActive,
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
      sysGravityX: frame.gravity ? frame.gravity.x : null,
      sysGravityY: frame.gravity ? frame.gravity.y : null,
      sysGravityZ: frame.gravity ? frame.gravity.z : null,
      sysGravityTimestamp: frame.gravityTimestamp !== undefined ? frame.gravityTimestamp : null,
      linearAccX: frame.linearAcceleration ? frame.linearAcceleration.x : null,
      linearAccY: frame.linearAcceleration ? frame.linearAcceleration.y : null,
      linearAccZ: frame.linearAcceleration ? frame.linearAcceleration.z : null,
      linearAccTimestamp: frame.linearAccelerationTimestamp !== undefined ? frame.linearAccelerationTimestamp : null,
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

  private writeJsonLine(line: string): void {
    if (!this.running || !this.tempLogFile) {
      return;
    }
    this.writeBuffer.push(line);
    this.recordCount += 1;
    this.hasExportableLog = true;
    if (this.writeBuffer.length < WRITE_BUFFER_LIMIT) {
      return;
    }
    this.flushWriteBuffer();
  }

  private flushWriteBuffer(): void {
    if (!this.tempLogFile || this.writeBuffer.length < 1) {
      return;
    }
    try {
      fs.writeSync(this.tempLogFile.fd, `${this.writeBuffer.join('\n')}\n`);
      this.writeBuffer = [];
    } catch (error) {
      this.running = false;
      this.lastEvent = 'write_record_failed';
      this.lastExportPath = 'record file write failed';
      this.writeBuffer = [];
      this.closeTempLogFile();
      this.refreshExportableLogState();
    }
  }

  private ensureTempLogPath(context: common.UIAbilityContext): void {
    if (this.tempLogPath.length < 1) {
      this.tempLogPath = `${context.filesDir}/${TEMP_LOG_FILE_NAME}`;
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
      // Export can still try to copy the file; status text is updated on export failure.
    }
  }

  private copyTempLogToUri(uri: string): boolean {
    let sourceFile: fs.File | undefined = undefined;
    let targetFile: fs.File | undefined = undefined;
    try {
      sourceFile = fs.openSync(this.tempLogPath, fs.OpenMode.READ_ONLY);
      targetFile = fs.openSync(uri, fs.OpenMode.WRITE_ONLY | fs.OpenMode.TRUNC);
      const buffer = new ArrayBuffer(EXPORT_COPY_BUFFER_BYTES);
      while (true) {
        const bytesRead = fs.readSync(sourceFile.fd, buffer);
        if (bytesRead <= 0) {
          break;
        }
        const bytesWritten = fs.writeSync(targetFile.fd, buffer, { length: bytesRead });
        if (bytesWritten !== bytesRead) {
          return false;
        }
      }
      fs.fsyncSync(targetFile.fd);
      return true;
    } catch (error) {
      return false;
    } finally {
      if (sourceFile) {
        try {
          fs.closeSync(sourceFile);
        } catch (error) {
        }
      }
      if (targetFile) {
        try {
          fs.closeSync(targetFile);
        } catch (error) {
        }
      }
    }
  }

  private closeTempLogFile(): void {
    if (!this.tempLogFile) {
      return;
    }
    try {
      this.flushWriteBuffer();
      fs.closeSync(this.tempLogFile);
    } catch (error) {
      // Future writes create a new session file.
    }
    this.tempLogFile = undefined;
    this.writeBuffer = [];
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
      if (typeof restoredEvent === 'string' && restoredEvent.length > 0) {
        this.lastEvent = restoredEvent;
      } else if (typeof restoredType === 'string' && restoredType.length > 0) {
        this.lastEvent = restoredType;
      }
    } catch (error) {
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
