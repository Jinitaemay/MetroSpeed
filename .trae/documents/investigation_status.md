# MetroSpeed 项目工作记忆

> **记忆版本**：v20
> **最后更新**：2026-06-28
> **对应阶段**：4传感器实现入库，build-profile 隔离 debug 签名，脚本参数化，待路测采集数据验证

---

## 一、项目基本信息

**项目名称**：MetroSpeed · 地铁测速
**平台**：鸿蒙 HarmonyOS (ArkTS)
**项目路径**：`<项目根目录>`
**算法版本**：`anchor-delta-20260626-r1`
**当前阶段**：v1.0.0已开源，AppGallery审核已提交，4个辅助传感器数据采集功能完成，待路测
**许可证**：MIT
**包名**：`com.codex.metrospeed`
**应用名称**：地铁测速
**版本号**：versionName = "1.0.0"，versionCode = Unix 时间戳（自动生成）
**SDK版本**：compatibleSdkVersion/targetSdkVersion = 5.0.0(12)（保持API12，不升级API20）

---

## 二、项目核心功能

### 核心算法
- **纯惯性测速**：自研 9 状态检测算法 + 主轴学习 + 重力补偿，从原始传感器数据中提取真实运动加速度，积分得到实时速度
- **9 状态优先级**：`CURVE > CONDUCTION_VIB > STRONG_VIB > LOW_CONFIDENCE > IDLE > ACCEL > BRAKING > CRUISE`
- **GNSS 锚点融合**：GNSS 信号良好时自动启用锚定模式，以 GNSS 速度为锚点叠加惯性增量（pure=0 模式）
- **速度合成公式**：锚定速度 = GNSS锚点 + (当前纯惯性 - GNSS时刻纯惯性)
- **信噪比切换**：`gnssSpeedKmh < speedAccuracyMps × 3.6` 时回退纯惯性，否则用锚点+增量
- **核心原则**：算法仅使用原始加速度计（含重力）+ 陀螺仪，自算重力+统计主轴学习，不依赖系统融合输出

### 关键特性
1. **隧道模式**：用户手动拨动开关切换，入隧时冻结 GNSS 锚点，防止系统推算污染速度；同时调用 `refreshGravityAtEntrance()` 尝试刷新重力估计（扫描 preCalBuffer 最优 75 帧，五道稳定性检查，只更新 gravityEstimate，不动速度/主轴/锚点）
2. **自适应停车校准**：停车时点击校准按钮，自动扫描 preCalBuffer（180 帧环形缓冲区，约 3.6 秒）内最优 75 帧（1.5 秒）静止段，速度补偿 + 重力重估
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

### refreshGravityAtEntrance() 详细逻辑
**功能定位**：只刷新重力估计向量，不重置速度、不重置主轴、不重置锚点。

**执行流程**：
1. 从 `preCalBuffer`（180帧环形缓冲区 ≈ 3.6秒）中扫描
2. 找出 rmsDeviation 最低的 75 帧（1.5秒）滑动窗口
3. 五道传感器稳定性检查：
   - 陀螺仪均值 < 0.08 rad/s
   - 陀螺仪最大值 < 0.25 rad/s
   - 加速度跳动 < 0.65 m/s²
   - rmsDeviation < 0.12 m/s²
   - 重力模长误差 < 0.25 m/s²
4. 通过检查 → 更新 `gravityEstimate`
5. 失败 → 返回 false，什么都不改

### 停车校准（calibrateAtStop）完整逻辑
**触发方式**：用户点击"停车校准"按钮 → 调用 `SpeedEstimator.calibrateAtStop()`

**详细步骤**：
1. 标记待校准状态：`parkingCalibrationPending = true`，同时启动一段 1.5 秒的普通校准（作为兜底）
2. 持续记录原始数据：preCalBuffer（180 帧环形缓冲区，约 3.6 秒）一直在记录
3. 1.5 秒后判定（finishCalibrationIfNeeded）：
   - 如果 parkingCalibrationPending 且缓冲区够 75 帧：
     - 扫描最优窗口：在 preCalBuffer 里滑动 75 帧窗口，找 rmsDeviation 最低的那一段
     - 五道稳定性检查（陀螺仪均值/最大值、加速度跳动、rmsDeviation、重力模长误差）
     - 通过检查 → 更新 gravityEstimate + 调用 applyParkingZero()
     - 没通过 → 校准拒绝，10 秒内不再自动校准
   - 否则（普通模式）：用校准期间采集的 1.5 秒数据算平均重力

**applyParkingZero() 做了什么**：
1. 速度补偿：velocityMps = max(0, velocityMps - parkingWindowEndVelocityMps)，用校准窗口末端速度作偏移量归零
2. 清空滤波状态：filteredAcceleration 重置
3. 清空窗口帧：windowFrames = []
4. 重置主轴：mainAxisInitialized = false，主轴重新学习
5. 重置原始加速度：lastRawAcceleration = undefined

**和普通校准的区别**：
| 方面 | 普通校准 | 停车校准 |
|------|---------|---------|
| 数据来源 | 校准开始后的 1.5 秒 | 历史缓冲区里最优的 75 帧 |
| 速度补偿 | ❌ 不补偿 | ✅ 补偿 |
| 重置主轴 | ❌ 不重置 | ✅ 重置 |
| 适用场景 | 启动时第一次校准 | 停车后主动校准，消除漂移 |

### 实时数据面板
- 融合速度（大字显示）
- 纯惯性速度（灰色参考）
- GNSS 速度（蓝色参考）
- 最高速度 / 平均速度 / 行驶时长 / 校准次数

---

## 三、项目结构

```
MetroSpeed/
├── AppScope/
│   └── app.json5                    # 应用配置（versionName: 1.0.0, versionCode: 时间戳）
├── entry/
│   └── src/main/ets/
│       ├── entryability/EntryAbility.ets
│       ├── pages/Index.ets              # 主界面 + 锚点逻辑
│       └── model/
│           ├── SpeedEstimator.ets       # 惯性速度估算核心（仅用原始加速度+陀螺）
│           ├── SensorController.ets     # 50Hz 加速度计+陀螺仪+4个辅助传感器
│           ├── LocationController.ets   # GNSS 定位 + 卫星状态
│           ├── ResearchRecorder.ets     # JSONL 全量记录（schema v13）
│           ├── BackgroundState.ets      # 后台记录状态共享
│           └── SpeedTypes.ets           # 类型定义、向量运算、四元数
├── tools/
│   ├── replay_estimator.py             # 离线回放引擎 + 锚点v2 (--pure-zero 匹配手机) — 核心工具，无下划线
│   ├── _baseline_all.py                # 全量基线对比 (--dir --anchor-v2) — 临时诊断脚本，下划线开头
│   ├── _tunnel_diag.py                 # 隧道分段MAE + 纯速度曲线 — 临时诊断脚本
│   ├── _bias_diag.py                   # cal_0积分不对称 + 重力/主轴追踪 — 临时诊断脚本
│   ├── param_sensitivity.py            # 83参数 ±50% 敏感度扫描 — 通用工具
│   ├── sync_version.py                 # 版本号 ArkTS ↔ Python 同步 — 通用工具
│   ├── trim_cal_segment.py             # 裁剪校准段 — 通用工具
│   ├── _scan_anchor_interval.py        # 锚点间隔多进程并行扫描 — 诊断工具
│   ├── _check_gnss.py                  # GNSS检查脚本
│   ├── _speed_profile.py               # 速度剖面分析
│   ├── _speed_series.py                # 速度时间序列分析
│   ├── _run_new_batch.py               # 批量多组参数对比 (--dir/--files) — 诊断脚本
│   └── sign_app.ps1                    # 一键签名脚本
├── signing/                             # 签名文件（敏感，不提交）
│   ├── release.p12                     # 密钥库（EC 256位）
│   ├── release.cer                     # 发布证书
│   └── releaseRelease.p7b              # Profile 文件
├── .trae/                              # AI 项目配置
│   ├── rules/project_rules.md          # 项目规则
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

## 四、数据资产

> 2026-06-28：用户清理了一批过长的、可能产生误导性测试结果的记录，精简了测试集。

### 核心验证记录（精简后）
| 记录 | pure MAE | pure=0 | pure=0(-40ms) | 场景 |
|------|----------|--------|---------------|------|
| 地铁_航津路-保税区北 | 7.31 | 0.93 | **0.03** | 直线，理想 |
| 地铁_上海赛车场-马陆 | 13.52 | 1.12 | **0.25** | 多弯道 |
| 磁浮_浦东机场-龙阳路 | — | 1.03 | **0.04** | 极速306km/h |
| 地铁_沈杜公路-汇臻路(浦江线) | — | 0.39 | **0.16** | 胶轮APM，GNSS可信 |
| 地铁_虹桥-浦东机场(市域机场线) | — | 1.21 | **0.26** | 高速122km/h，长距离 |

**传感器频率发现**：代码请求 50Hz（sensorIntervalMs=20ms），但实际系统输出只有 33~37 Hz。

**数据存放路径**：本地研究记录目录，不纳入版本控制

---

## 五、核心发现与技术要点

### 核心发现
1. **偏置根因**：偏置在原始信号，非参数问题。pure=0 有效原理：绝对速度偏置无限累积，增量只累积几秒
2. **隧道精度问题根因**：当前算法仅用6DOF（加速度+陀螺仅做转弯检测），无绝对重力/航向参考，长时间行驶（尤其进隧道后GNSS失效）重力估计缓慢漂移，导致前向加速度投影混入重力分量，速度误差累积。
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

### 技术要点
- **时间源**：`computeDeltaSeconds` 优先 sensorTimestamp，其余用 Date.now()（墙上时间语义），双轨正确
- **双端一致**：~60 个常量一致，`ALGORITHM_VERSION` 由 `sync_version.py --check` 验证。分析层可自由扩展
- **LocationSourceType**：1=GNSS, 4=RTK，`tunnelState !== 'inside'` 是防系统推算冒充的唯一防护
- **传感器类功能开发流程**：必须遵循"先在研究记录中加字段采集数据→观察实际数据表现→再决定是否接入算法"，禁止在没有数据支撑的情况下直接修改算法
- **UI兼容性**：API12下Button组件自定义borderRadius不生效，使用系统默认胶囊形，不要手动设置圆角数值，升级API版本后需要重新验证所有组件样式

---

## 六、签名与上架

### 签名密钥信息
**密钥库文件**：`signing/release.p12`
**密钥库类型**：PKCS#12
**密钥算法**：EC (secp256r1) 256位
**签名算法**：SHA256withECDSA
**有效期**：36500 天（约 100 年）
**密码长度**：36 位（满足鸿蒙 32 位最低要求）
**密码存储**：密钥库密码(storePassword)和密钥密码(keyPassword)为同一个，从环境变量`METROSPEED_KEYSTORE_PASSWORD`读取，无硬编码在代码或脚本中

**debug签名**：DevEco Studio已自动配置debug签名，存放在`C:\Users\18918\.ohos\config\`目录下，直接点击运行即可自动签名安装，无需手动处理。`build-profile.json5` 已从版本控制移除（含本地 debug 签名属敏感配置），仓库只保留 `build-profile.template.json5` 模板，由 `.gitignore` 排除实际文件。首次 clone 后需复制模板为 `build-profile.json5` 再让 DevEco 填充签名。

**其他签名文件**：
- 发布证书：`signing/release.cer`
- Profile：`signing/releaseRelease.p7b`

### signing/ 目录三个文件的作用
1. **release.p12 — 密钥库（核心，最重要）**：装着私钥的保险箱，丢了就没法更新应用
2. **release.cer — 发布证书**：从华为 AGC 下载的公钥证书，证明公钥已被华为认可
3. **releaseRelease.p7b — Profile 配置文件**：鸿蒙系统安装应用时检查，证明应用经过华为认证

### 构建与签名命令

**环境变量设置（PowerShell）**：
```powershell
$env:NODE_HOME = "C:\Program Files\Huawei\DevEco Studio\tools\node"
$env:DEVECO_SDK_HOME = "C:\Program Files\Huawei\DevEco Studio\sdk"
$env:JAVA_HOME = "C:\Program Files\Huawei\DevEco Studio\jbr"
$env:PATH = "$env:NODE_HOME;$env:JAVA_HOME\bin;" + $env:PATH
```

**构建命令**：
```powershell
# 构建 APP（上架用）
& "C:\Program Files\Huawei\DevEco Studio\tools\hvigor\bin\hvigorw.bat" assembleApp --mode project -p product=default -p buildMode=release --no-daemon
```

**一键签名脚本**：
```powershell
powershell -ExecutionPolicy Bypass -File tools\sign_app.ps1
```

**注意**：release 证书签名的包不能 hdc install 直接安装，只能通过应用市场分发；调试直接在DevEco Studio点击运行即可，自动使用debug签名。

### 当前状态
- ✅ AppGallery审核已提交（使用DevEco Studio 6.1.1 Release构建，对比度问题已修复）
- ✅ 一键签名脚本可用
- ✅ 开源版本v1.0.0已发布到GitHub
- ✅ debug签名自动配置完成，直接运行即可安装

---

## 七、项目规则（project_rules.md 摘要）

1. **Python-ArkTS 一致性**：`tools/replay_estimator.py` 中的 `SpeedEstimator` 类必须 bug-for-bug 复现 ArkTS 端逻辑。
2. **构建时版本号**：`hvigorfile.ts` 自动更新 `versionCode`（Unix 时间戳）。`versionName` 手动管理（语义化版本）。
3. **信任用户校准**：停车校准由用户手动触发，不引入额外速度阈值拦截。
4. **数据文件路径**：所有 JSONL 数据存放在本地研究记录目录。
5. **算法改动必须多记录验证**：任何算法层面的改动必须在所有可用 JSONL 记录上跑对比验证。
6. **代码修改纪律**：任何代码修改（包括加功能、回滚、改配置、删文件）必须等用户明确指令后再执行，禁止自作主张修改代码，哪怕是"很小很安全"的改动。
7. **传感器开发流程**：先采集数据观察实际表现，再决定是否接入算法，禁止无数据支撑直接改算法。
8. **不要主观判断"多余"就删**：任何配置、文件、代码，没验证过不要删。
9. **说明文件维护**：三个文件——`project_rules.md`（硬规则）、`investigation_status.md`（AI 上下文快照）、`README.md`（对外项目说明）。
10. **不要轻易升级SDK版本**：升级SDK可能导致系统组件默认样式变化，且新API不一定能带来实际收益，确认有明确收益且验证过UI兼容性后再升级。

---

## 八、项目时间线

| 日期 | 阶段 | 关键动作 |
|------|------|----------|
| 04月 | 弃案 | 网页应用 + 系统线性加速度传感器，被融合误差吞掉起步加速 → 搁置一个多月 |
| 06-12~17 | 基建 | 先有测速再补记录；初始目标手持，妥协为稳定放置；传感器经历三代；SpeedEstimator 核心算法成；JSONL 全量记录和 Python 回放引擎同步搭建；v1→v13 快速迭代 |
| 06-18 | 定标 | 固定记录命名格式；首批数据采集；确立双端验证链路 |
| 06-19~21 | 采集 | 地铁4条 + 驾车3条 + 公交2条 + 纯隧道1条 |
| 06-22 | 优化 | 转向已有记录算法优化；产出 v13→v18 四个活跃改动 |
| 06-23 上午 | 突破 | 偏置根因，pure=0 锚定 MAE sub-2 km/h；信噪比切换；自适应停车校准；入隧重力刷新 |
| 06-23 下午 | 验证 | 采集6号线/浦江线/市域机场线/磁浮线/北安跨线/奉浦快线6条新记录；发现GNSS固定-40ms延迟 |
| 06-24 | 补偿 | 将 -40ms GNSS 延迟补偿同时部署到 ArkTS 和 Python；全量 baseline 重跑 |
| 06-25 | 发布准备 | 算法版本重命名；磁浮线验证；定 MIT 许可证；上架应用介绍文案定稿；release 构建链验证；签名密钥生成；版本号改为 1.0.0；一键签名脚本；正式上架包；死文件清理；死代码排查；提交 AppGallery 审核；README 重写 |
| 06-26 | 审核修复 | 修复三个自检问题：退后台传感器占用（长时任务+emitter）、Scroll回弹动效（EdgeEffect.Spring）、深色文字对比度（#94A3B8）；停车校准速度补偿入包；--anchor-interval-ms 诊断参数；多进程并行扫描；全量代码核查文档修复；死代码清理；开源上线GitHub |
| 06-27 | Beta API 修复 | AppGallery审核因beta API被拒，降级到DevEco Studio 6.1.1 Release重新构建；sign_app.ps1路径bug修复；对比度修复完成重新提交审核 |
| 06-28 | 传感器采集 | 完成4个辅助传感器数据采集功能：GRAVITY、LINEAR_ACCELEROMETER、ROTATION_VECTOR、MAGNETIC_FIELD；确认GAME_ROTATION_VECTOR ArkTS API不支持，清理相关死代码；研究记录schema升级到v13；保持SDK版本为API12，不升级API20；清理过长的误导性测试记录 |
| 06-28 | 工程清理 | build-profile.json5 移出版本控制改用 template 机制隔离 debug 签名；4 传感器实现提交入库（316 行）；_run_new_batch.py / _scan_anchor_interval.py 改为 --dir/--files 参数化（规则 7.1）；project_rules.md 2.3 节同步 |

---

## 九、当前任务状态

**最近完成的任务**：
1. v1.0.0版本开源发布到GitHub
2. AppGallery审核提交（beta API+对比度问题已修复）
3. 4个辅助传感器数据采集功能完成，schema升级到v13（4传感器实现已提交入库）
4. 清理GAME_ROTATION_VECTOR相关死代码，确认API不支持
5. 保持SDK版本为API12，解决UI样式兼容问题
6. 精简测试数据集，清理过长的误导性记录
7. build-profile.json5 移出版本控制，改用 template 机制隔离 debug 签名
8. _run_new_batch.py / _scan_anchor_interval.py 改为 --dir/--files 参数化（规则 7.1）
9. project_rules.md 2.3 节同步为 template 机制

**待执行任务（按优先级）**：
1. 🔴 路测采集包含4个辅助传感器的数据，验证核心假设：系统重力+系统线性加速度≈原始加速度计读数
2. 🔴 分析各传感器在加减速/过弯/隧道场景下的实际表现，确定隧道精度优化的融合方案
3. 🟡 sign_app.ps1中`$signTool`硬编码路径改为可配置
4. 🟡 精简后的核心数据集跑全量baseline验证
5. 🟢 多语言支持（英文）
6. 🟢 后台长时记录稳定性测试
7. 🟢 历史记录管理界面

---

## 十、重要提醒与注意事项

1. **绝对不要自作主张改代码**：任何修改，哪怕你觉得100%是bug、是多余的，也必须先问用户，用户说改你再改。
2. **不要替用户做技术决策**：传感器方案、算法路线、功能优先级、SDK版本选择，全部听用户的，你只负责客观分析利弊和执行。
3. **不要相信文档里写的"已完成"**：一定要看实际代码验证，历史上出现过文档虚报进度的情况。
4. **隧道模式是手动切换的**：不是自动检测的。
5. **refreshGravityAtEntrance() 只刷新重力估计**：不重置速度、不重置主轴、不重置锚点。
6. **build-profile.json5 已移出版本控制**：仓库只有 `build-profile.template.json5` 模板（signingConfigs 为空）。本地 `build-profile.json5` 由 DevEco 自动填充 debug 签名，已加进 `.gitignore`。首次 clone 后需 `Copy-Item build-profile.template.json5 build-profile.json5` 再用 DevEco 打开。
7. **.trae/目录要保留**：属于AI工作记忆，不加入.gitignore，随仓库提交。
8. **signing目录绝对不能提交git**：里面是签名私钥。
9. **改算法必须双端一致**：ArkTS改了什么，Python replay_estimator.py必须一模一样改，然后跑sync_version.py --check。
10. **改算法必须全量验证**：所有JSONL记录都要跑baseline对比，不能只看单条记录。
11. **这次加的4个传感器只做数据采集**：SpeedEstimator.ets一行都不要改，只是把传感器数据记到研究记录里。
12. **GAME_ROTATION_VECTOR暂时不要碰**：ArkTS公开API不支持，不要为了这个写Native C API，成本太高，等后续API开放再说。
13. **不要轻易升级SDK版本**：API12目前稳定可用，升级API20/23会导致Button等组件默认样式变化，确认有明确收益且验证过UI兼容性后再升级。
14. **API名称坑**：线性加速度传感器正确名称是LINEAR_ACCELEROMETER，不是LINEAR_ACCELERATION；响应类型是LinearAccelerometerResponse，不是LinearAccelerationResponse。
15. **debug安装直接在DevEco点运行**：不要折腾命令行签名，DevEco会自动处理debug签名，手机连接后直接点运行即可。