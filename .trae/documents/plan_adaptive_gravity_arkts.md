# 自适应重力源方案 (adaptive-gravity ArkTS)

## 摘要

在 ArkTS 端落地磁力计场景检测器 + 系统重力源切换，解决驾车坡道偏置问题。Python 分析层已验证：驾车 MAE 85%↓、地铁零劣化。

---

## 当前状态

| 组件 | 现状 |
|------|------|
| MAGNETIC_FIELD | 仅在 `startAuxiliarySensors()`（研究记录时）采集 |
| GRAVITY | 同上，仅在研究记录时可用 |
| SpeedEstimator.ets | `ingest()` 第 268 行硬编码 `subtract(rawAcceleration, this.gravityEstimate)`，完全不读 `frame.gravity` |
| SensorFrame | 已有 `gravity?` 和 `magneticField?` 字段（由 SensorController 填入） |
| Python `--adaptive-gravity` | 已通过 2 条 v13 记录验证：苏沪高速 MAE 14.14→2.10，地铁零劣化 |

---

## 方案

### 1. SensorController.ets：磁力计 + 重力提升为核心传感器

**为什么**：场景检测需要磁力计数据判断地铁/驾车，系统重力切换需要 GRAVITY 传感器数据。两者不能依赖研究记录是否开启。

**做什么**：`start()` 中加 `subscribeMagneticField()` 和 `subscribeGravity()`，与加速度计/陀螺仪一样常驻。`stop()` 中对等反订阅。

```typescript
// start() 新增：
this.subscribeMagneticField();
const gravityStarted = this.subscribeGravity();
```

- 低频、低功耗传感器（和陀螺仪相当），不改 50Hz 核心间隔
- 有年龄限制（`MAX_SENSOR_AGE_NS = 60ms`），过期不附加到帧
- `startAuxiliarySensors()` 中删掉 MAGNETIC_FIELD 和 GRAVITY 订阅，避免重复

### 2. SpeedEstimator.ets：加 `useSysGravity` + 场景检测器

#### 2.1 新增字段

```typescript
private useSysGravity: boolean = false;
private scenarioDecided: boolean = false;

// 磁力计滑窗（50 帧，~1s）
private magWindow: number[] = [];
private magStdSamples: number[] = [];
private readonly MAG_WINDOW = 50;
private readonly MAG_STD_SAMPLES = 450;
private readonly MAG_THRESHOLD = 2.5;
```

#### 2.2 `ingest()` 流程

在 `finishCalibrationIfNeeded()` 之前插入场景检测：

```typescript
// 场景检测：前 ~10s 采样磁力计 std，一次判定
if (!this.scenarioDecided && frame.magneticField) {
    const magVal = Math.sqrt(
        frame.magneticField.x ** 2 +
        frame.magneticField.y ** 2 +
        frame.magneticField.z ** 2
    );
    this.magWindow.push(magVal);
    if (this.magWindow.length > this.MAG_WINDOW) {
        this.magWindow.shift();
    }
    if (this.magWindow.length >= this.MAG_WINDOW) {
        const mean = this.magWindow.reduce((a, b) => a + b, 0) / this.magWindow.length;
        const variance = this.magWindow.reduce((s, x) => s + (x - mean) ** 2, 0) / this.magWindow.length;
        this.magStdSamples.push(Math.sqrt(variance));
    }
    if (this.magStdSamples.length >= this.MAG_STD_SAMPLES) {
        const sorted = [...this.magStdSamples].sort((a, b) => a - b);
        const medianStd = sorted[Math.floor(sorted.length / 2)];
        this.scenarioDecided = true;
        this.useSysGravity = medianStd < this.MAG_THRESHOLD;
    }
}
```

#### 2.3 运动分解

`useSysGravity` 在 `subtract()` 处生效：

```typescript
const gravityForMotion = (this.useSysGravity && frame.gravity)
    ? frame.gravity
    : this.gravityEstimate;
const motionAcceleration = subtract(rawAcceleration, gravityForMotion);
```

#### 2.4 校准不动自估重力

用系统重力时，校准不更新 `gravityEstimate`：

```typescript
// finishCalibrationIfNeeded() 中：
if (stableResult) {
    if (!this.useSysGravity) {
        this.gravityEstimate = gravityCandidate;
    }
    // ... 其余校准逻辑不变
}
```

### 3. Index.ets：start() 重置场景检测状态

`startMeasurement()` 和 `start()` / `reset()` 中重置 `useSysGravity` 和 `scenarioDecided`（已在 SpeedEstimator 内部处理）。

---

## 不变的部分

- 自估重力校准逻辑不动
- 停车校准逻辑不动
- 主轴学习/运动状态检测/积分/锚点完全不碰
- 研究记录采集（Schema v13 辅助传感器）不动
- Python `replay_estimator.py` 不动（仅需确认双端一致）

---

## 验证

1. `sync_version.py --check` 确认 `ALGORITHM_VERSION` 双端一致
2. 全量 14 条旧记录 baseline 对比（pure 和 anchor-v2），确认零劣化
3. 用户上机采集市内驾车记录（此前因 c7ab07f bug 缺失），验证场景检测生效
4. 苏沪伪通勤记录回放，确认 MAE 从 14.14 降至 ~2 区间
5. 双端 `ALGORITHM_VERSION` 同步为 `adaptive-gravity-20260705-v1`
