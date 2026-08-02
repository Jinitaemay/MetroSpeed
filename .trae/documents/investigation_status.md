# MetroSpeed 当前交接快照

> - 最后核验：2026-08-02
> - 基准分支：`master`
> - 基准源码提交：`be68d37a85c2`
> - 应用：`1.2.0` / versionCode `1785660666`
> - 算法：`anchor-delta-20260801-r4`
> - 研究记录：schema v17
> - SDK：compatible `6.0.0(20)` / target `6.1.1(24)`

本文只记录当前候选、已经验证的行为边界、未决风险和下一步。历史演进由 Git 历史承担；公开说明见 [`README.md`](../../README.md)，硬规则见 [`project_rules.md`](../rules/project_rules.md)，HarmonyOS 隧道定位研究见[权威主记录](harmonyos_tunnel_positioning_reverse_engineering.md)。

## 1. 当前外部状态

截至最后核验时间：

- AppGallery 曾驳回 versionCode `1785602095`，原因是“停车校准”和“导出”禁用文字对比度仅 1.86/1.84。
- 修复后的 versionCode `1785660666` 已替换旧候选并提交复审；尚未记录后续审核结果。
- 收到新反馈时，先核对审核所指 versionCode 和截图，不把旧包问题归到当前候选。
- 本文不把待审候选写成已经公开上架的版本；公开状态变化后再更新。

## 2. 当前正式候选

- 文件：`build/outputs/default/MetroSpeed-1.2.0-1785660666-release.app`
- 大小：354,877 字节
- SHA-256：`1DCF1D428A7AFE595637F9A2E4D32B8BF00A6F89674AB31FECC550D524A1D209`
- 内部 HAP SHA-256：`470F675450F2B796B6D76DA8A83428962E91E010BA08A131ACA7472F31EBD85D`
- 验证证据：`build/outputs/default/verify-1785660666-final/`

该候选已经完成 57 项自动化测试、Python 全量编译、版本同步、差异检查、工程级 release 构建、APP/HAP 双层签名、代码签名与 Profile 独立验签，以及同源码真机 UI 复检。包内确认：

- 包名 `com.codex.metrospeed`，版本 1.2.0/1785660666；
- compatible20、target24、compile SDK 6.1.1.125；
- `debug=false`、`buildMode=release`；
- 算法 r4、schema v17；
- 恰好五项预期权限，不含 `LOCATION_IN_BACKGROUND`。

正式发布签名 APP 不能通过 HDC 直接安装，只用于应用市场分发。任何进入 APP/HAP，或改变行为、权限、版本、资源、构建及签名输入的修改都会使本候选失效；仅证据文档修改不要求重建。

## 3. 当前实现边界

- 首页将惯性测速与灯光打点测速作为并列、互斥的使用方式；打点结果不是惯性测速的真值，也不自动融合。
- 惯性估算只使用加速度计和陀螺仪，按加速度计回调驱动并使用传感器时间戳积分；陀螺仪是硬要求。
- 可靠 GNSS 锚点仍按来源、速度精度和速度/误差量级门控；传导振动和强振动不再阻止可靠锚点刷新。
- “GNSS 锚点冻结”是手动对照实验，不是自动隧道模式或精度增强开关；它会停止刷新锚点，并可能增加纯惯性漂移。关闭后旧锚立即停用，等待下一次可靠回调。
- 停车校准从按钮前近期证据中寻找静止窗，重估重力后从零速锚重放后段；初始校准完成前拒绝停车校准。
- 持续晃动会停止惯性测速并引导切换灯光打点；当前检测器只是安全门禁，不能描述为已经可靠区分手持与底盘振动。
- 首次定位授权在估算器和传感器启动前完成；定位授权可拒绝，纯惯性测速仍可使用。
- schema v17 请求六类 10 ms 周期传感器：加速度计、陀螺仪、旋转矢量、校准磁场、未校准陀螺仪、未校准磁场。实际频率必须按日志时间戳统计，不能把请求周期写成设备必然达到 100 Hz。
- 设备健康数据在记录开始、结束及运行中每 10 秒保存电池温度和系统热等级；当前不采集系统 GRAVITY、LINEAR_ACCELEROMETER、气压或光照。
- 研究记录仅维护本机最近一次完整会话；新会话成功开始后事务化替换旧记录。导出成功后源记录仍保留，可以再次导出。
- 对比度修复绕开 ArkUI `.enabled(false)` 的强制灰显，同时通过命中测试、焦点、无障碍文案和业务 guard 阻止不可用操作。

## 4. 未决风险

### P0：AppGallery 复审收口

状态：versionCode `1785660666` 已提交，等待结果。

完成条件：记录该 versionCode 的通过或驳回结果；若驳回，保存审核截图并复现当前候选后再决定是否修改。

### P1：实车、锁屏与长时验收

状态：已有地铁、公交、驾车三条 schema v17 当前代记录，短时前后台已经验证；锁屏、长时后台、发热、缺帧和日志体积风险仍未关闭。

完成条件：逐条统计六类传感器的实际频率、抖动、丢样、前后台/锁屏状态、电池温度、热等级和日志增长，并补足真实车辆运行验收。

### P1：手持检测器可靠性

状态：RMS+ZCR 在硬质表面传导振动下不能可靠区分手持晃动，缺少合格的真手持对照集。

完成条件：采集明确标注的真手持与稳定放置记录；候选指标在当前主回归集和新增手持集上达到可接受的误停、漏停结果，再同步 ArkTS 与 Python。

### P1：坡道偏置与重力估计

状态：系统重力在地铁场景已经判定 NO-GO，磁力计场景检测也已证伪；坡道静止校准仍可能把坡度投影当作纵向偏置。

完成条件：找到跨地铁、公交和驾车成立的可观测约束，在主回归集上无回归并完成坡道专项实测；未经数据验证不得重新接入系统重力。

### P2：HarmonyOS 量产机动态闭环

状态：供给镜像的静态链已经较完整，但 Pura 70 上 1105 的实际载荷、配置、传感器节拍、PVT cadence、VDR/PF/TMM 启停和输出 receiver 尚未闭环。

完成条件：取得 1105 对应 binary/maps/Build ID 与实际配置，或在具备权限的环境记录端到端动态证据；如果量产权限无法跨越，明确记录阻断点，不用镜像请求值代替实测。

## 5. 下一步

1. 等待并核实 AppGallery 对 versionCode `1785660666` 的复审结果；等待期间不重复构建相同源码。
2. 优先完成三条 schema v17 记录的长时频率、丢样、锁屏和热状态分析。
3. 下一次出行补真手持/稳定放置对照数据，以及坡道专项数据。
4. 系统机制研究继续追踪 1105 动态载荷、PVT 和输出 receiver；新证据只写入逆向主记录，本文件仅同步结论边界。
5. 算法或融合语义修改前后运行当前代主回归；同步 ArkTS、Python 和 README 中的算法标识。
6. 只有最后一项包内容修改完成且验证通过后，才生成新的正式候选。

## 6. HarmonyOS 隧道定位研究摘要

供给镜像已经静态还原 HiGeo 的 3D VDR、安装角估计、NHC、Kalman、AI-VDR，以及 1106 的隧道路网 PF、三轴磁匹配和资产管理主干。

1102a/1106 对主要 IMU、磁场和旋转矢量的名义请求为 10 ms，对气压/光照请求为 50 ms；这只证明镜像变体的请求值和时间戳语义，不等于量产机实际频率，也不能推出 PVT tick 为 100 Hz。

Pura 70 只读核查目前只确认 `hignss_1105_ohos` 和 XDR 控制下发；普通 shell 不能确认其实际 HiGeo 载荷、配置和传感器节拍。公开 `sourceType` 也不能区分实时卫星解与私有融合延拓。完整证据、等级、复现路径和未决项统一见[逆向主记录](harmonyos_tunnel_positioning_reverse_engineering.md)。

## 7. 关键路径与接手命令

- 页面：`entry/src/main/ets/pages/Index.ets`
- 惯性测速：`entry/src/main/ets/pages/InertialSpeed.ets`
- 灯光打点：`entry/src/main/ets/pages/TunnelLight.ets`
- 估算器：`entry/src/main/ets/model/SpeedEstimator.ets`
- 传感器：`entry/src/main/ets/model/SensorController.ets`
- 记录器：`entry/src/main/ets/model/ResearchRecorder.ets`
- 离线回放：`tools/replay_estimator.py`
- 主回归：`tools/_baseline_all.py`
- 版本同步：`tools/sync_version.py`

```powershell
python -m unittest discover -s tools -p "test_*.py" -v
python tools/sync_version.py --check
python tools/_baseline_all.py --dir "$env:METROSPEED_DATA_DIR" --anchor-v2 --pure-zero --gnss-lag-ms=-40
```

正式构建、签名和验签必须按 [`project_rules.md`](../rules/project_rules.md) 执行。`signing/` 是本地敏感目录，绝对不能提交 Git；本文不记录口令或凭据关系。
