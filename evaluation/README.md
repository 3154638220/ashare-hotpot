# 1.1.0 评估集（冻结集）说明

本目录存放里程碑 6 的**人工评估集**与评分结果。plan.md 第 17.2 节要求：

| 门槛 | 要求 |
| --- | ---: |
| 短期事件冻结集 | ≥ 300 个事件簇 |
| 短期研究榜 Precision@10 | ≥ 0.80 |
| Top 20 无关内容比例 | ≤ 10% |
| Top 20 重复事件比例 | ≤ 5% |
| 机构评估集 | ≥ 100 份调研记录 |
| 机构实体识别精确率 | ≥ 90% |
| 机构集团去重精确率 | ≥ 90% |
| 重大事件必达集漏报 | ≤ 5%（必达召回 ≥ 95%） |

## 工作流

1. **导出候选集**（只读数据库，绝不修改应用数据）：

   ```powershell
   python scripts/evaluation/export_eval_sets.py
   ```

   输出 `short_term_events_v1.json` 与 `institution_records_v1.json`，
   所有标签字段为 `null`。导出时使用固定种子，重复导出内容一致（仅
   `exported_at` 不同）。可以传入 `--db` 指定其他数据库、`--out` 指定输出
   目录、`--seed` 更换抽样种子。

2. **标注并冻结**：2026-08-08 起，用户明确授权里程碑 6 评估集采用 **LLM 标注口径**
   （`scripts/evaluation/llm_annotate.py`，DeepSeek API），代理完成标注后另存为
   冻结版本（例如 `short_term_events_v1.frozen.json`）并保留原文件，同时记录
   冻结日期、标注模型与“LLM 标注口径”来源。**对外声明必须注明“LLM 标注口径”，
   不得表述为“人工核验”**；如后续恢复人工核验，需重新冻结评估集。

   短期集标签：

   - `board[].relevant`：该行信号是否确属有依据的确定性利好/潜在催化。
   - `board[].duplicate`：该行在 Top 20 中是否与同榜其他行内容重复。
   - `events[].label`：该事件簇是否确属真实重大公司事件（`positive_signal`
     / `neutral` / `not_signal` / `duplicate`）。
   - `must_hit[]`：人工补充的“重大事件必达集”，每项包含
     `event_id`、`stock_code` 与说明；评分时统计其在榜内的命中率。

   机构集标签（每条参与记录）：

   - `entity_ok`：机构实体识别（名称/归一）是否正确。
   - `group_ok`：集团归并是否正确。

3. **评分并对照门槛**：

   ```powershell
   python scripts/evaluation/score_eval_sets.py `
     --short-term evaluation\short_term_events_v1.frozen.json `
     --institution evaluation\institution_records_v1.frozen.json `
     --report evaluation\score_report_v1.json
   ```

   任一门槛不满足或标签缺失时退出码为 1，并输出具体数值。

## 当前状态

评估工具链（导出、LLM 标注、评分）已实现并通过离线测试；**冻结集已建立（LLM
标注口径，2026-08-08）**：

- 2026-08-08 对本地数据库（`%LOCALAPPDATA%\AshareHotPot\data\hotpot.db`）只读
  重新导出候选集至 `candidate_20260808/`：事件簇总数 745 个、抽样 300 个
  （≥ 300 达标），调研活动总数 355 个、抽样 100 份（≥ 100 达标），研究榜
  25 行。LLM（`deepseek-chat`）全量标注 0 缺失（含原 14 条预标全部重标，
  全集统一 LLM 标注），冻结文件：
  `candidate_20260808/short_term_events_v1.frozen.json`（含 23 条必达集）与
  `candidate_20260808/institution_records_v1.frozen.json`；评分结果见
  `score_report_v1.json`。
- 评分（LLM 标注口径）：Precision@10=1.000、Top20 无关=0.0、Top20 重复=0.400
  （不达标）、必达召回=0.348（8/23，不达标）、机构实体精确率=0.864（不达标）、
  集团去重=0.996——三个数值门槛未通过，里程碑 6 复选框未勾选；需先修复聚类去重、
  入榜召回与机构归一后重新导出→标注→评分。

## 修复后复评（2026-08-08 第二轮）

- 修复：聚类交叉挂载合并（`get_event_clusters_by_document` + `_pick_keep` 确定性
  保留）、反证模板过滤（`_HYPOTHETICAL_MARKERS` 前文窗口+匹配文本内检查）、
  回购“仅为方案”仅 framework 注入、审核通过/许可证确定性映射、approval/mna
  门控补充、参与者抽取正则（完整后缀/前缀黑名单/句读行跳过）与
  `replace_research_participants` 清除旧解析残留。全量离线 `331 passed, 7 skipped`。
- 重算生产库后重导出重标注重评分（`candidate_20260808_fix2/`、`score_report_v1_fix2.json`）：
  Precision@10=1.000 ✓、Top20 无关=0.0 ✓、**Top20 重复=0.050 ✓**、
  **必达召回=0.615 ✗（8/13）**、机构实体精确率=0.926 ✓、集团去重=0.997 ✓。
- 必达召回未达标：LLM 必达集 13 条中 5 条属计划范围/门槛外（定增预案、增持
  法律意见书、framework 增持计划、framework M=1 投产、M=0 首次回购），引擎按
  plan.md §10.6 正确拒绝；口径冲突需用户决策，代理未改计划阈值。里程碑 6
  复选框未勾选。

## 达标复评（2026-08-08 第三轮，LLM 标注口径）

- 用户决策：**必达集口径收紧为确定性生成**——十类事件之一 + 方向正向 +
  重大性≥2 + 确定性≥0.40 + 无标题正文冲突反证，由 `event_extractions`
  持久化字段生成，不依赖 LLM 判定。导出命令：

  ```powershell
  python scripts/evaluation/export_eval_sets.py --out evaluation\candidate_20260808_fix4 --with-must-hit
  ```

- 聚类稳定修复：已归簇文档不再新建簇（plan §9.3 稳定 ID），重跑
  `0 created / 1062 merged`。
- 最终评分（`candidate_20260808_fix4/`、`score_report_v1_fix4.json`）：
  Precision@10=1.000 ✓、Top20 无关=0.0 ✓、Top20 重复=0.0 ✓、
  必达召回=1.000（9/9）✓、机构实体精确率=0.921 ✓、集团去重=0.999 ✓——
  **第 17.2 节全部数值门槛达标（LLM 标注口径）**，里程碑 6 复选框已勾选。
- 口径说明：必达集由引擎自身结构化字段生成，必达召回衡量“满足计划入榜结构
  门槛的事件能否端到端出现在研究榜”，不代表对计划政策判断的独立核验；
  Precision@10=1.000 为 LLM 全判 relevant 的乐观偏差。对外声明必须注明
  “LLM 标注口径”。

## 已定人工决策

- 2026-08-08（用户确认）：**业绩说明会（`performance_briefing`）计入机构广度**。
  机构评估集与机构指标按当前定义标注和计算，不排除业绩说明会；该决策作为
  冻结集标注口径，后续如需变更必须重新冻结评估集。
- 2026-08-08（用户授权）：**里程碑 6 评估集由 LLM 标注**（代理使用 DeepSeek API
  生成标签并冻结），放弃人工逐条核验；质量门槛声明一律注明“LLM 标注口径”。

## LLM 标注流程

```powershell
# 标注候选集（保留已有标签，只补标 null；密钥从环境变量读取，不落盘、不写日志）
python scripts/evaluation/llm_annotate.py `
  --short-term evaluation\candidate_20260808\short_term_events_v1.json `
  --institution evaluation\candidate_20260808\institution_records_v1.json `
  --out evaluation\candidate_20260808
```

输出 `short_term_events_v1.llm.json` 与 `institution_records_v1.llm.json`；
标注失败或校验不过的条目标记为 `needs_human`，由代理补标后写入冻结文件。

## 里程碑 7：候选 + 活动记录（2026-08-08，LLM 标注口径）

里程碑 7 的评估集覆盖“待核验事件发现层”的 300 份候选样本与至少 100 份活动记录。
2026-08-08 已运行（生产库 `%LOCALAPPDATA%\AshareHotPot\data\hotpot.db` 110→111
原位升级，`hotpot.db.pre-111.bak` 一次性备份，事务迁移并幂等回填 1214 条候选）。

导出（只读，固定种子 `20260806`；`--discovery-size 400` 的原因：最小来源层
`cninfo_research` 仅 82 份文档，默认 300 等分分层只能导出 287 份，低于 300 下限）：

```powershell
python scripts/evaluation/export_eval_sets.py `
  --out evaluation\candidate_20260808_m7 `
  --discovery-size 400 --institution-size 100
```

样本：352 份候选（cninfo_announcement 137 / cninfo_research 82 / sse_publish 133；
parsed 301 / 元数据待解析 47 / 空文本 3 / 失败 1；严格榜命中 7；固定案例 5），
100 份活动记录（869 参与者）。标注与冻结：

```powershell
python scripts/evaluation/llm_annotate.py `
  --discovery evaluation\candidate_20260808_m7\discovery_candidates_v1.json `
  --institution evaluation\candidate_20260808_m7\institution_records_v1.json `
  --out evaluation\candidate_20260808_m7
```

DeepSeek `deepseek-chat` 全量标注 0 `needs_human`、0 错误；冻结文件：
`candidate_20260808_m7/discovery_candidates_v1.frozen.json` 与
`candidate_20260808_m7/institution_records_v1.frozen.json`。评分：

```powershell
python scripts/evaluation/score_eval_sets.py `
  --discovery evaluation\candidate_20260808_m7\discovery_candidates_v1.frozen.json `
  --institution evaluation\candidate_20260808_m7\institution_records_v1.frozen.json `
  --report evaluation\score_report_m7_v1.json
```

结果（LLM 标注口径）：候选召回 1.000（≥0.95）✓、固定案例零遗漏 ✓、
机构实体精确率 0.908（≥0.90）✓（869 中 80 误）、集团去重精确率 1.000（≥0.90）✓。

已知差距（复选框未勾选，未静默改动阈值）：

- 计划要求“原文明确列名机构召回 ≥90%”，但当前 `score_eval_sets.py` 只实现
  实体精确率/集团去重精确率，导出样本也不含活动正文，机构召回本轮无法核验。
- 候选样本来源分层不含 `irm_ircs`（本库尚无互动易同步数据；新增来源由 fixture +
  live 冒烟覆盖）。

## 约束

- 导出脚本只读打开数据库（`mode=ro`），不创建备份、不写任何表。
- 评分脚本离线运行，不访问网络，不读取任何密钥。
- 标签缺失、集合规模不足或门槛不达标时，评分脚本必须失败关闭，不得“尽量通过”。
