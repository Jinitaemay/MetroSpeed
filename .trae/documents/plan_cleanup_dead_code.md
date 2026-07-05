# 死文件与死代码清理

## 摘要

扫描整个项目目录，删除死文件，修复硬编码路径。

---

## 当前状态扫描结果

### ArkTS 源码

代码健康，无死代码块、无 TODO/FIXME、无未使用 import。

### tools/ 目录

| 文件 | 状态 |
|------|------|
| `_baseline_parallel.py` | **死文件** — `_baseline_all.py` 的简化并行版，功能缺失，无任何引用 |
| `_check_gnss.py` | 34 行简单脚本，仅 investigation_status 列表提及 |
| `_speed_profile.py` | 与 `_speed_series.py` 功能重叠 |
| `_run_new_batch.py` | 与 `_baseline_all.py` 功能重叠（多组参数 vs 单配置） |
| `_confidence_calibrate.py` | **硬编码路径 bug** — fallback 写死 `C:\Users\18918\...` |

### .trae/documents/

| 文件 | 状态 |
|------|------|
| `plan_docs_sync_v102.md` | **已过期** — 1.0.2 同步计划已全部执行 |
| `plan_confidence_sensor_status.md` | **已过期** — 三项改动已执行（置信度被后续重写取代） |

---

## 方案

### 1. 删除死文件（3 个）

| 文件 | 理由 |
|------|------|
| `tools/_baseline_parallel.py` | 死文件，无引用 |
| `.trae/documents/plan_docs_sync_v102.md` | 已过期的计划文档 |
| `.trae/documents/plan_confidence_sensor_status.md` | 已过期的计划文档 |

### 2. 修复硬编码路径（1 处）

`tools/_confidence_calibrate.py` 第 77 行：
```python
# 修复前
data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"C:\Users\18918\Documents\研究记录\旧记录")
# 修复后
data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(os.environ.get("METROSPEED_DATA_DIR", "."))
```

### 3. 低优先级（先保留）

| 文件 | 保留原因 |
|------|---------|
| `_check_gnss.py` | 34 行简单工具，无硬编码路径，不占空间 |
| `_speed_profile.py` | investigation_status 提及，偶用诊断 |
| `_run_new_batch.py` | README 和 investigation_status 均有引用（多参数对比能力是 `_baseline_all.py` 没有的） |

### 4. 同步更新文档

| 文档 | 改动 |
|------|------|
| README.md | 项目结构中移除 `_baseline_parallel.py` |
| investigation_status.md | 项目结构中移除以上 3 个文件的条目 |

---

## 验证

- `git status` 确认只删除/修改目标文件
- `sync_version.py --check` 确认双端同步
