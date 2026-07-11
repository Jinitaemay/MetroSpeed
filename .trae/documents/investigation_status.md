# MetroSpeed 项目工作记忆

> **记忆版本**：v43
> **最后更新**：2026-07-10
> **对应阶段**：全量可靠性修复 — 停车后段重放、后台连续采集、回放/工具一致性、签名配置隔离；算法版本 anchor-delta-20260710-r3

---

## 一、项目基本信息

**项目名称**：MetroSpeed · 地铁测速
**平台**：鸿蒙 HarmonyOS (ArkTS)
**项目路径**：`<项目根目录>`
**算法版本**：`anchor-delta-20260710-r3`
**当前阶段**：v1.1.2 发布候选；停车校准、后台连续采集、研究日志完整性、双端回放和工程工具可靠性修复已完成
**许可证**：MIT
**包名**：`com.codex.metrospeed`
**应用名称**：地铁测速
**版本号**：versionName = "1.1.2"，versionCode = Unix 时间戳（自动生成）
**SDK版本**：compatibleSdkVersion/targetSdkVersion = 5.0.0(12)（保持 API12，不升级 API20）

---

## 二、项目目标

本项目有两个核心目标：

1. **纯惯性测速**：仅用手机加速度计和陀螺仪，不依赖 GNSS 或系统融合输出，实时估算轨道交通/车辆的行驶速度。算法自行分离重力、学习主轴、检测运动状态、积分速度。
2. **复刻鸿蒙隧道定位机制**：在 GNSS 失效的隧道场景中，用惯性推算维持速度输出——模拟鸿蒙系统在隧道内从 GNSS 切换到 IMU dead reckoning 的行为。隧道模式手动切换，入隧时冻结 GNSS 锚点，防止系统推算污染速度。

所有算法设计、数据采集、技术选型均围绕这两个目标展开。

---

## 三、项目核心功能

### 核心算法
- **纯惯性测速**：自研 9 状态检测算法 + 主轴学习 + 重力补偿，从原始传感器数据中提取真实运动加速度，积分得到实时速度
- **9 状态优先级**：`CURVE > CONDUCTION_VIB > STRONG_VIB > LOW_CONFIDENCE > IDLE > ACCEL > BRAKING > CRUISE`
- **GNSS 锚点融合**：GNSS 信号良好时自动启用锚定模式，以 GNSS 速度为锚点叠加惯性增量（pure=0 模式）
- **速度合成公式**：锚定速度 = GNSS锚点 + (当前纯惯性 - GNSS时刻纯惯性)
- **信噪比切换**：`gnssSpeedKmh < speedAccuracyMps × 3.6` 时回退纯惯性，否则用锚点+增量
- **核心原则**：算法仅使用原始加速度计（含重力）+ 陀螺仪，自算重力+统计主轴学习，不依赖系统融合输出

### 关键特性
1. **隧道模式**：用户手动拨动开关切换，入隧时冻结 GNSS 锚点，防止系统推算污染速度。已移除 `refreshGravityAtEntrance()` 调用（行驶中 preCalBuffer 无静止段，会把加速度当重力）
2. **自适应停车校准**：只扫描按钮前 preCalBuffer（180 帧）内最优 75 帧静止段，重估重力后从零速锚重放后续帧；静止严格归零，按钮后立即起步仍保留增量
3. **GNSS -40ms 固定延迟补偿**：所有记录一致显示 locationTimeMs 比传感器时间晚约 40ms，ArkTS 端在锚点采集时用速度历史缓冲区查找 40ms 前的惯性速度，Python 端通过 `--gnss-lag-ms=-40` 补偿
4. **研究记录模式**：全量 50Hz 传感器数据 + GNSS 数据 JSONL 格式记录，支持导出离线分析
   - **Schema v13**：sensor记录新增4个辅助传感器共17个字段，全部用于数据采集验证，**暂未接入算法**
   - 新增字段：系统重力(sysGravityX/Y/Z)、系统线性加速度(linearAccX/Y/Z)、9DOF旋转矢量(rotVecX/Y/Z/W)、磁场(magX/Y/Z)，及各传感器时间戳
   - 现有`gravityX/Y/Z`（estimator记录中）仍为算法自估计重力，与系统传感器输出分开记录方便对比

### 辅助传感器数据采集（v13新增，仅记录不参与算法）
为验证系统传感器融合误差特性、为隧道精度优化提供数据支撑，v13版本开始记录以下4个辅助传感器数据：

| 传感器 | SensorId | API版本 | 输出 | 采集目的 |
|--------|----------|---------|------|----------|
| GRAVITY | sensor.SensorId.GRAVITY | API9+ | 重力向量(x,y,z) | 对比系统融合重力与自估计重力差异，验证"吃小加速度"问题 |
| LINEAR_ACCELEROMETER | sensor.SensorId.LINEAR_ACCELEROMETER | API9+ | 线性加速度(x,y,z) | 验证核心假设：**系统重力 + 系统线性加速度 ≈ 原始加速度计读数** |
| ROTATION_VECTOR | sensor.SensorId.ROTATION_VECTOR | API9+ | 四元数(x,y,z,w) | 9DOF融合含磁力计，分析磁干扰对姿态的影响 |
| MAGNETIC_FIELD | sensor.SensorId.MAGNETIC_FIELD | API9+ | 磁场向量(x,y,z) | 测量隧道内实际磁干扰强度 |

**重要说明**：GAME_ROTATION_VECTOR（6DOF无磁旋转矢量）底层C API支持（ID=262，API13+），但**鸿蒙ArkTS公开API未暴露该SensorId常量**，普通应用无法直接订阅，相关死代码已全部清理，待后续API版本开放后再考虑支持。

**实现细节**：
- 所有传感器统一请求20ms间隔（50Hz），与加速度计/陀螺仪保持一致
- 每个传感器独立订阅，try-catch包裹，不支持时静默跳过，不影响其他传感器
- 和陀螺仪相同的60ms新鲜度判断逻辑，过期数据不写入帧
- 启动后状态文字显示所有可用传感器列表
- DevEco已自动配置debug签名，直接点击运行即可自动签名安装到手机

### refreshGravityAtEntrance() — 已删除
**状态**：调用和 ArkTS/Python 死方法均已删除；入隧只冻结 GNSS 锚点。
**停用原因**：行驶中 preCalBuffer（3.6s）无静止段，扫描出的"最优窗口"会把行驶加速度当重力。驾车浦东大道实测 gY 从 4.29→3.73，10 分钟飙至 991 km/h。

### 停车校准（calibrateAtStop）完整逻辑
**触发方式**：用户点击"停车校准"按钮 → 调用 `SpeedEstimator.calibrateAtStop()`

**详细步骤**：
1. 标记 `parkingCalibrationPending` 并记录按钮时间；重复点击幂等，不重启窗口或增加计数
2. 约 1.5 秒后只在按钮时间之前的缓冲帧中扫描最优 75 帧静止窗；短历史直接拒绝，不再用“当前时点强制归零”兜底
3. 通过陀螺仪均值/最大值、加速度跳动、rmsDeviation、重力模长五道检查后更新 `gravityEstimate`
4. 把静止窗作为零速锚，清空并预热低通/判态短窗，再以新重力逐帧重放静止窗之后的数据；保留主轴初始化、锁定和计数状态
5. 若没有持续起步证据则速度严格置 0；若按钮后已起步则保留重放得到的真实增量
6. 一次性结果 `consumeParkingCalibrationResult()` 返回 1/−1/0；应用只在结果为 1 时重置 GNSS/惯性锚点，并分别记录请求、成功、拒绝事件

**初始校准保护**（07-06 新增）：
- 初始校准完成前，`calibrateAtStop()` 直接返回“请等待初始校准完成”
- 初始 1.5 秒从首个真实传感器样本开始计时，至少需要 30 帧且覆盖 1000ms；迟到首帧不会永远 pending，也不会把 calibrationCount 增为 2

**和普通校准的区别**：
| 方面 | 普通校准 | 停车校准 |
|------|---------|---------|
| 数据来源 | 校准开始后的 1.5 秒 | 历史缓冲区里最优的 75 帧 |
| 速度处理 | 从零开始积分 | 静止窗归零并用新重力重放后段 |
| 主轴处理 | 初始学习 | 保留既有主轴并按新重力正交化 |
| 适用场景 | 启动时第一次校准 | 停车后主动校准，消除漂移 |

### 实时数据面板
- 融合速度（大字显示）
- 纯惯性速度（灰色参考）
- GNSS 速度（蓝色参考）
- 最高速度 / 平均速度 / 行驶时长 / 校准次数

---

## 四、项目结构

```
MetroSpeed/
├── AppScope/
│   └── app.json5                    # 应用配置（versionName: 1.1.2, versionCode: 时间戳）
├── entry/
│   └── src/main/ets/
│       ├── entryability/EntryAbility.ets
│       ├── pages/Index.ets              # 主界面 + 锚点逻辑
│       └── model/
│           ├── SpeedEstimator.ets       # 惯性速度估算核心（仅用原始加速度+陀螺）
│           ├── SensorController.ets     # 50Hz 加速度计+陀螺仪+4个辅助传感器
│           ├── LocationController.ets   # GNSS 定位 + 卫星状态
│           ├── ResearchRecorder.ets     # JSONL 全量记录（schema v14）
│           ├── BackgroundState.ets      # 后台记录状态共享
│           └── SpeedTypes.ets           # 类型定义、向量运算、四元数
├── tools/
│   ├── replay_estimator.py             # 离线回放引擎 + 锚点v2 + --use-sys-gravity 分析开关 — 核心工具
│   ├── _baseline_all.py                # 全量基线对比 (--dir --anchor-v2) — 临时诊断脚本
│   ├── _tunnel_diag.py                 # 隧道分段MAE + 纯速度曲线 — 临时诊断脚本
│   ├── _bias_diag.py                   # cal_0积分不对称 + 重力/主轴追踪 — 临时诊断脚本
│   ├── param_sensitivity.py            # 78参数敏感度扫描（默认 ±20%）— 通用工具
│   ├── sync_version.py                 # 版本号 ArkTS ↔ Python 同步 — 通用工具
│   ├── trim_cal_segment.py             # 裁剪校准段 — 通用工具
│   ├── _scan_anchor_interval.py        # 锚点间隔多进程并行扫描 — 诊断工具
│   ├── _check_gnss.py                  # GNSS检查脚本
│   ├── _speed_profile.py               # 速度剖面分析
│   ├── _speed_series.py                # 速度时间序列分析
│   ├── _run_new_batch.py               # 批量多组参数对比 (--dir/--files) — 诊断脚本
│   ├── _handheld_detector.py           # 手持检测离线验证（gyroRms + zeroCrossingRate）— 诊断脚本
│   ├── _confidence_analysis.py          # 置信度延迟扫描 + 状态误差分析 — 诊断脚本
│   ├── _confidence_calibrate.py         # 置信度全量标定（多进程）— 诊断脚本
│   └── sign_app.ps1                    # 一键签名脚本
├── signing/                             # 签名文件（敏感，不提交）
│   ├── release.p12                     # 密钥库（EC 256位）
│   ├── release.cer                     # 发布证书
│   └── releaseRelease.p7b              # Profile 文件
├── .trae/                              # AI 项目配置
│   ├── rules/project_rules.md          # 项目规则
│   ├── specs/gravity-sensor-integration/ # 重力传感器接入 spec
│   └── documents/investigation_status.md  # 研究状态（本文件）
├── hvigorfile.ts                       # 构建脚本（自动更新 versionCode）
├── build-profile.template.json5        # 构建配置模板（signingConfigs 为空，仓库只保留此模板）
├── build-profile.json5                 # 实际构建配置（.gitignore 排除，含 DevEco 自动填充的 debug 签名）
├── oh-package.json5
├── LICENSE                              # MIT
├── .gitignore
└── README.md
```

**tools/ 目录命名约定**：
- 正常命名（如 replay_estimator.py）：核心/通用工具，长期保留
- `_` 下划线开头（如 _baseline_all.py）：临时诊断脚本，一次性/探索性的，用完可能会清理或合并

---

## 五、数据资产

> 2026-06-28：用户清理了一批过长的、可能产生误导性测试结果的记录，精简了测试集。

### 核心验证记录（精简后）
| 记录 | pure MAE | pure=0(-40ms) | 场景 |
|------|----------|---------------|------|
| 地铁_航津路-保税区北 | 7.23 | **0.03** | 直线，理想 |
| 地铁_上海赛车场-马陆 | 17.33 | **0.10** | 多弯道，rms放宽改善 |
| 磁浮_浦东机场-龙阳路 | 45.87 | **0.04** | 极速306km/h |
| 地铁_沈杜公路-汇臻路(浦江线) | 12.23 | **0.16** | 胶轮APM |
| 地铁_虹桥-浦东机场(市域机场线) | 16.02 | **0.08** | 高速184km/h，rms放宽显著改善 |
| 公交_奉浦快线 | 7.62 | **1.12** | 快速公交 |
| 驾车_东靖路(短) | 3.21 | **0.13** | 城市短途 |
| 驾车_东靖路-沪常高速(长) | 23.89 | **14.52** | 高速+隧道 |

> pure MAE 为 pure inertial 模式 moving MAE，pure=0(-40ms) 为 anchor-v2 + pure-zero + GNSS lag 补偿。

### v13 全传感器记录
| 记录 | pure MAE | pure=0(-40ms) | 辅助传感器 |
|------|----------|---------------|------------|
| 地铁_南京东路-豫园-老西门·新天地_20260629 | — | — | ✅ gyro/grav/la/rot/mag 15076帧全，已采集算法输出 |
| 驾车_苏沪伪通勤_放置在充电位_20260630 | — | 正常段0.46 | ✅ 162753帧/4706定位/81min，延迟扫描-40ms |

**传感器频率发现**：代码请求 50Hz（sensorIntervalMs=20ms），但实际系统输出只有 33~37 Hz。

**数据存放路径**：本地研究记录目录，不纳入版本控制

---

## 六、核心发现与技术要点

### 核心发现
1. **偏置根因**：偏置在原始信号，非参数问题。pure=0 有效原理：绝对速度偏置无限累积，增量只累积几秒
2. **隧道精度问题根因**：**纯惯性积分误差累积**（非重力/主轴漂移）。隧道模式拒绝 GNSS 锚点更新后，前向加速度积分误差持续累积，累积速率与速度×时长正相关。苏沪伪通勤记录 3 次入隧数据验证（2026-07-02）：
   - 段3（291s，入隧时 83.5km/h 高速行驶）：206s 内漂移到 831 km/h（校准前），Python 回放 max=909
   - 段2（513s/8.5min，静止入隧）：max 仅 71.6 km/h，因为速度低、误差累积慢
   - 段1（175s，静止入隧）：max 仅 24.0 km/h
   - **重力估计稳定**：段3 全程 gx/gy/gz 不变（-0.135/4.308/8.850），重力方向偏移 0°（sec 0-200）
   - **主轴稳定**：段3 sec 0-200 主轴方向偏移 0°
   - **filtered fy 分量是真实前向加速度**：sec 120-200 fy 持续为正（0.55-0.78 m/s²），车辆确实在加速
   - **早期错误根因假设已修正**：之前文档写"重力估计缓慢漂移导致前向加速度投影混入重力分量"——数据验证不成立，重力/主轴在隧道内均稳定
   - **sec 210 重力变化（gy 4.308→4.908）是校准时车辆停在坡道导致**，不是行驶中漂移，且发生在 831 km/h 漂移之后
3. **历史踩坑记录**：
   - 系统线性加速度传感器：0.1g起步加速被系统融合吃进重力估计，进入匀速后速度倒退回零——这也是为什么要采集数据验证"重力+线性加速度是否等于原始加速度"
   - 未校准加速度计：系统bias恒为0，无法去偏
   - 陀螺仪积分旋转矩阵：MEMS零偏累积，几分钟后重力方向漂移速度爆炸
   - gyro gravity（ω×g）：同样受陀螺仪零偏影响，重力方向歪掉
4. **核心教训**：加速度计本身分不清重力和0.1g的起步加速，任何依赖系统融合分离重力的输出都可能继承这层误差——必须先采集数据验证，不直接接入算法
5. **GNSS -40ms 固定延迟**：全量记录一致显示 locationTimeMs 比传感器时间戳 ~40ms
6. **传感器实际频率只有 33~37 Hz**：代码请求 50Hz，但系统实际输出达不到。
7. **鸿蒙API坑**：GAME_ROTATION_VECTOR底层C API支持，但ArkTS公开API未暴露SensorId常量，普通应用无法订阅；LINEAR_ACCELERATION正确名称是LINEAR_ACCELEROMETER，响应类型是LinearAccelerometerResponse
8. **API版本选择**：不升级API20，因为API20也不支持GAME_ROTATION_VECTOR，反而会导致Button等系统组件默认样式变化（自定义borderRadius在API12不生效，用系统默认胶囊形；API20自定义样式生效导致圆角变小），且之前beta API问题是DevEco Beta版导致的，不是API12本身问题
9. **rawAcc ≈ sysGravity + linearAcc 已验证成立**：首条 v13 全传感器地铁记录（南京东路→新天地），15076帧上中位偏差 0.05 m/s²，|la|>0.5 加速段中位 0.12 m/s²。等式在地铁动态场景成立，系统传感器的拆分自洽。
10. **地铁地板微振阻止初始校准**：列车停站时空调/铁轨传导的高频微振（~10-50Hz）导致加速度计 rmsDeviation=0.20，超过旧阈值 0.12。现象：设备放地铁地板上，用户按"开始"后几秒显示"开始失败：初始校准不稳"。陀螺仪无异常（gyroMax=0.048），重力值正常（9.81±0.004），纯 rmsDeviation 超标。
11. **隧道模式阻止 GNSS 锚点导致长隧道惯性漂移**：苏沪伪通勤记录（81min）中 3 次手动入隧（44-65min，最长单段 8.5min），`tunnelState='inside'` 完全拒绝 GNSS 锚点更新（Index.ets#L171）。惯性在 8.5min 无锚点修正后漂移至 361 km/h，校准时达 831 km/h。正常段（排除隧道）锚点 v2 MAE = 0.46 km/h。隧道模式拒绝锚点是刻意设计——复刻鸿蒙系统隧道定位机制，漂移是纯惯性测速的固有限制。
12. **鸿蒙系统隧道定位机制 = IMU 惯性推算**：苏沪伪通勤记录数据分析确认，系统在隧道内不是接收真实 GNSS 信号，而是 IMU dead reckoning。证据：入隧后 `satelliteFixCount` 从 71→5 并恒定，`satelliteCount=57` 全程不变，`accuracy` 从 5m 劣化到 40m 并精确卡住。推算持续约 7 分钟后置信度耗尽，降级到基站定位（srcType=2, speed=0, accuracy=141m）。偏离路网的原因是陀螺仪零偏导致航向积分累积旋转。
13. **系统重力传感器分场景结论**：`--use-sys-gravity` 模式对比（2 条 v13 记录）显示**地铁 NO-GO / 驾车有效**：
    - **地铁场景吃加速度（NO-GO）**：中位速度从 40.9 km/h 塌到 0.6 km/h——系统重力将地铁起步加速吃进重力分量，与 4 月踩坑一致
    - **驾车场景显著有效**：苏沪伪通勤记录（81min，含 3 次隧道段）anchor-v2+pure-zero 模式下，moving MAE 从 14.14 → 1.86 km/h（87%↓），bias 从 13.80 → 0.47 km/h，pure maxKmh 从 909 → 80 km/h
    - **修正早期错误论断**：之前文档写"驾车改善实为系统自比不可信，系统重力隐含 GNSS 推算"——**此论断错误**。鸿蒙 GRAVITY 传感器是 9DOF 融合（加速度计+陀螺仪+磁力计），**不融合 GNSS 速度**；数据反证：若依赖 GNSS，隧道段（GNSS 失效）应突然变差，但系统重力模式隧道段 maxAbsKmh 仅 58（vs 默认 231），磁力计提供绝对航向参考抑制了陀螺仪零偏漂移
    - **当前状态**：驾车有效但地铁无效，差异来自运动模式不同（地铁起步加速被系统融合误判为重力分量），待研究场景自适应切换方案
14. **`--adaptive-gravity` 分析层验证通过**：磁力计场景检测器 + 场景切换重力源，在两条数据完整的 v13 记录上验证：
    - **方案**：前 500 帧（~15s）采集磁力计 |mag| 滑窗 std 中位数，判定一次场景（阈值 2.5μT），之后不再切换。判定为驾车→全程启用系统重力，判定为地铁→全程自估重力
    - **地铁记录**（南京东路→新天地）：medianStd=4.26→判地铁→全程自估→median=40.928（与默认完全一致，零劣化）
    - **苏沪高速**（81min）：medianStd=2.01→判驾车→全程系统重力→anchored moving MAE=2.10（vs 默认 14.14，85%↓），接近 sys-gravity 的 1.86（差 0.24 来自前 15s 判定期用自估重力）
    - **设计演进**：逐帧切换不可行——磁力计 std 在地铁运行中频繁波动（175 次切换），任何切到驾车的帧都会让系统重力吃掉加速度，积分特性导致速度塌掉无法恢复。一次判定避开了所有逐帧切换问题
    - **市内驾车记录（07-03）数据缺失**：magX/sysGravityX 仅 0.0% 非空（15/109064 帧），原因是 c7ab07f 引入的辅助传感器丢失 bug，已修复，待重新采集验证
15. **手持设备检测系统**（07-05 完成）：
    - **方案**：40 帧滑窗（~0.8s）计算陀螺仪 RMS + 三轴 zeroCrossingRate。当前止血阈值 gyroRms > 0.5 + ZCR > 5/s + 40 帧（~0.8s）持续确认 → 触发手持
    - **与车辆运动区分原理**：车辆急弯 RMS 可达 0.4 但零交叉率 ≈0.5/s（单方向转弯），手持零交叉率 37+ /s（手频繁换向）。路面颠簸 RMS 短暂升高但 ZCR 不高 + 0.8s 持续判决过滤
    - **验证**：14 条记录（地铁×7、驾车×3、公交×3、磁浮×1）零误触发。唯一触发是真手持（公交记录中靠在车窗上触发）
    - **UI**：SpeedPanel + StatsGrid 红色不透明（#DC2626）覆盖，三行引导文案："请将设备稳定放置，停车时重新开始测速，设备移动会中止测速"
    - **行为**：触发后调用 `stopMeasurement()` 终止测速，传感器状态由研究记录传感器自然覆盖，永不自恢复。开始测速时重置所有手持检测缓冲区
16. **置信度公式重写**（07-05）：基线 1.0、倍率衰减模型：时间衰减×状态倍率（弯道×3/加速×2/振动×4/传导振动×1.5）+ 陀螺噪声项。pureMode 双速率（锚点 3min 触底 / 纯惯性 2min 触底）。停车校准请求/拒绝不刷新成功校准时龄，只有确认成功才重置衰减基准。22.9万帧 14 条记录标定验证单调性成立（10%→56.7 vs 90%→9.2 km/h P90）。双端同步
17. **传感器状态汇总**：`startAuxiliarySensors()` 由仅列出辅助传感器改为列出全部可用传感器（加速度计、陀螺仪、重力、线性加速度、旋转向量、磁力计），避免覆盖测速启动时显示的核心传感器信息
18. **停止测速后记录传感器不重启 bug**：`sensorController.stop()` 关闭全部传感器后 `researchSensorActive` 未重置，导致 `startResearchSensors()` 的 `if (researchSensorActive) return` 短路。修复：`stopMeasurement()` 中加 `this.researchSensorActive = false`
19. **adaptive-gravity ArkTS 双端落地→已移除**（07-06）：磁力计场景检测器从 Python 分析层落地 ArkTS SpeedEstimator——前 500 帧滑动 std 中位数判定场景，驾车启用系统重力 (useSysGravity=true)，地铁用自估重力。后经 4 条新记录验证被移除：公交浦东100路 medianStd=0.19→错判驾车→系统重力吃加速→速度崩坏（MAE 1.73→6.92↓300%）；驾车苏沪新记录 medianStd=2.82→错判地铁→漏掉增益。且系统重力在新版偏置已修复的驾车记录上增益微弱（0.72→0.66）
20. **初始校准期间禁止停车校准**（07-06）：新增 `initialCalibrationDone` 标志位——初始校准（beginCalibration→preCalBuffer 75 帧完成）之前 caribrateAtStop 被拒止（ArkTS 返回"请等待初始校准完成"状态文本，Python 返回 False）。根因：初始校准期间点停车校准会覆盖 `calibrationUntilMs`，而 preCalBuffer 不足 75 帧导致后续所有校准失败
21. **手持检测 RMS+ZCR 算法失效**（07-06 确认）：公交浦东100路硬质表面上 10 次 stop 中大部分由手持误触发。根因：底盘高频振动在三个轴上同时过零——所有记录 ZCR P50=20-35，ZCR>5 阈值形同虚设；gyro RMS 均值法用 8s 窗仍压不掉公交底盘的 gyro_mean_mag（P99=0.109 vs 车窗 P99=0.054，区间重叠）。真手持数据缺乏。v1.1.1 将 GYRO_RMS 临时上调至 0.5（max_streak=36<40）。
22. **版本号纪律**（07-06 沉淀）：只有 SpeedEstimator 内部逻辑变更才改 ALGORITHM_VERSION；`sync_version.py` 同时检查 ArkTS、Python 和 README，并在写入前完成输入与四份 staged 文本验证
23. **入隧重力刷新必须移除**（07-07 确认）：`refreshGravityAtEntrance` 用 3.6s 滑动窗口从行驶数据中扫"最像静止的 1.5s"，任意时刻调用都会把行驶加速度当重力。驾车浦东大道记录 gY 从 4.29→3.73 导致 10 分钟飙到 991 km/h。地铁同理——入隧时车在高速行驶，buffer 里没有静止段。入隧只需冻结 GNSS 锚点
24. **手持停止需标记来源**（07-07）：`stopMeasurement` 增加 `reason` 参数，JSONL 中区分 `handheld` vs `manual` 停止
25. **测速与研究记录均需后台连续采集**（07-10）：`BackgroundState` 分离 `measurementActive` / `recordingActive`，任一活动存在时 `EntryAbility` 启动 LOCATION 长时任务，不再暂停普通测速传感器。增加操作代次校验，权限请求返回后只继续当前有效会话；长时任务启动结果使用前后台 generation 校验，避免回前台后异步启动滞留；停止失败有界重试。长时任务启动失败时暂停传感器，回前台恢复。同步补齐 `SensorController.stop()` 遗漏的磁力计退订。此项不改 SpeedEstimator 算法，ALGORITHM_VERSION 不变
26. **停车校准未保证归零/立即起步丢失**（07-10 修复）：旧实现仅做“当前速度−历史窗末速度”，没有用新重力重算后段，静止可从 1.47km/h 变成 1.73km/h；成功后还清空主轴。现改为按钮前静止窗零速锚 + 新重力后段重放，且请求不再重置积分时间基准。点击时冻结按钮前证据，避免 100Hz 回调在 1.5s 等待期挤掉 75 帧窗口；候选窗末帧必须在点击前 300ms 内，避免复用过旧静止段；拒绝不刷新成功校准时龄。确定性验证 50/100Hz 停稳严格归零、按钮后立即起步保留正速度、拒绝轨迹零扰动
27. **应用生命周期与研究数据保全**（07-10）：GNSS 锚只在停车成功结果后归零；GNSS 速度精度缺失/非正数明确回退纯惯性；页面 emitter 在销毁时注销；后台采集状态变化会即时收口/重启长时任务。研究日志维持单份覆盖语义，开始新记录时清理旧会话并截断固定本地文件；导出与新记录互斥，成功复制后才删除捕获的本地源文件；写入失败会同步停止后台记录状态。异常中断、缺少 `stop_record` 或尾部不可读时保留完整性 sidecar，界面告警并以 `INCOMPLETE` 文件名导出
28. **离线回放/工具链漂移**（07-10）：修复停车 event 零锚不可达、GNSS 可靠性 gate、pureMode 双速率、重复“测速已在运行”误重置、runId/measurementActive 缺失边界、appParity 漏检锚点参数、reset 丢参数、坏 JSONL/同路径覆盖/非原子输出/空锚点。回放保留 JSONL 追加顺序，不再按可回拨墙钟全局重排；锚点按位置回调顺序激活，只读取回调前最近 5 帧的 -40ms 样本；GNSS 对比按 `measurementRunId` 隔离，禁止跨测速段插值。基线无有效 GNSS 指标、无成功样本或任一失败现返回非零，参数扫描与当前 CLI 参数自动验真，多个诊断脚本直接崩溃和旧字段已修
29. **记录口径升级 schema v14**（07-10）：旧 schema v13 的 `estimatedSpeedKmh` 实为 GNSS 锚定后的界面显示速度，曾被离线工具误作纯惯性回归基准。v14 显式记录 `pureInertialSpeedKmh` / `displaySpeedKmh`，传感器行补齐 session/run/version 元数据并逐实际回调落盘；估算器回归按相邻 `recordSeq` 精确配对，不再跨缺失帧插值。旧 v13 只报告纯惯性比对不可用，不再产生伪差值
30. **工程安全**（07-10）：`build-profile.json5` 已从 Git 索引移除并由 `.gitignore` 排除，本地文件保留；`sync_version.py` 使用进程锁、compare-and-swap、原换行保留与失败回滚防并发半写，并默认拒绝版本降级；`sign_app.ps1` 默认官方 `-pwdInputMode 1` 交互密码并阻止输出覆盖/路径别名。历史中的旧配置是否清理、签名材料是否轮换需按远端暴露情况单独决策

### 技术要点
- **时间源**：`computeDeltaSeconds` 优先 sensorTimestamp，其余用 Date.now()（墙上时间语义），双轨正确
- **双端一致**：估算器配置与逐帧逻辑一致，`ALGORITHM_VERSION` 由 `sync_version.py --check` 在 ArkTS、Python、README 三处验证。分析层可自由扩展
- **LocationSourceType**：1=GNSS, 4=RTK，`tunnelState !== 'inside'` 是防系统推算冒充的唯一防护
- **传感器类功能开发流程**：必须遵循"先在研究记录中加字段采集数据→观察实际数据表现→再决定是否接入算法"，禁止在没有数据支撑的情况下直接修改算法
- **UI兼容性**：API12下Button组件自定义borderRadius不生效，使用系统默认胶囊形，不要手动设置圆角数值，升级API版本后需要重新验证所有组件样式
- **replay_estimator.py 分析层开关**：`--use-gyro-gravity`（陀螺仪重力追踪，已验证失败）、`--use-sys-gravity`（系统重力替代自估重力，地铁 NO-GO / 驾车在偏置未修复时有效，偏置修复后增益微弱 0.72→0.66）、`--adaptive-gravity`（磁力计场景检测器，4 条新记录验证失败，已从 ArkTS 移除但分析层开关保留）

---

## 七、签名与上架

**签名文件**（`signing/` 目录，不提交 git）：`release.p12`（EC 256 位密钥库）、`release.cer`（发布证书）、`releaseRelease.p7b`（Profile）。`sign_app.ps1` 默认使用官方交互密码模式；仅显式 `-NonInteractivePassword` 时读取 `METROSPEED_KEYSTORE_PASSWORD`，且受 hap-sign-tool 接口限制会出现在 Java 进程参数中。

**debug 签名**：DevEco Studio 自动配置，直接点运行即可。`build-profile.json5` 已移出版本控制，仓库只保留 `build-profile.template.json5` 模板。首次 clone 后需复制模板再让 DevEco 填充签名。

**构建与签名命令**见 `project_rules.md` 2.2 节。release 签名的包不能 hdc install 直接安装，只能通过应用市场分发。

**当前状态**：AppGallery v1.1.1 已发布；v1.1.2 可靠性修复候选正在完成发布构建、正式签名与真机回归。

---

## 八、项目规则

完整规则见 `.trae/rules/project_rules.md`。关键约束：
- **Python-ArkTS 一致性**：`SpeedEstimator` 类 bug-for-bug 复现，分析层可自由扩展
- **算法改动必须全量验证**：所有 JSONL 记录跑 baseline 对比，不得仅凭单条记录
- **传感器开发流程**：先采集数据观察，再决定是否接入算法
- **代码修改纪律**：任何修改等用户明确指令后再执行
- **不轻易升级 SDK**：API12 稳定，升级会导致组件样式变化

---

## 九、项目时间线

| 日期 | 阶段 | 关键动作 |
|------|------|----------|
| 04月 | 弃案 | 网页应用 + 系统线性加速度传感器，被融合误差吞掉起步加速 → 搁置一个多月 |
| 06-12~25 | 基建→发布 | 核心算法成；JSONL 记录 + Python 回放引擎；9条验证记录；偏置根因 + pure=0 锚定 MAE sub-2 km/h；GNSS -40ms 延迟补偿；MIT 许可证；签名链路；提交 AppGallery 审核；README 重写；开源上线 GitHub |
| 06-26~27 | 审核修复 | 退后台传感器占用、Scroll 回弹、对比度修复；beta API 被拒→降级 DevEco 6.1.1 Release 重新构建 |
| 06-28 | 传感器采集+工程清理 | 4 辅助传感器（GRAVITY/LINEAR_ACCELEROMETER/ROTATION_VECTOR/MAGNETIC_FIELD）采集功能；schema v13；GAME_ROTATION_VECTOR 确认不支持，清理死代码；build-profile.json5 移出版本控制改用 template 机制 |
| 06-29 09:49 | 上架通过 | AppGallery 审核通过（versionCode=1782556056），"地铁测速" 1.0.0 正式上架 |
| 06-29 | 数据验证+阈值调整 | 首条 v13 地铁记录：rawAcc ≈ sysGravity + linearAcc 成立；地铁地板微振 rmsDeviation=0.20 超标，阈值 0.12→0.25 |
| 06-30 | 长途驾车验证 | 第 2 条 v13 记录：苏沪伪通勤 81min/162753帧；正常段锚点 v2 MAE=0.46；3 次入隧惯性漂移至 831 km/h |
| 07-02 | 重力传感器分析 | `--use-sys-gravity` 分析工具；分场景对比：地铁 NO-GO（中位 40.9→0.6），驾车有效（MAE 14.14→1.86，87%↓）；确认系统隧道定位为 IMU 惯性推算 |
| 07-02 | 隧道漂移根因验证 | 修正早期"重力估计漂移"错误根因，确认实际为纯惯性积分误差累积（重力/主轴在隧道内均稳定） |
| 07-03 | 发布 1.0.1→1.1.0 | ①校准阈值放宽 ②传感器按需启动 ③schema v13 ④权限文案 ⑤长记录读取优化；1.0.1 上架后发现状态文本 bug，引入手持防呆后升为 1.1.0 |
| 07-03 | 坡道偏置根因分析 | 新驾车记录 baseline MAE=156 km/h 崩溃；根因：下坡入地时 gY 偏移 0.51 m/s²，互补滤波冻结错误重力 1200 秒；校准 #7 坡道偏置 Δ=-0.35 m/s²（≈2°）；算法盲区：只验证传感器静止，不验证路面水平 |
| 07-03 | 磁力计场景检测器验证 | 坡道偏置与系统重力场景自适应是同一任务；磁力计 std 可分离地铁（4.38μT）与驾车（1.55μT）/公交（0.52μT），阈值 2.5μT |
| 07-04 | `--adaptive-gravity` 验证 | 分析层实现磁力计场景检测器 + 一次判定切换；地铁零劣化，苏沪高速 85%↓；逐帧切换不可行，一次判定解决 |
| 07-04 | 辅助传感器丢失 bug 修复 | c7ab07f 引入回归：`start()` 内部 `stop()` 停掉辅助传感器后 `startMeasurement()` 漏恢复；修复：成功后若研究记录在运行则调 `startResearchSensors()` |
| 07-04 | UI 文案优化 + 1.1.0 构建 | 状态文本去"研究"二字；灰色提示补充上下坡/姿态限制说明；构建签名 1.1.0 release 包 |
| 07-05 | 手持检测系统 | 陀螺仪 RMS + zeroCrossingRate 双指标滑窗检测，40 帧确认触发，14 条记录零误触发。触发 stopMeasurement() + 红色不透明覆盖 SpeedPanel/StatsGrid，永不自恢复 |
| 07-05 | 置信度重写/传感器/文案 | ①置信度倍率衰减模型（基线 1.0、弯道×3/加速×2/振动×4、pureMode 双速率、3min 触底、校准不重置）；②传感器状态汇总、停止后重启 bug 修复、启动失败后 researchSensorActive 复位；③文案优化（三行引导、底部精简）；④22.9万帧标定验证 |
| 07-06 | adaptive-gravity ArkTS 落地 | 磁力计场景检测器从 Python 分析层 → SpeedEstimator.ets；前 500 帧 mag std 中位数判定场景（阈值 2.5μT）；驾车启用 `useSysGravity=true`（系统重力不冻结、持续修正），地铁保持自估重力；`calibrateAtStop`/`refreshGravityAtEntrance` 增加 `useSysGravity` guard；SensorController 将磁力计+重力提升为核心传感器 |
| 07-06 | 初始校准停车校准保护 | 新增 `initialCalibrationDone` 标志位；preCalBuffer 不足 75 帧时 `caribrateAtStop()` 被拒止，ArkTS 显示"请等待初始校准完成"；`applyParkingZero()` 成功后置 `initialCalibrationDone=true` |
| 07-06 | v1.1.1 止血发布 | ①移除 adaptive-gravity（4条新记录验证磁力计 std 无法可靠分离公交/驾车：浦东100路公交 medianStd=0.19→错判驾车→MAE 1.73→6.92↓300%；苏沪高速新_0705 medianStd=2.82→错判地铁→漏掉系统重力增益；系统重力在偏置已修复的新版驾车记录上增益仅 0.72→0.66，不构成保留理由）；②手持阈值 GYRO_RMS 0.3→0.5（浦东100路 max_streak=36<40 不再误触；靠在车窗记录 rms>0.5 仅 70 窗=0.5%，也不触发）；③磁力计回归辅助传感器；④ALGORITHM_VERSION → anchor-delta-20260707-r2 |
| 07-06 | 手持检测算法失效分析 | RMS+ZCR 双指标本质区分不了底盘振动和手持晃动——所有记录 ZCR P50 为 20-35（底盘高频振动导致三轴频繁过零），当前 ZCR>5 阈值形同虚设；gyro_mean_mag 用 8s 长窗仍无法分离（浦东100 P99=0.109 vs 车窗 P99=0.054，区间重叠）。缺乏真手持记录，新算法需重新设计 |
| 07-07 | 移除入隧重力刷新 | `refreshGravityAtEntrance` 在入隧时从 preCalBuffer（3.6s）扫最优窗口重算重力，但行驶中 buffer 全是加速度数据。驾车浦东大道-延安东路记录因此从正确重力 [−0.16,4.29,8.87] 被拉到错误 [−0.02,3.73,9.16]，10 分钟积分飙至 991 km/h。入隧/出隧现在只切换 tunnelState 冻结 GNSS 锚点，不再调用 refreshGravityAtEntrance |
| 07-07 | 手持停止标记 | `stopMeasurement(reason)` 增加参数区分 `'handheld'` vs `'manual'` 停止，JSONL 中停止事件的 notes 字段标记 `reason=handheld\|manual` |
| 07-07 | 惠南东-新场速度跳变调查 | 手机 v1.1.0（adaptive-gravity）记录出现 13→71、27→90→0 等速度跳变。经回放验证：当前 v1.1.1 revert 后完全无法复现——确认是 adaptive-gravity 在低置信度 + 系统重力下的产物，已随 revert 消除 |
| 07-07 | 公交 71 路区间 / 地铁大连路-世纪大道 记录 | 公交 71 路纯惯性 MAE 8.89→锚点 3.83，手机侧躺轮壳重力正常；地铁全地下无 GNSS，积分 3.15km/4 站，6 次手动校准正常 |
| 07-08 | 发布 1.1.1 最终包 | 签名、同步 README 应用介绍、commit 规范写入项目规则、移除过时计划文档 |
| 07-10 | 全量可靠性修复 | 停车校准改为按钮前近期静止窗 + 新重力后段重放；初始样本保护；测速/研究后台连续采集；GNSS 仅成功归零且无有效精度时回退；研究日志 schema v14/单文件覆盖与完整性标记；回放按 run 和真实回调时序隔离；诊断工具、版本同步和签名脚本系统性修复；本地 build-profile 正式解除 Git 跟踪 |
| 07-11 | v1.1.2 发布候选 | versionName 切换至 1.1.2；算法版本 `anchor-delta-20260710-r3`；开始发布构建、正式签名与真机验收 |
---

## 十、当前任务状态

**最近完成的任务**（早期基建工作见时间线 06-12~25）：
见时间线 2026-07-01 之后条目及核心发现第 15-18 条，此处不重复。以下仅列出本节独有的待执行任务备忘。

**待执行任务（按优先级）**：
1. 🟡 重力估计可靠性修复（坡道偏置用系统重力消化，场景自适应方案待重新设计）
   - ✅ 磁力计场景检测已移除 — 4 条新记录验证无法靠磁力计 std 可靠分离公交/驾车
   - ✅ 停车校准保护（`initialCalibrationDone`）保留
   - 坡道偏置子问题仍待解决；system gravity 在偏置已修复的新版驾车记录上增益微弱（0.72→0.66），需要找真正有效的分离信号
2. 🟡 手持检测算法重新设计
   - ✅ 阈值临时上调至 0.5（止血），暂不误触
   - 当前 RMS+ZCR 方案在有振动传导的硬质表面上无法区分底盘振动 vs 手持摇晃
   - 下一步：需采集真手持记录 → 分析可行指标（加速度姿态变化、ROTATION_VECTOR 角速度等）
3. 🟢 多语言支持（英文）
4. 🟢 后台长时采集真机稳定性测试（代码已支持测速/研究记录，仍需补充 lifecycle background/foreground 事件记录并验证系统限电策略）
5. 🟢 历史记录管理界面

---

## 十一、重要提醒与注意事项

**行为规范**：
1. **不自作主张改代码**：任何修改必须先问用户。不替用户做技术决策。
2. **不看文档信"已完成"**：看实际代码验证。
3. **signing/ 绝对不能提交 git**；`.trae/` 要保留，随仓库提交。

**功能澄清**：
4. **隧道模式手动切换**，不是自动检测。拒绝 GNSS 锚点是刻意设计（复刻鸿蒙系统隧道定位行为）。
5. **refreshGravityAtEntrance() 已删除**；入隧只冻结 GNSS 锚点，不在行驶中刷新重力。
6. **4 个辅助传感器只做数据采集**，SpeedEstimator.ets 一行都不要改。

**技术坑**：
7. **GAME_ROTATION_VECTOR**：ArkTS 公开 API 不支持，不要为此写 Native C API。
8. **API 名称坑**：线性加速度传感器是 LINEAR_ACCELEROMETER（不是 LINEAR_ACCELERATION），响应类型 LinearAccelerometerResponse。
9. **c7ab07f 回归教训**：拆分启动逻辑时必须检查所有调用方，特别是 `start()` 内部会 `stop()` 这个隐式副作用。

> 核心发现第 11-14 条已包含隧道定位机制、系统重力分场景、adaptive-gravity 验证等结论，此处不再重复。
