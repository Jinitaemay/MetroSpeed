# MetroSpeed · 地铁测速

> 基于惯性传感器的轨道交通测速工具。
>
> 无需依赖 GPS，仅凭手机加速度计和陀螺仪，就能实时估算地铁列车的行驶速度。隧道段，GPS 没信号也能测。
>
> **算法版本**：`anchor-delta-20260710-r3`
---

## 核心功能

### 1. 纯惯性测速
自研 9 状态检测算法 + 主轴学习 + 重力补偿，从原始传感器数据中提取真实运动加速度，积分得到实时速度。需稳定放置，建议把手机平放在地板上，不支持手持使用，当前版本未适配上下坡。

### 2. GNSS 锚点融合
GPS 信号良好时自动启用锚定模式，以 GNSS 速度为锚点叠加惯性增量，精度更高、漂移更小。

### 3. 隧道模式
一键切换隧道状态，入隧时冻结锚点；出隧后恢复正常，无缝衔接。

### 4. 自适应停车校准
停车时点击校准按钮，自动扫描点击前的最优静止段重估重力，再用新重力重放后续帧：停稳时严格归零，点击后马上起步也会保留真实速度增量。

### 5. 实时数据面板
- 融合速度（大字显示）
- 纯惯性速度（灰色参考）
- GNSS 速度（蓝色参考）
- 最高速度 / 平均速度 / 行驶时长 / 校准次数

### 6. 研究记录模式
按设备实际回调逐帧记录传感器数据（请求频率 50Hz）及 GNSS 数据，支持导出离线分析。schema v14 为每条传感器记录补齐会话/测速段标识，并在估算器记录中分开保存 `pureInertialSpeedKmh` 与 `displaySpeedKmh`。本地只保留最近一次记录，开始新记录会覆盖尚未导出的旧记录；异常中断或尾部损坏的日志会保留完整性标记，并以 `INCOMPLETE` 文件名导出，避免被误当成完整样本。

### 7. 后台连续采集
测速或研究记录运行时自动启用 LOCATION 长时任务，应用退到后台后继续采集传感器与 GNSS 数据；回到前台后停止长时任务，测量状态保持连续。

---

## 🚀 适用场景

- 地铁 / 轻轨 / 市域铁路 测速
- 公交 / BRT 快速公交 测速
- 自驾测速，第二仪表盘

---

## 📖 项目起源

**首次尝试失败**（2026 年 4 月）：用手机线性加速度传感器的网页应用测地铁速度。起步加速 ~0.1g 量级，传感器融合把这段加速度吃进了重力估计里——进入匀速段后，速度从十几 km/h 缓慢倒退回零。移除所有滤波，数据更难看。搁置一个多月。

6 月回归后继续探索传感器路线：
- **未校准加速度计**（ACCELEROMETER_UNCALIBRATED）：系统给出的 bias 估计恒为 0，无法做去偏处理，放弃。
- **陀螺仪坐标轴旋转**：用陀螺仪角速度积分姿态旋转矩阵，将加速度变换到世界坐标系。MEMS 陀螺仪零偏随时间累积，积分几分钟后重力方向漂移，速度爆炸。
- **gyro gravity**（ω×g 重力追踪）：经典捷联惯导做法，每帧用叉乘更新重力方向。同样受陀螺仪零偏影响，时间稍长重力方向歪掉。Python 端保留为实验开关（`--use-gyro-gravity`），ArkTS 端未实现。
- **方向传感器、旋转矢量传感器**：从未在代码中使用。从原理判断，方向传感器精度不足；旋转矢量属于系统融合姿态输出，与线性加速度同根因（依赖系统内部重力估计，0.1g 加速时不可靠），直接排除。

核心教训：加速度计本身分不清重力和 0.1g 的起步加速，任何依赖系统融合分离重力的输出（线性加速度、旋转矢量等）都会继承这层误差。

因此当前技术栈——**原始加速度计（含重力）+ 陀螺仪（仅状态检测）+ 自算重力 + 统计主轴**——是踩过坑之后唯一可行的路径。

---

## 🧠 算法原理

### 整体流程

```
传感器帧 50Hz
  → 重力补偿（preCalBuffer 自适应扫描最优 75 帧）
  → clip(3.5m/s²) → 低通滤波(α=0.22)
  → 主轴学习（锁定后 EMA α=0.003, 门槛 60帧/5.0m/s/30s）
  → 正交化（Graham-Schmidt, |proj|>0.15）
  → 投影 → 9状态检测 → 积分 → 纯惯性速度
  → 锚定合成（pure=0: GNSS锚点 + 惯性增量）
```

### 9 状态优先级

`CURVE > CONDUCTION_VIB > STRONG_VIB > LOW_CONFIDENCE > IDLE > ACCEL > BRAKING > CRUISE`

### 速度合成 (pure=0)

```
锚定速度 = GNSS锚点 + (当前纯惯性 - GNSS时刻纯惯性)
```

增量只累积几秒偏置，GNSS 一刷清零。pure=0 承认偏置不可在信号层面消除，在合成层面绕过。

**信噪比切换**：速度精度缺失/非正数，或 `gnssSpeedKmh < speedAccuracyMps × 3.6` 时回退纯惯性；只有精度有效且速度信号高于误差量级时才使用锚点+增量。五帧惯性历史与 GNSS 查询都使用各自回调入口时间戳，固定选取位置回调前约 40ms 的既有帧，避免 UI/写盘耗时改变锚点。

### 自适应停车校准

`preCalBuffer` 180 帧环形队列 → 点击时冻结按钮前历史 → 只扫描窗口末帧距点击不超过 300ms 的 75 帧滑动窗口 → 取 rmsDeviation 最低段 → 五道稳定性检查（陀螺均值/最大值、加速度跳动、RMS、重力模长）→ 更新重力并从该静止窗归零重放后续原始帧。冻结快照避免 100Hz 等高回调率在等待确认期间挤掉静止证据。主轴不清空；历史不足、静止窗过旧或稳定性不通过时明确拒绝，不改速度，也不刷新最近成功校准时间。

### 隧道锚点冻结

锚点采集需 `src∈{1,4}` + `tunnelState≠inside` + `!inVibration`。隧道内系统推算冒充 type 1，`tunnelState` 是唯一有效防护。

### GNSS -40ms 固定延迟补偿

全量记录一致显示 `locationTimeMs` 比传感器时间戳晚 ~40ms。应用补偿后 MAE 大幅下降：
- 航津路：0.93 → 0.03 km/h
- 上海赛车场：1.12 → 0.25 km/h
- 磁浮线：1.03 → 0.04 km/h

来源推测：GNSS 芯片定位解算延迟或系统回调缓冲。

---

## 📁 项目结构

```
MetroSpeed/
├── AppScope/
│   └── app.json5                       # 应用配置（versionName: 1.1.2, versionCode: 时间戳）
├── entry/
│       ├── entryability/EntryAbility.ets
│       ├── pages/Index.ets              # 主界面 + 锚点逻辑
│       └── model/
│           ├── SpeedEstimator.ets       # 惯性速度估算核心
│           ├── SensorController.ets     # 50Hz 加速度计+陀螺仪+4个辅助传感器
│           ├── LocationController.ets   # GNSS 定位 + 卫星状态
│           ├── ResearchRecorder.ets     # JSONL 全量记录（schema v14）
│           ├── BackgroundState.ets      # 后台测速/记录状态共享
│           └── SpeedTypes.ets           # 类型定义、向量运算
├── tools/
│   ├── replay_estimator.py             # 离线回放引擎 + 锚点v2 + --use-sys-gravity 分析开关
│   ├── _baseline_all.py                # 全量基线对比 (--dir --anchor-v2 --files)
│   ├── _tunnel_diag.py                 # 隧道分段MAE + 纯速度曲线
│   ├── _bias_diag.py                   # cal_0积分不对称 + 重力/主轴追踪
│   ├── param_sensitivity.py            # 78参数敏感度扫描（默认 ±20%）
│   ├── sync_version.py                 # 版本号 ArkTS ↔ Python 同步
│   ├── trim_cal_segment.py             # 裁剪校准段
│   ├── _scan_anchor_interval.py        # 锚点间隔多进程并行扫描
│   ├── _run_new_batch.py               # 批量多组参数对比 (--dir/--files)
│   ├── _handheld_detector.py           # 手持检测离线验证（gyroRms + zeroCrossingRate）
│   ├── _confidence_analysis.py          # 置信度延迟扫描 + 状态误差分析
│   ├── _confidence_calibrate.py         # 置信度全量标定（多进程）
│   └── sign_app.ps1                    # 一键签名脚本
├── signing/                             # 签名文件（敏感，不提交）
│   ├── release.p12                     # 密钥库（EC 256位）
│   ├── release.cer                     # 发布证书
│   └── releaseRelease.p7b              # Profile 文件
├── .trae/                              # AI 项目配置
│   ├── rules/project_rules.md          # 项目规则
│   ├── specs/                          # spec 驱动开发
│   └── documents/investigation_status.md  # 研究状态
├── hvigorfile.ts                       # 构建脚本（自动更新 versionCode）
├── build-profile.template.json5        # 构建配置模板（signingConfigs 为空）
├── oh-package.json5
├── LICENSE                              # MIT
├── .gitignore
└── README.md
```

---

## 📱 使用方式

### 首次构建准备

根目录的实际 `build-profile.json5` 含本机 debug 签名配置，不再纳入 Git。首次 clone 后执行 `Copy-Item build-profile.template.json5 build-profile.json5`，再用 DevEco Studio 打开工程生成/填写本地 debug 签名。release 签名脚本默认交互读取密码；只有自动化环境才显式使用 `-NonInteractivePassword` 和 `METROSPEED_KEYSTORE_PASSWORD`。

### 测速

1. 将手机稳定放置后点 **开始**，保持静止约 1.5 秒完成初始校准（至少 30 帧且覆盖 1 秒）
2. 行驶中实时显示三行速度：融合速度（大字，锚点+惯性增量 pure=0）、纯惯性（灰色）、GNSS（蓝色）
3. 入隧/出隧时拨动开关——入隧时冻结锚点，出隧后恢复
4. 停稳后点 **停车校准**——校准约 1.5 秒后确认，但车辆可以立即起步；算法会保留按钮后的运动增量
5. 测速期间可退到后台，系统通过 LOCATION 长时任务继续采集；需授予定位和后台运行权限

### 研究记录与分析

1. 研究记录区点 **开始记录**
2. 点击事件标记（开始测速/停车校准/入隧/出隧等）
3. 结束后 **导出** JSONL；开始下一次记录前请先导出，本地旧记录会被覆盖
4. PC 端回放分析：

需要 Python 3.10 或更高版本。

```bash
# 全量基线对比（纯惯性）
python tools/_baseline_all.py

# 全量基线对比（锚点 v2，匹配手机 pure=0）
python tools/_baseline_all.py --anchor-v2 --pure-zero

# 全量基线对比（含 GNSS -40ms 延迟补偿）
python tools/_baseline_all.py --anchor-v2 --pure-zero --gnss-lag-ms=-40

# 指定目录
python tools/_baseline_all.py --dir <目录> --anchor-v2 --pure-zero

# 单条回放
python tools/replay_estimator.py <导出的.jsonl>
python tools/replay_estimator.py <导出的.jsonl> --anchor-v2 --pure-zero

# 隧道诊断
python tools/_tunnel_diag.py <文件> --tunnel --speed

# 偏置诊断
python tools/_bias_diag.py <文件> --cal0 --gravity

# 参数敏感度
python tools/param_sensitivity.py <文件> --perturbation 0.2

# 版本一致性检查
python tools/sync_version.py --check

# 显式版本同步默认拒绝降级；仅本地有意回滚时使用 --allow-downgrade
python tools/sync_version.py --code <versionCode> [--allow-downgrade]
```

---

## ⚠️ 已知限制

- 纯惯性模式（GNSS 不可信时）存在累积漂移，站间距越长漂移越大；GNSS 可用时 pure=0 锚定+增量可抑制漂移
- 只有停车校准能归零，途中无外部速度参考
- 锚点 v2 依赖 GNSS 位置回调（type 1/4, accuracy>0），隧道内冻结
- 设备放置需稳定——手持姿态会被陀螺仪自动检测并中止测速
- 弯道、道岔场景下误差可能增大
- 后台连续采集依赖系统允许 LOCATION 长时任务；若权限或系统策略拒绝，应用会暂停传感器并在回到前台后恢复

---

## 1.1.2 更新说明

```text
Code by GPT-5.6 Sol (Codex harness)
1.算法版本升级至anchor-delta-20260710-r3，修复部分情况下停车校准后速度未归零的问题。
2.支持测速在后台持续采集传感器与 GNSS 数据。
3.优化 GNSS 与惯性速度融合，适配更多定位精度情况。
4.修复后台切换、传感器释放及页面生命周期相关问题。
5.增强研究记录完整性检查，异常日志导出时会明确标记。
```

---

## 📅 项目时间线

| 日期 | 阶段 | 关键动作 |
|------|------|----------|
| 04月 | 弃案 | 系统线性加速度传感器被融合误差吞掉起步加速 → 搁置 |
| 06-12~22 | 基建+优化 | 核心算法成型；双端验证链路；偏置根因突破，pure=0 锚定 MAE sub-2 km/h |
| 06-23~25 | 验证+发布 | 6 条新记录验证；-40ms 延迟补偿；MIT 许可证；提交 AppGallery 审核；开源上线 GitHub |
| 06-26~27 | 审核修复 | 退后台占用/对比度/beta API 修复；重新提交审核 |
| 06-28 | 传感器采集 | 4 个辅助传感器采集功能完成；schema v13；签名配置隔离 |
| 06-29 | 上架通过 | AppGallery 审核通过，"地铁测速" 1.0.0 正式上架 |
| 06-29~07-02 | 持续研究 | rmsDeviation 阈值调整 0.12→0.25；v13 记录分析；系统重力分析（地铁 NO-GO / 驾车有效）；隧道定位机制确认；隧道漂移根因验证（纯惯性积分误差累积） |
| 07-02 | 发布 1.0.1 | 放宽初始校准条件（适配地铁地板微振）；传感器按需启动（纯测速仅加速度计+陀螺仪）；schema v13（4 辅助传感器字段）；权限说明文案修正 |
| 07-03~05 | 发布 1.1.0 | **传感器状态文本修复**（停止测速后记录传感器不重启 bug、辅助传感器状态文本不更新）；**置信度公式重写**（基线 1.0、倍率衰减模型：弯道×3/加速×2/振动×4、pureMode 锚点感知双速率、3min 锚点巡航触底）；**手持检测系统**（陀螺仪 RMS + zeroCrossingRate 双指标滑窗检测，40 帧确认，14 条记录零误触发，触发时终止测速 + 红色不透明覆盖）；**ZCR 计算矫正**（硬编码 40Hz 改为实际时间戳差值）；**CLI 默认值同步**（replay_estimator.py 与 ArkTS 参数对齐）；**传感器状态汇总**（startAuxiliarySensors 列出全部可用传感器）；**文案优化**（手持覆盖三行引导、底部说明精简）；**置信度标定**（22.9万帧 14 条记录验证单调性） |
| 07-06 | 发布 1.1.1 | **停车校准修复**（初始校准期间禁止停车校准，避免 preCalBuffer 不足 75 帧导致后续校准失败）；**手持阈值上调**（GYRO_RMS 0.3→0.5，公交硬质表面底盘振动不再误触）；**移除 adaptive-gravity**（磁力计场景检测器经 4 条新记录验证无法可靠分离公交/驾车场景）；**移除入隧重力刷新**（`refreshGravityAtEntrance` 在行驶中会把加速度当重力，驾车飙至 991 km/h）；**手持停止标记**（JSONL 区分 handheld vs manual 停止） |
| 07-10 | 后台连续采集 | 普通测速与研究记录统一纳入后台活动状态；退后台时启用 LOCATION 长时任务持续采集；增加异步操作代次校验和长时任务前后台竞态保护；补齐磁力计退订 |
| 07-10 | 全量可靠性修复 | 停车校准改为新重力后段重放并仅在成功后重置 GNSS 锚；初始校准增加样本/覆盖保护；研究日志升级 schema v14 并保留单文件覆盖语义；回放按测速段隔离且按真实回调时序复现锚点，旧 v13 显示速度不再冒充纯惯性比对；修复 GNSS gate、pureMode 和工具假绿；解除本地签名配置的 Git 跟踪 |
| 07-11 | 1.1.2 发布候选 | 算法升级至 `anchor-delta-20260710-r3`；完成版本切换、发布构建与签名前检查 |

---

## 📜 许可证

MIT License · Copyright (c) 2026 Jinitaemay

源码：`https://github.com/Jinitaemay/MetroSpeed`
