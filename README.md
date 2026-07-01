# MetroSpeed · 地铁测速

> 基于惯性传感器的轨道交通测速工具。
>
> 无需依赖 GPS，仅凭手机加速度计和陀螺仪，就能实时估算地铁列车的行驶速度。隧道段，GPS 没信号也能测。
>
> **算法版本**：`anchor-delta-20260626-r1` · 源码：`https://github.com/Jinitaemay/MetroSpeed`

---

## ✨ 核心特性

### 1. 纯惯性测速
自研 9 状态检测算法 + 主轴学习 + 重力补偿，从原始传感器数据中提取真实运动加速度，积分得到实时速度。需稳定放置。

### 2. GNSS 锚点融合
GPS 信号良好时自动启用锚定模式，以 GNSS 速度为锚点叠加惯性增量，精度更高、漂移更小。

### 3. 隧道模式
一键切换隧道状态，入隧时冻结锚点；出隧后恢复正常，无缝衔接。

### 4. 自适应停车校准
停车时点击校准按钮，自动扫描±1.5秒最优静止段完成校准，精度重置。

### 5. 实时数据面板
- 融合速度（大字显示）
- 纯惯性速度（灰色参考）
- GNSS 速度（蓝色参考）
- 最高速度 / 平均速度 / 行驶时长 / 校准次数

### 6. 研究记录模式
全量 50Hz 传感器数据 + GNSS 数据 JSONL 格式记录，支持导出离线分析，适合技术爱好者和研究使用。Schema v13 新增 4 个辅助传感器（系统重力、线性加速度、旋转矢量、磁场强度）采集，用于验证系统融合误差特性。

---

Code by GPT-5.5 (Codex harness) → DeepSeek-V4-Pro (Trae Code harness) → GLM-5.2 (Trae Code Harness)

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

**信噪比切换**：`gnssSpeedKmh < speedAccuracyMps × 3.6` 时回退纯惯性，否则用锚点+增量。

### 自适应停车校准

`preCalBuffer` 180 帧环形队列 → 按按钮时扫描所有 75 帧滑动窗口，取 rmsDeviation 最低段 → 三道传感器检查（陀螺均值/最大值/加速度跳动）。缓冲区 <75 帧回退原逻辑。

### 入隧重力刷新

拨入隧开关 → 扫描 `preCalBuffer` 最优 75 帧重算重力，只更新 `gravityEstimate`，不动速度/主轴/锚点。

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
│   └── app.json5                       # 应用配置（versionName: 1.0.1, versionCode: 时间戳）
├── entry/
│   └── src/main/ets/
│       ├── entryability/EntryAbility.ets
│       ├── pages/Index.ets              # 主界面 + 锚点逻辑
│       └── model/
│           ├── SpeedEstimator.ets       # 惯性速度估算核心
│           ├── SensorController.ets     # 50Hz 加速度计+陀螺仪+4个辅助传感器
│           ├── LocationController.ets   # GNSS 定位 + 卫星状态
│           ├── ResearchRecorder.ets     # JSONL 全量记录（schema v13）
│           ├── BackgroundState.ets      # 后台记录状态共享
│           └── SpeedTypes.ets           # 类型定义、向量运算
├── tools/
│   ├── replay_estimator.py             # 离线回放引擎 + 锚点v2 + --use-sys-gravity 分析开关
│   ├── _baseline_all.py                # 全量基线对比 (--dir --anchor-v2 --files)
│   ├── _tunnel_diag.py                 # 隧道分段MAE + 纯速度曲线
│   ├── _bias_diag.py                   # cal_0积分不对称 + 重力/主轴追踪
│   ├── param_sensitivity.py            # 83参数 ±50% 敏感度扫描
│   ├── sync_version.py                 # 版本号 ArkTS ↔ Python 同步
│   ├── trim_cal_segment.py             # 裁剪校准段
│   ├── _scan_anchor_interval.py        # 锚点间隔多进程并行扫描
│   ├── _run_new_batch.py               # 批量多组参数对比 (--dir/--files)
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

### 测速

1. 将手机稳定放置后点 **开始**，保持静止 1.5 秒完成初始校准
2. 行驶中实时显示三行速度：融合速度（大字，锚点+惯性增量 pure=0）、纯惯性（灰色）、GNSS（蓝色）
3. 入隧/出隧时拨动开关——入隧时冻结锚点并刷新重力估计，出隧后恢复
4. 停稳后点 **停车校准** 归零——自动扫描按钮前 3.6 秒取最优静止段，无需等待

### 研究记录与分析

1. 研究记录区点 **开始记录**
2. 点击事件标记（开始测速/停车校准/入隧/出隧等）
3. 结束后 **导出** JSONL
4. PC 端回放分析：

```bash
# 全量基线对比（纯惯性）
python tools/_baseline_all.py

# 全量基线对比（锚点 v2，匹配手机 pure=0）
python tools/_baseline_all.py --anchor-v2 --pure-zero

# 全量基线对比（含 GNSS -40ms 延迟补偿）
python tools/_baseline_all.py --anchor-v2 --pure-zero --gnss-lag-ms=-40

# 指定目录
python tools/_baseline_all.py --dir <目录> --anchor-v2

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
```

---

## ⚠️ 已知限制

- 纯惯性模式（GNSS 不可信时）存在累积漂移，站间距越长漂移越大；GNSS 可用时 pure=0 锚定+增量可抑制漂移
- 只有停车校准能归零，途中无外部速度参考
- 锚点 v2 依赖 GNSS 位置回调（type 1/4, accuracy>0），隧道内冻结
- 设备放置需尽量稳定，剧烈晃动会破坏估算
- 弯道、道岔场景下误差可能增大

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

---

## 📜 许可证

MIT License · Copyright (c) 2026 Jinitaemay
