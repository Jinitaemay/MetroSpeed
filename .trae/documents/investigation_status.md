# MetroSpeed 项目工作记忆

> **记忆版本**：v14
> **最后更新**：2026-06-26
> **对应阶段**：开源 push GitHub 完成，README 精简完成，待重新提交 AppGallery 审核

---

## 一、项目基本信息

**项目名称**：MetroSpeed · 地铁测速
**平台**：鸿蒙 HarmonyOS (ArkTS)
**项目路径**：`<项目根目录>`
**算法版本**：`anchor-delta-20260626-r1`
**当前阶段**：死代码清理完成，构建签名完成，待重新提交 AppGallery 审核
**许可证**：MIT
**包名**：`com.codex.metrospeed`
**应用名称**：地铁测速
**版本号**：versionName = "1.0.0"，versionCode = Unix 时间戳（自动生成）

---

## 二、项目核心功能

### 核心算法
- **纯惯性测速**：自研 9 状态检测算法 + 主轴学习 + 重力补偿，从原始传感器数据中提取真实运动加速度，积分得到实时速度
- **9 状态优先级**：`CURVE > CONDUCTION_VIB > STRONG_VIB > LOW_CONFIDENCE > IDLE > ACCEL > BRAKING > CRUISE`
- **GNSS 锚点融合**：GNSS 信号良好时自动启用锚定模式，以 GNSS 速度为锚点叠加惯性增量（pure=0 模式）
- **速度合成公式**：锚定速度 = GNSS锚点 + (当前纯惯性 - GNSS时刻纯惯性)
- **信噪比切换**：`gnssSpeedKmh < speedAccuracyMps × 3.6` 时回退纯惯性，否则用锚点+增量

### 关键特性
1. **隧道模式**：用户手动拨动开关切换，入隧时冻结 GNSS 锚点，防止系统推算污染速度；同时调用 `refreshGravityAtEntrance()` 尝试刷新重力估计（扫描 preCalBuffer 最优 75 帧，三道传感器检查，只更新 gravityEstimate，不动速度/主轴/锚点）
2. **自适应停车校准**：停车时点击校准按钮，自动扫描 preCalBuffer（180 帧环形缓冲区，约 3.6 秒）内最优 75 帧（1.5 秒）静止段，速度补偿 + 重力重估
3. **GNSS -40ms 固定延迟补偿**：所有记录一致显示 locationTimeMs 比传感器时间晚约 40ms，ArkTS 端在锚点采集时用速度历史缓冲区查找 40ms 前的惯性速度，Python 端通过 `--gnss-lag-ms=-40` 补偿
4. **研究记录模式**：全量 50Hz 传感器数据 + GNSS 数据 JSONL 格式记录，支持导出离线分析

### refreshGravityAtEntrance() 详细逻辑
**功能定位**：只刷新重力估计向量，不重置速度、不重置主轴、不重置锚点。

**执行流程**：
1. 从 `preCalBuffer`（180帧环形缓冲区 ≈ 3.6秒）中扫描
2. 找出 rmsDeviation 最低的 75 帧（1.5秒）滑动窗口
3. 三道传感器稳定性检查：
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
     - 三道稳定性检查（陀螺仪均值/最大值、加速度跳动、rmsDeviation、重力模长误差）
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
│           ├── SpeedEstimator.ets       # 惯性速度估算核心
│           ├── SensorController.ets     # 50Hz 加速度计+陀螺仪
│           ├── LocationController.ets   # GNSS 定位 + 卫星状态
│           ├── ResearchRecorder.ets     # JSONL 全量记录
│           ├── BackgroundState.ets      # 后台记录状态共享
│           └── SpeedTypes.ets           # 类型定义、向量运算
├── tools/
│   ├── replay_estimator.py             # 离线回放引擎 + 锚点v2 (--pure-zero 匹配手机) — 核心工具，无下划线
│   ├── _baseline_all.py                # 全量基线对比 (--dir --anchor-v2) — 临时诊断脚本，下划线开头
│   ├── _tunnel_diag.py                 # 隧道分段MAE + 纯速度曲线 — 临时诊断脚本
│   ├── _bias_diag.py                   # cal_0积分不对称 + 重力/主轴追踪 — 临时诊断脚本
│   ├── param_sensitivity.py            # 83参数 ±50% 敏感度扫描 — 通用工具
│   ├── sync_version.py                 # 版本号 ArkTS ↔ Python 同步 — 通用工具
│   ├── trim_cal_segment.py             # 裁剪校准段 — 通用工具
│   ├── _scan_anchor_interval.py        # 锚点间隔多进程并行扫描 — 诊断工具
│   └── sign_app.ps1                    # 一键签名脚本
├── signing/                             # 签名文件（敏感，不提交）
│   ├── release.p12                     # 密钥库（EC 256位）
│   ├── release.cer                     # 发布证书
│   └── releaseRelease.p7b              # Profile 文件
├── hvigor/
│   └── hvigor-config.json5            # 构建配置
├── .trae/                              # AI 项目配置
│   ├── rules/project_rules.md          # 项目规则
│   └── documents/investigation_status.md  # 研究状态
├── hvigorfile.ts                       # 构建脚本（自动更新 versionCode）
├── build-profile.json5                 # 构建配置
├── oh-package.json5
├── LICENSE                              # MIT
├── .gitignore
└── README.md
```

**tools/ 目录命名约定**：
- 正常命名（如 replay_estimator.py）：核心/通用工具，长期保留
- `_` 下划线开头（如 _baseline_all.py）：临时诊断脚本，一次性/探索性的，用完可能会清理或合并

---

## 四、数据资产（17 条记录）

| 记录 | pure MAE | pure=0 | pure=0(-40ms) | 场景 |
|------|----------|--------|---------------|------|
| 地铁_航津路-保税区北 | 7.31 | 0.93 | **0.03** | 直线，理想 |
| 地铁_上海赛车场-马陆 | 13.52 | 1.12 | **0.25** | 多弯道 |
| 地铁_陈翔公路-桃浦新村 | 15.11 | 1.02 | **0.29** | |
| 地铁_双江路-新江湾城 | 20.20 | 1.78 | **1.36** | 驾驶台 |
| 地铁_港城路-东方体育中心(6号线) | — | 0.99 | **0.56** | 长线路，半程移机 |
| 地铁_沈杜公路-汇臻路(浦江线) | — | 0.39 | **0.16** | 胶轮APM，GNSS可信 |
| 地铁_虹桥-浦东机场(市域机场线) | — | 1.21 | **0.26** | 高速122km/h，长距离 |
| 磁浮_浦东机场-龙阳路 | — | 1.03 | **0.04** | 极速306km/h |
| 地铁_浦东大道-大连路 | N/A | N/A | N/A | 纯隧道 |
| 驾车_东靖路(短) | 3.21 | 0.26 | **0.13** | 短途 |
| 驾车_东靖路-隧道-沪常高速 | 24.31 | 17.22(a2) | **14.52** | 含入隧/出隧 |
| 驾车_中山南路-东长治路 | N/A | N/A | **16.55** | 旧格式 |
| 公交_申崇五线 | 15.74 | 5.31 | **5.20** | 特长 |
| 公交_许昌路 | — | 0.45 | **0.55** | 硬质表面 |
| 公交_北安跨线 | — | 0.79 | **0.54** | 硬质表面 |
| 公交_奉浦快线(BRT) | — | 1.52 | **1.12** | BRT快速公交 |
| 公交_新乐路 | 45.70 | — | **44.65** | 极不稳 |

**数据存放路径**：本地研究记录目录，不纳入版本控制

---

## 五、核心发现与技术要点

### 核心发现
1. **偏置根因**：偏置在原始信号，非参数问题。pure=0 有效原理：绝对速度偏置无限累积，增量只累积几秒
2. **已排除的传感器/方案**：
   - **实际用过又放弃的**：线性加速度（0.1g 起步加速被系统融合吃进重力）、未校准加速度计ACCELEROMETER_UNCALIBRATED（系统bias恒为0无法去偏）、陀螺仪坐标轴旋转（MEMS零偏积分漂移）、gyro gravity（ω×g重力追踪，同样零偏漂移）
   - **原理上直接排除未使用的**：方向传感器（精度不足）、旋转矢量传感器（系统融合姿态输出，同线性加速度根因）
3. **核心教训**：系统融合输出的误差在重力/加速分离环节。原始加速度计+陀螺仪+自算重力+统计主轴是唯一可行路径
4. **GNSS -40ms 固定延迟**：全量 17 条记录一致显示 locationTimeMs 比传感器时间戳晚 ~40ms

### 技术要点
- **时间源**：`computeDeltaSeconds` 优先 sensorTimestamp，其余用 Date.now()（墙上时间语义），双轨正确
- **双端一致**：~60 个常量一致，`ALGORITHM_VERSION` 由 `sync_version.py --check` 验证。分析层可自由扩展
- **LocationSourceType**：1=GNSS, 4=RTK，`tunnelState !== 'inside'` 是防系统推算冒充的唯一防护

---

## 六、签名与上架

### 签名密钥信息
**密钥库文件**：`signing/release.p12`
**密钥库类型**：PKCS#12
**密钥算法**：EC (secp256r1) 256位
**签名算法**：SHA256withECDSA
**有效期**：36500 天（约 100 年）
**密码长度**：36 位（满足鸿蒙 32 位最低要求）

**其他签名文件**：
- 发布证书：`signing/release.cer`
- Profile：`signing/releaseRelease.p7b`

### signing/ 目录三个文件的作用
1. **release.p12 — 密钥库（核心，最重要）**：装着私钥的保险箱，丢了就没法更新应用
2. **release.cer — 发布证书**：从华为 AGC 下载的公钥证书，证明公钥已被华为认可
3. **releaseRelease.p7b — Profile 配置文件**：鸿蒙系统安装应用时检查，证明应用经过华为认证

### 密码安全性讨论
- **密钥库文件** = 保险箱，密码 = 保险箱密码。只有密码没有密钥库 → 没用；只有密钥库没有密码 → 打不开；两者都有 → 就能签名。
- **32位密码要求**：鸿蒙构建工具链的要求，华为认为签名密钥是核心资产，强制要求强密码
- **改密码 vs 换密钥**：改密码只是改变保护密钥库的方式，私钥没变，签名不变，不需要重新提交审核；换密钥是换全新的密钥库，签名变了，必须重新提交，老用户没法直接升级

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
# 构建 HAP（模块级，调试用）
& "C:\Program Files\Huawei\DevEco Studio\tools\hvigor\bin\hvigorw.bat" assembleHap --mode module -p product=default -p buildMode=release --no-daemon

# 构建 APP（工程级，上架用）
& "C:\Program Files\Huawei\DevEco Studio\tools\hvigor\bin\hvigorw.bat" assembleApp --mode project -p product=default -p buildMode=release --no-daemon
```

**一键签名脚本（推荐）**：
```powershell
# 用法1：默认输入输出
powershell -ExecutionPolicy Bypass -File tools\sign_app.ps1

# 用法2：指定输入输出
powershell -ExecutionPolicy Bypass -File tools\sign_app.ps1 -AppPath "输入.app" -OutputPath "输出.app"
```

**签名完整流程**：
1. 解压 .app 文件
2. 给内部的 entry-default.hap 签名
3. 替换原 hap，重新打包成 .app
4. 给 .app 文件本身签名

**sign_app.ps1 脚本现状**：
- 密码已改为从环境变量 `METROSPEED_KEYSTORE_PASSWORD` 读取
- 输入输出路径已改为基于 `$PSScriptRoot` 的相对路径
- `$signTool` 路径仍为 DevEco Studio SDK 绝对路径，开发者需根据本地环境调整

**验证签名**：
```powershell
$signTool = "C:\Program Files\Huawei\DevEco Studio\sdk\default\openharmony\toolchains\lib\hap-sign-tool.jar"
java -jar $signTool verify-app -inFile <签名后文件> -outCertChain <输出证书链> -outProfile <输出Profile> -inForm zip
```

**安装到手机**：
```powershell
$hdc = (Get-ChildItem -Path "$env:DEVECO_SDK_HOME" -Recurse -Filter "hdc.exe").FullName
& $hdc -t <设备序列号> app install entry/build/default/outputs/default/entry-default-signed.hap
```

**注意**：release 证书签名的 HAP 不能直接 hdc install 安装，会报 "signature verification failed due to not trusted app source" 错误。需要用 DevEco Studio 调试运行，或使用调试证书。

### 签名构建问题记录
1. **密码长度不足**：第一次生成16位密码不符合要求，重新生成36位
2. **build-profile.json5 手动配置签名失败**：错误 "Can not find signing material fd"，替代方案：清空 signingConfigs，先构建未签名包，再手动签名
3. **release 证书不能直接安装到手机**：release 发布证书签名的包只能通过应用市场分发，不能直接 hdc install

### 当前状态
- ✅ build-profile.json5 的 signingConfigs 为空数组，构建时跳过自动签名，使用手动签名
- ✅ 未签名的 HAP 构建成功
- ✅ 手动签名 HAP 成功
- ✅ 未签名的 APP 构建成功
- ✅ 手动签名 APP 成功（解压→签内部hap→重打包→签app）
- ✅ 签名验证通过
- ✅ 一键签名脚本 tools/sign_app.ps1 已编写
- ✅ 最终上架包：`build/outputs/default/MetroSpeed-release.app`（已签名，约 314KB）

---

## 七、死文件清理（已完成）

### 已删除的文件
| 类别 | 内容 |
|------|------|
| **签名临时文件** | verify-*.cer / verify-*.p7b（4个）、release.csr、material/ 目录 |
| **token 计数相关** | _count_tokens*.py（4个）、deepseek_tokenizer/ 整个目录、deepseek_v3_tokenizer.zip |
| **临时分析脚本** | _analyze_line6.py |
| **macOS 垃圾** | __MACOSX/ 目录、.DS_Store |
| **构建缓存** | build/（根目录）、.hvigor/、tools/__pycache__/ |
| **IDE 配置** | .idea/ |

### .gitignore 已更新
新增忽略规则：
- `signing/` - 签名密钥目录（绝对不能提交！）
- `build/`、`*.hap`、`*.app` - 构建产物
- `.DS_Store`、`Thumbs.db` - 系统垃圾
- `*.pyc`、`__pycache__/` - Python 缓存
- `node_modules/`、`oh_modules/` - 依赖目录
- `*.tmp`、`*.temp`、`*.log` - 临时文件

### 保留的目录
- `entry/build/` - 模块级构建缓存（用户要求保留）→ 后来用户又说删了，已删
- `signing/` - 签名密钥（敏感文件，手动打包时包含）
- `.trae/` - AI 项目配置

---

## 八、死代码清理（已完成）

### 已删除的死代码
1. **`SpeedEstimator.getConfidence()`** — 公开方法，从未被外部调用
2. **samples 历史数组整条链路** — `SpeedEstimator.samples` 数组、`pushSample()`、`getSamples()`、`Index.ets` `@State samples` 及 6 处赋值
3. **`CalibrationState` 冗余字段** — `lastCalibrationMs`、`calibrationCount`，暴露给外部但无任何读取代码

---

## 九、项目规则（project_rules.md 摘要）

1. **Python-ArkTS 一致性**：`tools/replay_estimator.py` 中的 `SpeedEstimator` 类必须 bug-for-bug 复现 ArkTS 端逻辑。
2. **构建时版本号**：`hvigorfile.ts` 自动更新 `versionCode`（Unix 时间戳）。`versionName` 手动管理（语义化版本）。
3. **信任用户校准**：停车校准由用户手动触发，不引入额外速度阈值拦截。
4. **数据文件路径**：所有 JSONL 数据存放在本地研究记录目录。
5. **算法改动必须多记录验证**：任何算法层面的改动必须在所有可用 JSONL 记录上跑对比验证。
6. **参数扫描方法**：分两阶段——灵敏度筛选（±50%，至少两条互补记录）→ 全量验证（敏感参数跑全 8 条有效记录）。
7. **说明文件维护**：三个文件——`project_rules.md`（硬规则）、`investigation_status.md`（AI 上下文快照）、`README.md`（对外项目说明）。
8. **规则质疑与违规告知**：违反规则前必须明确告知用户并等待决策。

---

## 十、文档更新（已完成）

### README.md
项目结构、时间线、算法版本号等已同步为当前状态。

---

## 十一、项目时间线

| 日期 | 阶段 | 关键动作 |
|------|------|----------|
| 04月 | 弃案 | 网页应用 + 系统线性加速度传感器，被融合误差吞掉起步加速 → 搁置一个多月 |
| 06-12~17 | 基建 | 先有测速再补记录；初始目标手持，妥协为稳定放置；传感器经历三代；SpeedEstimator 核心算法成；JSONL 全量记录和 Python 回放引擎同步搭建；v1→v13 快速迭代 |
| 06-18 | 定标 | 固定记录命名格式；首批数据采集；确立双端验证链路 |
| 06-19~21 | 采集 | 地铁4条 + 驾车3条 + 公交2条 + 纯隧道1条 |
| 06-22 | 优化 | 转向已有记录算法优化；产出 v13→v18 四个活跃改动 |
| 06-23 上午 | 突破 | 偏置根因，pure=0 锚定 MAE sub-2 km/h；信噪比切换；自适应停车校准；入隧重力刷新 |
| 06-23 下午 | 验证 | 采集6号线/浦江线/市域机场线/磁浮线/北安跨线/奉浦快线6条新记录；发现GNSS固定-40ms延迟 |
| 06-24 | 补偿 | 将 -40ms GNSS 延迟补偿同时部署到 ArkTS 和 Python；全量 17 条 baseline 重跑 |
| 06-25 | 发布准备 | 算法版本重命名；磁浮线验证；定 MIT 许可证；上架应用介绍文案定稿；release 构建链验证；签名密钥生成；版本号改为 1.0.0；一键签名脚本；正式上架包；死文件清理；死代码排查；提交 AppGallery 审核；README 重写 |
| 06-26 | 审核修复 | 修复三个自检问题：退后台传感器占用（长时任务+emitter）、Scroll回弹动效（EdgeEffect.Spring）、深色文字对比度（#94A3B8）；停车校准速度补偿入包；--anchor-interval-ms 诊断参数；多进程并行扫描；全量代码核查文档修复；死代码清理（samples链路/getConfidence/CalibrationState冗余字段）；构建签名完成 |
| 06-26 | 开源准备 | 变量命名规范化（GPS→GNSS、Vector→Vector3）；sign_app.ps1 密码改环境变量；LICENSE 作者 Codex→Jinitaemay；三份文档交叉审计修复 10 处不一致；.trae/specs/ + 3 份规划文档清理；.gitignore 注释清理；git init + commit（37 文件） |
| 06-26 | 开源上线 | git remote add origin → push GitHub master 成功；仓库地址 https://github.com/Jinitaemay/MetroSpeed；单条初始 commit（首次开源：MetroSpeed 地铁测速 v1.0.0）；认证通过 Git Credential Manager 浏览器授权 |
| 06-26 | README 精简 | 删除「构建与安装」「性能数据」两节（对外读者参考价值低，且性能表含多个 N/A）；修正停车校准「±1.5秒」→「3.6 秒缓冲区取 1.5 秒最优窗口」；项目起源段去夸张加粗；介绍语去重；时间线补开源上线行。investigation_status 内部的数据资产表和签名节保留（AI 上下文恢复用） |

---

## 十二、开发者备注

- 开发方向：鸿蒙系统
- 隧道模式是手动切换，不是自动检测
- 改密码不需要重新提交审核（私钥未变），换密钥才需要

---

## 十三、当前任务状态

**最近完成的任务**：
1. 修复三个审核自检问题：退后台传感器占用、Scroll无回弹、深色文字对比度
2. 停车校准速度补偿：校准期取消冻结，窗口末端velocity补偿，ArkTS/Python 两端同步
3. 新增BackgroundState.ets共享模块、module.json5加KEEP_BACKGROUND_RUNNING权限和backgroundModes
4. 新增--anchor-interval-ms诊断参数 + _scan_anchor_interval.py多进程并行扫描
5. 全量代码核查 + 三份文档修复 + 死代码清理
6. 构建签名完成
7. 开源准备：变量命名规范化（GPS→GNSS、Vector→Vector3）；sign_app.ps1 密码改环境变量；LICENSE 作者→Jinitaemay；三份文档交叉审计修复 10 处不一致；.trae/specs/ + 3 份规划文档清理；.gitignore 注释清理；git init + commit（37 文件）
8. 开源上线：git remote add origin → push GitHub master 成功（https://github.com/Jinitaemay/MetroSpeed）
9. README 精简：删除「构建与安装」「性能数据」两节；修正停车校准描述；项目起源段去夸张加粗；介绍语去重；时间线补开源上线行
---
**历史完成任务（06-25 发布准备阶段）**：
1. 撰写上架用的应用介绍和一句话简介（用户已确认版本）
2. 验证构建环境（Node + JDK + hvigor）
3. 生成签名密钥库 release.p12（第一次16位密码不符合要求，重新生成36位密码）
4. 导出 CSR 文件，用户从 AGC 下载 .cer 和 .p7b
5. 确认 build-profile.json5 自动签名失败原因，采用"构建未签名包 + 手动签名"方案
6. 成功构建并签名 HAP 和 APP
7. 修改 versionName 为 "1.0.0"，versionCode 继续用时间戳自动生成
8. 编写 tools/sign_app.ps1 一键签名脚本
9. 清理死文件（verify-*.cer/p7b、material/、release.csr、临时分析脚本、构建缓存等）
10. 更新 .gitignore，添加 signing/、build/ 等忽略规则
11. 死代码排查：找到 1 处确定的死代码 + 多处冗余代码
12. 创建 MIT LICENSE 文件
13. 重写 README.md，更新时间线、性能数据表
14. 更新 project_rules.md 和 investigation_status.md
15. 最终上架包：build/outputs/default/MetroSpeed-release.app（已签名）

**下一步待执行**：
1. 等待 AppGallery 审核结果
2. sign_app.ps1 中 `$signTool` 路径改为可配置
3. 降级 API 9/10 以支持 HarmonyOS 4.3 设备
4. 新一批采集记录深度分析（6号线/浦江线/市域机场线/磁浮线）

---

## 十四、重要提醒与注意事项

1. **隧道模式是手动切换的**：不是自动检测的，用户需要手动拨动开关。之前曾写错为"自动刷新"，被用户指出。
2. **refreshGravityAtEntrance() 只刷新重力估计**：不重置速度、不重置主轴、不重置锚点。副作用极小。
3. **密码长度要求**：鸿蒙构建要求 storePassword 和 keyPassword 至少 32 位。
4. **build-profile.json5 配置**：signingConfigs 为空数组，构建时跳过自动签名，使用 tools/sign_app.ps1 手动签名。
5. **用户已确认前三项上架准备**：应用图标、应用截图、隐私政策用户说"只要能过审核就不用你操心"，意味着这些用户已经搞定了。
6. **手动签名方案**：当前采用"构建未签名包 + hap-sign-tool.jar 手动签名"的方案，已验证 HAP 和 APP 签名成功。
7. **APP 签名完整流程**：解压 .app → 给内部 hap 签名 → 重新打包 → 给 .app 签名。已封装为 tools/sign_app.ps1 脚本。
8. **华为应用市场版本号展示**：显示格式为 "versionName (versionCode)"，用户可见两者。
9. **versionName 管理**：手动管理，发版时修改 app.json5；versionCode 自动用时间戳。
10. **release 证书不能直接安装**：release 签名的 HAP/APP 不能通过 hdc install 直接安装到手机，需要用应用市场分发或调试证书。
11. **.trae/ 目录用户说保留**：用户说"除了.trae/都删掉"，意思是死文件都删，但 .trae/ 目录保留。
12. **改密码不需要重新提交审核**：因为私钥没变，签名指纹不变。换密钥才需要重新提交。
13. **密钥库文件是核心资产**：丢了就没法更新应用，必须多备份几份。
14. **用户手动打包是给自己用的**：所以 signing/ 目录要包含进去，不是开源打包。
15. **tools/ 目录命名约定**：正常命名=核心/通用工具，_下划线开头=临时诊断脚本。
