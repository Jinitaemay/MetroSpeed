# MetroSpeed 项目规则

> 本文档是项目的硬规则，约束 AI 助手的行为。规则本身也可被质疑和修改——前提是人工明确要求。

---

## 1. Python-ArkTS 一致性（算法层）

`tools/replay_estimator.py` 中的 `SpeedEstimator` 类和 `replay()` 函数必须 **bug-for-bug** 复现 `entry/src/main/ets/model/SpeedEstimator.ets` 的估算逻辑。

- 一致性作用域：`SpeedEstimator` 类内部的所有方法（状态检测、主轴追踪、有效加速度、积分、校准等）。给定相同的传感器帧序列，两端必须产出相同速度序列。
- **不得**在算法层擅自增加 ArkTS 端没有的检查、闸门或分支
- 改完算法逻辑后跑 `python tools/sync_version.py --check` 确认 `ALGORITHM_VERSION` 两端同步

一致性规则**不约束** `replay_estimator.py` 中的分析层函数。以下属于分析层，可自由扩展：
- `compare_with_location`、`scan_location_lag`、`compare_bucketed` — GNSS 对比与统计
- `build_anchored_outputs_v2` — 锚点速度合成
- `summarize`、`compare_with_recorded` — 汇总输出
- argparse 命令行参数（如 `--anchor-v2`、`--anchor-power`、`--no-strict-start` 等）
- 参数可配置能力（`use_gyro_gravity` 开关等）

分析层不修改 `SpeedEstimator` 的行为，仅供离线测试使用。

---

## 2. 构建与版本号

### 2.1 版本号

每次构建（`hvigorw assembleHap` / `assembleApp` 或 DevEco Studio build）时，`hvigorfile.ts` 会自动更新 `AppScope/app.json5` 中的 `versionCode`。

- `versionCode` = Unix 时间戳（秒），自动递增
- `versionName` = 语义化版本号（如 `1.0.0`），**手动管理**，发版时修改 `app.json5`
- 不要在构建前手动编辑 `versionCode`，但可以手动修改 `versionName`
- `sync_version.py --code <timestamp>` 用于手动同步（无需每次构建执行）

### 2.2 构建命令

CLI 构建（需设置环境变量）：
```powershell
$env:NODE_HOME = "C:\Program Files\Huawei\DevEco Studio\tools\node"
$env:DEVECO_SDK_HOME = "C:\Program Files\Huawei\DevEco Studio\sdk"
$env:JAVA_HOME = "C:\Program Files\Huawei\DevEco Studio\jbr"
$env:PATH = "$env:NODE_HOME;$env:JAVA_HOME\bin;" + $env:PATH

# 构建 HAP（模块级，调试用）
& "C:\Program Files\Huawei\DevEco Studio\tools\hvigor\bin\hvigorw.bat" assembleHap --mode module -p product=default -p buildMode=release --no-daemon

# 构建 APP（工程级，上架用）
& "C:\Program Files\Huawei\DevEco Studio\tools\hvigor\bin\hvigorw.bat" assembleApp --mode project -p product=default -p buildMode=release --no-daemon
```

### 2.3 签名

当前 `build-profile.json5` 的 `signingConfigs` 为空数组，构建时跳过自动签名。使用 `tools/sign_app.ps1` 脚本手动签名。

签名前需设置环境变量：
```powershell
$env:METROSPEED_KEYSTORE_PASSWORD = "<密钥库密码>"
```

```powershell
# 默认输入输出
powershell -ExecutionPolicy Bypass -File tools\sign_app.ps1

# 指定输入输出
powershell -ExecutionPolicy Bypass -File tools\sign_app.ps1 -AppPath "输入.app" -OutputPath "输出.app"
```

签名流程：解压 .app → 签内部 HAP → 重新打包 → 签 .app 本身。

签名文件位于 `signing/` 目录：
- `release.p12`：密钥库（EC 256位）
- `release.cer`：发布证书
- `releaseRelease.p7b`：Profile 文件
- 密钥库密码和密钥密码相同（36位）

> **注意**：release 证书签名的 HAP/APP 不能直接通过 `hdc install` 安装到手机，会报 "signature verification failed due to not trusted app source"。release 签名包只能通过应用市场分发。调试请使用 DevEco Studio 的 debug 证书。

---

## 3. 信任用户校准

停车校准由用户手动触发，`calibrate_at_stop` 不引入额外速度阈值拦截。

- 校准从 `preCalBuffer`（180 帧环形缓冲区，3.6s）中扫描 rmsDeviation 最低的 75 帧（1.5s）滑动窗口做重力估计，通过陀螺仪均值/最大值/加速度跳动三道传感器检查即完成
- **不得**用估算器自身的速度输出去质疑用户操作

---

## 4. 数据文件路径

所有 JSONL 数据存放在本地方研究记录目录，设置环境变量 `METROSPEED_DATA_DIR` 指向该目录。

回放分析使用：
```
python tools/replay_estimator.py "<数据目录>\<文件名>.jsonl"
```

---

## 5. 算法改动必须多记录验证

任何算法层面的改动（阈值、条件、状态机顺序、缩放系数等）必须在**所有**可用 JSONL 记录上跑对比验证，包括驾车、地铁、公交。**不得**仅凭单条记录的 MAE 变化决定改动是否生效。

- 改动前跑全量基线: `python tools/_baseline_all.py`
- 改动后跑全量对比: `python tools/_baseline_all.py` （对比两次输出）
- 地铁数据零影响 ≠ 改动安全——可能只是地铁场景未触发该分支
- 如果某个改动对部分记录改善、部分记录恶化，需逐条分析原因后再决定

---

## 6. 参数扫描方法

参数扫描分两阶段，不得跳过第一阶段的筛选：

1. **灵敏度筛选** — 至少两条互补记录（如制动占比高的 + 制动占比低的，或地铁 + 驾车），每个参数 ±50% 各跑一次。MAE 变化 ≤ 0.5 km/h 的归档为"不敏感"，仅敏感参数进入下一阶段。
2. **全量验证** — 敏感参数跑全 8 条有效记录，按规则 5 检查改善/恶化比例。如果最优点在不同记录间冲突，可尝试密集网格（如 0.0/0.5/0.75/0.85/0.9/0.95/1.0/1.1/1.2/1.5/2.0）寻找公共可行区间。

以下类型的参数**不进入扫描范围**：
- 转换因子（1000、3.6、10^9 等）
- 只影响 UX 不改变回放 MAE 的参数（校准门槛、记录节流等）
- 纯数学保护闸（epsilon）

灵敏度筛选不要求每个参数只跑一条记录——多跑几条互补记录的筛选不算"跳过筛选"。

参数扫描使用 subprocess 调用 `replay_estimator.py` 的 CLI 参数，**不得**用文本补丁法或 monkey-patch 修改源代码。

参数扫描默认在 pure inertial 模式进行。当 pure 模式找到有效改进点后，应复跑该参数在锚点 v2 模式下是否仍改善——pure 最优 ≠ anchor 兼容。

---

## 7. 工具与文件命名约定

### 7.1 tools/ 目录命名

| 命名方式 | 类型 | 说明 |
|---------|------|------|
| **正常命名** | 核心/通用工具 | 长期保留，是项目的一部分。如 `replay_estimator.py`、`param_sensitivity.py`、`sync_version.py` |
| **`_` 下划线开头** | 临时诊断脚本 | 一次性/探索性的，用完可能会清理或合并为通用工具。如 `_baseline_all.py`、`_tunnel_diag.py`、`_bias_diag.py`、`_scan_anchor_interval.py` |

一次性诊断脚本必须接受命令行参数指定 JSONL 路径，**不得硬编码特定文件**。任务完成后应及时清理或合并为通用工具。

### 7.2 死文件清理

- 构建产物（`build/`、`entry/build/`、`.hvigor/`）可随时清理，需要时重新构建
- IDE 配置（`.idea/`）不提交，打包时可删除
- Python 缓存（`__pycache__/`、`*.pyc`）随时可删
- `signing/` 目录是敏感文件，**绝对不能提交到公开仓库**

---

## 8. 说明文件维护

以下三个文件的所有内容均可被后续会话质疑和修正——它们不是不可变的"事实"，而是当前阶段的决策记录。关键在于保留轨迹：回退时能知道当初为什么那么做。

| 文件 | 定位 | 更新时机 |
|---|---|---|
| `.trae/rules/project_rules.md` | 硬规则，约束 AI 行为 | 发现缺失或不适用时经用户确认后更新 |
| `.trae/documents/investigation_status.md` | AI 上下文快照。每次会话读完这篇就恢复全部上下文，不必依赖记忆 | 每次会话，任何改动生效后立即更新 |
| `README.md` | 对外项目说明。算法版本号、速度公式、数据资产表、时间线、项目结构 | 改动生效后同步更新 |

README 中以下内容随项目演进变化，改动时一并更新：
- **算法版本号** — 与 `ALGORITHM_VERSION` 同步
- **速度公式** — 与当前显示逻辑一致
- **数据资产表** — 新增记录、新增 MAE 列
- **时间线** — 阶段性成果
- **项目结构** — 新增/移除/重命名工具文件

---

## 9. 规则质疑与违规告知

上述规则均为当前阶段的最佳实践总结，**不是死命令**。当 AI 认为某条规则在特定情境下不再适用、或违反规则能带来明确收益时，必须：

1. **明确告知用户** — 说明哪条规则、为什么认为应该违反、预期收益是什么
2. **等待用户决策** — 不得在用户确认前自行违反规则
3. **记录违规原因** — 用户确认后，在本次改动的说明中注释违规理由，供后续回溯

无意的规则违反（如遗漏了全量验证）应在发现后第一时间告知用户并补做。
