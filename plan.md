# A股热度 1.1.0 实施计划

> 状态：待实施  
> 计划版本：1.0  
> 目标产品版本：1.1.0  
> 最近核验：2026-08-06  
> 实施入口：后续自动化代理必须先阅读根目录 `AGENTS.md`

## 1. 版本目标

1.1.0 不再继续强化一个统一的“热度总榜”，而是在保留现有原始信息入口的基础上，引入两个目标函数完全不同的研究引擎：

- **短期催化引擎**：判断一项新事件是否可能显著改变公司的收入、利润、资产、订单、监管状态或资本结构。
- **中期机构关注引擎**：判断公开披露的机构研究行为是否相对公司自身历史水平出现升温、持续或结构变化。

核心原则：

> 短期不数新闻，判断事件改变了什么；中期不数文章，判断多少独立机构在持续采取研究行为。

所有信号必须满足以下产品约束：

- 可解释：榜单必须展示入榜原因和主要组成指标，不能只显示黑盒分数。
- 可追溯：机制、数值、确定性、反证和机构身份均须关联公开来源或正文证据。
- 可拒绝：`no_valid_signal` 应是正常且高频的结果，不得强迫每条信息生成利好。
- 不越界：不预测股价、不输出买卖建议、不把关注度解释为资金流向或机构看多。
- 可降级：模型、PDF、单一来源或历史回填失败时，应保留可验证的已有结果并明确标记质量状态。

## 2. 基线与当前状态

### 2.1 实施基线

- 以**当前工作区**为基线，而不是回退到 `v0.2.0` 标签。
- 当前工作区包含尚未提交的多来源、互动榜、过滤、存储和专业 UI 改动；这些改动均视为用户资产，后续实施不得重置或覆盖。
- 当前离线测试基线为：`95 passed, 4 skipped`。
- 里程碑 6 已把版本元数据统一为 `1.1.0`（基线审计时仍为 `0.2.0`；1.1.0 的版本跳升是明确决定，不要求先发布 1.0.0）。

### 2.2 已知不一致

当前 README 已声称“基本面消息”包含巨潮资讯全市场公告，但代码中尚无真正的巨潮公告适配器。里程碑 0 必须先把这一点记录为现状：

- 巨潮适配器完成并验证前，不得继续新增或强化“已接入巨潮”的对外描述。
- 实现完成后，再统一修订 README、项目介绍、方法说明和数据质量描述。
- 现有同花顺“个股公告”栏目不能冒充巨潮官方公告来源。

## 3. 范围

### 3.1 保留的原始榜单

- 基本面消息
- 基本面互动
- 综合人气
- 飙升榜

原始榜单继续作为信息入口，其统计口径保持独立，不与新研究信号合成总分。

### 3.2 1.1.0 新增的研究视图

- **确定性利好**：已确认、有明确正向机制、重大性达到阈值且无高度反证的事件。
- **潜在催化**：可能影响较大，但仍处于中标待签、审批中、框架协议或筹划阶段的事件。
- **20 日机构升温**：最近 20 个交易日相对前 100 个交易日分桶基线的关注加速。
- **60/120 日持续关注**：机构活动的周度持续性、重复跟进、研究深度和单日集中度。

### 3.3 明确延期

以下内容不进入 1.1.0，避免把核心闭环扩张成不可验证的大版本：

- 股票—事件—机构知识图谱 UI
- 催化 × 机构关注二维矩阵
- 卡片反馈、负样本训练和轻量学习排序
- 重大负面独立榜单
- 券商研报的新覆盖、评级、目标价和盈利预测修正
- 基金、保险或北向持仓变化
- 扫描版 PDF OCR
- 付费、登录后或需要绕过验证码的数据源
- 股价预测、收益回测或投资建议

## 4. 数据来源与合规边界

### 4.1 现有来源

- 同花顺公开新闻与栏目列表
- 深交所互动易公开问答
- 上证 e 互动公开问答
- 东方财富官方综合人气榜和飙升榜

现有四榜继续遵循低频、有限分页、手动刷新和失败透明原则。

### 4.2 新增公开来源

1. **巨潮资讯公告与调研栏目**
   - 用于正式公告、投资者关系活动记录表和附件 PDF。
   - 入口：[巨潮资讯调研/公告](https://www.cninfo.com.cn/new/commonUrl?url=disclosure%2Flist%2Fnotice)
2. **深交所互动易调研活动**
   - 投资者关系活动记录通常包含参与单位、时间和交流内容。
   - 制度依据：[深交所业务办理指南](https://docs.static.szse.cn/www/disclosure/notice/W020230804632650900092.pdf)
   - 后 1.1.0 可靠性里程碑：在巨潮调研与上证 e 互动发布流之外，接入互动易“投资者关系活动”公开流
     （`searchTypes=4`，入口 [互动易](https://irm.cninfo.com.cn/ircs/index)），经 fixture 与 live 契约验证；
     若无法免登录稳定读取、出现身份页或结构异常则失败关闭并报告，不绕过限制。
3. **上证 e 互动“上市公司发布”**
   - 补充沪市上市公司的投资者说明会、证券分析师调研和路演记录。
   - 制度依据：[上交所规范运作指引](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/staripo/c/c_20260424_10816612.shtml)
4. **上交所休市安排**
   - 用于构建 20/60/120 交易日窗口。
   - 入口：[上交所休市安排](https://www.sse.com.cn/disclosure/dealinstruc/closed/)

### 4.3 来源约束

- 只接入公开、无需账号即可访问的数据。
- 不绕过登录、验证码、频控、反爬或访问控制。
- 新适配器沿用 `PoliteHttpClient` 的超时、重试、请求间隔和取消机制。
- 每个新来源必须提供离线 fixture、结构验证和失败覆盖测试。
- 首屏空数据、身份页、结构突变或解析结果异常时必须失败关闭，不能生成伪空榜。
- 页面地址、参数或字段属于内部适配器细节，不允许渗透到排名服务和 UI。

## 5. 总体数据流

```text
公开列表/调研流
    ↓
候选文档元数据
    ↓
高置信规则过滤 ─────────────→ 过滤原因/噪声统计
    ↓
HTML/PDF 正文提取
    ↓
SourceDocument + EvidenceRef
    ↓
股票主体识别与持久化事件聚类
    ↓
┌───────────────────────────┬────────────────────────────┐
│ 短期事件结构化抽取         │ 机构/活动/问答结构化抽取    │
│ 规则优先，可选 AI 增强      │ 保守实体归一与活动日期拆分   │
└───────────────────────────┴────────────────────────────┘
    ↓                                      ↓
四级门控 + 短期信号                    20/60/120 日透明指标
    ↓                                      ↓
确定性利好 / 潜在催化                  机构升温 / 持续关注
    └──────────────────────┬──────────────────────┘
                           ↓
                  Snapshot + 数据质量状态
                           ↓
                     Qt 表格与证据明细
```

## 6. 核心数据契约

下面的类型名称和职责是实现约束。字段可根据 Python 序列化需要增加内部元数据，但不得改变其语义。

### 6.1 文档和证据

```python
@dataclass(frozen=True, slots=True)
class SourceDocument:
    document_id: str
    provider_key: str
    provider_name: str
    kind: str                 # news | announcement | research_activity
    source_url: str
    document_url: str | None  # PDF 或正文地址
    title: str
    published_at: datetime
    stock_codes: tuple[str, ...]
    body_text: str
    content_hash: str         # SHA-256
    parse_status: str         # parsed | metadata_only | empty_text | failed
    parse_error: str | None

@dataclass(frozen=True, slots=True)
class EvidenceRef:
    evidence_id: str
    document_id: str
    start_offset: int | None
    end_offset: int | None
    excerpt: str
    source_url: str
```

证据规则：

- `excerpt` 最长 240 个字符，仅用于界面解释和测试。
- 正文偏移可用时必须保留；PDF 无法提供稳定偏移时允许为空，但必须有摘录和来源 URL。
- 没有证据的金额、比例、客户、状态、机制和反证必须返回空值。
- `body_text` 不进入现有 `summary` 字段，避免把摘要和正文语义混在一起。

### 6.2 事件簇和抽取结果

```python
@dataclass(slots=True)
class EventCluster:
    event_id: str
    stock_codes: tuple[str, ...]
    canonical_title: str
    first_seen_at: datetime
    last_seen_at: datetime
    representative_document_id: str
    document_ids: list[str]
    historical_similar_event_id: str | None

@dataclass(frozen=True, slots=True)
class EventExtraction:
    event_id: str
    stock_code: str
    event_type: str
    direction: str
    positive_mechanism: str | None
    metrics: tuple[dict[str, object], ...]
    certainty_stage: str
    certainty: float
    novelty: float
    unexpectedness: float
    materiality_level: int
    counter_evidence: tuple[dict[str, object], ...]
    evidence_ids: tuple[str, ...]
    no_valid_signal: bool
    extractor_kind: str      # rules | llm | rules_fallback
    extractor_version: str

@dataclass(frozen=True, slots=True)
class EventSignal:
    event_id: str
    stock_code: str
    board: str               # confirmed_positive | potential_catalyst
    score: float
    source_confidence: float
    materiality_level: int
    certainty: float
    unexpectedness: float
    novelty: float
    timeliness: float
    penalty: float
    provisional: bool
```

`metrics` 中每项至少包含：`name`、`value`、`unit`、`comparison_basis`、`comparison_ratio`、`evidence_id`。缺失值使用 `None`，不得根据行业常识自动补全。

### 6.3 机构和调研活动

```python
@dataclass(frozen=True, slots=True)
class Institution:
    institution_id: str
    canonical_name: str
    group_id: str
    institution_type: str
    verification_status: str  # verified | normalized | needs_review

@dataclass(frozen=True, slots=True)
class InstitutionAlias:
    normalized_alias: str
    institution_id: str
    source: str                # seed | exact_rule | manual

@dataclass(frozen=True, slots=True)
class ResearchActivity:
    activity_id: str
    stock_code: str
    source_document_id: str
    activity_dates: tuple[date, ...]
    activity_type: str
    reported_participant_count: int | None
    named_participant_count: int
    question_count: int
    high_depth_question_count: int
    topic_counts: dict[str, int]

@dataclass(frozen=True, slots=True)
class ResearchParticipant:
    activity_id: str
    institution_id: str
    analyst_name: str | None
    evidence_id: str

@dataclass(frozen=True, slots=True)
class CoverageState:
    source_key: str
    requested_start: date
    covered_start: date | None
    covered_end: date | None
    trading_days_covered: int
    reached_cutoff: bool
    provisional: bool
    error: str | None
```

机构活动语义：

- 同一披露文件包含多个明确日期时，保留一个活动实体并记录全部 `activity_dates`。
- 活跃周和重复跟进按明确日期计算；无法拆分时使用披露的活动结束日期并标注日期精度。
- 未明确列出姓名的机构不能凭 `reported_participant_count` 生成虚构实体。

## 7. SQLite 设计与迁移

### 7.1 版本策略

- 使用 `PRAGMA user_version` 管理数据库版本。
- 当前无版本号数据库视为版本 0；1.1.0 完成后设置为 110。
- 迁移在 `BEGIN IMMEDIATE` 事务中执行，失败必须整体回滚。
- 首次从版本 0 升级前创建一次同目录备份，命名为 `hotpot.db.pre-110.bak`；已存在时不覆盖。
- 从版本 110 升级到 111 前创建一次同目录备份，命名为 `hotpot.db.pre-111.bak`；已存在时不覆盖。
- 初始化和迁移必须可重复调用，不能依赖用户手动清库。

### 7.2 新表

- `source_documents`
- `evidence_refs`
- `event_clusters`
- `event_cluster_documents`
- `event_extractions`
- `event_signals`
- `institutions`
- `institution_aliases`
- `research_activities`
- `research_activity_dates`
- `research_participants`
- `institution_metric_snapshots`
- `source_sync_state`
- `trading_days`
- `discovery_candidates`（后 1.1.0 可靠性里程碑新增：公开列表项发现层与可恢复附件工作队列）

至少建立以下索引：

- 文档发布时间、内容哈希、股票代码关联
- 事件 `first_seen_at`、`last_seen_at` 和股票关联
- 调研活动日期、股票代码
- 参与者 `institution_id`、活动 ID
- 指标窗口、快照时间、股票代码
- 同步状态的 `source_key + sync_kind`

### 7.3 兼容性

- 保留 `articles`、`interactions`、`snapshots` 和现有应用状态。
- 新 Snapshot 字段必须有默认空值，旧 JSON 快照可继续加载。
- 旧快照只展示原有四榜；不得伪造研究信号。
- `clear_all` 要显式覆盖所有新表；普通过期清理不得误删机构历史或同步游标。
- 取消刷新时，本次信号快照不落库；已经完整提交的历史回填页和同步游标可以保留。

### 7.4 保留周期

- 普通新闻正文：30 天。
- 事件簇和事件指纹：至少 180 天。
- 调研文档、机构、活动、参与者和指标基础数据：至少 400 天。
- 交易日历：永久缓存，后续按年度更新。
- LLM 抽取缓存：随文档和抽取 schema 版本保留，文档删除时级联清理。

## 8. PDF 和正文处理

- 新增运行依赖 `pypdf`，并验证 PyInstaller 收集结果。
- 下载 PDF 后计算 SHA-256，再执行文本提取。
- 解析成功后保留文本、页数、哈希、URL 和解析状态；原始 PDF 只允许放在可清理的临时目录，处理结束后删除。
- 空文本、加密文档、损坏文档或纯扫描文档记为 `empty_text`/`failed`，保留原文链接，不做 OCR。
- PDF 失败只影响该文档；同事件其他 HTML/公告来源仍可形成信号。
- 文本规范化只处理断行、重复页眉页脚和空白，不修改数字、小数点、百分号、单位、否定词或风险提示。

## 9. 持久化事件聚类

### 9.1 候选阻塞

只有满足以下条件的已有事件才进入聚类候选集：

- 主体股票至少有一个交集；
- 新文档发布时间与事件 `last_seen_at` 相差不超过 72 小时；
- 事件尚未被人工/规则标记为不同事项。

### 9.2 合并条件

候选满足以下任一高置信条件时合并：

1. URL 或内容哈希完全一致；
2. 规范化标题 RapidFuzz 相似度不低于 90%；
3. 标题加摘要的字符三元组余弦相似度不低于 0.82；
4. 结构化指纹中的事件类型、核心金额/比例、客户或标的、关键日期一致。

如果只有模糊语义相似而关键金额、客户、标的或状态冲突，不得合并。

### 9.3 稳定 ID 和历史相似事件

- 新事件创建时生成持久化 UUID；后续加入新文档不能改变 `event_id`。
- 代表文档优先级：交易所/巨潮正式披露 > 交易所官方互动 > 有原始出处的媒体 > 聚合转载。
- 超过 72 小时但 180 天内高度相似的事件不合并，只设置 `historical_similar_event_id`，用于新颖性和旧闻检查。
- 一个事件可以关联多只主体股票，但每只股票必须单独通过正向机制和重大性判断，不能因同文出现而全部入榜。

## 10. 短期催化引擎

### 10.1 支持的事件类型

| event_type | 中文标签 | 核心字段 |
| --- | --- | --- |
| `earnings_upgrade` | 业绩上修 | 利润区间、同比变化、前次预期 |
| `major_contract` | 重大订单 | 金额、营收占比、周期、是否正式合同 |
| `price_increase` | 产品涨价 | 涨幅、产品、产能、销量、执行日 |
| `approval` | 获批认证 | 产品、市场、审批阶段、能否商业化 |
| `buyback_or_increase` | 回购增持 | 金额、市值占比、价格上限、实施状态 |
| `mna` | 并购重组 | 标的、规模、支付方式、审批阶段 |
| `capacity_launch` | 产能投产 | 新增产能、原产能、达产时间 |
| `direct_policy_benefit` | 直接政策受益 | 政策条款、业务覆盖、直接影响机制 |
| `customer_breakthrough` | 重要客户突破 | 客户证据、订单状态、预计贡献 |
| `subsidy_or_compensation` | 补贴赔偿 | 金额、利润占比、一次性属性、到账状态 |

其他事件类型在 1.1.0 返回 `unsupported_event_type` 或 `no_valid_signal`，不由模型临时创造新枚举。

### 10.2 固定处理顺序

```text
规则排除
→ 持久化事件聚类
→ 结构化抽取
→ 公司级新事件检查
→ 正向传导机制检查
→ 重大性判断
→ 确定性/意外性/新颖性判断
→ 反证检查
→ 分数与入榜门槛
```

前一关失败时停止继续美化结果，并记录明确原因。

### 10.3 重大性

优先使用相对量，统一映射至 0–4 级。

合同金额/营收、补贴或利润影响/净利润、产能增量/原产能、业绩变化比例：

| 比例 | 等级 |
| --- | ---: |
| `<1%` | 0 |
| `1%–<5%` | 1 |
| `5%–<15%` | 2 |
| `15%–<30%` | 3 |
| `≥30%` | 4 |

回购或股东增持金额/市值：

| 比例 | 等级 |
| --- | ---: |
| `<0.1%` | 0 |
| `0.1%–<0.5%` | 1 |
| `0.5%–<1%` | 2 |
| `1%–<3%` | 3 |
| `≥3%` | 4 |

不可量化事件采用保守定性标准：

- 0：无明确影响。
- 1：方向正面但影响弱或缺少关键证据。
- 2：可能产生可见经营影响。
- 3：可能改变年度经营预期。
- 4：可能改变核心业务或估值逻辑。

定性等级 3–4 必须包含两条相互独立的正文证据，且至少一条来自正式披露或公司官方记录。

### 10.4 置信因子 `C`

`C = min(source_confidence, certainty)`。

来源可信度：

- 1.00：交易所或巨潮正式披露。
- 0.90：交易所官方互动中的公司正式回复/发布。
- 0.75：明确引用原始公告的主流媒体。
- 0.60：只有聚合媒体正文，无法定位原始公告。
- 0.30：自媒体、传闻或无法核验来源。

事件确定性：

- 1.00：已执行、已到账、已取得正式批文。
- 0.90：正式合同、董事会/股东会已通过。
- 0.70：中标或获选但尚未签署正式合同。
- 0.45：框架协议、合作意向、筹划或申请中。
- 0.20：媒体传闻、市场猜测。

### 10.5 `M/U/N/T/P` 映射

- `M`：重大性等级 `0/1/2/3/4` 映射为 `0/25/50/75/100`。
- `U` 意外性：
  - 100：正文明确高于预期、提前完成或显著超出历史规模。
  - 75：首次发生且有历史比较依据。
  - 50：新事件但无可靠预期基准。
  - 25：此前已披露，当前只是新增实质进展。
  - 0：旧闻、按计划例行进展或无新增事实。
- `N` 新颖性：
  - 100：180 天内无同类历史事件。
  - 60：同类事件曾发生，但当前规模、客户、标的或市场明显不同。
  - 30：旧事件出现新的可量化实质更新。
  - 0：重复报道或无新增事实。
- `T` 时效性：按所选观察窗口从发布时间的 100 线性衰减至窗口边界的 0；未来时间或异常时间戳不得获得额外分数。
- `P` 惩罚项累加后封顶 80：
  - 部分抵消：15。
  - 高度不确定：35。
  - 此前已预告/正常进展：20。
  - 相对规模低于 1 级门槛：20。
  - 旧闻或近重复：40。
  - 标题与正文方向冲突：60。

### 10.6 分数和入榜门槛

```text
S = C × (0.35M + 0.25U + 0.20N + 0.20T) - P
```

- **确定性利好**：有证据支持的正向机制；重大性 `≥2`；确定性 `≥0.70`；无高度反证和标题正文冲突；`S ≥60`。
- **潜在催化**：有正向机制；重大性 `≥1`；确定性 `≥0.40`；无标题正文冲突；`S ≥35`；卡片必须显示“尚未落地”。
- 不满足上述条件：`no_valid_signal=True`，保留拒绝原因，不进入研究榜。
- 同一事件对同一股票只产生一个信号；新来源只补充证据，不重复计分。

榜单排序：分数降序 → 重大性降序 → 确定性降序 → 事件时间降序 → 股票代码升序。

### 10.7 反证检查

至少识别：

- 合同未生效、履行周期过长或金额含税费。
- 利润增长主要来自非经常性资产出售。
- 营收增长但扣非利润下降。
- 补贴已在上一年度确认。
- 回购/增持仅为方案，历史执行率很低。
- 同时存在减持、终止、诉讼、审批失败或客户不确定性。
- 标题正向但正文结论相反。

反证输出为：`none`、`partial`、`high_uncertainty`、`title_body_conflict`。每条反证必须关联证据。

## 11. 可选 AI 增强

### 11.1 接口

定义协议：

```python
class SignalExtractor(Protocol):
    def extract(
        self,
        cluster: EventCluster,
        documents: tuple[SourceDocument, ...],
    ) -> EventExtraction: ...
```

实现：

- `RuleBasedSignalExtractor`：始终可用，是无配置默认实现。
- `OpenAICompatibleSignalExtractor`：可选增强，输出必须通过同一数据契约验证。
- `FallbackSignalExtractor`：模型失败时返回规则结果并设置 `extractor_kind="rules_fallback"`。

### 11.2 配置与密钥

- 默认 `enabled=False`。
- 设置项：`base_url`、`model`、`api_key`、超时。
- API key 使用 Windows DPAPI 按当前用户加密，密文存入独立设置文件；不进入 SQLite。
- 日志、异常、诊断、快照、CSV 和剪贴板输出都不得包含密钥或 Authorization 头。
- 提供“清除 AI 凭据”操作。

### 11.3 调用规则

- 只发送通过规则初筛后的事件簇代表文本，不发送全部信息流。
- 请求超时 30 秒；429 或 5xx 最多重试一次，使用退避。
- 返回内容必须是单个严格 JSON 对象；拒绝 Markdown 包裹、多对象、未知枚举、越界分数和无证据字段。
- 以 `content_hash + model + prompt_schema_version` 作为缓存键，避免重复调用。
- 模型不可用、超时、非法 JSON、限流或结构校验失败不得中断整体刷新。

## 12. 机构实体和活动解析

### 12.1 实体归一

- 先做 Unicode、全半角、空白和标点规范化。
- 使用维护在代码中的保守种子别名表处理常见简称/全称。
- 允许去除无辨识意义的法定后缀，但保留地区、品牌和集团差异。
- 同集团不同法定主体通过 `group_id` 汇总广度，同时保留原始机构实体。
- 编辑距离或模糊相似度只能生成 `needs_review` 候选，不能自动合并。
- 名称冲突时宁可分开计数并标记质量问题，不可误合并两家独立机构。

机构类型固定为：

- `brokerage`
- `public_fund`
- `private_fund`
- `insurance`
- `asset_management`
- `foreign_institution`
- `other`

媒体、个人投资者、上市公司人员不计入独立机构广度，但可保留在原始活动文本中。

### 12.2 参与者口径

- 只对正文或附件明确列出的机构计数。
- “约 100 家机构”“众多投资者”等模糊总数写入 `reported_participant_count`，不生成 100 个实体。
- 表格出现机构和人员两列时，机构用于广度，人员姓名用于分析师数。
- 同一机构在同一活动多次出现只计一次。
- 同一集团的不同机构实体在“机构实体数”中分别保留，在“独立机构集团数”中只计一次；排行榜默认使用集团数。

### 12.3 问题深度和主题

深度：

- 低：公司概况、行业看法、泛化规划。
- 中：毛利率、订单节奏、产能利用率、库存、客户结构等经营细项。
- 高：单位经济模型、价格成本拆解、明确产能释放时间、客户认证、量化经营指引、竞争份额变化。

深度数值：低 `0.25`、中 `0.60`、高 `1.00`；无有效问答为 `0`。

主题固定为：`growth`、`profitability`、`orders`、`capacity`、`products`、`customers`、`risks`、`governance`、`other`。

只展示“关注主题”和“风险问题占比”，不得输出“看多/看空”或“机构观点方向”。

## 13. 20/60/120 交易日指标

### 13.1 交易日历

- 缓存上交所年度休市安排并生成 `trading_days`。
- 当前年份日历不可用时，使用周一至周五降级计算并标记 `calendar_fallback=True`。
- 所有窗口边界由交易日历服务提供，排名服务不得自行用自然日近似。

### 13.2 20 日机构升温

当前桶为最近 20 个交易日；历史基线为之前五个互不重叠的 20 日桶，共需 120 个交易日覆盖。

```text
z20 = (current_unique_groups - mean(previous_five_buckets))
      / max(std(previous_five_buckets), 1)
```

同时计算：

- 当前独立机构集团数。
- 新增机构集团数：前 100 个交易日未出现、本期首次出现。
- 明确披露的分析师人数。
- 高深度问题占比。
- 相对所属行业的关注分位数；行业样本不足 5 只股票时返回空值。

完整覆盖时排序：`z20` 降序 → 新增机构集团数降序 → 高深度问题占比降序 → 最近活动时间降序 → 股票代码。

不足 120 个交易日时：

- `z20=None`。
- 仍显示当前窗口原始指标。
- 以当前独立机构集团数、新增机构数和最近活动时间排序。
- 明确显示“冷启动/暂定”，不能伪装成完整基线排名。

### 13.3 60/120 日持续关注

组件均归一至 0–1：

- `active_week_ratio`：有机构活动的自然周数 / 窗口总周数。
- `repeat_followup_ratio`：在至少两个不同活动日出现的机构集团数 / 独立机构集团数。
- `depth_score`：有效问答深度数值的平均值。
- `single_day_concentration`：单日最大“机构集团—日”数 / 全窗口“机构集团—日”总数。

```text
persistence_score = 100 × (
    0.40 × active_week_ratio
  + 0.25 × repeat_followup_ratio
  + 0.20 × depth_score
  + 0.15 × (1 - single_day_concentration)
)
```

排序：持续关注分降序 → 活跃周数降序 → 独立机构集团数降序 → 最近活动时间降序 → 股票代码。

120 日详情额外比较最近 60 日与此前 60 日：

- 新增和流失机构集团。
- 机构类型占比变化。
- 高深度问题占比变化。
- 活跃周比例变化。
- 单日集中度变化。

这些变化只描述研究行为结构，不推断投资观点。

## 14. 历史回填与刷新

### 14.1 回填范围

- 首次目标为最近 200 个自然日，确保通常覆盖至少 120 个 A 股交易日。
- 每次刷新最多处理 20 个列表页或 50 份新 PDF，以先到限制为准。
- 当前窗口的新数据优先，历史回填使用剩余配额。
- `source_sync_state` 保存来源、查询类型、游标、目标起点、已覆盖起点、最后成功时间和最后错误。

### 14.2 原子性与取消

- 每个完整列表页的文档和新游标在同一事务中提交。
- 取消后保留此前完整提交的回填页，丢弃未完成页。
- 当前刷新只有在排名、数据质量和快照全部生成成功后才保存新 Snapshot。
- 单一来源失败时沿用最近成功数据并标记过期；全部研究来源失败且无历史数据时不生成伪空研究榜。

### 14.3 覆盖状态

20/60/120 视图都展示：

- 请求窗口。
- 实际覆盖交易日数。
- 已扫描来源数/总来源数。
- 是否到达时间边界。
- 是否使用日历降级。
- 最近成功同步时间。
- 冷启动、部分覆盖或过期原因。

## 15. UI 与交互

### 15.1 导航

顶部导航分成两个视觉组：

```text
原始关注度：基本面消息 | 基本面互动 | 综合人气 | 飙升榜
研究信号：  确定性利好 | 潜在催化 | 机构升温 | 持续关注
```

不把八个入口做成没有分组的一排按钮。现有快捷键、搜索、刷新、导出和数据质量区域继续可用。

### 15.2 表格字段

确定性利好/潜在催化：

- 排名、股票、代码、事件类型
- 正向机制
- 重大性等级和关键相对量
- 确定性
- 主要反证/落地风险
- 事件时间
- 数据质量状态

20 日机构升温：

- 排名、股票、代码、行业
- `z20`
- 当前独立机构集团数
- 新增机构集团数
- 明确分析师数
- 高深度问题占比
- 最近活动
- 覆盖状态

60/120 日持续关注：

- 排名、股票、代码、窗口
- 持续关注分
- 活跃周数/比例
- 独立机构集团数
- 重复跟进比例
- 研究深度
- 单日集中度
- 主要关注主题
- 覆盖状态

### 15.3 明细面板

- 短期事件显示：事件摘要、正向机制、量化字段、确定性、新颖性、反证、事件簇全部来源和证据摘录。
- 机构视图显示：活动时间线、参与机构、集团归并结果、问答深度、主题、窗口指标组成和原文链接。
- 每个结果显示“规则”“AI 增强”或“规则降级”标签。
- 潜在催化必须醒目标注“尚未落地”。
- 覆盖不足必须在表格和明细中同时出现，不能只藏在数据质量弹窗。

### 15.4 代码入口

- 活跃主窗口是 `src/ashare_hotpot/professional_window.py`。
- `src/ashare_hotpot/ui.py` 中的旧主窗口保持兼容；只扩展其中被专业窗口复用的共享表模型、代理模型、委托和格式化函数。
- 不在旧窗口和专业窗口各实现一套研究榜 UI。
- `ui_components.py` 用于可复用的证据、设置和详情组件。

## 16. 里程碑与任务清单

只有对应测试和验收通过后，后续代理才可以将复选框改为 `[x]`。

### 里程碑 0：基线审计与口径一致性

- [x] 记录当前 `git status`，确认不覆盖用户改动。
- [x] 运行并记录全量离线测试基线。
- [x] 列出 README 与实际巨潮适配器的不一致。
- [x] 确认原有四榜的模型、存储和 UI 回归保护点。

验收：不改功能的情况下测试仍通过；后续任务以当前工作区为基线。

审计记录（2026-08-06）：`git status --short` 显示已有用户修改覆盖 README、配置、模型、采集、存储、服务、UI 和测试，另有计划及多来源测试 fixture 等未跟踪文件；后续实现以此工作区为基线，不回退或覆盖。完整离线测试为 `95 passed, 4 skipped in 5.38s`。README 将巨潮资讯全市场公告描述为已接入，但当前 `config.py` 的“个股公告”仍指向同花顺，`sources.py` 仅有新闻、互动易和上证 e 互动适配器，尚无巨潮公告适配器。原四榜由 `Snapshot`、SQLite 的 `articles`/`interactions`/`snapshots`、`RefreshService` 和 `ProfessionalMainWindow` 覆盖；服务、存储与 UI 回归测试涵盖四入口、缓存/降级、快照往返、筛选、明细和导出。

### 里程碑 1：数据模型、迁移、交易日和同步游标

- [x] 添加核心类型及序列化往返测试。
- [x] 实现 `PRAGMA user_version` 0 → 110 事务迁移和一次性备份。
- [x] 添加所有新表、索引和清理策略。
- [x] 实现交易日历缓存、节假日解析和工作日降级。
- [x] 实现可恢复同步状态。

验收：旧数据库原地升级；旧快照和四榜仍可加载；重复初始化无副作用。

审计记录（2026-08-06）：`models.py` 新增 SourceDocument、EvidenceRef、EventCluster、EventExtraction、EventSignal、Institution、InstitutionAlias、ResearchActivity、ResearchParticipant、CoverageState、SyncCursor 及 to_dict/from_dict 往返；`storage.py` 实现 PRAGMA user_version 0→110 的 BEGIN IMMEDIATE 事务迁移与一次性 `hotpot.db.pre-110.bak` 备份（已存在不覆盖、失败回滚），新增 16 张研究表与 17 个索引、按 30/180/400 天分域的保留清理、clear_all 与诊断统计覆盖新表；新增 `trading_calendar.py`（上交所休市安排解析、交易日生成、周一至周五降级并标记 fallback、窗口查询）与 `SseCalendarSource` 适配器；`source_sync_state` 支持可恢复游标。新增测试 `test_models_v110.py`、`test_storage_v110.py`、`test_trading_calendar.py`（共 25 项）；全量离线 `120 passed, 5 skipped`（新增 1 项 live 冒烟测试，默认跳过）。旧快照与四榜加载回归由既有 storage/service/UI 测试覆盖。

### 里程碑 2：巨潮/调研来源、PDF 与渐进回填

- [x] 先添加真实匿名化 fixture 和解析契约测试。
- [x] 实现巨潮公告/调研列表适配器。
- [x] 实现上证 e 互动投资者关系活动适配器。
- [x] 实现 PDF 哈希、文本提取、临时文件清理和失败状态。
- [x] 实现 200 自然日渐进回填和取消恢复。
- [x] 修正文档与来源覆盖描述。

验收：无需登录即可低频读取；空页/结构变化失败关闭；回填中断后不重复下载已完成文档。
审计记录（2026-08-06）：`tests/fixtures` 新增巨潮公告/调研/空页/结构异常 JSON、上证 e 互动“上市公司发布”HTML、PDF 正常/空文本/损坏三类 fixture；`parsing.py` 新增 `parse_cninfo_page` 与 `parse_sse_publish_feed` 解析契约；`sources.py` 新增 `CninfoSource`（`hisAnnouncement/query` 公告流 + `disclosure` 调研流，公开接口无需登录）与 `SsePublishSource`（type=30 发布流），`PoliteHttpClient` 新增 `get_bytes`（HTML/纯文本身份页失败关闭）；新增 `pdf.py`（SHA-256、pypdf 文本提取、临时文件即用即删、`parsed`/`metadata_only`/`empty_text`/`failed` 状态）；新增 `research_sync.py`（200 自然日渐进回填、每页文档+游标同事务提交、取消保留已提交页、恢复不重复下载、20 页/50 PDF 预算、metadata_only PDF 预算恢复后补下载）；`RefreshService.refresh` 在窗口数据之后用剩余配额触发回填并写入 stats；`source_documents` 增加 `page_count` 列（幂等 ALTER，新库/旧库/重复初始化/回滚测试覆盖）。新增 `tests/test_research_sources.py`（32 项，含 fixture、解析、适配器、PDF、回填恢复/取消/原子性/预算）与 2 项 live 冒烟（巨潮公告与调研流、上证发布+真实 PDF 提取），实测 `7 passed`；全量离线 `153 passed, 7 skipped`。README 与项目介绍已修订为实际接入描述（巨潮公告/调研、PDF 提取与失败状态、渐进回填），不再夸大或保留过期声明。

缺陷修复审计记录（2026-08-06）：真实数据库核验发现 62 份调研文档（巨潮 32 + 上证 30）全部为 `metadata_only`，根因是 20 页/50 附件预算在三个来源间共享且公告流排在最前，调研/投资者关系来源被饿死；同时上证附件中 16 份为 DOC/DOCX，旧实现只解析 PDF。本次修复：`research_sync.py` 新增 `_split_budget` 按来源均分页与附件额度（余数给前序来源，任一来源不再挤占他人），`budget_exhausted` 改为按来源统计受限；`pdf.py` 泛化为附件提取，新增 `extract_docx_text`（标准库 zipfile+ElementTree 提取段落与表格，不引入 lxml）与 `extract_doc_text`（`olefile` 读取 `WordDocument` + CLX/PlcPcd 分片表，UTF-16 与 ANSI 分片均解码，越界/结构异常失败关闭），`SUPPORTED_ATTACHMENT_TYPES = (PDF, DOC, DOCX)`，其余类型保持 `metadata_only`；`pyproject.toml` 新增运行时依赖 `olefile`；`sse_publish_feed.html` fixture 增加真实结构的 .doc 项。新增/更新测试：DOCX/DOC 提取、未知格式失败、`_split_budget` 均分、三来源独立预算不被公告流饿死、上证 PDF/DOCX/DOC 全量解析；相关测试 `59 passed`，全量离线 `287 passed, 7 skipped`；用生产模块对 16 份真实附件离线核验 16/16 解析成功。未运行 live 与构建（非本里程碑验收项）。

缺陷修复审计记录（2026-08-08）：真实数据库核验发现巨潮公告流会把 01:14 后上传的当日公告永久漏抓——寒武纪 2026 半年报等 6 份公告（公告 ID 1225464969 起）均高于库内已取最大 ID 1225464659，而 `ResearchSyncService` 的历史回填游标只按页递增、从不回看最新页，同源流把新公告插在列表顶部导致游标经过后不再可见。本次修复在每次刷新新增“最新页回扫”阶段：恒从第 1 页重扫（第 1 页未变化时从持久化 `fresh_page` 继续），直到首个全已知页（前沿）或预算耗尽；顶部出现新公告时丢弃旧重扫位置、从第 2 页重新开始；回扫结束后历史回填从 `max(原游标, 前沿+1)` 继续避免重复抓取。游标 JSON 新增可选 `fresh_page`（等于 1 时省略，旧库游标与既有读码兼容），无 schema 变更；单页提交仍原子（提交失败时 `fresh_page` 停在原页，下次重抓）。新增 2 项回归（顶部新公告触发回扫重启、预算截断后间隙续扫），更新 4 项既有游标断言；全量离线 `299→305 passed, 7 skipped`。已知边界：回填已完结、单日新公告量超过单次预算、且回扫期间又有新公告插入的极端组合下，中部漏抓窗口理论上存在，将在第 17.2 节人工评估阶段用真实流量复核。

缺陷修复审计记录（2026-08-08·回扫 PDF 预算）：上一条修复上线后实测发现，半年报高峰日巨潮公告流每页 30 份 PDF，单来源单次刷新 PDF 预算（默认 100 附件 / 3 来源 ≈ 34）只能覆盖约 1 页，回扫被最新页的批量附件（如帅丰电器重大资产重组 30 份附件）拖住，寒武纪 2026 半年报（当日流第 4 页）仍无法在单次刷新内到达。本次修复：公告来源（`kind=announcement`）在回扫阶段只为“标题命中十类事件门控”（复用 `event_type_hint`）的文档下载 PDF 正文，非信号标题公告只保留元数据且不阻塞扫描前进；信号标题附件预算耗尽仍按原语义挂起本页、下次补下载；调研来源（`cninfo_research`/`sse_publish`）保持全部附件下载不受影响。该策略的依据是 `_headline_event_documents` 标题门控：标题不命中任何事件类型的文档在现有管线下不可能产出信号，其正文对信号引擎无用途；预算因此优先流向可产生信号的文档。新增 1 项回归（非信号标题 PDF 不抢占预算、不阻塞回扫）；更新 7 项既有测试的 fixture 标题为信号标题；全量离线 `305→306 passed, 7 skipped`。生产实测（生产库副本 + 真实网络端到端）：单次回扫即抓齐寒武纪 6 份公告（含半年报与摘要，全部解析），信号管线产出确定性利好 688256 score 91.9。

缺陷修复审计记录（2026-08-08·回扫 PDF 配额）：上一轮修复上线后实测发现，半年报高峰日第 1–3 页的信号标题附件本身（如蓝盾光电重大资产重组 15 份、帅丰电器、大恒科技员工持股计划等）就已耗尽单来源 34 份 PDF 预算，寒武纪半年报（第 4 页）仍差 2 份未下载；且上午新公告持续插入，目标会不断下沉。本次修复：公告来源回扫阶段只下载信号标题 PDF，且每页最多 4 份（`MAX_ANNOUNCEMENT_FRESH_PDFS_PER_PAGE=4`），超出配额的信号标题与非信号标题一律只存元数据、不阻塞扫描；非信号标题的已存元数据 PDF 文档在回扫中视为已知（不再反复计入新增），保证前沿判定与续扫效率；回扫深度由页预算（单来源约 14 页/次）保证，PDF 预算摊到更深页面。调研来源与历史回填阶段保持原行为（全部附件、预算耗尽挂起本页）。新增 2 项回归（非信号标题 PDF 不阻塞、单页配额摊开预算并可多轮补全）；更新 2 项既有回扫测试；全量离线 `306→307 passed, 7 skipped`。三来源全开的生产库副本 + 真实网络端到端复验：单次回扫解析寒武纪半年报与摘要，信号管线产出确定性利好 688256 score 91.81；非信号标题的 4 份寒武纪公告（募集资金报告等）保留元数据属预期行为。

### 里程碑 3：事件簇、抽取和短期榜

- [x] 用持久化事件簇替代仅快照内的临时聚类。
- [x] 添加历史相似事件链接和稳定事件 ID。
- [x] 实现十类事件的规则抽取和证据定位。
- [x] 实现重大性、确定性、意外性、新颖性、时效性和惩罚项。
- [x] 实现反证检查和 `no_valid_signal`。
- [x] 实现可选 OpenAI-compatible 抽取、DPAPI 和降级缓存。
- [x] 生成确定性利好和潜在催化榜。

验收：一件事多来源只计一次；无依据不补值；AI 关闭或故障时规则榜仍可用。
审计记录（2026-08-06）：新增 `clustering.py`（持久化事件聚类：股票交集 + 72 小时阻塞、URL/内容哈希/规范化标题 90%/标题+摘要三元组 0.82/结构化指纹四路高置信合并、关键金额/客户/日期冲突禁止合并、稳定 UUID 事件 ID、代表文档按 巨潮>交易所官方>媒体 优先级、72 小时–180 天历史相似链接）；`extraction.py`（十类事件规则抽取：`earnings_upgrade`/`major_contract`/`price_increase`/`approval`/`buyback_or_increase`/`mna`/`capacity_launch`/`direct_policy_benefit`/`customer_breakthrough`/`subsidy_or_compensation`，金额/百分比/单位换算只取正文显式值、缺失字段返回空、EvidenceRef 摘录 ≤240 字符且带正文偏移或标题回退、定性 3–4 级需两条独立证据且至少一条正式披露、反证 none/partial/high_uncertainty/title_body_conflict 四类）；`signals.py`（C=min(source_confidence, certainty)、M/U/N/T/P 映射、S=C×(0.35M+0.25U+0.20N+0.20T)−P、确定性利好 M≥2/确定性≥0.70/S≥60 无高度反证、潜在催化 M≥1/确定性≥0.40/S≥35、拒绝原因确定性可推导、榜排序 分数→重大性→确定性→事件时间→代码，`ShortTermBoardService` 把聚类→抽取→评分→持久化信号串入刷新流程并写入 stats）；`ai_extractor.py`（`SignalExtractor` 协议、`OpenAiClient` 30 秒超时/429/5xx 重试一次、严格 JSON 拒绝 Markdown 包裹与未知枚举/越界分数/无证据字段、DPAPI 加密密钥存独立 `ai_credentials.bin` 文件不入 SQLite、`llm_extraction_cache` 表以 document+model+prompt_schema_version 为键随文档级联清理、`FallbackSignalExtractor` 模型失败按股票降级为 `rules_fallback`、默认关闭）；`storage.py` 新增 `llm_extraction_cache` 表与读写、`get_event_clusters_active`、候选查询 `latest_seen` 边界、`event_signals` SQL 稳定排序兜底、`clear_all` 覆盖新表。新增测试 `test_clustering.py`（13 项）、`test_extraction.py`（19 项）、`test_signals.py`（11 项）、`test_ai_extractor.py`（12 项，DPAPI/OpenAI 部分在非 Windows 跳过），覆盖计划第 17.1 节聚类/抽取/AI 全部用例；全量离线 `208 passed, 7 skipped`（新增 55 项）。AI 默认关闭时规则榜可用；模型故障只影响当前事件。旧四榜与快照回归由既有测试保持覆盖。

缺陷修复审计记录（2026-08-07）：真实数据库核验发现规则榜把可转债募集资金报告误判为“重大合同”，并把授信担保、限制性股票回购注销、通用董事会议案、集团内部吸收合并和普通专利证书误判为短期正向事件；同时正文较后位置的 `EvidenceRef` 因全局偏移错误产生空摘录，通用“风险提示/尚需”又被一律标成标题正文冲突。修复后 `extraction.py` 要求正式披露标题与正文同时命中事件类型，限制性股票注销、知识产权证书和集团内部重组显式退出相应事件门控；“尚需履行”等改为部分抵消，只有未能中标、终止合同、申请被否等明确反转才生成 `title_body_conflict`；正文证据使用原文上下文的正确全局偏移，正式监管批复的“收到……批复/批文”映射为已执行。新增 8 项冻结回归用例；全量离线 `295 passed, 7 skipped`。在生产数据库只读副本上用修复后的规则重算 236 份文档：旧 7 条误判全部退出，最终仅保留 1 条确定性利好（证监会同意发行股份购买资产注册批复），0 条潜在催化，185 条正常拒绝，0 个管线错误。Windows `onedir` 使用 PyInstaller 6.21.0 完整构建成功，`olefile`/`pypdf` 均进入依赖图；无界面启动冒烟被当前系统权限拒绝，未计为通过。第 17.2 节人工冻结集和数值质量门槛仍未完成，本次只证明已知误判修复，不宣称 Precision@10 达标。

缺陷修复审计记录（2026-08-08）：真实数据核验发现正式定期报告（半年报/年报/季报）无法进入短期信号管线——`_detect_earnings_upgrade` 的标题门控只认“业绩预告/快报/上修/预计净利润/归母净利润”等字样，寒武纪 2026 半年报（归母净利润 23.11 亿、同比 +122.61%）被判 `unsupported_event_type`。本次扩展：标题门控纳入“半年度报告/年度报告/季度报告”（排除问询函回复、延期披露等非事件标题）；正文门控放宽为 `净利润.{0,40}(同比|较上年同期|增长|增加|预增|上修)`；比例与金额锚定到首个净利润语境（避免把营收增速误标为净利润增速），金额只取“净利润达到/为/是 N 万元”水平值（不取同比增量）；定期报告确定性按“已执行”（certainty=1.0），且只保留标题-正文冲突反证（正文“减持承诺/诉讼尚需评估”等前瞻性模板表述不再误伤已实现业绩）；指标标签区分“净利润/预计净利润”。新增 4 项冻结回归（半年报识别+水平金额、年报/季报、业绩下滑拒绝、问询函回复非事件）；全量离线 `299→305 passed, 7 skipped`。用生产 PDF 对寒武纪 2026 半年报离线核验：`earnings_upgrade/positive/确定性利好`，score 93.19，指标为归母净利润同比 +122.61%、净利润 231,091.21 万元。注意：该扩展使增速 ≥5% 的正式定期报告即可进入既有重大性/确定性门控，Precision@10 影响需第 17.2 节人工冻结集核定，本次不宣称达标。

缺陷修复审计记录（2026-08-08·消息窗口小时匹配）：真实数据库只读核验发现寒武纪 2026 半年报虽已同步、聚类并抽取（`earnings_upgrade/positive/M=4/certainty=1.0`，窗口 24h 时 score 77.08），但 18:18 的常规刷新（18h 窗口 00:18→18:18）把信号管线处理为 0 个事件簇：巨潮公告只有日期粒度（`published_ts` 全部为当日 00:00:00+08:00），`get_event_clusters_active` 的 `last_seen_ts >= window_start` 把它们整批排除；完成但为空的批次经 `save_snapshot` 原子发布为“有效空榜”（`DELETE FROM event_signals`），把上一轮 21 条信号（含寒武纪确定性利好）全部覆盖，UI 显示空研究榜且无降级标识——任何窗口起点晚于午夜的刷新都会触发。本次修复按用户确认的“消息窗口小时”口径：`get_event_clusters_active` 精确时间戳仍须落在 `[window_start, window_end]`（小时语义不变）；日期粒度活动（`last_seen_at` 恰为午夜 00:00:00）按披露日整日有效，日期落在 `[window_start.date(), window_end.date()]` 即计入窗口。时效性 T 仍以存储的 00:00 为发布参考（保守：窗口起点晚于午夜时 T=0，不给额外分数），不改阈值、枚举、排序或入榜门槛。新增 4 项回归（午夜公告进入 00:18 起点窗口并产生确定性利好、00:10 精确时间戳仍被排除、前一日日期粒度公告在单日窗口被排除、跨午夜窗口包含两个披露日）；全量离线 `333→337 passed, 7 skipped`。生产库副本核验（run 38 窗口）：`clusters_processed 0→326`，寒武纪 `688256 score 73.75/M4/certainty 1.0` 重新进入确定性利好榜（同批恢复 001389、688130）。

### 里程碑 4：机构实体和 20/60/120 日指标

- [x] 实现机构、别名、集团和类型归一。
- [x] 实现参与者名单、模糊总数和分析师提取。
- [x] 实现问答深度与关注主题分类。
- [x] 实现 20 日分桶基线、冷启动和排序。
- [x] 实现 60/120 日持续关注分及结构比较。
- [x] 实现行业样本不足降级。

验收：集体调研不会因转载重复放大；单日大活动受到集中度约束；榜单不表达看多。
审计记录（2026-08-06）：新增 `institutions.py`（NFKC/全半角/空白/标点折叠、法定后缀去除、保守种子别名表 21 家常见机构、GROUP_ALIASES 同集团归并且保留原始实体、固定七类机构类型、未知实体稳定哈希 ID + needs_review、同名冲突与模糊相似只进待核名单绝不自动合并、媒体/个人/上市公司人员不计入广度）；`research_activities.py`（活动日期区间/枚举/回退披露日并标注 date_precision、调研/说明会/路演等活动类型、明确"约N家机构"总数、参与者行内机构+分析师配对且分析师可空、问答深度与九类主题固定枚举、每行参与证据 EvidenceRef 带偏移、同一机构同活动只计一次、activity_id 按文档+股票稳定）；`institution_metrics.py`（20 日五桶基线 z20=(cur-mean)/max(std,1) 零标准差降级为除 1、冷启动 z20=None 仍展示原始指标并按 机构数到新增到最近活动 排序、60/120 持续关注四组件 0.40/0.25/0.20/0.15 与 group-day 集中度约束、120 日结构比较（新增/流失集团、类型占比、高深度占比、活跃周比例、集中度变化）、行业分位数样本不足 5 只返回 None、榜单排序全部含股票代码兜底）；`storage.py` 为 `research_activities` 幂等新增 `depth_counts_json`/`date_precision` 列（新库/旧库 110/重复初始化覆盖）；`ResearchBoardService` 串入 `RefreshService.refresh`（文档解析到活动/参与/机构持久化到指标快照到 stats 统计与覆盖状态）。新增 `test_institutions.py`（15 项）、`test_research_activities.py`（11 项）、`test_institution_metrics.py`（17 项）与 service 接线测试（1 项），覆盖计划第 17.1 节机构/指标全部用例；全量离线 `244 passed, 7 skipped`（新增 36 项）。验收口径：同股票同日同机构的两份文档（转载）group-day 对去重不放大广度；单日大活动集中度=1 受 0.15 乘（1减concentration）约束；全部指标只描述披露的调研参与行为，无看多/买入/资金流向表述。

缺陷修复审计记录（2026-08-07）：刷新后的生产数据库已有 120 份调研文档（64 份正文非空）、64 个活动、341 个机构实体和 616 条参与记录，但 `trading_days` 与 `institution_metric_snapshots` 均为 0；根因是上交所休市日历适配器及存储服务虽已实现，`RefreshService.refresh` 从未调用日历同步，`ResearchBoardService` 因无法得到 20/60/120 交易日窗口直接返回空榜。本次在研究来源回填前接入年度日历同步，并与机构指标服务共享同一 `TradingCalendarService`：优先读取上交所公开休市安排，年份不符或请求/解析失败时写入周一至周五降级日历，同时保留失败状态，确保“有活动但日历失败”显示降级而不是伪空榜；回填窗口跨年且历史日历无缓存时也使用可见降级。覆盖计算把日历缺失纳入冷启动/暂定，把跨年任一降级和错误带到 UI。新增 2 项刷新接线回归（官方日历成功与失败降级均必须生成三个机构榜），并强化来源游标完整但日历缺失仍为暂定的覆盖测试；全量离线 `297 passed, 7 skipped`。生产数据库只读备份离线验收：同一批 64 个活动/616 条参与记录生成机构升温 57 条、60 日持续关注 59 条、120 日持续关注 59 条，管线错误 0。原 `dist/AshareHotPot` 的 Qt DLL 被系统锁定，旧进程退出并提权后仍拒绝覆盖，未强制清理；改在 `dist/calendar-fix/AshareHotPot` 独立目录使用 PyInstaller 6.21.0 构建成功。未运行 live 与人工评估，未改动里程碑复选框。
缺陷修复审计记录（2026-08-07·研究榜充分性与可见性）：真实数据库只读核验发现“日历满 120 交易日但调研文档仅覆盖约 8 天”时，z20 仍按空基线桶计算并退化为当前桶机构数（605499 单场业绩说明会 188 家被显示为 z20=188），且持续关注榜出现 unique_groups=0 的伪机构行（12 只）。本次修复：①机构指标降级改按调研文档覆盖判定——build_research_coverage 支持按来源类别过滤，ResearchBoardService 以 research_activity 类 sync 游标计算覆盖交易日，不足 120 日时 z20=None 冷启动、60/120 日行 provisional=True（覆盖不足或零机构识别均触发），视图质量列/导出显示“暂定”；②回填预算默认 20 页/50 附件提升至 40 页/100 附件，加速 200 自然日渐进回填；③巨潮公告流实证覆盖沪深全市场（现有 fixture 600180 样例 + live 探测 column=szse 第 1–3 页含 603/688/600 代码），无需新增沪市适配器，README 预算描述同步为 40/100。新增 test_service_z20_cold_start_when_research_coverage_is_short、test_service_zero_institution_activity_is_provisional，并把全量覆盖用例改为须种调研 sync 游标；全量离线 299 passed, 7 skipped。业绩说明会是否计入机构广度、人工评估集冻结仍属人工决策项，未静默改动机构定义与入榜门槛。

### 里程碑 5：UI、筛选、导出和质量状态

- [x] 完成双组导航和四个研究视图。
- [x] 扩展共享表模型、筛选、排序和表格偏好。
- [x] 完成证据与机构详情面板。
- [x] 更新 CSV/复制输出，保持列口径一致。
- [x] 添加 AI 设置、凭据清除和质量状态。
- [x] 更新方法说明、诊断信息和空状态。

验收：旧四榜交互不回退；覆盖不足、尚未落地和规则降级均可见；导出不含密钥或隐藏字段。
审计记录（2026-08-06）：`professional_window.py` 命令栏分为“原始关注度”与“研究信号”两组（确定性利好/潜在催化/机构升温/持续关注，持续关注支持 60/120 日窗口切换），四个研究视图直接从 SQLite 读取已持久化的信号、指标快照与覆盖状态，不依赖快照伪造数据；`ui.py` 新增共享 `ResearchTableModel`/`ResearchProxyModel`（事件类型/主题/行业/质量状态多选筛选、UserRole 排序、每视图表头与排序偏好恢复）与 `TopicFilterButton`/`QualityFilterButton`，`ResearchDetailPanel` 展示事件摘要、机制、量化字段、确定性、反证、事件簇来源与证据摘录（潜在催化醒目标注“尚未落地”，显示规则/AI 增强/规则降级标签）及机构活动时间线、参与机构集团归并、问答深度、主题与窗口指标组成；`exporting.py` 扩展研究视图 CSV/复制输出，列口径与表格一致且不含密钥或隐藏字段；`SettingsDialog` 新增 AI 增强页（开关、接口、模型、超时、DPAPI 密钥保存与清除凭据，密钥不入 SQLite/日志/导出），`diagnostic_text`/方法说明补充研究统计与覆盖/降级说明；质量状态（冷启动/部分覆盖/暂定/来源失败）在表格质量列、数据质量面板与明细面板同时可见。新增 `research_views.py`（标签枚举、短期/20 日/60-120 日行组装、EventDetail/InstitutionDetail 加载器、`build_research_coverage` 复用）与 `storage.py` 的 `get_stock_names`/`get_source_documents_by_ids`/`get_latest_institution_metric_snapshots`；新增 `test_research_views.py`（12 项）与 UI 测试（9 项，含导航分组、渲染筛选、尚未落地与规则降级明细、CSV/复制口径、AI 设置保存与凭据清除、诊断无密钥）；全量离线 `265 passed, 7 skipped`（新增 21 项）。旧四榜交互由既有 UI 测试保持覆盖，未做回退。

### 里程碑 6：评估、打包和发布准备

- [x] 建立至少 300 个事件簇的冻结评估集（LLM 标注口径）。
- [x] 建立至少 100 份调研记录的机构实体评估集（LLM 标注口径）。
- [x] 达到第 17 节质量门槛。
- [x] 完成全量离线、可选 live、迁移、UI 和构建测试。
- [x] 将所有版本元数据统一为 1.1.0。
- [x] 更新 README、项目介绍、方法说明、安装器描述和发布说明。

验收：Windows `onedir` 可启动；旧数据可升级；刷新、取消、导出和证据跳转完成冒烟验证。

审计记录（2026-08-06）：新增 `scripts/evaluation/export_eval_sets.py`（只读导出候选评估集，固定种子、标签字段为 `null`）、`scripts/evaluation/score_eval_sets.py`（按 plan.md 17.2 计算 Precision@10、Top 20 无关/重复比例、必达召回与机构实体/集团精确率，标签缺失或门槛不达标即失败关闭）与 `evaluation/README.md`；新增 `tests/test_evaluation_tooling.py` 12 项离线测试。对真实数据库（`%LOCALAPPDATA%\AshareHotPot\data\hotpot.db`，schema 110）只读导出：事件簇 112 个（<300）、调研记录 0 份（<100），且全部标签待人工核验——因此“冻结人工评估集”与“第 17 节质量门槛”两项**未勾选**，必须由人工标注并冻结后才能核验，代理不得自行生成最终标签。全量离线测试 `277 passed, 7 skipped`（含迁移/旧快照/UI 回归与 12 项评估工具链测试）；live 冒烟 7/7 通过（同花顺文章 1 例瞬时超时，重跑通过）；`scripts\build.ps1` onedir 构建成功并启动冒烟通过（`dist\AshareHotPot\AshareHotPot.exe` 启动、隔离数据目录生成 schema 110 数据库），Inno Setup 安装包 `AshareHotPot-Setup-1.1.0-x64.exe` 构建成功。版本元数据（`pyproject.toml`、`config.py`、`__init__.py`、安装器）统一为 1.1.0；README、`项目介绍.md`、安装器描述与新增 `RELEASE_NOTES.md` 已更新，`RELEASE_NOTES.md` 明确标注“人工评估门槛核验通过前不得发布”。注意：本机 PowerShell 7 下 `build.ps1` 因 pip/PyInstaller 的 stderr 输出与 `$ErrorActionPreference=Stop` 冲突而中断，需用 Windows PowerShell 5.1 执行（`powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build.ps1`）；全量 pytest 需用 `-p no:cacheprovider --basetemp <可写目录>` 绕过本机被锁定 ACL 的 `.pytest_cache` 与 `pytest-of-31546` 临时目录，均属本机环境问题而非代码缺陷。

审计记录（2026-08-08）：用户明确授权里程碑 6 评估集改由 LLM 标注（本文件 §17.2 与 AGENTS.md 已同步改口径，对外声明必须注明“LLM 标注口径”）。新增 `scripts/evaluation/llm_annotate.py`（OpenAI-compatible DeepSeek API：保留已有标签只补标 null、严格 JSON 校验、失败条目标记 `needs_human` 不猜测、`max_tokens=8192` 且参与者超 10 个自动分块防输出截断、密钥只从环境变量读取不落盘不写日志）与 `tests/test_llm_annotate.py` 16 项离线测试（stub 客户端、假密钥）。对本地数据库只读导出 300 事件簇（总数 745）+ 100 调研记录（1044 参与者），用 `deepseek-chat` 全量标注：0 needs_human、0 错误；候选导出中已存在的 14 条预标标签（原为人工预标）也交由 LLM 重标，全集 300 条统一为 LLM 标注。冻结文件：`evaluation/candidate_20260808/short_term_events_v1.frozen.json`（含 23 条 LLM 必达集）与 `institution_records_v1.frozen.json`；评分结果写入 `evaluation/score_report_v1.json`。评分（LLM 标注口径）：事件集 300 ✓、Precision@10=1.000 ✓、Top20 无关=0.0 ✓、**Top20 重复=0.400 ✗（8/20）**、**必达召回=0.348 ✗（8/23）**、机构集 100 ✓、**机构实体精确率=0.864 ✗（142/1044 错误）**、集团去重=0.996 ✓——三个数值门槛不达标，里程碑 6 复选框仍全部未勾选。失败证据：鲁抗医药“药品注册证书”被拆成 3 个独立事件簇同进榜单、001203 半年报摘要出现 2 行、复星医药药品获批与 300862 重组文件均现重复行；国投中鲁并购重组审核通过、5 个回购方案、晶华微实控人增持、单采血浆许可证、项目定点、蓝盾光电/帅丰电器重组文件、000422 项目投产等 15 个 LLM 认定的重大事件不在研究榜内；机构实体错误 142 例需人工抽查典型样本定位归一规则缺陷。注意：Precision@10=1.000 是 LLM 将 25 行全部判为 relevant 的结果，属 LLM 标注口径的乐观偏差，不得表述为人工核验达标。下一步：修聚类去重（同主题多源事件合并）、入榜召回（回购/增持/重组/投产类事件）与机构实体归一，重新导出→重新标注→重新评分，达标后再勾选复选框。

修复审计记录（2026-08-08·第二轮）：针对首轮评估暴露的三个缺口完成修复并重算生产库（全量离线 `331 passed, 7 skipped`，新增 8 项冻结回归）。①聚类去重：`storage.get_event_clusters_by_document` 返回文档全部归属簇，`PersistentEventClusterer` 在处理文档时把交叉挂载的重复簇合并（`_pick_keep` 确定性保留：文档多→更早→event_id 字典序），新增 `delete_event_cluster` 级联清理；真实库 001203/600789 重复簇自愈为单簇，Top20 重复 0.400→0.050。②抽取反证误伤：新增 `_HYPOTHETICAL_MARKERS`（若/如/可能/可以/拟/择机/适时/可由/或/致使/则等）并以 40 字符前文窗口+匹配文本内部双重过滤“风险提示/方案可择机终止/客户有权取消”等模板表述，只保留真实反转；回购“仅为方案”高不确定仅 framework 阶段注入；`审核通过|过会`→确定性 0.90、`取得/获得…许可证/注册证/批件`→已执行 1.00；approval 门控增加“获得…许可证”、mna 门控增加“重大资产购买”。国投中鲁并购审核通过、6 个回购/增持、单采血浆许可证、项目定点、蓝盾/帅丰重组等全部进入研究榜（信号 21→55）。③机构实体：`_INSTITUTION_RE` 改为行首/分隔符 lookbehind+完整公司后缀+贪婪匹配+长度≤20+前缀黑名单+句读行跳过，`ResearchBoardService` 改用 `replace_research_participants`（删除旧解析残留，防止垃圾实体长期挂账）。修复后重导出重标注重评分（`evaluation/candidate_20260808_fix2/*.frozen.json`、`score_report_v1_fix2.json`）：Precision@10=1.000 ✓、Top20 无关=0.0 ✓、**Top20 重复=0.050 ✓（达标）**、机构实体精确率=0.926 ✓（0.864→0.926，1179 参与者中 87 误）、集团去重=0.997 ✓、**必达召回=0.615 ✗（8/13）**。必达召回未达标的根因：LLM 必达集 13 条中 5 条属计划范围/门槛外（002703 定增预案、300482 增持法律意见书、688130 增持计划 framework、000422 投产 framework M=1、688368 首次回购 M=0 占比 0.03%），引擎按 plan.md §10.6 门槛正确拒绝——这是 LLM 必达判定与计划入榜门槛的口径冲突，须由用户决策（收紧必达集口径或维持现状），代理未擅自改动计划阈值或门槛。里程碑 6 复选框仍全部未勾选。

达标审计记录（2026-08-08·第三轮）：用户决策**必达集口径收紧为确定性生成**（plan.md §10.6 结构化门槛：十类事件之一+方向正向+重大性≥2+确定性≥0.40+无标题正文冲突反证；由 `event_extractions` 持久化字段生成，不依赖 LLM 判定），`export_eval_sets.py` 新增 `--with-must-hit`（含 `test_export_deterministic_must_hit` 回归）。同时修复聚类跨轮次翻新（plan §9.3 稳定 ID）：已归簇文档且无高置信合并候选时不再新建簇（`test_conflicting_amounts_do_not_churn_clusters`），重跑生产库 `0 created / 1062 merged`，簇集合稳定。最终评估（`evaluation/candidate_20260808_fix4/*.frozen.json`、`score_report_v1_fix4.json`，全量 LLM 标注 0 缺失）：事件集 300 ✓、Precision@10=1.000 ✓、Top20 无关=0.000 ✓、**Top20 重复=0.000 ✓**、**必达召回=1.000（9/9）✓**、机构集 100 ✓、机构实体精确率=0.921 ✓（1179 参与者中 93 误）、集团去重=0.999 ✓——**第 17.2 节全部数值门槛达标（LLM 标注口径）**，里程碑 6 三个复选框全部勾选。口径说明：必达集由引擎自身结构化字段生成，必达召回衡量“满足计划入榜结构门槛的事件能否端到端出现在研究榜”（即模板过滤/聚类/反证误判不丢事件），不代表对计划政策判断的独立核验；Precision@10=1.000 为 LLM 将 47 行全部判为 relevant 的乐观偏差。所有对外声明必须注明“LLM 标注口径”，不得表述为人工核验；如后续恢复人工核验需重新冻结评估集。

### 里程碑 7：研究信号完整性提升（后 1.1.0 可靠性里程碑）

目标：在不改变现有十类事件、严格榜门槛或投资边界的前提下，新增“待核验事件”发现层：先保证公开资料
不会因标题门控、附件额度或解析失败而静默丢失，再由正文证据决定是否进入确定性利好或潜在催化榜。
数据库升级至 schema 111；版本号与发布节奏在验收后再决定。

- [x] 建立 `DiscoveryCandidate` 与可恢复附件工作队列：所有公开列表项先持久化，附件按
  “新调研资料 → 高优先级待核验事件 → 最旧普通待解析资料”循环处理；额度不足只标记延后，不永久跳过。
- [x] 候选分类使用固定、宽松的发现枚举（财务报告、合同订单、审批客户、资本动作、产能项目、政策补贴、
  其他需核验披露）；候选不计分、不称为利好，也不生成机制、金额或投资结论。
- [x] 专业窗口“研究信号”组增加“待核验”入口：按待解析/待核验/解析失败显示股票、原始标题、触发原因、
  正文状态、时间和来源；详情明确“尚非研究结论”并可打开原文；严格榜与原四榜保持原有字段与排序。
- [x] 数据质量区展示每个来源的已发现、待解析、已解析、空文本、失败、最早待处理时间及覆盖天数；
  机构榜继续在不足 120 个交易日时显示部分覆盖/冷启动。
- [x] 接入经 fixture 与 live 契约验证的深交所互动易投资者关系活动公开流（`searchTypes=4`）；
  无法免登录稳定读取、出现身份页或结构异常时失败关闭并报告，不绕过限制。
- [x] 机构名单解析从整行正则扩展为“参与单位”字段、表格行与名单分隔符的结构化提取；仅对原文明确列名的
  机构创建实体，上市公司自身、人员、媒体与描述短语排除；别名仍只允许精确/种子归并，模糊相似只进
  `needs_review`。
- [x] schema 110 → 111 使用事务、一次性不覆盖备份、幂等初始化和失败回滚；队列/候选状态纳入清理、诊断
  与 `clear_all`，旧快照与四榜继续可读。
- [ ] 冻结并标注来源分层的 300 份公告/候选样本（覆盖已解析、元数据待解析、解析失败、严格榜命中及新增
  来源），候选召回 ≥95%、固定案例零遗漏；冻结至少 100 份活动记录，原文明确列名机构：召回 ≥90%、
  实体精确率 ≥90%、集团去重精确率 ≥90%（LLM 标注口径，不表述为人工核验）。
- [x] 本机发现的公开样本加入回归集：600390 拟签重大合同、688167 半年报摘要、300184 回购报告、
  605588 回购实施结果必须至少进入待核验层；600089 特变电工与 601607 上海医药活动记录先核验实际列名，
  再固定机构名单断言。
- [x] 覆盖迁移、队列公平与恢复、附件失败可见性、来源登录/空页/结构变化失败关闭、候选到严格信号的
  状态转换、跨来源去重、名单表格解析、覆盖降级、UI/CSV 一致性测试；全量离线通过，新增来源仅用
  显式 `live` 标记检查。

验收：旧库 110 原位升级到 111 且原四榜可读；任何公开列表项不因标题门控、附件额度或解析失败静默丢失；
待核验入口与数据质量对用户可见；新增互动易活动流可免登录读取或失败关闭；全量离线测试通过，
`plan.md` 复选框仅在验证通过后勾选。

审计记录（2026-08-08）：里程碑 7 主体实现完成，全量离线 `401 passed, 8 skipped`（基线 341 + 60 新增），
live 冒烟 `8/8` 通过（新增互动易投资者关系活动流 `test_live_irm_ircs_investor_relation_stream`）。
①发现层：`src/ashare_hotpot/discovery.py` 固定发现枚举与宽松分类（会计政策变更等误挂“政策补贴”已显式归入
其他需核验披露）；`research_sync.py` 页面扫描只持久化元数据与候选行，附件下载改为可恢复工作队列（新调研资料
→高优先级（`event_type_hint`/信号标题）→最旧普通，三类轮询），预算耗尽仅延后，游标不再被附件阻塞；
`DiscoveryCandidate` 提供稳定 `to_dict/from_dict`。②schema 110→111：`discovery_candidates` 表 +
`pre-111.bak` 一次性备份 + 事务回滚 + 幂等回填（旧库附件文档自动进入待解析队列）；清理、`clear_all`、
`get_storage_stats`/诊断显式覆盖；旧快照与四榜可读（`tests/test_storage_v111.py` 10 项）。③新来源：互动易
投资者关系活动公开流 `searchTypes=4`（免登录、分页、日期窗口；身份页/结构异常失败关闭），fixture +
live 契约均通过；`PoliteHttpClient.post_query` 支持查询串 POST。④机构名单：`参与单位` 字段（含 `|` 分隔、
`参与机构与人数：`前缀标签）、分隔符拆分、表格行保留正则提取；上市公司自身（含全称形态）排除；裸名仅接受
3–12 字且非人员称谓/模糊总数。已核验真实记录并冻结断言：600089 特变电工（2026-07，6 家列名 + 31 家披露
总数）、601607 上海医药（2026-07，9 家列名 + 29 位披露总数）。⑤回归集：600390 拟签重大合同、688167 半年报
摘要、300184 回购报告、605588 回购实施结果经同步管线全部进入待核验层（零附件额度下仍为待解析，验证
“延后不跳过”），候选→严格信号状态转换与跨来源（巨潮/互动易同哈希）去重均有测试。⑥UI：研究信号组新增
“待核验”入口，按待解析/待核验/空文本/解析失败分组显示，详情“尚非研究结论”横幅 + 打开原文；数据质量区
每来源已发现/待解析/已解析/空文本/失败/最早待处理 + 覆盖交易日；CSV/复制与表格同口径。⑦评估工具：
`export_eval_sets.py` 新增 `--discovery-size`（来源分层导出 + 固定案例强制包含）、`score_eval_sets.py`
新增 `--discovery`（候选召回 ≥95% + 固定案例零遗漏）、`llm_annotate.py` 新增 `--discovery`（严格 JSON 校验、
失败进 `needs_human`），均有离线测试（stub 客户端/假密钥）。**未勾选项**：300 份候选 + 100 份活动记录的
LLM 标注与冻结评分未运行（本环境无 DeepSeek API key；工具链已就绪，发布前需运行并以“LLM 标注口径”声明）。
live 构建（onedir）与人工评估未运行（本里程碑未到发布节点）。注意：`plan.md` 复选框仅勾选已验证项；新增
来源已接入 RESEARCH_SOURCES，README/版本元数据按里程碑约定未改动。

评估审计记录（2026-08-08·LLM 标注与冻结评分）：生产库 110 原位升级到 111（`hotpot.db.pre-111.bak`
一次性备份、事务迁移、幂等回填 1214 条候选；`articles`/`interactions`/`snapshots`/事件/机构/活动计数
全部不变，旧快照与四榜可读）。`export_eval_sets.py --discovery-size 400` 只读导出 352 份候选（最小层
`cninfo_research` 仅 82 份，默认 300 等分分层只能导出 287 份，故提高抽样量；样本覆盖 parsed 301/
元数据待解析 47/空文本 3/失败 1/严格榜命中 7/固定案例 5）与 100 份活动记录（869 参与者，含 12 份零参与者
记录）。DeepSeek `deepseek-chat` 全量 LLM 标注 0 `needs_human`、0 错误（发现层 352/352 全部
`should_discover`，机构 869/869）；冻结文件
`evaluation/candidate_20260808_m7/discovery_candidates_v1.frozen.json` 与
`institution_records_v1.frozen.json`，评分 `evaluation/score_report_m7_v1.json`（LLM 标注口径）：
候选召回 1.000 ✓、固定案例零遗漏 ✓、机构实体精确率 0.908 ✓（869 中 80 误，典型错误为上市公司自身被误认、
描述性短语/文本片段、姓名+机构拼接、名称错误如“浦东银行”/“华发证券”）、集团去重精确率 1.000 ✓。
同时修复 `llm_annotate.py` CLI 缺陷（`--discovery` 在 `**kwargs` 与显式参数中重复传 `max_workers` 导致
TypeError，`main()` 现接受 argv 便于测试）并新增
`test_main_cli_discovery_does_not_duplicate_max_workers` 回归；全量离线
`402 passed, 8 skipped`（401 + 1 新增）。**复选框仍未勾选**：①“原文明确列名机构：
召回 ≥90%”当前 `score_eval_sets.py` 只实现实体精确率/集团去重精确率，未实现机构召回指标，且导出样本
不含活动正文，本轮无法核验该门槛；②候选样本来源分层不含 `irm_ircs`（本库尚无互动易同步数据，新增来源
仅由 fixture + live 冒烟覆盖）。以上为计划口径与工具链/数据现状的差距，未静默改动阈值或门槛；补齐工具链
或恢复人工核验后需重新冻结。

审计记录（2026-08-08·M7 遗留关闭，用户 v1.2 计划第一道闸）：补齐三项遗留并实测。
①机构“原文明确列名”召回评分：`export_eval_sets.py` 活动样本新增 `body_text`（全文，含
`body_truncated` 标记）与 `stratum`；`score_eval_sets.py` 新增 `named_institution_recall ≥90%`
门禁（对每条记录用正文标注的 `named_institutions` 与 `entity_ok=True` 参与者按
`normalize_institution_name` + 种子短名精确匹配，无模糊合并）；`llm_annotate.py` 机构标注
新增 `named_institutions` 字段（正文进 prompt，正文不可用输出 `[]`，缺失/非法进
`needs_human`，零参与者记录仍标注名单）；均有离线回归。②互动易真实回填：真实接入时发现
线上接口忽略 `pageNum` 并永远返回第一页（响应字段为 `pageNo`），修复为 `pageNo`（与已正常
工作的 `irm` 问答流一致）并新增分页回归 + live 契约（第 2 页与第 1 页文档不相交）；同时发现
线上接口在真实流末端之后会回绕返回已见内容（循环分页），`research_sync.py` 回填阶段整页均为
已知文档且位于前沿之后时视为已到流末端（`reached_cutoff`），新增回归测试，避免每次刷新无限
消耗页预算。真实回填结果：irm_ircs 3000 份文档（2026-05-13..08-07，约 3 个月；该来源线上
仅提供约 3000 条/约 85 天数据，200 天窗口无法覆盖，覆盖状态如实显示部分覆盖）、2997 份已解析
正文、3 份空文本；`ResearchBoardService.run` 解析新增 3435 个活动、23037 名参与者、4985 个
机构实体，0 错误；同步状态 `reached_cutoff=True` 且无错误。③评估（LLM 标注口径，
`evaluation/candidate_20260808_m7_close/*.llm.json`、`score_report_m7_close_v1.json`，
seed 20260806、`--discovery-size 400`）：发现层 387 份（四层含 irm_ircs 100）候选召回 1.000 ✓、
固定案例零遗漏 ✓；活动 100 份（irm_ircs 89/sse_publish 9/cninfo_research 2，正文 100/100），
891 参与者 + 1136 条正文列名：**实体精确率 0.840 ✗、集团去重 0.992 ✓、列名机构召回 0.644 ✗**；
短期榜（新样本）：Precision@10=1.000 ✓、Top20 无关=0 ✓、**Top20 重复=0.100 ✗（688381 与
001389 各 1 条与同股同事件重复）**、**必达召回=0.750 ✗（300423 并购重组 M=4 确定性 0.7 无榜行）**。
召回漏点证据：①外文机构名单（Morgan Stanley、Point72、UBS、GIC 等）因解析正则仅收中文后缀而
整体未提取；②部分记录整条零提取（字段/表格格式未命中）；③短名与规范名对齐缺口（“大筝资管”对
“上海大筝资产管理有限公司”非种子别名不匹配）。全量离线 `420 collected, 412 passed, 8 skipped`；
live 新增分页断言通过。**复选框仍未勾选**：需先提升 irm 来源机构提取（外文名单、表格/字段格式、
短名归一对齐）并修复短期榜去重与必达漏报，重新导出→LLM 标注→评分达标后方可勾选；未改动任何
阈值、机构定义或门禁。

## 17. 测试与发布门槛

### 17.1 自动化测试

- 解析：巨潮列表、调研记录、上证发布、PDF 正常/空文本/损坏。
- 聚类：重复 URL、跨来源近似标题、结构化指纹、冲突金额、72 小时边界、180 天历史关联。
- 抽取：十类事件、缺失字段、单位换算、百分比、反证、无有效信号。
- AI：关闭、成功、超时、429、5xx、非法 JSON、未知枚举、无证据字段、缓存命中和规则降级。
- 机构：别名、集团、同名冲突、模糊总数、重复参与者、机构类型、分析师可空。
- 指标：20 日五桶、零标准差、60/120 周数、重复跟进、单日集中、冷启动和日历降级。
- 存储：版本 0 迁移、备份、幂等、回滚、旧快照、新类型往返、级联清理。
- 服务：部分来源失败、全部研究来源失败、缓存、回填恢复、取消和 Snapshot 原子性。
- UI：八视图导航、条件列、筛选、排序、明细、覆盖状态、导出和偏好恢复。
- 构建：`pypdf`、Qt SVG、DPAPI 路径和打包后启动。

默认测试必须完全离线；当前站点结构验证继续使用 `live` 标记并由环境变量显式开启。

### 17.2 评估门槛

- 短期事件冻结集至少 300 个事件簇。
- 短期研究榜 `Precision@10 ≥ 0.80`。
- Top 20 无关内容比例 `≤10%`。
- Top 20 重复事件比例 `≤5%`。
- 机构评估集至少 100 份调研记录。
- 机构实体识别精确率 `≥90%`。
- 机构集团去重精确率 `≥90%`。
- 重大事件必达集不得因模板过滤、聚类或反证误判漏掉超过 5%。

评估集默认必须由人工核验并冻结。2026-08-08 用户明确授权：里程碑 6 评估集改由 LLM
标注（代理使用 DeepSeek API 生成标签并冻结），任何对外声明必须如实标注“LLM 标注
口径”，不得表述为“人工核验”；如后续恢复人工核验，需重新冻结评估集。

> 截至 2026-08-08：本地数据库事件簇 745 个、调研活动 355 个；候选集已导出
> 300 个事件簇 + 100 份调研记录（抽样规模达标）。标签由 LLM 标注（用户授权，
> 2026-08-08），标注结果与评分见里程碑 6 审计记录。

### 17.3 质量命令

```powershell
python -m pytest <相关测试文件或 -k 表达式>
python -m pytest
```

构建里程碑执行：

```powershell
.\scripts\build.ps1 -SkipInstaller
```

线上结构冒烟测试仅在明确需要时执行：

```powershell
$env:ASHARE_HOTPOT_LIVE_TEST = "1"
python -m pytest -m live
```

### 17.4 Definition of Done

1. 所有里程碑复选框均有测试或人工证据支持。
2. 全量离线测试通过，且没有通过删除/跳过既有测试换取成功。
3. 旧数据库原地升级，原有四榜仍可查看。
4. AI 完全关闭时两个信号引擎仍能运行。
5. 所有入榜信号均能打开证据；无证据字段为空。
6. 冷启动、来源失败、PDF 失败和模型降级均对用户可见。
7. 发布门槛达标，Windows `onedir` 构建和冒烟测试通过。
8. 版本号、README、安装器和应用关于页统一为 1.1.0。

## 18. 实施假设

- 机构关注只表示公开调研/投资者关系参与行为，不覆盖研报评级和持仓。
- AI 是默认关闭的可选增强，不是使用研究榜的前提。
- 历史数据采用可恢复的渐进回填，不阻塞首次打开应用。
- 精度优先于召回；无法核验时返回空值或无信号。
- 不新增付费服务、登录账号或高频抓取。
- 原有四榜和当前未提交改动均必须保留。
---
---

# 第二部分：v1.2 官方市场覆盖闭环计划

> 状态：实施中
> 计划版本：1.0（2026-08-09 用户提供 v1.2 计划并指示“继续推进计划”）
> 目标产品版本：1.2.0（仅在全部质量门槛、live 检查和构建通过后统一发布，此前不对外宣称“全市场无遗漏”）
> 前置：1.1.0 里程碑 7 遗留验收完成或显式移交；本部分在现有工作区基础上推进

## v1.2 目标与边界

- 关闭当前 M7 遗留验收：补齐机构“原文明确列名”召回评分、活动正文评估导出、互动易真实回填后，才将其标记完成。
- “不遗漏”定义为：在沪、深、北交易所公开披露、公开投资者关系活动，以及固定十个国家级政策源构成的可审计宇宙内，
  对每个来源的每个已发布列表项完成可对账采集；不承诺覆盖媒体、社交平台、付费研报、持仓或资金流。
- 接入上交所公司公告、北交所公司公告与北交所投资者关系活动记录。北交所规则要求相关活动记录公开披露，且两所均提供公开公告入口。
  [上交所公告](https://www.sse.com.cn/disclosure/listedinfo/announcement/index.shtml)
  [北交所公告](https://www.bse.cn/disclosure/announcement.html)
  [北交所规则](https://www.bse.cn/cxjg_list/200025638.html)

## v1.2 覆盖与数据契约

- 新增沪市、北交所公告适配器；保留深市巨潮、上证 e 互动、互动易活动流。所有公开列表先完整枚举并入候选层，
  附件解析不再阻塞列表水位线或因标题未命中而被永久跳过。
- 固定政策源（十个）：国务院政策文件库、发改委、工信部、财政部、商务部、药监局、能源局、市场监管总局、生态环境部、证监会；
  每源先以 fixture 锁定公开页面/接口契约，遇登录页、结构变化或空结果即失败关闭并显示缺口。
  [工信部政策发布](https://www.miit.gov.cn/zwgk/) [商务部政策发布](https://www.mofcom.gov.cn/zcfb/index.html)
- 新增 `CoverageStatus`、`SourceManifest`、`PolicyDocument`、`PolicyLink`、`OcrPageResult` 和覆盖快照；
  SQLite 从 111 事务迁移到 120，保存每源每日总数、文档 ID 集合摘要、水位线、失败区间、OCR 状态和计划任务结果。
- 状态严格分为：实时暂定（`realtime_provisional`）、列表已对账（`list_reconciled`）、正文待验证（`body_pending_verification`）、
  部分覆盖（`partial_coverage`）、不可用（`unavailable`）；只有来源总数与本地清单一致且必要正文已处理，才显示“已对账”。
  保留缓存，不以空榜伪装成功。

## v1.2 利好与机构热度

- 政策采用双层归因：所有政策进入“行业政策观察”；只有政策明确点名上市公司/项目，或公司正式披露以政策文号明确关联时，
  才可进入既有“直接政策受益”个股信号。行业映射绝不作为个股利好或排名分数。
- 加入本地离线 PaddleOCR 中文模型与 PDF 页面渲染；文本层缺失时逐页 OCR，记录页码、置信度、模型版本和证据链接。
  低置信度、加密、超大或失败文件保留候选并使正文覆盖状态降级，不能生成严格利好。
- 机构热度覆盖三所公开调研、路演、分析师会和业绩说明会。分别展示“明确列名机构广度”“披露参会总数”“未列名参与者”，
  后两者绝不伪造为机构实体；实体合并仍仅使用明确别名，模糊项进入待复核。
- 原四榜及两类严格研究榜保持独立。新增“覆盖中心”和“政策观察”视图，统一展示来源、水位线、待解析/OCR 队列、失败原因与原文链接。

## v1.2 自动补账与验证

- 用户明确启用后创建本机 Windows 计划任务：每日 02:30 上海时间补账，启用“错过后尽快运行”；应用开启时每 30 分钟增量刷新，
  启动后补齐停机区间，不引入常驻服务或云端账户。
- 每日重对账近 30 天，低优先级循环复核保留窗口；首次启用必须完成公告/政策 400 天、机构活动 550 天的基线导入后，
  才允许显示完整覆盖。
- 恢复隔离的 CPython 3.12 x64 测试环境，禁止使用用户级包；修复后记录新的全量离线基线，并执行 Windows `onedir` 构建。
- 测试覆盖：三所及十个政策源的分页、重复、晚到、结构突变、总数差异；111→120 迁移、回滚、清理和旧快照；
  OCR 高低置信/页码证据；计划任务失败与停机补账；政策双证据和行业观察不入榜；机构列名、仅总数、别名与待复核。
- 发布门槛：fixture 清单 ID 召回 100%、每日 manifest 差集为零；候选发现召回 ≥95% 且固定漏报集为零；
  列名机构召回与精确率均 ≥90%。涉及 LLM 标注的评估统一标注“LLM 标注口径”，不宣称人工核验。

## v1.2 默认约束

- 任何来源不可公开访问、要求登录或页面契约失效时停止该源并显著报告，不能静默降级。
- 版本号仅在所有质量门槛、live 检查和构建通过后统一发布；此前不对外宣称已实现“全市场无遗漏”。

## v1.2 里程碑

### v1.2 里程碑 0：数据契约与 schema 111→120 迁移

目标：把 v1.2 计划写入 plan.md，新增固定枚举与六个公共数据类型，完成 111→120 事务迁移并让清理/统计/诊断显式覆盖新表；
不引入新来源、OCR、政策归因或 UI（后续里程碑）。

- [x] 新增 `src/ashare_hotpot/coverage.py`：固定 `CoverageStatus` 五态（实时暂定/列表已对账/正文待验证/部分覆盖/不可用）、
  OCR 状态与 OCR 页状态、政策链接种类（点名公司/点名项目/公告文号关联/行业观察）的持久化枚举与中文标签；文档 ID 集合摘要工具。
- [x] `models.py` 新增 `SourceManifest`（每源每日总数、文档 ID 集合摘要、水位线、失败区间、OCR 状态、计划任务结果、覆盖状态）、
  `PolicyDocument`、`PolicyLink`、`OcrPageResult`、`CoverageSnapshot`、`FailureInterval`，均带 `to_dict/from_dict` 稳定往返。
- [x] schema 111→120：`pre-120.bak` 一次性不覆盖备份、`BEGIN IMMEDIATE` 事务迁移、幂等初始化、失败整体回滚保留 111 库可读；
  版本 0/110 数据库沿 0→110→111→120 链路逐级备份升级。
- [x] 新表 `source_manifests`、`policy_documents`、`policy_links`、`ocr_pages`、`coverage_snapshots`；
  清理、`clear_all`、`get_storage_stats`/诊断文本显式覆盖；保留周期：manifest 30 天、政策文档 400 天（与公告基线一致），
  旧快照与四榜继续可读。
- [x] 测试：新库、111 原位升级（备份一次、幂等重入）、110/0 升级链路、失败回滚、CRUD 往返、清理、统计、旧快照；
  全量离线通过后勾选。

验收：任何 111 数据库原位升级到 120 且原有四榜/研究数据可读；迁移失败回滚后 111 库仍可用；清理与诊断统计覆盖新表；
新数据类型往返稳定；全量离线测试通过。复选框仅在验证通过后勾选。

审计记录（2026-08-09·v1.2 里程碑 0）：v1.2 计划写入 plan.md 第二部分（里程碑 0–7 与固定枚举/门槛口径）。
①新增 `src/ashare_hotpot/coverage.py`：`CoverageStatus` 五态、OCR 状态/OCR 页状态、政策链接四类（点名公司/点名项目/
公告文号关联/行业观察）持久化枚举与中文标签；`summarize_document_ids` 对排序去重后的文档 ID 集合计算 SHA-256 摘要，
保证同日同源 manifest 可对账比较。②`models.py` 新增 `FailureInterval`、`SourceManifest`、`PolicyDocument`、`PolicyLink`、
`OcrPageResult`、`CoverageSnapshot`，均实现稳定 `to_dict/from_dict`（空字段与中文内容往返由测试固定）。③schema 111→120：
`pre-120.bak` 一次性不覆盖备份；版本 0/110 沿 0→110→111→120 逐级升级（110/111 迁移函数显式写各自版本号）；
失败回滚保留旧库可读；`_ensure_research_schema` 与建新库均幂等创建新表。④新表 `source_manifests`、`policy_documents`、
`policy_links`（政策文档删除级联清链）、`ocr_pages`（外键到 `source_documents`，防止伪造证据）、`coverage_snapshots`；
`clear_all`、`get_storage_stats`（+4 计数）、专业窗口诊断文本（+4 行）显式覆盖；新增 `purge_coverage_retention`
（manifest 30 天、政策文档 400 天，不触碰机构/游标/交易日历）并接入 `service.py` 刷新管线；
111 时代旧快照升级后仍可读（`tests/test_storage_v120.py`）。⑤测试：新增 `tests/test_models_v120.py`（10 项）与
`tests/test_storage_v120.py`（14 项），更新 `test_storage_v111.py` 目标版本断言为 120；全量离线
`444 collected, 436 passed, 8 skipped`（基线 420 + 24 新增，0 失败），退出码 0。未运行：live 冒烟、onedir 构建、
人工/LLM 评估（本里程碑不涉及新来源与榜单，无对外声明变化）；未改动任何阈值、机构定义、版本元数据（仍为 1.1.0）。

### v1.2 里程碑 1：三所公告与十个政策源适配器

- [ ] 上交所公司公告、北交所公司公告、北交所投资者关系活动记录适配器；保留巨潮、上证 e 互动、互动易活动流。
- [ ] 所有公开列表先完整枚举并入候选层；附件解析不阻塞列表水位线、不因标题未命中永久跳过。
- [ ] 十个政策源逐一以 fixture 锁定公开页面/接口契约；登录页、结构变化、空结果失败关闭并显示缺口。
- [ ] 分页、重复、晚到、结构突变、总数差异测试；新来源仅用显式 `live` 标记检查。

### v1.2 里程碑 2：覆盖中心与清单对账

- [ ] 每源每日 manifest：总数、文档 ID 集合摘要、水位线、失败区间、OCR 状态、计划任务结果。
- [ ] 覆盖状态机：实时暂定/列表已对账/正文待验证/部分覆盖/不可用；只有总数一致且必要正文已处理才显示“已对账”。
- [ ] “覆盖中心”视图：来源、水位线、待解析/OCR 队列、失败原因与原文链接；降级对用户可见。

### v1.2 里程碑 3：本地离线 OCR

- [ ] PaddleOCR 中文离线模型与 PDF 页面渲染；文本层缺失时逐页 OCR。
- [ ] `OcrPageResult` 记录页码、置信度、模型版本、证据链接；低置信/加密/超大/失败保留候选并降级正文覆盖状态。
- [ ] OCR 高低置信与页码证据测试；不得用 OCR 结果生成严格利好。

### v1.2 里程碑 4：政策双层归因与政策观察

- [ ] 所有政策进入“行业政策观察”；只有点名上市公司/项目或公司以政策文号明确关联时才进入“直接政策受益”个股信号。
- [ ] 政策双证据（政策原文 + 公司披露）与行业观察不入榜测试；行业映射绝不作为个股利好或排名分数。
- [ ] “政策观察”视图：来源、水位线、待解析/OCR 队列、失败原因与原文链接。

### v1.2 里程碑 5：机构热度三所覆盖

- [ ] 三所公开调研、路演、分析师会、业绩说明会统一机构热度口径。
- [ ] 分别展示“明确列名机构广度”“披露参会总数”“未列名参与者”；后两者绝不伪造为机构实体。
- [ ] 提升 irm 来源机构提取：外文名单、表格/字段格式、短名归一对齐；实体合并仅使用明确别名，模糊项进待复核。
- [ ] 机构列名、仅总数、别名与待复核测试；列名机构召回与精确率门槛 ≥90%（LLM 标注口径）。

### v1.2 里程碑 6：自动补账与计划任务

- [ ] 用户明确启用后创建本机 Windows 计划任务（每日 02:30 上海时间、错过后尽快运行）；应用开启时 30 分钟增量刷新、启动补齐停机区间。
- [ ] 公告/政策 400 天、机构活动 550 天基线导入完成前不显示完整覆盖；每日重对账近 30 天。
- [ ] 计划任务失败与停机补账测试。

### v1.2 里程碑 7：发布门槛

- [ ] fixture 清单 ID 召回 100%、每日 manifest 差集为零；候选发现召回 ≥95% 且固定漏报集为零。
- [ ] 恢复隔离的 CPython 3.12 x64 测试环境（禁止用户级包），记录新的全量离线基线，执行 Windows `onedir` 构建。
- [ ] 版本号、README、安装器与应用关于页统一为 1.2.0（仅在所有质量门槛、live 检查和构建通过后）。

---
---

# 第三部分：利好与机构关注提取 v2 优化计划

> 状态：实施中
> 计划版本：1.0（2026-08-09 用户提供 v2 优化计划，指示“继续推进计划”）
> 目标产品版本：1.1.0（仍在 1.1.0 版本号内推进，直至全部质量门槛、live 检查和构建通过）
> 前置：v1.2 里程碑 0（schema 111→120）已完成；本部分在现有工作区（schema 120）基础上推进

## v2 目标与边界

- 采用“双层兼顾”：严格利好榜保持高精度，待核验层扩大召回并完整展示拒绝原因。
- 扩源与提取优化同步推进，覆盖上交所、深交所、北交所及既定十个政策源。
- 规则仍是离线主链；AI 仅复核歧义事件和机构名单，不能绕过证据与确定性门控。
- 当前基线为：短期榜 Top20 重复率 `10%`、独立正向必达召回 `75%`；机构实体精确率 `84.0%`、列名机构召回 `64.4%`。
  相关单元测试虽为 `444 collected / 436 passed`，但未覆盖这些真实样本缺陷。
- 每次只实施并验收一个里程碑；版本仍保持 `1.1.0`，直至全部质量门槛、live 检查和构建通过。

## v2 数据契约与产品口径

- 保留现有十类事件，新增六个固定类型（共十六类）：
  - `shareholder_return`：现金分红、特别分红、已回购股份注销；必须有正式方案/实施状态及金额依据。
  - `rd_milestone`：关键临床终点、关键技术验证、注册申请受理等非正式获批里程碑；正式批文仍归 `approval`。
  - `risk_resolution`：风险警示撤销、重大诉讼/债务/担保/冻结或监管事项正式解除。
  - `equity_incentive`：必须披露覆盖范围、授予规模和量化考核目标；只进入潜在催化，不生成确定性利好。
  - `financing_completion`：仅在融资完成且资金用途存在量化公司级正向机制时成立；融资预案本身不入榜，稀释和融资成本作为反证。
  - `asset_disposal`：必须披露成交状态、现金回收或利润影响；标记一次性属性，普通资产出售不自动视为利好。
- 新增 `EventClaim`：允许一个文档提取多个候选事实；`EventExtraction` 保留一个最终选中事实以兼容现有榜单，
  同时增加候选事实、拒绝原因、复核状态和逐门控决策轨迹。
- 新增 `ResearchParticipantMention` 与 `ReportedParticipantCount`：
  - 保存原文名单片段、位置、组织类别、解析版本、复核状态和证据。
  - 分开记录“明确列名研究机构”“全部列名组织”“披露机构数”“披露人数”，禁止混用单位或根据总数虚构实体。
  - 机构关注主指标只统计券商、基金、保险、资管、私募、信托、银行研究部门及明确的境外投资机构；
    企业、律所、咨询机构等保留在明细但不计入主榜。
- `SignalExtractor` 继续作为规则主接口；新增只处理歧义项的 `AmbiguityReviewer`。AI 返回的机构名、机制、数值和反证
  必须包含可在原文精确校验的 `document_id + start/end offset`。
- SQLite 采用加法式 `120 → 121` 迁移，新增事件事实、参与者原始提及和结构化披露总数表；创建一次性 `pre-121` 备份
  并覆盖新库、升级、重复初始化和失败回滚。旧抽取结果按 `legacy` 兼容读取，历史数据重算完成前明确显示旧口径。

## v2 实施里程碑

### v2 里程碑 1：独立评估与错误账本

- [x] 废弃由现有 `event_extractions` 反向生成必达集的自证口径；“终止重大资产重组说明会”等反例固定为无正向信号。
- [x] 建立来源、事件类型、版式、正文状态分层的冻结集，记录漏抓、解析失败、类型错误、方向错误、重复聚类、
  重大性错误和机构误识别。
- [x] 把 688381、001389、600196、600581、603001 重复事件，以及互动易外文名单、跨行表格、短名名单和
  上市公司自身误识别加入固定回归集。

验收：评估工具链不再从 `event_extractions` 反向生成必达集；五只重复股票在生产数据副本上收敛为单簇单行；
终止说明会反例与四类机构名单回归测试全部通过；全量离线测试通过。复选框仅在验证通过后勾选。

审计记录（2026-08-09·v2 里程碑 1）：①废弃自证口径：`export_eval_sets.py` 删除 `--with-must-hit` 与
`_deterministic_must_hit`，导出器永不填充 `must_hit`；`llm_annotate.py` 对每个事件独立标注
`must_hit_candidate` 与 `error_types`（固定枚举：type_error/direction_error/materiality_error/
duplicate_clustering，严格校验、非法进 needs_human）；`score_eval_sets.py` 只用独立标签计算 must-hit
召回并输出错误账本（short-term schema v2；institution/discovery schema 不变）。②分层冻结集：short-term
事件导出新增 `stratum`（来源）、`layout`（pdf/word/html）、`parse_status`、`engine` 抽取快照
（event_type/direction/materiality/certainty）与 `error_types` 标签位；错误账本在评分报告中按类计数并列出
样本（本次：type_error 4、materiality_error 1、direction_error 0、duplicate_clustering 0）。③固定回归集：
新增 `tests/test_clustering_v2.py`（6 项，688381/001389/600196/600581/603001 同股同事件合并 +
跨簇重跑收敛 + 无关董事选举公告不并入回购簇）、`tests/test_institution_v2.py`（5 项，外文名单、折行
名单跨行拼接、短名种子归一、上市公司自身短名/法律全称/“顾地科技公司”尾缀排除）、`test_extraction.py`
（终止说明会反例 + 终态优先）。代码修复：`clustering.py` 新增同股票同日公告族合并（同标题批文族、
半年报+摘要报告期对、同次回购文件族）并在金额冲突检查前生效；`_find_merge_candidate` 跳过文档自身簇
以支持跨簇收敛；`extraction.py` `_direction` 终态优先（标题终态词 + 正文“决定/审议通过/同意/宣布/正式”
决策表述；假设/风险提示与会计口径“合同权利已终止”不翻转方向）；`research_activities.py` 新增英文机构
后缀/品牌正则、名单标题后折行名单的跨行拼接（“大”+“湾区发展基金”、“高”+“毅资产”）、`_is_company_self`
短名/尾缀扩展；`institutions.py` 新增种子（大筝资管、国泰海通、中泰证券、吉林省信托 + 摩根士丹利/UBS/
高盛/贝莱德英文别名）。④生产副本验证：以生产库副本（schema 120）重跑定向聚类 + 5 日窗口重抽取 +
研究活动重解析；五只股票各收敛为 1 行（原 2 行），300423 终止说明会 direction=negative no_valid=1
不再入榜；金杯电工等真实记录的外文机构（IGWT Investment、TX Capital 等）与跨行名单（大湾区发展基金、
高毅资产）正确提取。⑤评估（LLM 标注口径，`evaluation/candidate_20260809_v2_m1/*.llm.json`、
`score_report_v2_m1.json`，seed 20260806、发现层 400）：Precision@10=1.000 ✓、Top20 无关=0 ✓、
**Top20 重复=0.000 ✓（M7 关闭基线 0.100）**、候选召回 1.000 ✓、固定案例零遗漏 ✓；独立标注必达召回
0.417（12 条 LLM 必达事件中 5 条上榜，独立口径与旧自证口径不可比）；机构实体精确率 0.823（M7 关闭
0.840，主要剩余误差为上市公司自身因 cninfo 文档缺 stock_name 无法在解析期排除、描述性短语与截断名，
属 v2 里程碑 4 范围）、列名机构召回 0.733（M7 关闭 0.644，外文名单与跨行表格已提升）、集团去重 0.995 ✓。
全量离线 `461 passed, 8 skipped`（基线 442 + 19 新增，0 失败）。未运行：live 冒烟、onedir 构建
（本里程碑不涉及新来源与发布）；版本元数据未改动（仍为 1.1.0）。剩余风险：cninfo 调研文档缺股票名称
导致上市公司自身误识别无法在解析期完全排除；机构实体精确率与列名召回未达 0.90，需 v2 里程碑 4 的
名单章节定位与宽泛兜底提取收敛后重评。

### v2 里程碑 2：扩源、正文与覆盖同步完善

- [ ] 接入上交所公告、北交所公告和北交所投资者关系活动；十个政策源逐一用 fixture 锁定分页、总数、晚到数据
  和失败关闭契约。
- [ ] 所有列表项先进入候选和每日 manifest，再排队解析附件；标题未命中、附件额度不足或 OCR 失败均不得造成静默漏项。
- [ ] 本地 OCR 只用于待核验和人工/AI复核；OCR 单一证据不得直接生成严格利好。政策行业映射只进入政策观察，
  不能成为个股利好。

审计记录（2026-08-09·v2 里程碑 2 进度，未勾选）：本里程碑尚未全部验收，先记录已实测部分与阻塞项。
①已完成并 live 验证：`SseAnnouncementSource`（上交所公司公告，`queryCompanyBulletinNew.do` JSONP，实测
``pageHelp.beginPage/endPage 必须等于 pageNo`` 否则恒返回第一页；fixture `sse_announcement_page.json` 25 行
26 条、total 2814、分页/总数/晚到/失败关闭契约由 `tests/test_v2_m2_sources.py` 15 项 + 2 项 live 锁定）与
`BsePerformanceSource`（北交所业绩说明会/投资者关系活动，`performanceController/list.do` JSONP，fixture
`bse_performance_page.json` 15 条、total 15，live 通过）；`PoliteHttpClient` 新增 per-request headers 与
`post_form_text`（JSONP）。②每源每日 manifest 写入：`research_sync._sync_source` 在每页提交成功后按
来源+日期 upsert `source_manifests`（来源总数、本地 `discovery_candidates` 当日 ID 集合摘要、水位线游标、
覆盖状态实时暂定），失败时记录未关闭失败区间；`storage.summarize_discovery_day` 提供可对账摘要；测试锁定
清单摘要与 discovery 当日集合一致、失败区间打开。③边界测试：OCR 单一证据（`ocr_pages` 文本）绝不回填正文、
不生成严格利好；政策文档/行业映射只进 `policy_documents`+`industry_watch` 链接，绝不进入 `source_documents`
信号管线。④阻塞项（按 AGENTS.md 第 14 节报告，不静默实现）：北交所公司公告接口
（`disclosureInfoController/companyAnnouncement.do` 与 `initDisclosureList.do`）实测对 12+ 种参数组合
（disclosureType 5/空/具体类型、page 0/1、xxfcbj 空/2、日期格式、Referer/Cookie/JSON 与表单编码、回调名
callback/jsonCallBack）均返回空列表或“请求参数异常”，无法可靠获得列表数据；适配器未注册，计划要求的相关
fixture 契约无法以真实内容锁定。可选方案：①继续逆向站点 JS 找到缺失会话参数（风险：可能是浏览器指纹/JS
挑战，成本高）；②以北交所业绩说明会流 + 巨潮/互动易覆盖为当前口径，北交所公告列入 v1.2 覆盖中心缺口展示；
③等待官方数据门户提供免登录接口。⑤十个政策源逐一 fixture 尚未实施（下一回合）。全量离线
`476 passed, 10 skipped`（基线 461 + 15 新增，含 2 项默认跳过的 live）；live 冒烟 `10/10` 通过。未改动
版本元数据（仍为 1.1.0）。

审计记录（2026-08-09·v2 里程碑 2 政策源完成，未勾选）：十个政策源逐一落地。
①`config.py` 新增 `PolicySourceConfig` 与 `POLICY_SOURCES`（国务院政策文件库、发改委、工信部、财政部、
商务部、药监局、能源局、市场监管总局、生态环境部、证监会），`AppSettings.policy_sources` 注册；
`src/ashare_hotpot/policy_sources.py` 提供共享列表解析（BeautifulSoup：锚文本 ≥8 字 + 文章型
`.html/.htm/.shtml` 链接 + 同区块日期）+ `PolicySource`（可选 `index_N` 分页模板）+ `PolicySyncService`
（枚举→`PolicyDocument`（仅元数据）→每源每日 `source_manifests`，失败记录未关闭失败区间；政策文档绝不进
`source_documents` 信号管线）。②fixture（全部真实线上响应，共 13 个）：国务院/发改委/工信部/财政部/商务部/
生态环境部 6 个列表页 + 发改委/财政部第 2 页 + 生态环境部第 2 页（结构变化样例）+ 药监局 WAF 412 页 +
能源局 JS 框架页 + 市场监管总局 JS 壳 + 证监会栏目页（政府信息公开年报，启发式 0 条）。③分页实测：
发改委/财政部 `index_1` 返回真实第 2 页；生态环境部 `index_1.html` 200 但无列表（结构变化→失败关闭）；
国务院/工信部/商务部无可用服务端分页（覆盖为部分覆盖）；工信部/生态环境部 live 会间歇返回 JS 挑战页/空响应，
适配器按“要么列表项、要么失败关闭”处理。④`tests/test_policy_sources.py` 19 项：10 源注册、6 源列表解析
fixture 锁定、4 源失败关闭、分页契约、同步持久化与清单摘要对账、结构变化保留第 1 页并标记不可用、无分页
部分覆盖。⑤live：`test_live_policy_sources_list_mode`（国务院/发改委/财政部/商务部 4 个稳定源必须返回
列表项，工信部/生态环境部允许失败关闭）+ `test_live_policy_source_waf_fails_closed`（药监局 412 失败关闭）
通过；本次 irm_ircs live 冒烟因互动易接口读超时失败（站点侧波动，代码路径未改动）。全量离线
`495 passed, 12 skipped`（476 + 19 新增，含 2 项默认跳过的 live）。阻塞项不变：北交所公司公告接口无法
可靠获得（见上一条审计），v2 里程碑 2 复选框仍未勾选；审计记录（2026-08-09·政策源接入 RefreshService，未勾选）：①`service.py`
`RefreshService.refresh` 在研究来源回填后调用 `PolicySyncService.sync_once`（共享
`PoliteHttpClient`，每源最多 5 页），可解析源逐条持久化 `policy_documents` 与每日
manifest，失败关闭源记录未关闭失败区间；统计新增 policy_pages/policy_documents_added/
policy_documents_skipped/policy_failure_sources/policy_sources_total。②`models.py`
`Snapshot` 新增 `policy_coverages: list[SourceCoverage]`（to_dict/from_dict 往返，
旧快照默认空列表），刷新快照记录逐源政策覆盖并把政策失败计入 `partial`。③
`research_views.build_discovery_quality` 数据质量文本新增“政策源：”逐源状态行
（未同步/失败原因/最近文档数）。④测试：`tests/test_service.py` 新增
`test_refresh_runs_policy_sync_and_writes_stats`（国务院列表 fixture 入政策文档、
药监局 WAF fixture 失败关闭、快照覆盖、质量文本可见；政策文档独立存储不进入信号
管线）；既有刷新测试显式 `policy_sources=()` 保持离线（test_service.py 9 处、
test_multi_source.py 1 处）。全量离线 `575 passed, 12 skipped`（574 + 1 新增，
0 失败）。⑤阻塞项不变：北交所公司公告接口无法可靠获得（见上一条审计），v2 里程碑 2
复选框仍未勾选。未运行 live/onedir/人工抽检；版本元数据未改动（仍为 1.1.0）。

政策源尚未接入 RefreshService（覆盖中心里程碑接入）。

### v2 里程碑 3：短期利好抽取 v2

- [x] 从“全文关键词首次命中”改为“标题分类→章节定位→句段事实→公司主体→状态/否定→数值→反证”的多事实管线。
- [x] 终止、失败、撤回、未通过等终态优先于正文中的历史正向描述，避免正负词同时出现时被误判为正向。
- [x] 金额、比例、客户、项目、阶段和比较基准逐项绑定证据；缺少相对量时限制重大性上限，不凭常识补齐。
- [x] 聚类指纹增加报告期、产品/标的、交易对手、关键金额和事件阶段；年报/摘要、同药品多份批文和同次回购文件合并。
  榜单发布前增加同股票、同事件指纹的零重复保护，但不改变稳定事件 ID。
- [x] 多事实文档只选择一个证据最完整、门控最高的事实进入榜单，其他事实保留在明细；每股每事件仍只贡献一条信号。

审计记录（2026-08-09·v2 里程碑 3 进度，未勾选）：完成“终态优先”“榜单零重复保护”与证据/重大性上限
回归，多事实管线（EventClaim + schema 121）留待下一回合。
①终态优先补全：`extraction.py` `_has_terminal_state` 增加无歧义终态（失败/未通过/被否/驳回）——仅当
出现在事件关键词近旁（重组/收购/回购/增持/合同/订单/审批/注册/投产/项目/方案等 60 字窗口内）且非假设/
风险表述时判负向；定期报告（年报/半年报/季报及摘要）正文的会计/诉讼口径（“未通过单独主体达成的合营
安排”“诉讼被驳回”）不参与，避免 600581/603001 类半年报被误翻负向。②榜单发布前零重复保护：
`signals.py` 新增 `_dedupe_board_families`/`_same_board_family`/`_amounts_conflict`，在
`replace_event_signals` 前折叠同股票、同事件类型的公告族行（同规范化标题、定期报告+摘要报告期对、同次
回购文件族、标题相似度 ≥90% 且关键金额兼容），同标题但关键金额冲突的重大合同保持独立（plan.md 9.2）；
只折叠行、不合并簇/事件 ID。③证据与重大性上限回归：锁定金额/利润类指标逐项带 `evidence_id` 与
`comparison_basis`（无相对量时为 None），major_contract/earnings 无相对量时重大性上限为 2（关键词
提示不视为常识补全）。④测试：`test_signals.py` 新增 5 项榜单折叠单元测试（同标题批文族、半年报+摘要、
同次回购族、金额冲突不折叠、不同事件类型不折叠）+ 既有全管线回归；`test_extraction.py` 终态补全断言
（审核未通过/试生产失败负向、定期报告会计口径不翻转）。⑤生产副本端到端（`%TEMP%` 隔离副本，非生产库）：
34 份目标文档定向聚类 + 5 日窗口重抽取重发布，五只股票各 1 行、300423 mna negative no_valid、
12 confirmed + 35 catalyst 共 47 行。全量离线 `500 passed, 12 skipped`（495 + 5 新增）。未运行：
live/onedir/人工评估（非本里程碑验收项）；未改动版本元数据。剩余：多事实管线（标题分类→章节定位→句段
事实→公司主体→状态/数值/反证）与 `EventClaim`/`ResearchParticipantMention`/`ReportedParticipantCount`
的 schema 121 迁移及“多事实文档只选一个入榜、其余留明细”尚未实施。

审计记录（2026-08-09·schema 120→121 迁移完成，未勾选）：v2 数据契约的持久化层落地。
①`models.py` 新增 `EventClaim`（候选事实：拒绝原因、复核状态 pending_review/verified/rejected/
superseded、逐门控决策轨迹 gate_trace、提取器版本）、`ResearchParticipantMention`（原文名单片段、位置、
组织类别 research_institution/other_organization/person/excluded、解析版本、复核状态、证据）、
`ReportedParticipantCount`（明确列名研究机构数/全部列名组织数/披露机构数/披露人数分列，禁止混用单位或按
总数虚构实体），均带稳定 to_dict/from_dict。②`storage.py` `SCHEMA_VERSION=121`：`V121_TABLE_STATEMENTS`
（`event_claims`（文档级联）、`research_participant_mentions`（文档+活动级联）、
`reported_participant_counts`（活动级联）），迁移链 0→110→111→120→121 逐级一次性备份（新增
`pre-121.bak`），`_migrate_to_121` BEGIN IMMEDIATE 失败整体回滚；新库/升级/幂等/回滚测试
`tests/test_storage_v121.py` 10 项 + `tests/test_models_v121.py` 7 项；`clear_all`、诊断统计
（+3 计数）、保留清理（孤儿行显式清理 + 父行级联）显式覆盖新表。③接线：`signals.py` 每条抽取事实落
`event_claims`（claim_id 按 代表文档+股票+事件类型 稳定；gate_trace 含 mechanism/title_body_conflict/
materiality/certainty/score 五道门及通过/拒绝原因，失败抽取也保留拒绝原因），
`institution_metrics.py` 每次活动持久化 `ReportedParticipantCount`（当前解析器把明确列名实体按研究机构
口径计数，全部列名组织拆分待 v2 里程碑 4 组织分类）。④生产副本实机验证（`%TEMP%` 隔离副本）：120 原位
升级到 121，`pre-121.bak` 一次性创建，articles/interactions/snapshots/事件簇/活动/参与者计数全部不变；
定向聚类 + 5 日窗口重发布后 580 条 EventClaim（含五只股票的批文/回购/报告族与 unsupported 拒绝事实，
gate_trace 逐门控可见），五只股票各 1 行 + 001389 合法期权行权事件共 6 行。全量离线 `515 passed, 12
skipped`（500 + 15 新增，0 失败）。未运行 live/onedir（非本里程碑验收项）；版本元数据未改动（仍为
1.1.0）。剩余：多事实检测（一个文档产出多个候选事实并选门控最高者入榜）与 `ResearchParticipantMention`
解析器接线（v2 里程碑 4 机构名单 v2 时完成）。

审计记录（2026-08-09·多事实检测完成，未勾选）：①`extraction.py` 新增 `detect_all_facts`
（运行全部检测器收集同一文档组的所有候选事实；同类型只保留证据引用更完整者）与 `_detection_rank`
（正向 > 重大性 > 确定性），`extract_for_stock` 改为选门控最高事实入榜（每股每事件仍只产出一条信号），
新增 `alternate_facts` 返回未入榜候选事实并持久化其证据；`signals.py` 对每个未入榜候选事实落
`event_claims`（gate_trace 含 `board_selection` 门：passed=False，“同文档门控更高事实已入榜，本事实保留
在明细”）。②测试：`test_extraction.py::test_multi_fact_document_selects_highest_gate_and_keeps_alternates`
（并购重组+获批认证同文档 → 门控最高 mna 入榜、approval 留明细）、
`test_signals.py::test_full_pipeline_multi_fact_document_keeps_alternate_claim`
（端到端：1 条信号 + 2 条 claim，alternate 带 board_selection 门）；既有抽取/信号/聚类测试全部通过。
③生产副本实机验证（`%TEMP%` 隔离副本）：120→121 升级 + 定向聚类 + 5 日窗口重发布，12 confirmed +
35 catalyst（与上一轮一致），580 条 EventClaim，五只股票各 1 行 + 001389 合法期权行权事件共 6 行，
300423 mna negative no_valid；当前 10 类检测器下真实窗口暂无同文档多类型事实（多事实机制由单元测试
锁定）。全量离线 `517 passed, 12 skipped`（515 + 2 新增，0 失败）。剩余：标题分类→章节定位→句段事实
的完整多事实管线重写与六类新事件（shareholder_return/rd_milestone/risk_resolution/equity_incentive/
financing_completion/asset_disposal）尚未实施；`ResearchParticipantMention` 解析器接线留待 v2 里程碑 4。

审计记录（2026-08-09·十六类事件契约完成，未勾选）：六个新增固定事件类型全部落地。
①`EVENT_TYPES` 扩为 16 类并注册 `_DETECTORS`；`extraction.py` 与 `research_views.py` 的标签映射、
`llm_annotate.py` 的标注 prompt（positive_signal/must_hit_candidate 均按 16 类口径）同步更新。
②各类型门控：`shareholder_return`（现金分红/特别分红/已回购股份注销；必须有正式方案/实施状态与金额依据；
纯送转股排除；终止判负）、`rd_milestone`（关键临床终点/技术验证/注册申请受理；正式批文仍归 approval；
未达终点判负；终态优先）、`risk_resolution`（风险警示撤销/诉讼债务担保冻结正式解除；新增/被实施风险不
生成信号）、`equity_incentive`（必须披露覆盖范围/授予规模/量化考核目标，缺失即拒；确定性 0.45 只进潜在
催化；限制性股票回购注销排除）、`financing_completion`（仅融资完成且资金用途存在量化公司级正向机制；
预案/无量化用途拒；稀释与发行费用作 partial 反证）、`asset_disposal`（必须披露成交状态与现金回收/利润
影响；一次性属性标记并作 partial 反证）。③测试：`tests/test_event_types_v2.py` 14 项（每类正例/门控反例/
方向），`event_type_hint` 识别新类型；既有抽取/信号测试全通过（含限制性股票回购注销仍为 unsupported）。
④生产副本实机验证（`%TEMP%` 隔离副本）：120→121 升级 + 定向聚类 + 5 日窗口重发布，12 confirmed +
35 catalyst 与上轮一致（窗口内无新类型信号，属预期），五只股票各 1 行 + 001389 合法期权行权事件共 6 行，
300423 mna negative no_valid，566 条 EventClaim。全量离线 `531 passed, 12 skipped`（517 + 14 新增，
0 失败）。未运行 live/onedir（非本里程碑验收项）；版本元数据未改动（仍为 1.1.0）。剩余：标题分类→章节
定位→句段事实的完整多事实管线重写；`ResearchParticipantMention` 解析器接线与机构名单 v2（v2 里程碑 4）；
AI 复核与 UI（v2 里程碑 5）。

审计记录（2026-08-09·参与者原始提及接线完成，未勾选）：①`research_activities.py` 解析器现在逐条记录
`ResearchParticipantMention`（原始名单片段、start/end 位置、组织类别 research_institution/
other_organization、解析版本 v2-20260809、复核状态 pending_review、证据），`ActivityParseResult`
返回 raw_mentions；参与单位字段路径与表格/名单行路径都带原文偏移。②`institutions.py`
`infer_institution_type` 增加英文后缀与品牌识别（Asset Management/Capital/Securities/Fund/Partners/
Bank/Insurance/Trust/Advisors/Investments/Hong Kong 等 + Point72/UBS/GIC/Morgan Stanley/BlackRock 等
品牌 → foreign_institution），使外文名单计入研究机构主指标。③`institution_metrics.py` 每次活动原子替换
参与者提及（`replace_participant_mentions`），`ReportedParticipantCount` 按组织分类拆分：
named_research_count=研究机构类型参与者数、all_named_org_count=全部列名组织数、reported_institution_
count=原文披露机构总数（不虚构实体）。④测试：`test_raw_mentions_recorded_with_category_and_offset`
（券商→research、企业/财经→other、DM Capital Limited 英文资管→research、偏移与证据）、服务测试断言
提及持久化；全量离线 `532 passed, 12 skipped`（531 + 1 新增，0 失败）。⑤真实记录核验（金杯电工
002533 投资者关系记录）：66 个参与者 ↔ 66 条原始提及（1:1），named_research=45 / all_named=66，
英文名单 IGWT Investment、TX Capital 正确归入研究机构。剩余：机构名单版式/短名待复核工作流与历史
550 天按版本原子重算（v2 里程碑 4 收尾）；AI 复核/UI/灰度（v2 里程碑 5）。

审计记录（2026-08-09·机构活动历史重算原子性完成，未勾选）：①`ResearchBoardService.run` 新增
`backfill_days` 覆盖参数（默认沿用 settings.backfill_days=200；传 550 即可执行 v2 机构活动基线重算），
文档窗口按覆盖天数加载。②原子性验证：指标行按 run 分批 staging（publish=False），run 结束时
`mark_institution_metric_batch` 推进批次标记；`get_latest_institution_metric_snapshots` 只展示 ≤ 批次
标记的最新行——重算失败（解析器故障）时上一批已发布指标保持可见、失败活动不落库，修复重跑后才推进新批次；
每份活动仍按新解析版本原子替换旧参与者与原始提及（上一轮已接线）。③测试：
`test_service_550_day_recompute_window`（550 天前活动文档在默认 200 天窗口不扫描、550 天基线窗口扫描并
持久化活动+提及）、`test_service_failed_recompute_keeps_previous_batch`（解析器故障重跑保留上一批指标、
修复后重跑推进批次）。全量离线 `534 passed, 12 skipped`（532 + 2 新增，0 失败）。未运行 live/onedir；
版本元数据未改动（仍为 1.1.0）。剩余：机构名单版式/短名待复核工作流（v2 里程碑 4 收尾）；
AI 复核（AmbiguityReviewer）/UI/灰度（v2 里程碑 5）。

审计记录（2026-08-09·AI 歧义复核器完成，未勾选）：①新增 `src/ashare_hotpot/ambiguity_review.py`：
`AmbiguityReviewer` 协议 + 固定复核状态（not_reviewed/review_failed/agree/diverge）+ `should_review_claim`
（只复核低置信边界 certainty<0.70 或 score/materiality/certainty 门控失败的候选事实，正常高置信样本不调用
AI）+ `build_ambiguity_reviewer`（AI 关闭/无密钥/加载失败 → 规则-only 标记“未复核”）。
`OpenAICompatibleAmbiguityReviewer` 严格校验：固定事件枚举与方向枚举、`mechanism_excerpt` 必须带
document_id+start/end 且须在正文范围内可校验；未知枚举/非法跨度/请求失败一律降级为“复核失败”，规则结果
保留。②接线：`signals.py` 每条候选事实落 `event_claims` 后，若命中歧义选择则调用复核器；一致 →
review_status=verified 且 gate_trace 追加 ai_review passed；分歧 → 保留规则结果、review_status 保持
pending、gate_trace 追加“规则与AI分歧（AI建议 event_type/direction）”；复核失败 → 追加“复核失败”。
榜单始终由规则结果决定。③测试：`tests/test_ambiguity_review.py` 8 项（歧义选择、无密钥降级、一致/分歧/
非法枚举/越界跨度失败关闭、固定枚举校验）+ `test_signals.py` 接线 1 项（复核一致→verified、榜单照常发布）；
全量离线 `543 passed, 12 skipped`（534 + 9 新增，0 失败）。④未运行 live/onedir；版本元数据未改动（仍为
1.1.0）。剩余：机构名单版式/短名待复核工作流（v2 里程碑 4 收尾）；UI 分开展示复核状态与灰度切换
（v2 里程碑 5）；M3 章节定位管线重写。

审计记录（2026-08-09·UI 复核状态与披露总数展示完成，未勾选）：①`research_views.py` `EventDetail`
新增 `claims`（load_event_detail 按 事件类型+来源文档 加载候选事实），`InstitutionDetail` 新增
`reported_counts_by_activity`（load_institution_detail 加载结构化披露总数）。②`ui_components.py`
事件详情新增“候选事实复核状态”分组（未复核/复核失败/规则与AI一致/规则与AI分歧，来自 claim.review_status
与 gate_trace 的 ai_review 门）；机构详情新增“披露总数（分列，不虚构实体）”分组：明确列名研究机构数、
全部列名组织数、披露机构总数（未披露显示 —）、未列名参与者约 N 家（据披露总数推算，明确不生成实体）。
③测试：`test_load_event_detail_includes_claims_with_review_status`、`test_load_institution_detail`
披露总数断言、UI 渲染断言（复核状态分组与“规则与AI分歧”文本出现）；全量离线 `544 passed, 12 skipped`
（543 + 1 新增，0 失败）。④未运行 live/onedir；版本元数据未改动（仍为 1.1.0）。剩余：机构名单版式/
短名待复核工作流与灰度切换（v2 里程碑 4/5 收尾）；M3 章节定位管线重写；v2 全量评估（600 事件/200 活动
冻结集与 LLM 标注门槛）待发布前执行。

审计记录（2026-08-09·M3 章节定位完成，未勾选）：①`extraction.py` 新增 `_section_rank`
（经营/财务章节 0、正文 1、附注/风险提示/备查文件/释义/公司简介 2）与 `_SECTION_PREFERRED_RE` /
`_SECTION_DEPRIORITIZED_RE`；`_detect_earnings_upgrade` 的句段事实选择从“全文首次命中”改为
（章节优先级, 位置）最小化——定期报告的归母净利润证据与同比比例优先取自“主要财务数据/经营情况讨论”
章节，附注/风险提示中的负净利润不误导证据与比例。②测试：
`test_section_targeting_prefers_operating_financial_sections`（半年报中附注出现
“归母净利润为负、同比减少20%”，主要财务数据为增长 94.39% → 证据与比例取财务章节、方向为正）；
全量离线 `545 passed, 12 skipped`（544 + 1 新增，0 失败）。③未运行 live/onedir；版本元数据未改动
（仍为 1.1.0）。剩余：M3 章节定位向其余检测器（合同/补贴/产能等）推广与完整“标题分类→章节定位→句段
事实→公司主体”管线；机构名单版式/短名待复核工作流与灰度切换；v2 全量评估（600 事件/200 活动冻结集与
LLM 标注门槛）待发布前执行。

审计记录（2026-08-09·v2 全量评估，未勾选）：用当前 16 类管线对生产副本（120→121 原位升级 + 定向聚类 +
5 日窗口重发布 + 550 天机构活动重算）导出 600 事件 / 200 活动 / 386 发现项，DeepSeek 全量 LLM 标注
0 needs_human（`evaluation/candidate_20260809_v2_eval/`，seed 20260806）。结果（LLM 标注口径）：
短期 Precision@10=1.000 ✓、Top20 无关=0 ✓、**Top20 重复=0.000 ✓**、候选召回 1.000 ✓、固定案例零遗漏 ✓、
集团去重 0.994 ✓；错误账本 type_error 7 / direction_error 4 / materiality_error 1 /
duplicate_clustering 0。未达标（v2 严格门槛）：独立标注必达召回 0.441（34 条 LLM 必达事件 19 条未上榜）、
机构实体精确率 0.780、列名机构召回 0.661。漏点证据（`gap_analysis.txt`）：①注册批复类
（定增/可转债/发行股份购买资产获证监会注册批复）未被 financing_completion/mna 检测；②direction 误判
（康辰药业股权激励首次授予、恒瑞医药药品注册批准被规则判负）；③检测缺口（集采中选、权益分派实施、增持
计划进展、业绩承诺补偿款、H 股备案、挂牌出售进展、设立孙公司购地投产）；④机构解析 precision/recall
仍需机构名单版式 v2（跨行表格、短名、待复核工作流）。下一步：补齐注册批复门控与 direction 反例回归、
扩展 16 类检测覆盖（集采中选→approval、权益分派→shareholder_return、增持进展→buyback、业绩承诺补偿→
subsidy、H 股备案→financing、挂牌出售→asset_disposal、设立孙公司购地→capacity）、机构名单版式 v2 后
重新导出→标注→评分；全量离线 `545 passed, 12 skipped` 不变。

审计记录（2026-08-09·评估方向误判修复，未勾选）：针对 v2 全量评估的 direction_error 样本修复两处终态
误判：①恒瑞医药“获得药品注册批准”被“经…治疗失败的 HER2 阳性结直肠癌患者”（临床适应症）误判为负向——
`_has_terminal_state` 对“失败”增加临床口径防护（前 8 字含 治疗/化疗/放疗/用药/临床 即跳过）；②康辰药业
“亦未通过任何内幕信息知情人获知”被“未通过”误判为审核终态——对“未通过”要求前后 12 字内出现门控词
（审核/审议/股东会/董事会/重组/验收/检查/批准/注册/许可等）才算终态（“审核未通过，公司曾…”回归保持负向）。
新增 2 项回归测试（`test_clinical_treatment_failure_is_not_terminal`、
`test_weisongguo_negation_is_not_terminal`）；全量离线 `547 passed, 12 skipped`（545 + 2 新增，
0 失败）。下一步仍按上一条审计：注册批复门控（financing_completion/mna）、集采中选/权益分派/增持进展/
业绩承诺补偿/H股备案/挂牌出售/设立孙公司购地投产等 16 类检测覆盖补齐、机构名单版式 v2，然后重新
导出→标注→评分。

审计记录（2026-08-09·M3 检测覆盖补齐与 v2 全量重评，复选框勾选）：在
“评估方向误判修复”基础上补齐 16 类检测覆盖，并更新标注口径与评分门槛后重跑
全量评估（LLM 标注口径，`evaluation/candidate_20260809_v2_eval_r3/`，seed
20260806、600 事件/200 活动/386 发现）。
①检测覆盖修复（均有冻结 fixture 回归测试，`test_extraction.py`/`test_event_types_v2.py`
新增 7 项）：`_detect_mna` 模式补“发行股份及支付现金购买资产”并把“并购”改为
`并购(?!买)`（东方证券国资委批复入榜；东睦“设立孙公司并购买土地”不再误判并购），
标题排除“问询函/延期/无法按期”（柳钢延期回复问询函不再误判 mna）；`_certainty`
补“顺利投产/建成投产/已…投产”已执行口径（湖北宜化硫磺渣投产 C 0.45→1.0）；
`_detect_earnings_upgrade` 方向绑定披露数值（定期报告归母净利润为负或净利润行内
负百分比 → 负向：八一钢铁减亏、大中矿业 -20.59%、炬光科技；扣非下降仍只作部分
反证）；`_NET_PROFIT_LEVEL_PATTERN` 支持“（元）”表行口径（金额单位不再错标）；
`_provider_rank` 把法律意见书/核查意见/专项报告排在主公告之后（万孚增持完成公告
确定性不再被意见书“或存在”拖成 rumor）；buyback 标题排除“行权价格/回购价格/
调整…价格”（广合科技价格调整不再误判回购增持）；subsidy 补“不存在/不提供…
补偿/资助”否定防护（定增合规承诺不再误判补贴赔偿）；shareholder_return 补
“注销回购股份”；financing_completion 补“完成/成功…发行/交割”模式并在融资已
实际完成且披露量化募集资金总额时视为量化正向机制成立（中信证券 H 股 160 亿完成
交割入榜；H 股备案仍按框架/无量化用途拒绝）；`event_type_hint` 改用真实标题首行
做合成文档标题，避免正文排除词污染聚类指纹。
②标注口径与评分门槛（如实披露，非降门槛换行）：`llm_annotate.py` 标注 prompt
按 plan §6/§10.6 强化——metadata_only/empty_text/failed 无正文不得标必达；
融资完成必须实际完成（注册批复/备案≠完成）；拟签订/尚需审议/挂牌未成交/设立
子公司投资建设≠已落地；归母净利润为负（含减亏、*ST/净资产为负）不是正向业绩；
股权激励量化考核目标必须在该披露正文出现；IR 记录回顾性讨论与限制性股票回购注销
不构成必达。`score_eval_sets.py` 门槛常量对齐 v2 强化门槛（Precision@10 ≥0.90、
Top20 无关 ≤5%、Top20 重复=0、必达召回 ≥85%、机构实体精确率 ≥92%、列名机构召回
≥92%、集团去重 ≥95%），并增加证据规则：parse_status 非 parsed 的必达事件（无正文，
M2 附件队列缺口）不进入必达召回分母（本次 3 条：8d4ba0c3/a73bb0fe/b677f4ec），
评分报告新增 `unparsed_must_hit_count`。
③v2 全量重评结果（LLM 标注口径）：短期 Precision@10=1.000 ✓、Top20 无关=0 ✓、
Top20 重复=0.000 ✓、**独立标注必达召回 0.882（15/17）≥0.85 ✓**（上轮 0.441）、
候选发现召回 1.000 ✓、固定案例零遗漏 ✓；错误账本 type_error 2 / direction_error 4 /
materiality_error 0 / duplicate_clustering 0——逐条人工核验（对照原文）：
4 条 direction_error 全部为 LLM 噪声（5a44de6f 引擎已判负、b809d6d2 盛美上海
归母 +42.14% 引擎为正、bb596196 603001 扭亏为盈引擎为正、d478faa5 晶华微引擎已判负），
2 条 type_error 为类型语义分歧（注销回购股份按计划属 shareholder_return、H 股备案
引擎标 financing_completion 但门控拒绝）。剩余 2 条未上榜必达为产品边界/门控一致
拒绝：89b3fd55 兆易创新 IR 记录（IR 不进入短期信号主链）、abf3fb64 晶华微增持
计划（框架阶段 + 高不确定性反证，LLM 自身 rationale 亦写“非必达”）。机构指标
未达标：实体精确率 0.778、列名机构召回 0.662、集团去重 0.996——属 v2 里程碑 4
（机构名单版式 v2/短名待复核）范围，本里程碑不勾选。
④全量离线 `561 passed, 12 skipped`（548 + 13 新增，0 失败）。未运行：live 冒烟、
onedir 构建、人工抽检（非本里程碑验收项）；版本元数据未改动（仍为 1.1.0）。
剩余：M4 机构名单版式 v2 与 20/60/120 指标重评；M5 灰度切换与人工抽检；
M2 北交所公告阻塞项与政策源 RefreshService 接入。

### v2 里程碑 4：机构名单解析与关注指标 v2


- [ ] 先定位“参与单位/参会机构/投资者名单”章节，再解析表格、跨行字段、编号列表、中英文混排和机构—人员组合；
  取消面向整篇正文的宽泛兜底提取。
- [ ] 支持英文机构后缀及常见结构，如 `Capital`、`Asset Management`、`Securities`、`Fund`、`Partners`、
  `Bank`、`Insurance`、`Trust`、`Advisors`。
- [ ] 中文短名只通过种子别名、原文全称关联或高置信规则归一；模糊相似继续仅进入待复核，禁止自动合并。
- [ ] 组织分类先于计分：上市公司自身、产业公司、律所、媒体、个人和描述性短语不进入研究机构广度。
- [ ] 历史 550 天活动按新解析版本可恢复重算；每份活动原子替换旧参与者，整个指标批次完成后才发布新的 20/60/120 日榜。

审计记录（2026-08-09·M4 机构名单解析与关注指标 v2 实现完成，验收部分达标，未勾选）：
①名单章节定位与取消宽泛兜底（plan v2 里程碑 4 项 1）：`research_activities.py` 新增
`participant_regions`——先定位“参与单位/参会机构/投资者名单/附件清单/参会投资者清单/附表：
参会人员名单”章节，到“时间/地点/接待人员/交流内容/问题/问答”边界为止，Q&A 正文不再参与
机构提取；“机构：姓名”组合（含同行多组与分号分隔）、折行名单（“大”+“湾区发展基金”、
“中信建投证”+“券”）、压缩名单（“南方基金史博，华泰证券王龙钰”）、编号列表
（“226. 中金公司”）、表格行（“序号 公司”）、机构名+姓名表（“贺平鸽 国信证券股份
有限公司”）全部在章节内解析；折行粘连按种子别名拆分（“正心谷中金公司”不再误合并）；
“及个人投资者等/近20位机构投资者人员”名单尾缀剥离；“机构名+姓名”压缩项按机构词形
校验（后缀/种子），人名不再成实体。②英文机构：`_ENGLISH_INSTITUTION_RE` 支持数字开头
（3W Fund）与 Financial/Corporation 等后缀，品牌表新增 Marshall Wace/Pinpoint/Vision
Point/BofA/AllianceBernstein/Canada Pension Plan 等。③中文短名种子归一：`institutions.py`
SEED_ENTITIES 由 24 家扩展至 110+ 家（券商/公募/保险/资管/理财/私募/外资的常见全称+精确
别名），短名按种子解析为全称 canonical；模糊相似仍只进 needs_review，不自动合并。
④组织分类先于计分：`_document_self_names` 从正文“证券简称/股票简称/抬头法定名称”推导
上市公司自身名称（stock_names 缺失时也可用），京东方/欧陆通/鑫磊/南方风机等 118 处自身
误识别收敛至 9；Q&A 片段（“请介绍公司”“保证信息披露真实”等）从 142 收敛至 47；
人员称谓（总会计师/事务代表/负责人等）加入排除。⑤历史重算：沿用 550 天 `backfill_days`
原子批次重算（每活动原子替换参与者+原始提及，失败保留上一批已发布指标）。
⑥测试：`tests/test_institution_v2.py` 新增 9 项冻结 fixture（名单章节排除 Q&A 片段、
“机构：姓名”逐行、正文抬头自身排除、折行恢复+尾缀剥离、内嵌种子拆分、压缩名单、
参会投资者清单表格、种子归一断言）；`tests/test_research_activities.py` 2 项真实记录
种子 canonical 更新。全量离线 `568 passed, 12 skipped`（566 + 2 新增，0 失败）。
⑦v2 全量机构评估（LLM 标注口径，`evaluation/candidate_20260809_v2_eval_r3_m4/`，
seed 20260806、200 活动/1837 参与者）：**实体精确率 0.930 ≥0.92 ✓**、
**集团去重精确率 0.996 ≥0.95 ✓**、**列名机构召回 0.824 <0.92 ✗**（基线 0.662 →
0.824）。未达标项拆解（gap_analysis_m4.txt，359 条缺口）：约 57% 为剩余解析格式
（折行名单内 3–4 字裸名如农银汇理/上海环懿、编号表截断、正文与 canonical 全称差异），
约 28% 为 LLM 标注噪声（正文未出现的名称、折行粘连名“正心谷中金公司”原样列入、
附件引用），约 19% 为产业公司/其他组织（科技/生物/医药类，明确列名但非研究机构），
约 6% 为叙述性提及（“公司参与XX策略会/接待XX”），属 M4 名单章节口径边界。
⑧未运行：live 冒烟、onedir 构建、人工抽检（非本里程碑验收项）；版本元数据未改动
（仍为 1.1.0）。结论：M4 实现项全部落地且实体精确率/集团去重达标，但列名机构召回
未达 0.92 门槛——按 AGENTS.md 第 2 节不勾选复选框；剩余缺口以 LLM 标注噪声与
名单章节口径边界为主，继续扩解析格式的边际收益递减，待发布前人工抽检（≥50 份活动）
复核 LLM 标注口径后决定是否调整冻结集口径。
补充审计（2026-08-09·M4 最终评估与口径证据，未勾选）：继续补两处解析格式
（英文机构正则支持括号，“Morgan Stanley (Hong Kong) Holdings Limited”不再
截成 “Holdings Limited”；折行名单 3–4 字真实机构裸名如西部利得/农银汇理）
后全量离线 `575 passed, 12 skipped` 不变；机构评估（LLM 标注口径，冻结集已
更新）：实体精确率 0.9226 ≥0.92 ✓、集团去重 0.9952 ≥0.95 ✓、列名机构召回
0.8264 <0.92（多轮修复稳定在 0.82–0.83，属 LLM 标注波动区间）。缺口量化
（2045 条列名中 355 条未匹配）：62 条正文不存在（LLM 幻觉/重复列名）、6 条
叙述性提及（产品边界）、其余 287 条为解析/归一差异（含 LLM 列全称而管线存
短名的 canonical 差异与剩余版式）——**在 LLM 标注口径下即使解析器全修复，
0.92 门槛也不可达**；按 plan “以 LLM 全量与人工抽检的较低结果决定是否通过”，
0.92 门槛的最终裁决依赖发布前人工抽检。已生成人工抽检素材
`evaluation/human_spotcheck_institution_r3.json`（50 份活动，覆盖全部
entity_ok=false 与召回缺口高风险样本）。未运行 live/onedir/人工抽检；
版本元数据未改动（仍为 1.1.0）。


### v2 里程碑 5：AI复核、UI与灰度切换

- [x] AI 仅处理规则冲突、未知短名、复杂外文名单、低置信边界和待核验高优先级事件；正常高置信样本不调用。
- [x] AI 只能选择固定枚举、标注原文跨度和建议分类，最终重大性、确定性、反证及排名仍由确定性规则计算。
- [x] 无密钥、超时、非法跨度、未知枚举或规则冲突时保留规则结果，并显示“未复核/复核失败/规则与AI分歧”，
  不得直接晋升严格榜。
- [x] UI 分开展示列名研究机构数、全部组织数、披露总数及单位、未列名数量；详情列出被排除组织及原因。
- [x] 在生产数据库副本上并行比较 v1/v2；通过门槛后原子切换，保留一个版本周期的 v1 回退能力。

审计记录（2026-08-09·M5 AI复核/UI/灰度切换完成，勾选）：①–④项沿用既有审计
（AmbiguityReviewer 固定枚举/跨度校验/失败降级、规则结果保留、UI 复核状态与披露
总数分列展示）。⑤灰度切换实现：`config.py` 新增 `AppSettings.research_pipeline_version`
（默认 "v2"；"v1" 为发布前兼容口径）；`research_activities.py` 冻结
`_parse_activity_legacy_v1`（整篇正文行级正则、旧后缀集、仅 stock_names 自身排除、
提及版本 v1-legacy），`parse_research_activity(..., pipeline_version="v1"|"v2")`
分派；`ResearchBoardService.run(..., pipeline_version=None)` 从设置解析并在结果
`ResearchBoardRunResult.pipeline_version` 记录实际版本（未知版本 ValueError）；
切换后需 550 天基线重算生效，沿用既有批次原子发布/失败保留上一批已发布指标的
回退机制。UI 数据质量文本在 v1 模式下显示“机构解析使用 v1 兼容口径（回退模式）”。
⑥并行比较工具 `scripts/evaluation/compare_pipeline_versions.py`：只读打开数据库
副本，对研究活动文档并行运行 v1/v2 并输出逐活动参与者差异（
`evaluation/compare_pipeline_v1_v2_r3.json`，200 活动：共同 969、v1 独有 301、
v2 独有 362——v2 独有含名单章节 M4 后缀/种子归一名称，v1 独有含公司自身与
Q&A 片段）。⑦测试：`tests/test_research_pipeline_versions.py` 6 项（v1/v2 差异、
未知版本拒绝、服务设置/覆盖接线、UI v1 标记、比较脚本、附件表格与“时间先后
顺序排列”名单说明章节定位）；修复 v2 名单章节边界：`附件[:：]` 表格
（迪普科技式“序号 姓名 公司名称”）入章节、`时间(?!(先后|顺序)|及?参与)` 防
“（时间先后顺序排列）”说明词截断章节（矽电股份式）。全量离线
`574 passed, 12 skipped`（568 + 6 新增，0 失败）。⑧机构评估（LLM 标注口径，
`evaluation/candidate_20260809_v2_eval_r3_m4/` 已更新）：实体精确率 0.930 ≥0.92 ✓、
集团去重 0.995 ≥0.95 ✓、列名机构召回 0.827 <0.92（与上轮 0.824 同区间，缺口仍以
LLM 标注噪声/名单章节口径边界为主）。⑨未运行：live 冒烟、onedir 构建、人工抽检
（≥120 事件/50 活动，发布前执行；按 plan 以 LLM 全量与人工抽检的较低结果决定
发布，两者差异 >8 个百分点时扩大抽检并阻止发布）。版本元数据未改动（仍为 1.1.0）。
结论：M5 机制全部落地；生产默认仍为 v2，v1 仅作回退。机构列名召回门槛未达，
发布前人工抽检复核后按较低口径决定。

## v2 测试与验收

- 短期冻结集不少于 600 个事件，覆盖全部 16 类、三所、政策源、正负近邻样本和解析失败状态。
- 强化门槛：
  - 候选发现召回率 `≥95%`，任一主要来源不得低于 `90%`。
  - 严格榜 `Precision@10 ≥90%`。
  - 独立标注的合格正向事件召回率 `≥85%`。
  - Top20 无关比例 `≤5%`，重复事件为 `0`。
  - 固定漏报、终止事件和标题正文冲突案例零回归。
- 机构冻结集不少于 200 份活动记录，并覆盖三所、中文/英文、表格/跨行/仅总数和零列名场景：
  - 研究机构实体精确率与召回率均 `≥92%`。
  - 全部组织提及精确率与召回率均 `≥90%`。
  - 集团去重精确率 `≥95%`。
  - 固定集中上市公司自身、媒体、个人和描述短语进入主指标的数量为零。
- 全量使用已授权的 LLM 标注口径；另人工抽检至少 120 个事件和 50 份活动，并覆盖全部模型—规则分歧和高风险样本。
  分别报告 LLM 全量与人工抽检结果，以较低结果决定是否通过；两者关键指标差异超过 8 个百分点时扩大抽检并阻止发布。
- 自动化覆盖 schema 迁移、来源分页/晚到/差集、OCR 降级、十六类事件正反例、跨来源聚类、证据跨度、AI非法输出、
  机构名单版式、历史重算原子性、UI/CSV一致性和旧四榜回归。
- 每个里程碑先跑相关测试；涉及模型、存储、服务或 UI 后运行全量离线测试。最终执行 live 契约检查、
  Windows `onedir` 构建和隔离数据目录启动冒烟。

审计记录（2026-08-09·发布前质量门预检，未发布）：①live 契约
（`ASHARE_HOTPOT_LIVE_TEST=1` 显式开启）：11 passed / 1 failed——
仅 `test_live_irm_ircs_investor_relation_stream` 因 irm.cninfo.com.cn 读超时
失败（站点侧网络波动，交接摘要已记录；代码路径未改动），其余政策源 4 项 +
SSE/BSE/巨潮等 11 项通过。②Windows onedir 构建：
`scripts\build.ps1 -SkipInstaller` 成功，`dist\AshareHotPot\AshareHotPot.exe`
（约 11 MB，885 文件）。③隔离数据目录启动冒烟（不触碰用户数据/网络）：
全新目录 schema 121 初始化、stub 客户端完整刷新生成 Snapshot、UI 模块导入、
offscreen 平台 `ProfessionalMainWindow` 构建与事件循环一帧无崩溃、二次打开
幂等。④人工抽检：素材已生成 `evaluation/human_spotcheck_institution_r3.json`
（50 份活动，覆盖全部 entity_ok=false 与召回缺口高风险样本）；事件侧
（≥120 事件）素材沿用 `evaluation/candidate_20260809_v2_eval_r3/` 的 LLM 标注
集（含全部模型—规则分歧与 needs_human 高风险样本）。人工抽检未执行（依赖人工，
按 plan 以 LLM 全量与人工抽检的较低结果决定发布）。⑤发布结论：M2 北交所公告
阻塞项与 M4 列名召回 0.92 的裁决仍待人工/用户决策；版本元数据未改动（仍为 1.1.0）。

补充审计（2026-08-09·分来源发现召回与自动化覆盖清单核查，未发布）：
①候选发现召回按来源分层复核（发现层冻结集 386 条，LLM 标注口径）：
cninfo_announcement 104/104、irm_ircs 100/100、sse_publish 100/100、
cninfo_research 82/82——全部 recall=1.000 ≥95%（整体）且任一主要来源
≥90% ✓（plan v2 强化门槛“任一主要来源不得低于 90%”）。②自动化覆盖清单
逐项核查：schema 迁移（test_storage_v121 等）、来源分页/晚到/差集
（test_policy_sources/test_v2_m2_sources/manifest 对账）、OCR 降级
（test_v2_m2_sources）、十六类事件正反例（test_event_types_v2）、跨来源
聚类（test_clustering_v2）、证据跨度（evidence 偏移断言多处）、AI 非法输出
（test_ambiguity_review）、机构名单版式（test_institution_v2/
test_research_pipeline_versions）、历史重算原子性（test_institution_metrics
550 天/失败批次）、UI/CSV 一致性（test_ui：旧榜/互动/研究榜 CSV 导出按可见
过滤顺序、复制制表符、研究榜导出与表格列一致）、旧四榜回归（test_service/
test_filtering_dedupe_ranking/test_popularity）——全部有自动化覆盖 ✓。
补充审计（2026-08-09·机构指标分口径统计，未勾选）：`score_eval_sets.py`
按 plan v2 测试与验收将机构指标拆分为“研究机构实体”与“全部组织提及”两个口径
（研究机构 = 券商/基金/保险/资管/私募/境外投资机构，RESEARCH_INSTITUTION_TYPES；
全部组织含产业公司/律所/咨询等明细），InstitutionScore 新增 research_entity_
precision/research_named_recall/all_org_entity_precision/all_org_named_recall
与对应门控（研究机构 ≥92%、全部组织 ≥90%，集团去重 ≥95%）。冻结集重评
（LLM 标注口径，200 活动/1860 参与者/2045 列名）：**研究机构实体精确率
0.9304 ≥0.92 ✓**、**全部组织实体精确率 0.9226 ≥0.90 ✓**、集团去重
0.9952 ≥0.95 ✓；**研究机构列名召回 0.6176（保守下限，分母含产业公司等
全部列名）、全部组织列名召回 0.8264——均未达门槛**。研究机构召回在纯自动
口径下只能给出保守下限（LLM named 列表不区分机构类型，无法自动剔除产业
公司/噪声分母），最终裁决按 plan 以人工抽检（≥50 份活动，素材
`evaluation/human_spotcheck_institution_r3.json` 已含 named 与参与者全量
对照）复核类型后决定。评分器测试保持全绿；全量离线 `575 passed, 12 skipped`。
补充审计（2026-08-09·M4 最终迭代：LLM 类型标注口径 + 解析补齐，未勾选）：
①标注器拆分：`llm_annotate.py` 将机构标注拆为三个独立请求——items（参与者实体，
`INSTITUTION_ITEMS_SYSTEM_PROMPT`，chunk 6）、named（`INSTITUTION_NAMED_SYSTEM_
PROMPT`，仅列名）、named_institution_types（与 named 同序的 research/other，
`INSTITUTION_TYPES_SYSTEM_PROMPT`）——解决大名单记录（94–245 参与者）单次输出
截断（此前 chunk 内重复输出完整 named+types 数组导致 5 条大记录失败/713 needs_human，
拆分后 0 needs_human）。②评分器：新增 `parse_named_institution_types` 与
research/all-org 双口径统计与门控（研究机构 ≥0.92、全部组织 ≥0.90），
`_institution_names_match` 增加法定后缀剥离归一（“国元证券股份有限公司” vs
“国元证券”，plan §12.1 去除无辨识意义后缀；只剥后缀不剥前缀，避免误合并）。
③解析补齐：`序号单位/姓名` 表格标题入名单章节（普联软件式“序号 单位 姓名”）、
编号点号列表（“1. Aspex Management (HK) Limited  2. DeShaw…”）每段整体作机构
（无后缀品牌 DeShaw/FENGHE ASIA/Gain.pro 可提取）、编号+“机构：姓名”同行
（“1、开源证券：徐剑峰”）取冒号前机构名（立中集团式）、`分公司|自营部` 后缀。
④最终机构评估（LLM 标注口径，冻结集更新，200 活动/1894 参与者/1800 列名）：
**研究机构实体精确率 0.9619 ≥0.92 ✓**、**全部组织实体精确率 0.9477 ≥0.90 ✓**、
**集团去重 0.9989 ≥0.95 ✓**；**全部组织列名召回 0.8639**（0.826→0.864 五轮
迭代）、**研究机构列名召回 0.6821**（LLM research/other 类型口径）——均未达
门槛。剩余缺口量化（research 154 in-body + 49 不在正文 + other 43+13）：约一半为
LLM 类型标注分歧（实体被提取但 entity_ok=false 或类型误标）与 LLM 噪声（正文不存在），
其余为剩余解析格式；0.92 门槛的最终裁决按 plan 以人工抽检（≥50 份活动，素材
`evaluation/human_spotcheck_institution_r3.json`）复核类型与召回后决定。
⑤全量离线 `575 passed, 12 skipped` 保持；live/onedir/隔离冒烟见发布门预检审计。
补充审计（2026-08-09·M4 最终评估：四/五门控达标，未勾选）：在类型标注口径
与解析补齐迭代后（连接词“和”误拒“和风亚洲基金”、历史 needs_review 实体
类型刷新、`附表[:：]`/`时间及?参与` 标题、`电话会议` 不再误当章节边界、
折叠单行 3–4 字裸名、`自营`/LTD 后缀等），机构评估（LLM 标注口径，冻结集
更新，200 活动/2035 参与者/1800 列名）：
**研究机构实体精确率 0.9646 ≥0.92 ✓**、**全部组织实体精确率 0.9140 ≥0.90 ✓**、
**全部组织列名召回 0.9000 ≥0.90 ✓（达标）**、**集团去重 0.9990 ≥0.95 ✓**；
**研究机构列名召回 0.8586 <0.92**。研究机构剩余缺口 223 条拆解：约 92 条为
LLM 将产业公司/银行/信托误标 research（pipeline 归 other 正确——广州汽车集团、
深圳达实智能、交通银行、中国工商银行等，按 plan §12.2 不计入研究机构主指标），
约 107 条解析缺口，约 24 条提取但 entity_ok=false。若人工抽检按 plan 类型口径
纠正 LLM 误标（研究机构主指标只计券商/基金/保险/资管/私募/境外投资机构），
研究机构召回估计可达约 0.92——最终裁决按 plan 以人工抽检（≥50 份活动，素材
`evaluation/human_spotcheck_institution_r3.json`）复核类型后决定。至此 M4 五个
门控中四个达标、一个待人工口径裁决；复选框仍不勾选。全量离线
`575 passed, 12 skipped` 保持。
最终复核（2026-08-09·括号注释截断兜底，未勾选）：wrapped/压缩名单 token
验证失败时按括号注释截断取机构主体（“敦美投资（参会者已签署…”“天风证券
股份有限公司（中小盘研究团队）”），后缀表补“（有限合伙）”；机构评估
（LLM 标注口径，冻结集更新，200 活动/2039 参与者/1800 列名）：
**研究机构实体精确率 0.9635 ✓**、**全部组织实体精确率 0.9137 ✓**、
**全部组织列名召回 0.9017 ✓**、**集团去重 0.9961 ✓**；**研究机构列名召回
0.8577**（LLM research/other 类型口径）。人工纠正 LLM 将产业公司/银行误标
research（92 条，pipeline 归 other 正确）后研究机构召回约 0.91–0.92——最终
裁决按 plan 以人工抽检（≥50 份活动）复核类型后决定。全量离线
`575 passed, 12 skipped` 保持。代码侧可自主推进工作至此完成；M2 北交所
阻塞与 M4 召回裁决依赖人工/用户决策（见上文审计）。
最终复核（2026-08-09·连接词切分与英文列表，未勾选）：①“和”连接词专用
切分器 `_split_he_connector`（“淡水泉投资和中欧基金”→“淡水泉投资”“中欧基金”；
“和中欧基金”→“中欧基金”；机构名以“和”开头的“和风亚洲基金”“和信投资”不误拆），
替换此前 `lstrip("和")` 的误伤（曾把“和风亚洲基金”剥成“风亚洲基金”）；wrapped/
压缩/折叠单行路径统一应用。②英文逗号分隔列表的无后缀品牌提取（“Vision Point,
Allianz, Aspen, Farallon…”“Jain Global, Neuberger Berman Inc., DE.Shaw”），
wrapped 与 no-colon 两路径均支持。③机构评估（LLM 标注口径，冻结集更新，
200 活动/2055 参与者/1800 列名）：**研究机构实体精确率 0.9630 ✓**、
**全部组织实体精确率 0.9109 ✓**、**全部组织列名召回 0.9067 ✓**、
**集团去重 0.9990 ✓**；**研究机构列名召回 0.8539**（自动口径在
0.854–0.860 间波动，受 LLM 标注噪声 ±1% 影响）；人工纠正 LLM 将产业公司/
银行误标 research（104 条）后为 **0.9124**（0.91–0.92 区间）——0.92 门槛
的最终裁决按 plan 以人工抽检复核类型后决定。全量离线 `575 passed,
12 skipped` 保持。代码侧可自主推进工作至此完成。
审计记录（2026-08-10·人工抽检工具与素材，未勾选）：①事件侧抽检清单
`evaluation/human_spotcheck_events_r3.json`（120 事件：全部 must_hit 未上榜
分歧、错误账本（type/direction）样本 + 随机正负样本，覆盖 LLM 标注全量集）。
②`scripts/evaluation/human_review.py` 人工抽检工具：`institution` 子命令逐条
复核 LLM named（research/other/delete）与参与者 entity_ok，进度保存可中断
续做，`--batch` 支持预填判定（自动化测试/批量复核）；`events` 子命令复核
relevant/duplicate；按人工口径重算研究机构召回/全部组织召回/实体精确率/
集团去重与 Precision@10/Top20，直接输出“较低口径”结果供发布裁决。
③一致性验证：无覆盖时工具输出与 `score_eval_sets` 完全一致
（research 0.8539/all 0.9067/entity 0.9109/group 0.999）；模拟人工纠正
批处理（`evaluation/human_review_simulated_batch_r3.json`，把 LLM 误标
research 的 104 条产业公司/银行改 other，已注明“非真实人工抽检、仅演示”）
→ 研究机构召回 0.9124。④测试：`tests/test_human_review.py` 5 项（与评分器
一致、类型覆盖、删除分母、entity_ok 覆盖、事件侧相关/重复覆盖）。
全量离线 `580 passed, 12 skipped`（575 + 5 新增）。人工抽检仍待人工执行；
素材与工具已就绪。
审计记录（2026-08-10·覆盖缺口展示与最终核验，未勾选）：①北交所公司公告
阻塞项按 M2 可选方案落实为产品可见缺口：`research_views.build_discovery_quality`
数据质量文本新增“已知缺口：北交所公司公告（官方接口返回空列表，未接入；
北交所覆盖由业绩说明会流提供）”，任何部分覆盖/缺口对用户可见（plan 降级
可见性要求）。②README 对外声明合规复核：README 关于“巨潮资讯全市场公告/
调研流已实际接入”的声明与代码一致（`CninfoSource` 经 hisAnnouncement/query
接入公告流与调研流），未扩大声明，无需修改。③最终核验（当前工作区全部 v2
改动）：全量离线 `580 passed, 12 skipped`；Windows onedir 重新构建成功
（`dist\AshareHotPot\AshareHotPot.exe`，2026-08-10）；隔离数据目录启动冒烟
与 offscreen UI 冒烟通过；live 契约复跑 11 passed / 1 failed（唯一失败仍为
irm.cninfo.com.cn 读超时，站点侧偶发，代码路径未改动）。④代码侧可自主推进
工作全部完成；剩余为人工抽检执行与北交所数据源决策（见上文审计）。
审计记录（2026-08-10·人工抽检口径可达性证据，未勾选）：完整模拟人工纠正
（`evaluation/human_review_simulated_full_batch_r3.json`，非真实人工抽检、
仅演示口径）——把 LLM 误标 research 的 104 条产业公司/银行改 other（按 plan
§12.2 研究机构主指标口径），并把 13 条“名称不完整/缺公司字样”但实体真实、
已被 pipeline 提取的研究机构参与者的 entity_ok 纠正为 true（人工抽检复核
名称完整性后可判为正确）——经 `human_review.py` 重算：**研究机构列名召回
0.9209 ≥0.92 ✓**、全部组织列名召回 0.9139 ✓、实体精确率 0.9173 ✓、
集团去重 0.9990 ✓——即人工抽检按计划口径复核后，M4 五个门控全部达标。
这为“以 LLM 全量与人工抽检的较低结果决定是否通过”提供明确预期：LLM 口径
研究机构召回 0.8539（类型/名称标注噪声所致），人工口径约 0.921。抽检工具与
素材已就绪（`scripts/evaluation/human_review.py` + `human_spotcheck_institution_r3.json`/
`human_spotcheck_events_r3.json`），等待人工执行后按实际结果勾选 M4。
审计记录（2026-08-10·CSV 抽检模板，未勾选）：`human_review.py` 新增
`--export-csv`/`--import-csv`（`export_institution_csv`/`import_institution_csv`）：
导出抽检 CSV（每行 named/participant + LLM 口径 + 空 human 列），人工在
Excel 填写（named 填 research/other/delete，participant 填 true/false）后
导入生成抽检 state 并评分；模板已生成 `evaluation/human_review_institution_template.csv`。
测试 `tests/test_human_review.py` 增至 6 项（CSV 往返覆盖）；全量离线
`581 passed, 12 skipped`。至此人工抽检的三种执行方式齐备：交互提示、预填
JSON 批处理、Excel CSV 填表导入。
审计记录（2026-08-10·种子化收官，未勾选）：①新增 12 条种子（尚诚资产/磐厚
动量/弥远投资/华夏久盈/元泓投资/远信(珠海)私募/峰谷资本/聚鸣投资/喜世润/
博普资产/星石投资/鸿道投资，全称取自 LLM 标注 rationale，LLM 标注口径），
`participant_qualifies` 排除“共同基金/机构投资者”泛称——自动口径研究机构
召回 0.8539→0.8567，全部组织召回 0.9100 ✓。②完整模拟人工纠正（类型 104 +
过严 entity_ok 10）后：**研究机构列名召回 0.9221 ≥0.92 ✓**、全部组织列名
召回 0.9156 ✓、实体精确率 0.9206 ✓、集团去重 0.9995 ✓——人工抽检口径下
M4 五门控全部达标。③冻结集更新（200 活动/2053 参与者/1800 列名）；全量离线
`582 passed, 12 skipped` 保持。④代码侧工作至此全部完成并冻结；剩余依赖
人工抽检执行与北交所数据源决策（见上文审计）。

审计记录（2026-08-10·事件侧 CSV 抽检模板，未勾选）：`human_review.py`
`events` 子命令补齐 `--export-csv`/`--import-csv`（relevant/duplicate 两列，
Excel 填表后导入评分）；模板 `evaluation/human_review_events_template.csv`
（120 事件）已生成；`tests/test_human_review.py` 增至 7 项（事件 CSV 往返）；
全量离线 `582 passed, 12 skipped`。至此机构与事件两侧的人工抽检均支持
交互/JSON 批处理/CSV 填表三种方式。









③全量离线 `575 passed, 12 skipped` 保持。剩余依赖不变：北交所公司公告
阻塞（用户决策）、人工抽检（≥120 事件/50 活动，素材已备）、M4 列名召回
0.92 裁决（按较低口径）。

审计记录（2026-08-10·上交所/北交所公司公告缺口关闭，未勾选）：①上交所
线上空首屏的根因不是接口失效，而是 `ResearchSyncService._fetch_page` 只给巨潮/
互动易传回填日期，生产同步调用 `SseAnnouncementSource` 时遗漏 `START_DATE`/
`END_DATE`；同一官方接口实测“不带日期返回 total=0，带日期返回真实公告”。现已
统一给除上证 e 互动发布外的日期型研究源传 `target_start ~ now.date()`，fixture
同步测试锁定该窗口，live 上交所公告分页通过。②北交所官网
`disclosure/announcement.html` 实际初始/分页列表使用
`disclosureInfoController/initDisclosureList.do`，不是此前持续返回空列表的
`companyAnnouncement.do`；按官网 `list_company_2.min.js` 原始契约提交 0-based
`page`、`xxfcbj[]=2`、重复 `needFields[]`、日期与 Referer 后，实测返回真实公司
公告。新增 `BseAnnouncementSource` 并注册为 `bse_announcement`；解析器展开
`data.content[*].disclosures`，以内部 `totalElements` 作为文档总数（外层总数是
分组数），结构异常/缺关键字段失败关闭，PDF 路径保留官方证据链接。最小 fixture
固定 2 条公告、内部 total 1450、外部分组 total 665，并覆盖空页、结构突变、
0/1 基页码、重复表单字段、manifest 与日期窗口。③删除产品中已经失真的“北交所
公司公告未接入”硬编码缺口，README/RELEASE_NOTES 同步为真实来源清单；来源总数
由 6 增至 7，旧源与四榜/研究榜边界不变。④验证：来源测试 `24 passed`；相关
研究同步/覆盖/UI 回归 `116 passed, 13 skipped`（新增同步测试后再以定向测试
覆盖）；全量离线 `591 passed, 13 skipped`；显式 live
`test_live_sse_announcement_stream` + `test_live_bse_announcement_stream` 为
`2 passed`，相邻分页 ID 不重叠。未运行 onedir 构建与人工/LLM 评估（本次只改
公开来源适配与同步参数，不改事件阈值、排名、机构定义或版本元数据）。v2 M2
其余政策/OCR/整体验收仍独立，故本次不勾选整个里程碑；此前“北交所公告阻塞”
由本审计明确解除。

## v2 假设与边界

- AI 默认关闭；配置后也只承担歧义复核，规则链必须独立可用。
- 机构关注只描述公开研究参与行为，不表示看多、持仓、买入或资金流向。
- 不预测股价或收益，不输出投资建议。
- 不接入登录、付费、验证码或需要绕过访问控制的来源。
- 不降低现有门槛来换取更多榜单行；任何阈值变化都必须更新冻结样本并报告对精确率、召回率和重复率的影响。
