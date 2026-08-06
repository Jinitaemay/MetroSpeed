# MetroSpeed 项目规则

> 本文档是项目的硬规则，约束 AI 助手的行为。规则本身也可被质疑和修改——前提是人工明确要求。

## 0. 规则使用与验收约定

稳定规则 ID 不随章节移动而改变。执行任务时先按“触发”选择规则，再完成
“验证”，并在交接记录中保存“最小证据”；章节正文仍是完整约束。命令示例
属于安全运行步骤，不携带本机口令、密钥关系或候选状态，也不能取代对应
硬规则。

| 规则 ID | 触发 | 必须达到的结果 | 验证入口 | 最小证据 |
|---|---|---|---|---|
| `ALG-001` | 修改估算器或产品显示速度的融合/锚点语义 | ArkTS 与 Python 产品等价路径保持 bug-for-bug 一致，并更新适用的算法标识 | `python tools/sync_version.py --check`；再按 `REG-001` 回放 | 完整命令、算法标识、逐记录结果 |
| `VER-001` | 执行 HAP/APP 构建或调整版本字段 | 两处版本字段由受控入口同步，最终包内版本与候选记录一致 | 构建钩子或 `sync_version.py`；最终包元数据核验 | versionName、versionCode、构建结果 |
| `REL-001` | 生成或替换正式候选 | 测试、工程级构建、双层签名/Profile/元数据核验全部针对同一源码状态和最终产物 | 第 2.3 节核验清单 | 原始验签输出、最终字节数与 SHA-256、候选状态 |
| `CAL-001` | 修改停车校准、静止窗或校准时龄 | 成功严格归零并保留按钮后真实增量；拒绝路径不改变速度或锚点 | 自动化测试与适用的确定性回放 | 成功、立即起步、拒绝三类结果 |
| `DATA-001` | 修改研究 schema、写入、保留或导出 | 保持事务替换、完整性标记、异步复制和敏感数据不入库 | 自动化测试、故障路径检查、最终包权限核验 | schema、失败/恢复结果、权限与忽略规则 |
| `REG-001` | 任何算法层或显示融合改动 | 对人工确认的当前代回归集做前后同输入、同参数的逐记录比较 | 第 5 节；版本化 manifest 落地前执行临时清单规则 | 输入清单及 SHA-256、参数、完整性、逐记录结果 |
| `SCAN-001` | 执行参数敏感度扫描 | 只接受结构完整输入，复用与产品同构的进程内回放入口，并完成筛选与主回归两阶段 | 第 6 节；`param_sensitivity.py` | 输入完整性、参数与扰动、逐记录结果 |
| `DOC-001` | 公开事实、当前状态、逆向证据或规则发生变化 | 信息写入唯一职责文档，其他文档只摘要并链接 | 第 8 节职责表与链接检查 | 更新文件、事实来源、交叉链接 |
| `GIT-001` | commit、push、tag、Release、PR 或上架操作 | 权限不逐级推导，操作范围与用户明确授权一致 | 第 10 节 | staged diff、提交哈希或外部操作结果 |

---

## 1. Python-ArkTS 一致性（算法层）

**规则 ID：`ALG-001`**

`tools/replay_estimator.py` 中的 `SpeedEstimator` 类和 `replay()` 函数必须 **bug-for-bug** 复现 `entry/src/main/ets/model/SpeedEstimator.ets` 的估算逻辑。

- 一致性作用域：`SpeedEstimator` 类内部的所有方法（状态检测、主轴追踪、有效加速度、积分、校准等），以及 `InertialSpeed.ets` 中会改变产品显示速度的 GNSS 可靠性、GNSS 锚点冻结和锚点增量语义。给定相同输入，两端的产品等价默认路径必须产出相同速度序列。
- **不得**在算法层擅自增加 ArkTS 端没有的检查、闸门或分支
- 修改 `SpeedEstimator`，或修改会改变显示结果的融合/锚点语义时，必须更换完整 `ALGORITHM_VERSION`；纯诊断、统计和不参与产品默认路径的实验参数不更换
- `ALGORITHM_VERSION` 使用 `算法族-算法定版日期-r公开修订号`。`rN` 只按已经向用户公开分发的算法代次连续编号，不因数字偏好跳号；尚未上架且后来被替代的内部候选不占正式修订号，由完整日期、`appVersionCode` 和 Git commit 区分
- 同一日期出现多个不同算法候选时，后续候选在完整标识末尾增加 `-c2`、`-c3` 等内部后缀，禁止让两个不同算法实现共用完全相同的标识。正式公开前移除内部后缀；一旦公开分发，该完整标识即冻结
- 已写入历史研究记录或签名包的旧标识不得改写；只能在文档中注明其为已替代的内部候选
- 改完算法逻辑后跑 `python tools/sync_version.py --check` 确认 ArkTS、Python 与 README 的 `ALGORITHM_VERSION` 同步

一致性规则**不约束** `replay_estimator.py` 中的分析层函数。以下属于分析层，可自由扩展：
- `compare_with_location`、`scan_location_lag`、`compare_bucketed` — GNSS 对比与统计
- `build_anchored_outputs_v2` — 锚点速度合成的非产品实验参数；其手机等价默认路径仍受上述一致性规则约束
- `summarize`、`compare_with_recorded` — 汇总输出
- argparse 命令行参数（如 `--anchor-v2`、`--anchor-power`、`--no-strict-start` 等）
- 参数可配置能力（`use_gyro_gravity` 开关等）

分析层不修改 `SpeedEstimator` 的行为，仅供离线测试使用。

分析层兼容性边界：
- `--use-gyro-gravity` 只允许作为离线实验开关；任何有效性结论必须引用对应
  回归证据，不能因开关存在而进入产品默认路径
- `--use-sys-gravity` 仅用于旧 v13/v15 日志离线兼容分析；schema v16 起的
  新记录不再采集系统 `GRAVITY`，不得把该开关描述成当前产品输入

---

## 2. 构建与版本号

### 2.1 版本号

**规则 ID：`VER-001`**

每次实际产物构建（`hvigorw assembleHap` / `assembleApp` 或 DevEco Studio build）时，`hvigorfile.ts` 会自动更新 `AppScope/app.json5` 中的 `versionCode`；`clean`、IDE 模型同步和任务枚举不得消耗版本号或改写受控源码。

- `versionCode` = `max(Unix 时间戳秒, 当前两处版本号 + 1)`，同秒构建或时钟回拨时仍自动递增
- `versionName` = 语义化版本号（如 `1.0.0`），**手动管理**，发版时修改 `app.json5`
- 不要在构建前手动编辑 `versionCode`，但可以手动修改 `versionName`
- `targetSdkVersion` 是包含系统版本与 API 级别的完整配置值（如 `6.1.1(24)`），`target API 24` 仅表示其中的 API 级别；文档描述当前配置时不得混淆两者
- `sync_version.py --code <timestamp>` 用于手动同步（无需每次构建执行）；默认拒绝低于当前值的显式版本号，只有本地有意回滚时才可加 `--allow-downgrade`
- `sync_version.py --algo <标识>` 用于同步新的算法候选，并同时分配不低于当前值的新 `versionCode`；算法逻辑和标识尚未确定时不得反复调用消耗编号
- 构建入口与 `sync_version.py` 共用 `.sync-version.lock`，并采用保留原换行的原子替换/失败回滚；锁初始化写入或刷新失败时也必须关闭描述符并清理本次新建的锁文件；不要绕过两者直接并发改写版本字段

### 2.2 构建命令

以下是安全运行步骤，不是本机环境状态的权威记录。路径按实际 DevEco 安装
位置调整，执行结果仍须满足 `VER-001` / `REL-001`：
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

### 2.3 发布与签名

**规则 ID：`REL-001`**

#### 2.3.1 硬约束

`build-profile.json5` 已从版本控制移除（含 DevEco 自动填充的 debug 签名，属本地敏感配置）。仓库只保留 `build-profile.template.json5` 模板（`signingConfigs` 为空数组），由 `.gitignore` 排除实际文件。模板只定义可实际使用的 `default` product；不得增加没有对应 signingConfig、且正式流程不会使用的伪 `release` product。

版本控制中的文档不得记录任何实际口令、口令之间的关系、私钥可用状态、
灾备清单内容或其他会随本机密钥轮换而变化的事实。此类信息只能保存在仓库
外的私有密钥管理系统中；发布证据只记录证书/Profile 的公开身份、有效期、
验证结果和最终产物指纹。

#### 2.3.2 安全运行步骤

首次 clone 后需：
1. 复制模板：`Copy-Item build-profile.template.json5 build-profile.json5`
2. 用 DevEco Studio 打开工程，让其自动填充 debug 签名；或手动配置签名

release 签名使用 `tools/sign_app.ps1` 脚本手动签名。脚本默认从实际 `build-profile.json5` 和仓库模板解析 compatible API，要求所有可用配置得到唯一一致的值；显式传入 `-CompatibleApiVersion` 时也必须与配置一致，禁止硬编码旧 API。脚本默认交互读取密码；仅自动化环境显式使用 `-NonInteractivePassword`，并按脚本接口在进程运行时提供所需环境变量。不得把变量值回显、写入仓库文件或复制到构建/验签记录：
```powershell
$env:METROSPEED_KEYSTORE_PASSWORD = "<密钥库密码>"
$env:METROSPEED_KEY_PASSWORD = "<密钥密码>"
```

```powershell
# 默认输入输出（交互输入密码）
powershell -ExecutionPolicy Bypass -File tools\sign_app.ps1

# 指定输入输出
powershell -ExecutionPolicy Bypass -File tools\sign_app.ps1 -AppPath "输入.app" -OutputPath "输出.app"

# 指定签名工具路径（默认自动从 DEVECO_SDK_HOME 或默认安装路径推断）
powershell -ExecutionPolicy Bypass -File tools\sign_app.ps1 -SignToolPath "D:\sdk\...\hap-sign-tool.jar"

# 共享机器推荐交互输入密码，避免密码出现在 Java 进程参数中
powershell -ExecutionPolicy Bypass -File tools\sign_app.ps1 -InteractivePassword

# 仅自动化环境：显式启用非交互模式（密码会进入 Java 进程参数）
powershell -ExecutionPolicy Bypass -File tools\sign_app.ps1 -NonInteractivePassword

# 必要时显式指定 Java；默认依次检查 JAVA_HOME、DevEco JBR 和 PATH
powershell -ExecutionPolicy Bypass -File tools\sign_app.ps1 -JavaPath "D:\Java\bin\java.exe"
```

签名流程：解压 .app → 签内部 HAP → 重新打包 → 签 .app 本身。

#### 2.3.3 正式候选生成与核验

正式候选必须在最后一项影响包内容的源码、资源、配置或构建脚本修改完成，并完成适用的自动化测试、`REG-001` 回归和版本一致性检查后，再执行工程级 `assembleApp`。构建钩子对 `AppScope/app.json5` 与 `ResearchRecorder.ets` 的同步更新属于本次构建的一部分。每个确定的源码状态只生成一个成功的正式候选，避免无意义重复构建抬升 `versionCode`。

成功构建后可以且必须补写仅由产物才能确定的证据文档，包括最终 `versionCode`、文件名、字节数、SHA-256、验签结果和候选状态；这些证据性文档修改不要求重新构建。若构建后又修改任何会进入 APP/HAP，或改变其行为、权限、版本、资源、构建或签名输入的文件，原候选立即失效，必须重新完成测试、构建、签名和验签。

若 `assembleApp` 失败，该次输出不构成正式候选，不得签名、改名或提交；应保留失败日志，确认没有遗留构建进程，并隔离或清理不完整的通用输出。构建钩子已经提升的 `versionCode` 默认视为废弃且不得复用，下一次构建继续递增。只有用户明确要求恢复本地工作树、且确认该编号从未分发时，才可使用 `python tools/sync_version.py --code <构建前值> --allow-downgrade` 同步回退，禁止手工修改两处版本字段。失败尝试不计作该源码状态的一个成功正式候选。

签名后至少完成以下核验并保存文本记录；可以使用多份原始验签文本加一份汇总 JSON，但汇总不得替代原始输出：

1. APP 外层 `verify-app`：摘要与签名验证成功
2. 内部 HAP `verify-app`：摘要、签名和代码签名验证成功
3. 内外 Profile：`verifiedPassed=true`，bundle、类型、分发渠道、证书和有效期一致
4. 包元数据：versionName/versionCode、compatible/target API、`debug=false`、权限列表与预期一致
5. 计算最终 APP 的字节数和 SHA-256；不得用签名前或历史包的哈希

正式包必须使用包含 `versionName` 和 `versionCode` 的独立文件名，验签证据也必须使用对应版本号的独立目录或文件名。新的候选完成正式签名、全部核验并由用户确认替代关系前，不得覆盖或删除当前已提交/已发布候选、上一份完整验签通过的候选及其验签证据；通用的 `MetroSpeed-default-*` 中间产物可以覆盖。已经提交或发布的旧包，其文件名、哈希、包内算法标识和提交事实只能作为历史事实保留，不得改写。

`tools/sign_app.ps1` 的默认本地文件名约定如下，`signing/` 整个目录不得提交：
- `release.p12`：密钥库输入
- `release.cer`：发布证书输入
- `releaseRelease.p7b`：Profile 输入

这些名称只是脚本接口，不证明任一文件当前存在、可用或采用何种密钥参数；
实际证书/Profile 身份只在对应候选的私有发布环境和最终验签证据中确认。

> **注意**：release 证书签名的 HAP/APP 不能直接通过 `hdc install` 安装到手机，会报 "signature verification failed due to not trusted app source"。release 签名包只能通过应用市场分发。调试请使用 DevEco Studio 的 debug 证书。

---

## 3. 信任用户校准

**规则 ID：`CAL-001`**

停车校准由用户手动触发，`calibrate_at_stop` 不引入额外速度阈值拦截。

- 点击时先冻结 `preCalBuffer` 中按钮前约 3.6 秒的数据，再从快照里取 rmsDeviation 最低、覆盖 1.5 秒且至少 30 个样本的静止窗；等待结果期间的高频新样本不得挤掉校准证据；候选窗末样本距按钮时刻不得超过 300ms，避免复用过旧静止段；历史不足、窗口过旧或稳定性检查失败时明确拒绝且不改速度
- 成功后以静止窗为零速锚，用校准后的重力重放窗口之后的原始帧并保留已学习主轴；因此停稳时严格归零，按钮后立即起步的真实增量也必须保留
- GNSS/惯性锚点只能在停车校准确认成功后归零，点击请求或拒绝不能改变锚点
- 停车校准请求或拒绝不得刷新最近成功校准时间；仅成功结果可以重置置信度的校准时龄
- **不得**用估算器自身的速度输出去质疑用户操作

---

## 4. 数据文件路径

**规则 ID：`DATA-001`**

所有 JSONL 数据存放在本地研究记录目录，设置环境变量 `METROSPEED_DATA_DIR` 指向该目录。

schema v14 起，所有实际传感器回调都必须逐条落盘并携带 `sessionId`、`measurementRunId` 和版本字段；估算器行必须同时保留纯惯性 `pureInertialSpeedKmh` 与界面显示 `displaySpeedKmh`。schema v15 进一步要求保存独立 `sensor_callback` 记录、请求周期、传感器时间戳和回调时间戳，任何频率判断都必须以实际时间戳为准，不得把请求值或加速度计驱动帧率套给其他传感器。schema v16 的新记录只订阅六类 100Hz 请求传感器，不再采集系统 `GRAVITY` / `LINEAR_ACCELEROMETER`；旧 v13/v15 的 `sysGravity*` 数据和分析开关必须保持可读。schema v17 沿用 v16 的传感器范围，并要求用独立 `device_health` 行在记录开始、结束及运行中每 10 秒保存 `batteryTemperatureC` 与 `thermalLevel`，不得把设备健康字段复制到每条 100Hz 记录。离线对比必须按 `measurementRunId` 隔离，纯惯性回归只允许与同一 `recordSeq` 邻接的精确传感器样本配对，禁止跨测速段或跨缺失样本插值；schema v13 的 `estimatedSpeedKmh` 是锚定后的显示速度，不能当作纯惯性回归基准。

研究记录的本地保留和导出必须满足事务语义：新会话文件完成首行写入、刷新和状态确认后才可删除上一条记录；任何建文件失败都必须恢复旧记录。导出只复制、不删除源文件，采用异步分块读取避免长日志阻塞 UI；受控失败时应通过支持公共文档 URI 的系统能力清理不完整目标，文档服务拒绝删除时必须明确提示可能残留，不能用只支持应用沙箱路径的接口伪装成功，也不能把直接写文档 URI 描述成掉电原子提交；成功后允许重复导出。JSONL 可能包含精确位置、卫星统计和设备热数据，必须由 `.gitignore` 排除且不得提交公开仓库。

离线工具在宣称输入可用于回归前，必须逐行验证受支持 schema 的会话边界、`recordSeq` 连续性、版本字段一致性以及 `start_record` / `stop_record` 生命周期。`inputIntegrity.complete` 采用三态语义：`true` 表示结构完整，`false` 表示已确认不完整，`null` 表示旧格式或未知结构无法判定；只有 `true` 可以计入完整回归通过。面向大日志的快速路径只能省略明确标注的可选统计，不能抽样、跳过输入行、绕过完整性校验或改变核心回放结果。实现应优先使用单次解析、在线统计、紧凑数值缓存或受控的第二遍顺序扫描，避免为同一分析重复解析整份日志和保留不必要的 Python 行对象。

回放分析使用：
```
python tools/replay_estimator.py "<数据目录>\<文件名>.jsonl"
```

---

## 5. 算法改动必须多记录验证

**规则 ID：`REG-001`**

任何算法层面的改动（阈值、条件、状态机顺序、缩放系数、GNSS 锚点条件等）都必须做改动前/后对比；**不得**仅凭单条记录的 MAE 变化决定改动是否生效。

### 5.1 当前代主回归集

当前代主回归集的长期权威入口必须是**受版本控制且经人工确认的
manifest**，至少记录每个输入的稳定逻辑 ID、场景、文件 SHA-256、字节数、
schema、完整性口径和必跑参数配置。算法或融合逻辑改动必须严格使用 manifest
锁定的全部输入，不能按目录当时恰好存在的文件静默增删，也不能仅因单文件
体积较大而跳过。

> **待办 `REG-MANIFEST-001`（尚未落地）**：当前仓库没有经过人工确认、可
> 安全提交的回归 manifest；本规则不伪造文件名、记录名或哈希。在该待办完成
> 前，`METROSPEED_DATA_DIR` 根目录中的 JSONL 暂作为当前代候选集合，明确
> 归档到 `50Hz/`、`旧记录/` 等子目录的文件不自动计入。每次基线开始前必须
> 先生成并保存本轮精确输入清单（路径或脱敏逻辑 ID、字节数、SHA-256、schema、
> 完整性和完整命令行参数），由人工确认仍对应地铁、公交、驾车三条当前记录；
> 缺少这份清单时不得声称完成“当前代全量回归”。

- 改动前跑主回归基线：`python tools/_baseline_all.py --dir <METROSPEED_DATA_DIR>`
- 改动后用相同文件、相同参数跑对比；涉及锚点时同时使用 `--anchor-v2 --pure-zero`
- 必须逐文件报告结果和异常，不能只给汇总平均值
- 地铁数据零影响不等于改动安全，可能只是该记录没有触发对应分支
- 如果部分记录改善、部分记录恶化，必须逐条分析原因后再决定
- manifest 落地后，运行报告必须记录 manifest 自身的版本或 Git commit；
  落地前必须记录上述临时输入清单的 SHA-256

### 5.2 历史档案与大文件

`50Hz/`、`旧记录/` 等历史档案用于兼容性和补充场景验证，不再要求每次算法改动全部重跑。出现以下情况时，必须从历史档案补跑相关记录：

- 修改旧 schema 解析、时间戳兼容或回放配对逻辑
- 当前代三条记录没有覆盖被修改的状态或交通场景
- 当前代结果出现异常，需要用历史记录判断是否为回归
- 准备进行跨版本算法总结，或用户明确要求全档案复核

主回归集中的大文件仍完整顺序回放，避免并行解码多个数百 MB 文件造成内存压力。改动前基线可以复用，但缓存键必须同时包含代码提交哈希、输入文件 SHA-256 和完整命令行参数；任一项变化都必须重算。报告中必须标明复用的基线，禁止把缓存结果冒充本轮新运行。

已确认仅在 EOF 写入中断的记录可以显式使用 `--allow-truncated-tail`，但必须让改动前后读取同一有效前缀，并把结果标为 `incomplete`。`_baseline_all.py` 必须显示 `inputIntegrity` 的完整、不完整或未知状态；只有明确完整可以返回该项成功，不完整或未知都必须以非零退出，禁止把截断或无法证明完整的输入计入完整全绿。

如果当前代主回归集增长到日常无法完整运行，应先在 manifest 中建立并经
人工确认的分段配置，再修改本规则；不得自行静默抽样。`REG-MANIFEST-001`
完成前不得以“数据量还小”为由继续推迟可复现清单。

---

## 6. 参数扫描方法

**规则 ID：`SCAN-001`；主回归验收仍受 `REG-001` 约束。**

参数扫描分两阶段，不得跳过第一阶段的筛选：

1. **灵敏度筛选** — 至少两条互补记录（如制动占比高的 + 制动占比低的，或地铁 + 驾车），默认每个参数 ±20% 各跑一次；必要时扩大到 ±50%。MAE 变化 ≤ 0.5 km/h 的归档为"不敏感"，仅敏感参数进入下一阶段。
2. **主回归集验证** — 敏感参数跑规则 5 定义的全部当前代主回归记录，并按改动范围补充必要的历史记录，检查改善/恶化比例。如果最优点在不同记录间冲突，可尝试密集网格（如 0.0/0.5/0.75/0.85/0.9/0.95/1.0/1.1/1.2/1.5/2.0）寻找公共可行区间。

以下类型的参数**不进入扫描范围**：
- 转换因子（1000、3.6、10^9 等）
- 只影响 UX 不改变回放 MAE 的参数（校准门槛、记录节流等）
- 纯数学保护闸（epsilon）

灵敏度筛选不要求每个参数只跑一条记录——多跑几条互补记录的筛选不算"跳过筛选"。

参数扫描必须调用 `replay_estimator.py` 提供的同构估算器默认参数和进程内顺序回放入口，使一份 JSONL 只解析一次；扫描前必须确认 `inputIntegrity.complete=true`。**不得**用文本补丁法或 monkey-patch 修改源代码，也不得另写一套与产品默认路径分叉的估算逻辑。

参数扫描默认在 pure inertial 模式进行。当 pure 模式找到有效改进点后，应复跑该参数在锚点 v2 模式下是否仍改善——pure 最优 ≠ anchor 兼容。

---

## 7. 工具与文件命名约定

### 7.1 tools/ 目录命名

| 命名方式 | 类型 | 说明 |
|---------|------|------|
| **正常命名** | 核心/通用工具 | 长期保留，是项目的一部分。如 `replay_estimator.py`、`param_sensitivity.py`、`sync_version.py` |
| **`_` 下划线开头** | 内部回归/诊断入口 | 不作为稳定公开接口；可以被规则或测试明确保留（当前为 `_baseline_all.py`），其余一次性脚本用完应清理或合并为通用工具 |

内部回归/诊断脚本必须接受命令行参数指定 JSONL 路径，**不得硬编码特定文件**。除非已被规则、README 或测试明确指定为当前入口，否则任务完成后应及时清理或合并为通用工具。

### 7.2 死文件清理

- 临时构建产物（`.hvigor/`、`entry/build/` 及 `build/` 中的通用中间产物）可在不影响当前/历史正式候选证据的前提下清理；版本化正式 APP 和对应验签证据按 2.3 节保留
- IDE 配置（`.idea/`）不提交，打包时可删除
- Python 缓存（`__pycache__/`、`*.pyc`）随时可删
- `signing/` 目录是敏感文件，**绝对不能提交到公开仓库**

---

## 8. 说明文件维护

**规则 ID：`DOC-001`**

以下四个文件均可被后续证据质疑和修正，但职责不得混用：

| 文件 | 定位 | 更新时机 |
|---|---|---|
| [`.trae/rules/project_rules.md`](project_rules.md) | 约束 AI 行为的硬规则 | 发现缺失或不适用时，仅在用户明确确认后更新 |
| [`.trae/documents/investigation_status.md`](../documents/investigation_status.md) | 当前工作交接快照：当前候选、已完成验证、关键结论边界、外部状态和下一步；隧道机制只保留可恢复上下文的摘要并链接主记录 | 任何持久代码、配置或文档改动，新验证证据或外部提交状态生效后，在提交或交接前更新 |
| [`.trae/documents/harmonyos_tunnel_positioning_reverse_engineering.md`](../documents/harmonyos_tunnel_positioning_reverse_engineering.md) | HarmonyOS 隧道定位研究的权威长记录：镜像/真机对象、哈希、函数链、证据等级、复现方法、未知项和研究时间线 | 系统机制研究出现新证据、反证、边界变化或解释修正时更新；纯 MetroSpeed 发版状态不写入此文档 |
| [`README.md`](../../README.md) | 面向公开仓库的稳定项目说明：当前公开版或下一公开候选、用户可见功能与限制、公式、数据资产、仓库结构和里程碑 | 公开事实、产品行为、限制、算法版本、数据资产或仓库结构变化时同步更新；不展开内部候选流水账 |

同一隧道机制结论以逆向主记录为权威来源；status 只摘要当前结论与未决项，README 只保留公开层面的研究边界和里程碑，禁止三处复制整段机制正文。

权限增删必须同步检查 `module.json5`、权限理由资源、README、应用市场后台文案、隐私政策和发布契约测试，并以最终 HAP 内 `module.json` 为准核验。

README 中以下内容随项目演进变化，改动时一并更新：
- **算法版本号** — 与 `ALGORITHM_VERSION` 同步
- **速度公式** — 与当前显示逻辑一致
- **数据资产表** — 新增记录、新增 MAE 列
- **时间线** — 阶段性成果
- **项目结构** — 新增/移除/重命名工具文件

AppGallery 和 GitHub Release 的更新说明是发布页面输入，默认不写入 README、status 或逆向主记录；只有用户明确指定仓库文件时才落盘。README 时间线只记录阶段性里程碑。更新说明必须以“上一公开分发版本 → 当前待发布版本”的用户可见差异为基准，不得把仅在开发候选中出现并在公开前修掉的问题包装成面向用户的修复，也不得把请求频率写成设备必然回调频率。

---

## 9. 规则质疑与违规告知

**例外流程 ID：`EXC-001`。任何规则例外必须引用被例外的稳定规则 ID。**

上述规则均为当前阶段的最佳实践总结，**不是死命令**。当 AI 认为某条规则在特定情境下不再适用、或违反规则能带来明确收益时，必须：

1. **明确告知用户** — 说明哪条规则、为什么认为应该违反、预期收益是什么
2. **等待用户决策** — 不得在用户确认前自行违反规则
3. **记录违规原因** — 用户确认后，在本次改动的说明中注释违规理由，供后续回溯

无意的规则违反（如遗漏了全量验证）应在发现后第一时间告知用户并补做。

---

## 10. Commit message 规范

**规则 ID：`GIT-001`**

- 标题应概括与上一个 commit 之间发生的变化，说明做了什么、为什么
- **不得包含版本号**（如 `v1.1.1`）——版本号由 Release tag 承载，commit message 描述内容变化
- 正文用 `-` 列出关键变更点，每条一行
- “修改”“构建”“更新文档”“准备提交”“下一步”等请求不自动授权 Git 操作；只有用户明确要求 `commit`/“提交”，或确认了明确包含本地提交的计划，才可创建本地 commit
- 本地 commit 不自动授权 push；只有用户明确要求 `push`/“推送”，或确认了明确包含推送的计划，才可向远端执行普通 push
- force-push、重写已推送历史、创建 tag、GitHub Release、PR 或 AppGallery 提交分别需要明确授权，不能由普通 commit/push 授权推导
- commit 前必须核对 staged diff，只纳入本次任务范围；commit 后报告哈希及 ahead/behind 状态。未推送 commit 的 amend 也只在用户要求修正提交或批准相应计划后执行
