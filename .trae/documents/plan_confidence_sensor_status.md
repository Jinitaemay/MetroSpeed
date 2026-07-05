# 修复置信度、传感器状态和定位状态逻辑

## 问题概述

三个模块各有逻辑缺陷：

1. **置信度值** — `computeConfidence` 基线 0.86 偏高、校准老化仅 4 分钟就满扣、Cruise 态不加分、所有扣分叠加无正反馈
2. **传感器状态覆盖** — `startAuxiliarySensors()` 输出只列辅助传感器，覆盖了核心传感器的存在
3. **停止测速后传感器状态** — 研究记录继续时 `sensorStatus` 写死"测速已停止"，尽管传感器已重启

---

## 问题 1：置信度计算

### 当前逻辑

基值 0.86，减去校准时间衰减（max -0.35 @4min）、陀螺仪衰减（max -0.2 @3rad/s）、运动状态罚分。Cruise 不扣分，LowConfidence -0.35，StrongVibration -0.48。夹到 [0.08, 0.95]。

### 问题分析

| 问题 | 根因 | 后果 |
|------|------|------|
| 基线偏高 | 0.86 然后只扣不加 | 刚校准完 Cruise 显示 86%，但巡航不一定代表精度高 |
| 衰减太快 | 240s (4min) 满扣 0.35 | 长程中 4 分钟后不再变化，失去参考意义 |
| 无正反馈 | Cruise/Accel/Braking 都不加分 | 算法无法表达"当前状态有利精度" |
| Cruise 不加分 | 只有罚分没有奖励 | 最可靠的匀速巡航得不到置信度体现 |

### 改动

仅修改 `SpeedEstimator.ets` 中 `computeConfidence()`：

- **基值** 0.86 → 0.72
- **校准衰减** 240s 满扣 → 600s (10min) 满扣
- **新增 Cruise 奖励** `+0.10`
- **新增 Accel/Braking 轻微奖励** `+0.05`（加速制动有清晰信号，比 LowConfidence/振动有价值）
- **下限** 0.08 → 0.05

新公式：
```
value = 0.72
value -= clamp(sinceCalibration / 600000, 0, 0.35)
value -= clamp(gyroMagnitude / 3, 0, 0.2)
value -= statePenalty
value += stateBonus       // 新增
return clamp(value, 0.05, 0.95)
```

状态奖励：
- Cruise: +0.10
- Accel / Braking: +0.05
- Idle: 0
- Curve / LowConfidence / StrongVibration / ConductionVibration / Calibrating: 0

### 影响范围

| 状态 | 旧 min | 旧 max | 新 min | 新 max |
|------|--------|--------|--------|--------|
| Cruise, 刚校准 | 0.08 | 0.86 | 0.05 | 0.82 |
| Cruise, 10min 后 | 0.08 | 0.51 | 0.05 | 0.47 |
| Accel, 刚校准 | 0.08 | 0.86 | 0.05 | 0.77 |
| LowConfidence, 4min 后 | 0.08 | 0.16 | 0.05 | 0.02→clamp 0.05 |
| Calibrating | 0.35 | 0.35 | 0.35 | 0.35（不变） |

---

## 问题 2：传感器状态覆盖

### 当前逻辑

`SensorController.start()` 输出 `已启动传感器：加速度计、陀螺仪`。`startAuxiliarySensors()` 输出 `已启动传感器：重力、线性加速度…` — 后者覆盖前者。

### 改动

修改 `SensorController.ets` 中 `startAuxiliarySensors()` 的 emitStatus，改为汇总所有活跃传感器：

```
已启动传感器：加速度计、陀螺仪、重力、线性加速度、旋转向量、磁力计
```

（仅列出实际可用的传感器）

### 代码位置

[SensorController.ets:L74-L84](file:///c:/Users/18918/Documents/Codex/2026-06-12/new-chat/outputs/MetroSpeed/entry/src/main/ets/model/SensorController.ets#L74-L84)

---

## 问题 3：停止测速后传感器状态

### 当前逻辑

`stopMeasurement()` 写死 `sensorStatus = '测速已停止'`，然后调 `startResearchSensors()`。后者通过 `sensorController.start()` 重新订阅传感器并回调更新 `sensorStatus`，但如果不是测速期（`isRunning=false`），回调会把 `sensorStatus` 写到研究记录模式的状态消息。

由于 `startResearchSensors` 的 sensorCallback 用的是 `onResearchSensorFrame`，不会更新 `sensorStatus`。而 statusCallback 会更新，但中间有一个时序："测速已停止" 先显示，然后 `start()` 的 emitStatus 再覆盖。

实测用户看到的是"测速已停止" 一直不变。

### 根因

`start()` 内部 `stop()` 先停掉所有传感器（包括辅助），然后只订阅加速度计+陀螺仪。`emitStatus` 调的是 `statusCallback`，这个 callback 已经重新绑定了。在非测速的研究记录模式下，`startResearchSensors()` 传入的 statusCallback 会正确写 `sensorStatus`。

但如果 `start()` 失败（acceleration 订阅失败），不会调 `emitStatus`，`sensorStatus` 停留在上次的值 —— 即 "测速已停止"。

### 改动

`stopMeasurement()` 中不再硬编码 `sensorStatus`，改为在研究记录继续时将状态交给 `startResearchSensors`：

```typescript
this.sensorStatus = this.researchStatus.running ? '测速已停止，仍在记录' : '测速已停止';
```

然后在 `startResearchSensors()` 成功后，`sensorController.start()` 的 emitStatus 会自然覆盖。如果研究记录不运行、传感器确实停了，则显示"测速已停止"。

### 代码位置

[Index.ets:L238](file:///c:/Users/18918/Documents/Codex/2026-06-12/new-chat/outputs/MetroSpeed/entry/src/main/ets/pages/Index.ets#L238)

---

## 三个改动的影响范围

| 改动 | 文件 | 行数变化 | 风险 |
|------|------|---------|------|
| 置信度公式 | SpeedEstimator.ets | ~8 行 | 低——公式是纯输出信号，不参与算法决策，只影响 UI 显示和锚点权重 |
| 传感器状态汇总 | SensorController.ets | ~8 行 | 低——纯 UI 文案，不改变传感器逻辑 |
| 停止时状态 | Index.ets | ~3 行 | 极低——仅文案分支 |

---

## 验证

- `python tools/sync_version.py --check` 确认 ALGORITHM_VERSION 双端同步
- 构建 `hvigorw assembleHap` 确认 ArkTS 编译通过
- 置信度验证：在已有记录上跑 python 回放对比新旧置信度序列（`replay_estimator.py` 的 `compute_confidence` 也需同步修改）
