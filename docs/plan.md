> 历史设计文档，非完成声明。后续产品边界以 [product-boundaries.md](product-boundaries.md) 为准，实际交付以 [STATUS.md](STATUS.md) 为准。Prometheus 源导入和 L0–L4/冷启动分离已按用户补充前移；主动探针仍未实现。

# agent-InferTriage 开发实施计划

2026-10-24 补充：总计时边界、Agent 全业务步骤、交接与瓶颈分类属于 R02/R03/R05 的
L0/L1 细化，不推迟到 L2–L4。实施契约及后续分工见 [l0-l1-workflow.md](l0-l1-workflow.md)。

日期：2026-10-23。状态：可进入开发的计划，尚未实现或运行本文中的产品命令。

需求来源：同目录的 draft.md，以及用户确认的项目名称、全阶段多轮计量、两条诊断路径和诊断自身开销回执。

本计划独立执行，不依赖 hmz、humanize1-flow 或任何固定 Agent 编排框架。本次交付是计划文件，不代表已完成代码、模型接入、GPU 验证或部署。

## 1. 首版目标与交付边界

首版 v0.1 交付一个 Python CLI：导入历史日志/trace/标准证据包，展示 Agent 编排、Prompt、缓存、推理调度、Kernel 的全部已观测阶段与轮次，计算关键路径、诊断浪费候选、给出有条件的收益上限，并输出本次诊断自身消耗。

两条可独立调用的路径均必须交付：

| 路径 | 执行过程 | 模型调用与开销 |
| --- | --- | --- |
| offline | 本地导入、校验、计量、规则和执行图分析，生成报告 | 不调用模型；诊断新增模型 token 为 0，时间实测 |
| agent | 同样的基础分析，加上单个诊断 Agent 多轮读取日志、查询证据、调用分析工具、组织结论 | 只调用显式配置的模型；记录全部轮次、工具、重试和最终总结 |

主动探针是后续 v0.2 的独立能力，不与 agent 模式绑定。v0.1 两条路径都不向被分析服务发送测试负载。

v0.1 支持导入五层数据，不承诺从任意一份应用日志中自动采齐五层。没有调度日志或 Kernel profile 时，仍展示对应层，但状态是 unknown，列出补采项。

首版不包含自动调参、系统修改、常驻采集平台、在线代理、新 GPU profiler、模型路由、自动 Kernel 优化或多 Agent 诊断团队。

## 2. 需求到交付的对应表

| ID | 必需需求 | 实现位置 | 验收组 |
| --- | --- | --- | --- |
| R01 | 五层覆盖和未知项可见 | 数据模型、coverage、报告 | T01、T02 |
| R02 | 阶段内多轮、多调用、重试可展开 | 关联模型、分组计量、报告 | T03、T04 |
| R03 | 耗时、token、缓存与成本分别核对 | 时间模块、workload 用量账本 | T05～T12 |
| R04 | 关键路径与条件性收益上限 | graph、counterfactual | T13～T19 |
| R05 | Agent 特有浪费候选和反例 | 三组 Python 规则 | T20～T23 |
| R06 | 独立 offline 路径 | offline pipeline、CLI | T24 |
| R07 | 真正可调用的 agent 日志分析路径 | Agent 执行器、只读工具、模型适配器 | T25～T28 |
| R08 | 本次诊断 token、费用与时间回执 | diagnosis 用量账本、finalize | T29～T32 |
| R09 | 缺失证据、异常和预算耗尽有明确结果 | validation、state machine、status | T02、T18、T30、T31 |
| R10 | 无 GPU/key 可体验，真实数据可导入 | demo、OTLP 适配器、fixtures | T33、T34 |
| R11 | 实验可选，运行有预算 | v0.1 仅建议，v0.2 独立 probe | T28、B-T01～B-T04 |
| R12 | 终端/JSON/HTML 结果与用量一致 | 统一结果模型和最终快照 | T32、T35 |

## 3. 技术决策

- Python 3.12 起步，uv 管理环境与锁文件。首版支持 Linux/macOS；Windows 不作为首发完成条件。
- Python 包名 agent_infertriage，CLI 为 agent-infertriage，仓库和产品名为 agent-InferTriage。
- 使用 Pydantic 做内部模型与 JSON Schema，Typer/Rich 做命令与摘要，Jinja2 和内嵌 CSS/JavaScript 生成单文件 HTML，httpx 做唯一首版模型 HTTP 适配器。
- 配置采用 TOML，由标准库 tomllib 读取，模型配置统一使用 model.toml。
- 时间和图计算使用标准库整数运算与拓扑排序；金额使用 Decimal，JSON 中金额用十进制字符串。首版不引入图数据库、向量数据库、pandas 或通用 Agent 框架。
- 证据索引在内存中建立，持久化使用版本化 JSON/JSONL。默认最多 100 MiB 总输入与 100,000 条规范化记录；超限明确退出/产生部分回执，不静默截掉后半任务。此数值是初始资源限制，可配置，不是性能基准承诺。
- 开发验证采用 pytest、Hypothesis、Ruff、mypy。只为核心算法、协议、计量、状态机和关键产品行为编写测试。
- 默认 Apache-2.0；第三方依赖及样例来源在引入时核对并保留归属。

具体依赖版本在 M0 实际创建工程时解析并锁定。本计划不将未验证的“最新版本”写成兼容性承诺。

## 4. 输入、命令与产物约定

### 4.1 首版输入

1. 原生 evidence 包：manifest.json + typed events.jsonl，允许引用选定的日志/内容片段；可包含五层时间、用量、缓存、成本和显式活动依赖。
2. 首个外部适配器：保存的 OTLP/HTTP JSON trace 请求体，或每行一个请求体的 JSONL。通过显式字段映射归一化 GenAI 与自定义属性。
3. 原始文本日志附件：可供 agent 路径按行读取/搜索；没有确定解析规则的文本只作为定性证据，不能自动变成精确耗时或因果关系。

OTLP 支持 JSON 编码，时间戳是纳秒字段，64 位值需要精确解析，trace/span ID 使用十六进制；不能按普通浮点数读入再转换。[OTLP 编码规范](https://opentelemetry.io/docs/specs/otlp/#json-protobuf-encoding)

GenAI 属性映射按适配器测试过的规范/导出器版本管理；原始属性保留，不接受“所有 OTel Agent trace 均完整支持”的说法。[GenAI 语义约定](https://github.com/open-telemetry/semantic-conventions-genai)

### 4.2 CLI 冻结接口

以下为待实现命令；输入与输出参数含义以此为准：

```bash
# 无模型、无 GPU 的标准演示
agent-infertriage demo --output-dir results/demo

# 标准证据校验，不发网络请求
agent-infertriage validate evidence/

# 外部 trace 导入；配置中提供经过核实的字段映射
agent-infertriage import traces.json --format otlp-json \
  --mapping mapping.toml --output-dir evidence/

# 离线模式默认可省略 --mode
agent-infertriage analyze evidence/ --mode offline \
  --price-file prices.toml --output-dir results/offline

# Agent 分析历史日志，仍不发送主动探针
agent-infertriage analyze evidence/ --mode agent \
  --model-config model.toml --output-dir results/agent

# 单份普通日志可直接分析，无需用户先手写证据包
agent-infertriage analyze session.log --format raw-text --mode agent \
  --model-config model.toml --output-dir results/log-analysis

# 根据落盘事件恢复已知开销和部分结果，不重发模型请求
agent-infertriage recover results/interrupted
```

价格文件可省略，省略时成本为 unknown/unpriced。输出目录需新建，不能覆盖原日志或已有诊断；recover 是对该诊断产物的显式恢复操作。传入多任务证据时必须选择 workload 或明确使用 --all；每个任务独立出结果，诊断共享开销在父级回执中只计一次。

--format raw-text 为选定文件创建临时来源清单，沿用相同索引与只读工具，不自动扫描上级目录。没有结构化计量时只展示可引用的日志证据、定性候选和五层 unknown，不宣称已恢复全部阶段。offline 对 raw-text 同样不调用模型，只输出可确定的基本信息与数据不足结果。

退出码：0 为诊断过程完成，允许报告有未知项或性能问题；2 为输入/配置错误；3 为预算耗尽或证据/执行中断导致的 partial；1 为其他执行失败；可捕获的用户中断返回 130 并保存已有回执。

### 4.3 结果目录

```text
results/<diagnosis_id>/
  report.json                # 规范化的 workload 分析 + diagnosis 回执
  report.html                # 同一结果的离线报告
  diagnosis_usage.json        # 最终开销快照，便于单独读取
  diagnosis_events.jsonl      # 调用/工具/状态变化的追加事件
  evidence_index.json        # 来源文件/行号/JSON Pointer/摘要
  agent_steps.jsonl          # agent 模式的模型与工具操作记录
```

日志正文默认不全文复制进 HTML；只显示必要且经配置允许的片段。所有结果均注明源数据标识、规则配置摘要、schema 版本和工具版本。运行 ID、时钟与诊断用时是非确定字段，不能据此要求两个离线报告逐字节相同。

## 5. 模块与调用关系

```text
cli
  → diagnosis session + event journal
  → adapters → normalized evidence → validate + index
  → deterministic analysis
      → coverage + per-round metrics
      → workload usage/cache/cost
      → execution graph + counterfactual
      → rules + findings + priority
  → offline result
    或 agent loop → read-only analysis tools → cited interpretation
  → final usage snapshot → terminal / JSON / HTML
```

建议工程结构与职责：

| 路径 | 职责 |
| --- | --- |
| src/agent_infertriage/models/ | evidence、metric、finding、report、diagnosis 模型与版本 |
| adapters/native.py、otlp.py | 导入，保留原始证据指针；不作病因判断 |
| evidence/index.py、validation.py | ID 去重、引用检查、时钟与覆盖校验 |
| analysis/timing.py、grouping.py | 区间计算、五层关联、多轮明细 |
| analysis/usage.py、cache.py、cost.py | workload 用量归一化、缓存口径、费用 |
| analysis/graph.py、counterfactual.py | 执行图、关键路径、情景重算 |
| rules/context.py、repetition.py、prefix.py | 三组规则与其反例语义 |
| diagnosis/journal.py、ledger.py、session.py | 诊断自身调用事件、预算、最终回执 |
| agent/runner.py、tools.py、provider.py | 有界分析循环、只读工具、模型协议 |
| reports/build.py、terminal.py、html.py | 统一结果渲染；不向模型请求“补数字” |
| cli.py | 组装流程和退出码，无核心计算 |
| schemas/、examples/、tests/、docs/ | 对外协议、样例、验收与接入说明 |

核心分析接口固定为 analyze(EvidenceSet, AnalysisConfig) → AnalysisResult。模型适配接口固定为 invoke(messages, tools, limits) → ModelResponse；所有请求必须经 diagnosis ledger 包装后调用。

## 6. 证据协议 v1

### 6.1 包和基本对象

manifest.json 至少含 schema_version、workloads、sources、clock_domains、content_policy。每个 source 有 ID、格式、路径、版本/导出器信息和允许读取的内容范围。相对路径相对 manifest 解析，工具只访问这些源。

原生 events.jsonl 每行一个带 record_type 与 record_id 的记录。首版定义：

| 对象 | 关键字段 | 语义 |
| --- | --- | --- |
| Workload | workload_id、root_span_id、start/end、时钟域 | 一次被分析任务，不是诊断运行 |
| Span | span_id、parent_id、operation、agent_id、start/end、source_refs | 描述观测与嵌套，不自动代表调度依赖 |
| Association | workflow_round_id、stage_occurrence_id、request_id、attempt_id、batch_id、kernel_invocation_id | 多对多关联，不复制活动或用量 |
| Activity | activity_id、scope_span_id、duration_ns、stage_tags、可选 start/end 与 release_ns | 可用于反事实模型的非重复计时单位；固定释放默认 0 |
| Dependency | from/to、kind、provenance、source_refs、可选 min_lag_ns | 显式执行依赖；与 contains/correlates 分开 |
| RequestUsage | request/attempt、provider/model、raw_usage、规范化字段 | 请求的用量所有者 |
| CacheSample | cache_type、scope、hit/query、occupancy/capacity、时间与单位 | 区分计数、率和容量占用 |
| CostItem | owner_event、amount、currency、pricing_ref、method | 独立收费事件或估算，不混为同一总量 |
| ContentBlock | owner_ref、role、token_count、tokenizer_ref、可选内容/指纹 | 供上下文和前缀规则使用 |

首版先在 M1 固定字段名和示例，活动图可选。这样普通 OTLP trace 即使无显式执行依赖，也可以展示观测、用量和规则，不必伪造图才能进入分析。

### 6.2 MetricValue 与缺失语义

所有关键数值采用统一值对象：value、unit、status、method、scope、source_refs、reason。status 取 reported、derived、estimated、unknown、not_applicable；value 在后两种情况下为 null。

coverage 独立按 orchestration/prompt/cache/scheduling/kernel 输出 observed、partial、unknown、not_applicable，附 missing_evidence 与 collection_hint。精确数值、字段覆盖和证据充分性不能用同一个布尔值代替。

数值聚合遇到未知项时保留 known_subtotal、unknown_count 和覆盖分母，严格 total 留空或标为 partial。0 只用于观测或确定推导为零的事件。

### 6.3 标识、时钟和去重

- workload 内的源 ID 按 source namespace 保存。确知同一请求/attempt 时合并证据；只靠相近时间或相同 Prompt 不自动合并。
- retry 保留新的 attempt；同一 attempt 的 usage 更新、流式最终 usage 和代理副本只归并一次。
- 内部时间使用整数纳秒，源单位写在元数据；前端展示相对毫秒/秒，不能用 JavaScript 浮点数直接保存大 Unix 纳秒整数。
- 跨进程/设备时钟必须有已声明的对齐信息和误差。无对齐仍可展示各自局部持续时间，但不构建精确跨域关键路径。
- workflow_round、retry attempt、engine iteration、kernel invocation 分字段保存。没有轮次 ID 时保留 unknown；展示序号可以生成，但必须标记为 display_index，不能冒充真实 round。
- 共享 batch/kernel 按独立实体保存，关联多个请求。缺少分摊模型时不生成独占的逐请求 GPU 时间或成本。

### 6.4 适配器支持声明

OTLP 解析 resourceSpans/scopeSpans/spans、attributes、events、links、status 与整数时间。parentSpanId 只建 contains；links 保留来源，不自动转换为 depends_on。GenAI 用量/模型/工具字段通过 mapping.toml 映射，轮次和执行依赖仅接纳来源明确的自定义属性或外部 sidecar。

适配器输出 ImportReport：识别/忽略字段、规范版本、缺失字段、无支持语义、读入/拒绝记录数和来源引用。未知 OTLP 字段保留原始引用且不使整包失败；不支持的原生 schema 主版本则拒绝。

## 7. 全阶段、多轮时间与资源计量

### 7.1 时间口径

- elapsed/inclusive：单个 span 的 end-start。
- self/exclusive：同一时钟域、完整包含子活动时，父区间减去子区间并集；不是减去所有子时长之和。跨边界子 span 需校验，不能默默剪裁后称精确。
- cumulative：活动持续时间之和，可大于任务 wall time，必须注明可能并行。
- active_union：指定 scope 内同类活动区间并集长度，不等于反事实改善量。
- critical contribution：仅在第 9 节模型通过资格校验后提供，并说明是固定图情景。
- 未归因时间按具体 scope 显示；没有完整事件时不能将全部 uncovered 区间自动命名为 Agent 开销。

客户端 TTFT、TPOT/ITL、总请求延迟与服务端 Prefill/Decode、排队时间分别保存，不能互相替代。输出片段不保证是一枚 token；没有 token 边界就报告 chunk gap，不伪称逐 token ITL。vLLM 的请求 TPOT 与流式输出事件 ITL 也有不同聚合语义。[vLLM 延迟指标说明](https://github.com/vllm-project/vllm/blob/main/docs/design/metrics.md)

### 7.2 五层报告与轮次聚合

固定五层行，每层展示 coverage、活动次数、轮次、elapsed/cumulative/union/self 可用项、关联 token、缓存量和成本。Prefill 可以被多层标签引用，但全任务活动和 token 都按独立 owner ID 去重。

查询维度固定为 workload → agent/sub-agent → workflow round → stage occurrence → request/attempt，并可链接 batch/engine iteration/kernel。每个阶段实例提供全部子轮次/调用，不用一条均值替代。缺少 token 计费语义的 Kernel 独立 token 为 not_applicable，可链接关联请求用量。

样本分布使用任务/请求样本计算，注明 sample_count 和 percentile 方法；首版采用 nearest-rank，可配置后仍需记入报告。缓存率汇总加分子/分母，不能平均百分比；p95 不按阶段求和。

## 8. workload 的 token、缓存和成本

### 8.1 TokenUsage 归一化

规范化 input_tokens 包含输入中的缓存 token 一次；output_tokens 包含 reasoning 子集一次。total=input+output 仅在两项覆盖充分且语义相容时可精确计算。raw_usage 永久保留，额外保存非缓存输入、cache_read/write、reasoning 与来源语义。

每个 source 的 usage adapter 在 M3 有独立测试。若原 provider 把 cache read/write 排除在原始 input 外，适配器按明确语义组合；不能仅凭字段名字猜。无法判定时字段保留 unknown，不跨口径相加。

### 8.2 缓存

分别报告 Prompt Cache 命中 token/请求、缓存读写计费、KV 占用和工具缓存。每种量记录 denominator、unit、scope、interval 和 source。没有采样到逐轮 KV 状态时显示区间粒度，不能复制全局均值到每个 round。

### 8.3 成本

prices.toml 按 provider/model/currency/effective_at 定义每百万 token 的输入、输出、cache read/write 价格。普通输入是否已包含缓存折扣由 pricing scheme 明确；字段不足以分桶时不计算伪精确费用。

使用 Decimal 计算、显示时舍入，原始金额保留。账单金额与按价目估算分列；不同货币不自动换汇。同一事件的按 token 与按 GPU 估算是替代口径，默认不相加。工具/API 单独记收费事件。

GPU 估算仅在用户提供 GPU 时价、时间和分摊依据时启用；共享批次无分摊信息则保留整段资源费用、逐请求 unknown。Kernel 成本是父资源成本的分解，不是新增一笔收费。

workload 与 diagnosis 分开计量；缓存 token 是其所属请求用量子集，不能被当成又一次调用。

## 9. 执行图与反事实算法

### 9.1 可计算模型的资格

首版只对显式活动和 observed/declared 的执行依赖计算可用于排序的精确情景。inferred 边只用于探索展示，精确全任务收益留空。

嵌套 span 先保留为描述图；活动图按同一物理执行的互斥分区建模。存在完整内部分解时，父 inclusive span 不再作为可加权活动；只有粗粒度 LLM span 时将它视为一个不可再分的活动，不能凭想象拆成 Prefill 与 Kernel。

资格检查包括：无环、无缺失关键依赖、权重非负、时钟对齐、无重复计时、等待/释放条件有来源，以及活动覆盖能解释任务开始与结束。只匹配总时长但缺乏真实依赖的图不算通过。

### 9.2 固定图求解

为活动 u 设权重 w[u]；边上的非零最小等待只接纳来源明确的约束，不从任意时间间隙自动推断：

```text
ready[v]  = max((finish[u] + declared_lag[u,v] for u in predecessors[v]), default=0)
start[v]  = max(release_ns[v], ready[v])
finish[v] = start[v] + w[v]
T         = max(finish[sink])
```

外部已知释放时刻通过相对任务起点的固定释放节点建模，情景中保持不变。已知睡眠/工具等待用有证据的活动表示。未知 gap 只显示为未归因，不为拟合基线填造因果边。

与观测完成时间比较，初始校验容差为 max(1ms, 观测 wall time×1%)，加上已声明时钟误差，并在结果中输出具体容差与 residual。这是工程校验阈值，不是置信区间；大误差、未解释 gap 或不完整关键依赖均阻止精确上限。

通过后用拓扑动态规划计算一条关键路径及多条等长路径存在标志，不枚举指数级全部路径。单次求解 O(V+E)。

### 9.3 情景重算

指定活动集合 S，支持 eliminate（w'=0）或 speedup=s（w'=w/s），s 必须是有限且≥1 的数。情景权重使用 Decimal 计算后按半入方式舍入到整数纳秒，并记录精度；不能出现负权重。保留依赖、外部释放、其他活动与资源约束，重新求 T'：

```text
saved_ns = T - T'
reduction_fraction = saved_ns / T
speedup = T / T'
```

T=0 时比例为 not_applicable；T'=0 时标记 all_modeled_duration_removed，不输出无法序列化的 Infinity。单项上限不能相加；组合情景必须把集合并集后重算。

图不合格时允许对完整局部 scope 计算“局部情景”，但不将局部比例写成全任务改善率。可计算的整个 LLM 改善空间只能标为 LLM scope 上限，不能改名成 Kernel 或缓存收益。

输出包含 model_id、scope、selected_activity_ids、baseline、scenario、saved、比例、assumptions、coverage、baseline_error 和 evidence_refs。动态排队重算、GPU 资源再调度和行为变化不属于首版模型。

## 10. 规则与投入排序

三组规则均为 Python 实现，返回 Finding；每条都有正例、反例、缺证据行为和数值来源。

| 规则 ID | 初始触发条件与产物 | 收益与反例 |
| --- | --- | --- |
| context_growth | 同一会话至少 3 次可比 LLM 调用；首末输入增长≥1.5 倍且净增≥2,048 token，报告逐轮增长 | 阈值是可配置启发式；必要长上下文不判无用，默认时间收益 unknown |
| tool_result_amplification | 已知来源工具结果在后续≥2 次请求中重复携带，报告实际出现次数、token 和缓存证据 | 缓存输入不按全价重复计费；没有 block token 证据不捏造精确量 |
| repeated_tool_call | 同 scope 相同工具/参数指纹重复，定位到 attempt 与资源版本 | 状态刷新/轮询/文件版本变化不标为可删除；幂等性充分时才申请情景计算 |
| retry_no_progress | 至少 3 次可关联的重复失败或来源声明的无进展重试 | 正常恢复性重试只作观测；删除调用不保证任务正确性 |
| prefix_instability | 至少 3 次请求，在可比内容/指纹中找到稳定前缀被动态块打断 | token 级精确性需相同 tokenizer/template；无 usage 只输出缓存风险 |
| prefix_reuse_gap | 有稳定前缀机会且存在同口径真实 cache usage，可报告机会和命中的差异 | TTL/驱逐/路由/隔离为反证，仍不仅凭相关性断言因果 |

阈值存 rules.toml，报告带配置摘要。字符、字节、token 和请求缓存语义分开；不使用“输入很长所以输出无价值”等语义猜测。

Finding 固定字段：id、rule_id、scope_refs、evidence_refs、fact_metric_refs、owner_layers、evidence_level、confidence_reason、counter_evidence、unknowns、benefit、quality_risk、next_check。

优先级列表分“可计算候选”和“待补证据”，按用户选择的同一目标分别展示。首版默认时延；token 和费用另列，不生成跨量纲的万能分数。可计算候选按 saved_time 上限排序并展示证据等级，不称其为可兑现承诺；未知层不作为 0 收益排末尾。

v0.1 可生成 NextProbePlan：假设、区分目标、最小输入、需要观测的信号、预计请求/token/时间和副作用。不能执行；执行器留给 v0.2。

## 11. Agent 日志分析执行器

### 11.1 最小实现

实现单个、有界、串行的分析循环，不引入外部 Agent 框架。首版模型适配器支持配置的 Chat Completions 兼容 HTTP 服务，既可连接本地推理也可连接远程 endpoint。模型名由配置给定，不自动下载/启动模型，不内置价格或默认付费模型。服务兼容性通过实际契约测试确认；兼容某协议不等于支持任意厂商所有扩展。[vLLM 兼容服务说明](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/)

优先采用非流式请求，简化最终 usage 收集。首版可用文本 JSON action 协议，不要求后端具备原生 function calling。模型响应只能是以下两种结构之一：

```json
{"action":"tool","name":"get_round_metrics","arguments":{"round_id":"r3"}}
```

```json
{"action":"finish","finding_refs":["f1"],"hypotheses":[{"text":"需检查前缀变化","evidence_refs":["e17"],"metric_refs":["m22"]}],"next_checks":[]}
```

action 由 Pydantic 校验后派发，不执行模型输出的 Python、shell、SQL 或任意网络请求。模型输出格式错误允许一次纠正请求，该调用也记账；不支持循环“直到 JSON 正确”。

### 11.2 分析工具白名单

| 工具 | 输入/产物 | 限制 |
| --- | --- | --- |
| list_sources | source 元数据、格式与覆盖 | 仅 manifest 中来源 |
| read_log | source_id、行范围 → 带行号片段 | 每次最多 200 行/16 KiB，可翻页 |
| search_log | source_id、文字与分页 → 命中位置 | 首版字面量搜索，避免任意正则资源开销 |
| get_coverage | scope → 五层证据覆盖与补采项 | 不触发采集 |
| get_round_metrics | agent/round/stage → 规范化计量 | 返回 metric_id 与来源 |
| get_request_usage | request/attempt → token/cache/cost | 不接受模型提交替代数值 |
| get_critical_path | scope → 已有图分析/不合格原因 | 使用统一算法 |
| evaluate_scenario | 已有活动 ID、eliminate/speedup → 条件结果 | 拒绝未知活动、负权重和任意改依赖 |
| get_findings | rule/scope → 已有 finding | 可查反证与缺失项 |

日志是待分析数据，不是运行指令。对未结构化日志，Agent 可提供有行号引用的候选解释，但不能由模型自行录入一个“观测到的 Kernel 时间”进入事实账本。

工具每次响应附 total_count、returned_count、truncated、continuation、evidence_refs；截断是可见的，避免 Agent 误认为已读完整日志。首版不用 embedding 或隐式 LLM 摘要，大日志先使用确定性过滤和分页。

### 11.3 状态机与预算

```text
created → ingesting → analyzing
                    → offline: building_report
                    → agent: requesting_model → validating_action → running_tool
                                  ↑_______________________________|
                               或 finishing
                    → building_report → finalizing_usage → completed
任何阶段可转入 partial / failed / cancelled，再尽可能 finalize 已知回执
```

初始预算默认值：最多 6 轮 action/finish、8 次真实 HTTP attempt（包括纠正和重试）、24 次工具调用、每次模型输出最多 1,024 token、全诊断 300 秒、单模型请求超时最多 45 秒且不超过剩余时间。模型上下文估算上限为每次 12,000、诊断累计 40,000 token。这些值是可覆盖的初始工程配置，不声称适合所有模型。

有匹配本地 tokenizer 时按完整待发送 messages 计数；没有时用标注为估计的本地计数。每次请求前检查预算、输入长度和剩余时长；输入、输出预算采用预留最大输出的方式控制。服务端隐藏 token 或估计偏差可能导致 usage 超出预估，必须如实记账并停止后续调用，不能声称绝对精确的 token 硬上限。

provider 默认关闭 HTTP 库的隐藏自动重试，由 runner 最多显式重试一次可重试传输错误/限流，每次使用新 attempt_id，且占总调用预算。无响应但可能已发送的 attempt 为 usage_unknown。截止时间到了就返回已有确定性分析与部分解释，不为生成总结额外突破预算。

### 11.4 有依据的输出

Agent 负责检索顺序、假设与解释。最终数值来自 metric/scenario 引用，由程序渲染；模型生成的 owner、证据引用、活动集合必须校验存在性与 scope。

最终结果把“确定性 findings”与“Agent hypotheses”分开。未被工具支持的解释只能标为 unverified，不升级为 observed 或实验确认。模型自由文本不作为精确数字、收益排名或费用的权威来源。

若最终结构无效、引用不存在或模型不可用，仍输出基础分析与失败/partial 状态，保留模型实际已发生的调用开销。不能偷偷退回 offline 并报告整个诊断 token=0。

## 12. diagnosis 用量、事件和恢复

### 12.1 两个账本及用途分类

workload ledger 只从被分析任务的源数据产生。diagnosis ledger 只记录当前 CLI/session 新产生的事件。可选 probe 属于 diagnosis 下的 purpose=probe，不能混入历史 workload。

诊断 ID 在读输入前生成；每个模型、工具和未来 probe 使用独立 event_id、attempt_id、purpose、monotonic start/end、源模型/endpoint标识。模型请求发送前先保存 prepared/sending 事件，返回后记录 completed/failed 和 raw usage。

同一 attempt 的多个状态事件更新同一条账，不累加多次用量。最终模型总结也通过相同入口，禁止 report renderer 私下调用模型。

### 12.2 Receipt v1

```text
identity: diagnosis_id, receipt_id, workload_ids, mode, config_digest
status: completed | partial | failed | cancelled | incomplete
time: started_at, ended_at, wall_time_ms, model_cumulative_ms,
      tool_cumulative_ms, measurement_scope
counts: analysis_rounds, model_attempts, tool_calls, retries, failures
tokens: input, output, total, cache_read, cache_write, reasoning,
        known_subtotal, reported_calls, estimated_calls, unknown_calls
cost: by_currency, analysis_model, tool_api, probe, known_subtotal,
      unpriced_items, pricing_refs
coverage: completeness, unknown_reasons, unassigned_items
```

total 仅在覆盖充分时精确输出；否则 value=null，并显示已知小计/估计总量。无模型调用时精确为 0；没有响应 usage 的失败调用不是精确 0。cache/reasoning 子集不再次加到 total。

### 12.3 时间测量与最终快照

wall_time 用本地单调时钟从诊断入口起计，包含读取、解析、计算、全部 Agent 循环、可选探针及报告主体生成，止于 final receipt snapshot。UTC 时间用于可读的 start/end，不用于测 duration。

先完成所有模型 usage 记录与报告主体构建，再冻结 receipt。终端、JSON、HTML 都引用同一 receipt_id 与内容摘要；只在最后替换固定的回执区域/序列化最终对象，不再请求模型。measurement_scope 明确末次回执编码、文件 flush 和终端刷新的尾部 I/O 不含在快照内。

模型累计时间与工具累计时间是事件时长和，不与 wall_time 混淆。其中 model_cumulative_ms 是客户端模型请求 attempt 的累计耗时，不是服务端纯模型计算时间。解析/本地查询也计入诊断时间，即使它们不消耗模型 token。

### 12.4 异常和恢复

session 在 finally 路径生成回执；JSONL 状态事件顺序追加，在发送请求前与取得 usage 后进行必要持久化。report.json、diagnosis_usage.json 采用同目录临时文件完成后原子替换，避免半写文件被当成完整结果。

recover 只读取已保存事件，合并同 attempt 的状态，标出 sending 但无终态的未知调用。恢复过程不重发请求；原诊断已知开销与恢复操作耗时分栏，不把恢复当作原调用的一部分。强制终止可能丢失最后 usage，receipt 必须保留 incomplete。

若输入尚未能解析就失败，workload 部分可以为空，但 diagnosis 回执和错误信息仍可输出到用户指定的结果目录。磁盘完全不可写时至少尽力输出终端回执，不能保证文件已保存。

## 13. 报告设计

终端与 HTML 首屏按相同顺序组织：

1. 被分析任务的总体 wall time、轮次、token、缓存与成本，显示已知量和缺失量。
2. 五层 coverage 与可展开的 Agent/round/stage/request 明细，明确 inclusive、cumulative 和 union 的口径。
3. 时间轴与依赖关系，能从 finding 点击/定位到具体证据；共享 GPU 活动单独泳道展示。
4. 有收益上限的候选列表，以及独立的待补证据清单；显示推翻结论所需条件。
5. Agent 模式的检索/工具/模型逐轮操作与结论引用；不要求暴露模型内部思维链。
6. 本次 diagnosis 用量回执：token、耗时、费用、轮次、失败/未知用量，始终可见。

HTML 采用表格、可折叠明细和轻量 SVG/Canvas 时间轴；不引入云端图表依赖。所有输入按文本转义，嵌入 JSON 不能通过 </script> 逃逸；禁用将日志直接作为 HTML 渲染。

分页/筛选只改变视图，不改变总量。报告中每个数字能追到 metric_id → source refs；所有估计注明模型和假设。屏幕顶部不以单一“健康分数”覆盖大量 unknown。

## 14. 开发里程碑与完成条件

以下任务按依赖顺序执行，全部属于首版。每个任务形成可审查的提交/变更集；本计划不要求使用特定工作流、模型或多人协作。

### M0：建立工程与冻结样例答案

依赖：无。

- 创建包、CLI 空入口、uv 配置、Ruff/mypy/pytest 基础配置和许可证。
- 建立 fixtures 目录与 expected.json；先写手算案例答案，再实现算法。
- 确定 source/version、record_type、MetricValue 与 Receipt v1 的文档草案。

产物：pyproject.toml、uv.lock、最小 CLI、docs/data-contract.md、tests/fixtures 的案例清单。

完成条件：可安装和运行 --help；类型/格式检查入口可用；fixture 有独立预期，不靠当前程序生成答案。

### M1：证据模型、原生输入与校验

依赖：M0。

- 完成第 6 节模型、JSON Schema 和 native reader。
- 实现 ID、引用、单位、重复事件、负时长、循环依赖和资源限制校验。
- 建立证据索引、内容范围与五层 coverage；未知字段和记录拒绝情况可追溯。

产物：models/、adapters/native.py、evidence/、schemas/evidence-v1.json。

完成条件：完整五层包和稀疏包都能导入；重复 ID 冲突明确报错；无轮次字段不会补造真实轮次；T01～T04 基础通过。

### M2：多轮时间计量与 OTLP 适配

依赖：M1。

- 实现区间并集、self time、累计活动时间、逐轮与共享批次关联。
- 实现 OTLP JSON/JSONL reader 和 mapping.toml，保留源指针。
- 创建一个实际执行的多轮程序导出 trace：顺序 LLM/工具与并行子任务，暂可用可控函数代替真实模型，明确标识 synthetic workload；验证 exporter 导入链路，不能将其宣传成模型/GPU 实测。
- 留存一个经允许的真实 Agent trace 接入说明与导入检查，列出可得/缺失字段。

产物：analysis/timing.py、grouping.py、adapters/otlp.py、examples/otel/、docs/import-otel.md。

完成条件：同阶段多轮不丢失；父子不重复计时；OTLP parent 不自动变依赖；真实 trace 导入状态有记录；T05、T06、T33 通过。

### M3：workload 用量、缓存与成本

依赖：M1；分组展示依赖 M2。

- 实现请求/attempt owner、raw/normalized usage、去重和缺失总量。
- 实现缓存分母/单位校验、分轮比率与总体加权汇总。
- 实现 prices.toml、Decimal 定价、不同货币分列、缓存分桶和共享 GPU 成本口径。

产物：analysis/usage.py、cache.py、cost.py、schemas/prices 示例、用量核对结果。

完成条件：1,000 输入含 800 缓存、100 输出含 60 reasoning 时总量为 1,100；未知价格不为零；跨层引用不重复记账；T07～T12 通过。

### M4：关键路径与情景上限

依赖：M1、M2。

- 实现活动建模、依赖与时钟资格检查、基线误差校验。
- 实现 DAG 最长路径、局部/全任务 scope、eliminate/speedup 和组合情景。
- 对零基线、零剩余耗时、等长关键分支、固定外部释放与未知 gap 明确处理。

产物：analysis/graph.py、counterfactual.py、docs/counterfactual.md。

完成条件：第 9 节公式与所有手算案例一致；仅有包含关系时不给精确全局收益；T13～T19 通过。

### M5：诊断规则与排序

依赖：M2、M3、M4。

- 实现三组六条规则、规则配置、反证、缺证据行为和稳定 finding ID。
- 规则引用计量与情景服务，不自己重复实现费用或图计算。
- 实现候选/未知两类清单及 NextProbePlan 的只读输出。

产物：rules/、schemas/finding-v1.json、默认 rules.toml。

完成条件：每组阳性、阴性、证据不足均有答案；独立 review、轮询、文件变化不被直接标可删除；T20～T23 通过。

### M6：诊断 session、账本和 offline 闭环

依赖：M1～M5。

- 实现开始/结束事件、attempt 归并、usage/费用回执、截止时间和 recover。
- 串联 offline analyze，先导出 JSON 与终端摘要。
- 用禁网测试保证 offline 的推理调用数为零；输入错误也保存诊断时间和状态。

产物：diagnosis/、offline pipeline、report.json、diagnosis_usage.json。

完成条件：原任务 100K token 与诊断 0 token 正确分开；partial/error/cancel 处理可核对；T24、T29～T31 通过。

### M7：Agent 路径与模型适配器

依赖：M5、M6。

- 实现第 11 节单 Agent 状态机、白名单工具、分页与 JSON action。
- 接入兼容 HTTP 模型协议，所有 attempt 通过 ledger；禁用隐藏重试。
- 实现预算预留、格式纠正、无 usage、最终总结纳入回执及确定性 fallback。
- 提供 model.example.toml，以 endpoint/model/api_key_env/limits 配置；密钥不写入报告或运行事件。

产物：agent/、model 配置示例、agent_steps.jsonl、真实适配契约测试。

完成条件：mock 覆盖多轮读日志→查指标→情景计算→结束；一次显式的真实模型冒烟验证记录实际 usage。若未提供模型环境，仅可标“实现与 mock 已验证，live 未验证”，不能将真实接入写为已验收；T25～T28 通过。

### M8：统一报告与双路径演示

依赖：M2～M7。

- 完成单文件 HTML、时间轴、五层覆盖、逐轮展开、证据定位和诊断回执。
- 完成 demo、validate/import/analyze/recover 命令语义和退出码，包括 raw-text 直接输入。
- 报告数值由统一 AnalysisResult 渲染；Agent 只补有引用的解释。

产物：reports/、demo 报告、离线与 Agent 模式的对照样例。

完成条件：禁网可打开 HTML；所有轮次可查看；终端/JSON/HTML 同一回执；输入脚本字符串不执行；T32、T34、T35 通过。

### M9：真实案例、文档与首版验收

依赖：M0～M8。

- 接入至少一个经允许的多轮 Agent trace，保存导入结果、覆盖范围和解释；不要求用户公开敏感内容。
- 发布可分发的 synthetic 五层正例与稀疏反例，真实案例与模拟数据明确标识。
- 完成英文 README、中文说明、数据接入、价格/模型配置、证据限制、计量与恢复说明。
- 执行第 15 节测试与双路径冒烟；记录 live 模型、真实引擎验证的实际状态。

产物：首版候选包、CHANGELOG、测试结果、examples/、docs/compatibility.md。

完成条件：R01～R12 的首版部分全部有可复查证据，且第 17 节完成清单通过。此阶段不自动发布到 PyPI、创建远程仓库或部署。

首个开发切片应为 M0→M1→M2 的 schema/时间闭环；完整 v0.1 仍必须完成 M7 的 agent 路径，不能把中间 offline 原型当作满足全部需求的发布版。

## 15. 验收测试矩阵

expected 值应由手算或独立输入定义，禁止先执行实现再将其输出无审查地存成 golden。以属性测试补充边界，但不能只断言“调用了某内部方法”。

| 测试 | 输入或触发 | 必须成立的结果 |
| --- | --- | --- |
| T01 | 完整五层证据包 | 五层、各轮、关联 token/cache/cost 可见；同请求只计一次 |
| T02 | 仅客户端 trace | 调度/Kernel 为 unknown，0 与 N/A 有区别，显示补采项 |
| T03 | 3 个 workflow round，每阶段每轮 2 次调用 | 保留 6 次明细，按轮与阶段聚合可核对 |
| T04 | 子 Agent、retry、decode step、共享 kernel | 各类 ID 独立，导航不复制设备活动或收费 |
| T05 | 父 20 秒，子区间 [0,12] 与 [8,20] | 子区间并集为 20 秒，父 self 为 0，不减成负数 |
| T06 | ns/us 输入、跨时钟未对齐、子事件越界 | 单位正确；对齐/越界不充分时相关精确量停用 |
| T07 | 输入 1000 含 800 缓存，输出 100 含 60 reasoning | 总 token=1100；子集不再相加 |
| T08 | 同 attempt 三来源副本，另有一次 retry | 副本合并，真实 retry 的用量单独计入 |
| T09 | 缓存命中 1/1 与 0/9 | 同口径总体 1/10，不是平均百分比 50% |
| T10 | Prompt 命中、KV 占用与工具缓存共存 | 不合成无分母的总体 cache rate |
| T11 | 有 token 无价格，部分调用有价格 | 严格总费用 unknown，已知小计和未计价项目可见 |
| T12 | 两货币、按 token 与 GPU 替代口径、Kernel 分解 | 不自动汇率换算，不重复相加 |
| T13 | 两活动顺序 10 秒+15 秒 | T=25，消除前者 T'=15，省 10 秒 |
| T14 | A=10、B=15 同时执行后汇合 | T=15；消除 A 省 0；B 两倍加速 T'=10，省 5 |
| T15 | 两条等长关键分支 | 缩短单条不错误宣称整体改善 |
| T16 | 明确固定外部释放与顺序活动 | 加速前序不穿越固定释放约束，外部等待被保留 |
| T17 | 零耗时模型、全部活动被消除 | 无除零/Infinity JSON；比例语义明确 |
| T18 | 循环、缺失依赖、仅 contains、拟合 residual 超限 | 不输出伪精确全任务上限，错误与部分结果明确 |
| T19 | 重叠情景与随机非负 DAG | 联合情景重算；同固定图减权不增加 T；0≤saved≤T |
| T20 | 上下文逐轮增长、工具结果重复与必要长输入 | 事实可追溯；必要长输入不被直接判无用 |
| T21 | 重读同版本与重读新版本、轮询、独立 review | 候选与正常行为分开；不直接删除调用 |
| T22 | 前部动态块、已命中缓存、不同 tokenizer/TTL | 风险、实测命中和反证分开，无 unsupported 因果结论 |
| T23 | 只有总 token 无内容，无确定工具参数版本；或直接输入普通日志 | 结构化已知总量仍保留；普通日志可引用行号，无法确定的归因/收益为 unknown |
| T24 | 原任务 100K token；offline 禁网执行 | diagnosis token=0、有实测 wall time；任何模型网络连接使测试失败 |
| T25 | mock Agent 读日志→查轮次→算情景→finish | 真实循环被执行；所有工具响应与结论有 evidence/metric refs |
| T26 | 无效 JSON、未知工具/ID、日志内恶意指令 | 有界纠正或退出；不执行任意命令，伪指标不入事实账本 |
| T27 | 轮次/调用/时长/输出预算到限 | 下一请求不再发出；已有用量和 partial 回执仍返回 |
| T28 | agent 未启用 probe，模型尝试访问未声明源/endpoint | 只准分析模型与白名单工具，不发送测试流量 |
| T29 | 两次分析调用+一次最终总结+一次传输 retry | 四次真实 attempt 都进入 diagnosis；相同 attempt 的 usage 更新只计一次 |
| T30 | 响应无 usage、失败可能已发送、本地估计 | reported/estimated/unknown 分开，不把失败记成精确 0 |
| T31 | 可捕获取消、预算中断、sending 状态后恢复 | 持久化已有开销；recover 不重发调用，未知量和恢复耗时可见 |
| T32 | 三种报告格式最终回执 | 同 receipt_id/摘要；最后一次模型 usage 已包含 |
| T33 | 实际 OTel 导出 JSON/JSONL、缺字段与未知属性 | 导入链路可复现，导出器/语义版本与缺失范围明确 |
| T34 | 离线 demo、mock Agent demo、显式 live smoke | 离线不需 key/GPU，mock 明确标识，live 另有真实 usage 和状态 |
| T35 | 包含 </script>/HTML 的日志，HTML 禁网打开 | 显示文本，无执行；五层、多轮与诊断回执仍可查看 |

验证层级：

- 核心数学/计量：单元测试 + 小 DAG 与区间属性测试。
- 输入与输出协议：schema/fixture/异常覆盖与 JSON round-trip。
- 产品流程：CLI 集成、禁网、错误退出码、Agent mock、多格式回执一致。
- 外部环境：OTLP exporter 导入、显式 live 模型冒烟、后续引擎采集分别记录，不能用 mock 替代。

建议 CI 检查入口在 M0 落成后固定为：

```bash
uv sync --locked --all-extras --dev
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest -m "not live and not gpu"
```

当前并未创建工程或执行这些命令。未来 live/gpu 测试由显式配置启用，不能在普通 CI 中读取用户密钥、自动租 GPU 或发送付费请求。

## 16. v0.2：服务端关联与可选主动实验

v0.1 验收后按下列顺序执行，保留当前 schema 与回执的兼容性。

### B1：固定版本的 vLLM 指标导入/只读采集

实现 versioned mapping：metric 名称、类型、单位、计数范围、重启重置和适用版本。优先支持已保存的多时刻 /metrics 快照，再支持用户显式配置的只读抓取，不搭建常驻平台。

产物包含请求队列、运行中请求、KV 占用、抢占、前缀查询/命中与延迟分布的实际可用信号。通过 endpoint/replica/time window 关联，只将聚合量作为窗口证据；没有 request ID 时不伪造逐请求排队/Prefill 时间。vLLM 的很多请求指标以 histogram 暴露，不能倒推出原始请求样本。[vLLM 指标设计](https://github.com/vllm-project/vllm/blob/main/docs/design/metrics.md)

完成条件 B-T01：计数器重置、采样缺失、共享 endpoint 和版本不支持均有结果；采集不可用不破坏原有 trace 分析。

### B2：前缀复用探针

先输出待执行 ProbePlan：目标 endpoint/model、合成或用户指定的输入、变量、固定条件、预热策略、请求/输出限制、预计 token/time、需要取得的 cache usage 与停止条件。原始生产 Prompt 不自动重放。

只有单独启用 probe 才执行；offline analyze 仍保持禁网。可从 agent 路径显式启用实验权限，但其默认仍关闭，不能因选择 agent 而自动探测推理服务。

最低实验包含可控的相同前缀重复组与变化前缀对照组，标记执行顺序和缓存暖态。初始限制最多 6 请求、并发 1、每请求输出 64 token、整个探针 60 秒；输入长度另限并计入总预算。该数量用于限制消耗，不保证统计显著或足够定位。

cache 无法安全复位时不复位，明确暖态与串扰；若对照被驱逐/路由等影响，允许返回 inconclusive。只看到 TTFT 变快不足以宣称缓存因果成立，还需要后端/usage 支持或更严谨的控制条件。

完成条件 B-T02：未启用零测试请求；启用后请求/长度/时间限制有效，失败/预算中止也有诊断回执。analysis 与 probe 用量各自小计，合并只记一次。

### B3：实验结果与结论升级

记录 Hypothesis→Control→Intervention→Observation→Verdict，保留正反证、样本量、变异、控制缺陷与质量限制。只有满足该假设所需证据时才能标 experiment_supported；否则为 unsupported/inconclusive。

完成条件 B-T03：缓存确实命中、只是网络波动、路由变化、没有 cache usage 四类 fixtures 不混淆。B-T04：探针与分析模型各自 token/time/cost 可核对，报告引用同一最终回执。

后续 SGLang/DeepInfer、更多原始 profile、分布式跨节点图、回退比较均另开范围，不作为 v0.1/v0.2 完成前提。启用 heavyweight profiling 会改变性能，应记录采集配置和开销，不能自动把 profiling 运行当作正常基准。[vLLM profiling 指南](https://github.com/vllm-project/vllm/blob/main/docs/contributing/profiling.md)

## 17. v0.1 完成清单

- [ ] R01～R10、R12 均有对应测试和演示；R11 在首版只输出建议且不执行实验。
- [ ] offline 与 agent 可独立使用，共享确定性计量、规则与图服务。
- [ ] 五层及阶段内部所有已提供轮次可展开；缺失信息明确为 unknown/N/A。
- [ ] 耗时、token、缓存分母、价格、共享资源成本和跨层去重均可核对。
- [ ] 关键路径资格、等待保留、并行情景和收益上限条件有明确证据。
- [ ] 三组浪费规则的正例、反例和数据不足行为通过。
- [ ] 两条路径最终都有诊断耗时与 token 回执，重试与最终总结不漏记。
- [ ] 失败、预算耗尽、可捕获中断和恢复均保留已知开销。
- [ ] 至少一种真实 trace 导入已验证；至少一次模型 live smoke 有实际结果，未验证项不写为通过。
- [ ] 无 key/GPU 的演示与正常 CI 通过；离线 HTML 与禁网分析通过。
- [ ] 终端、JSON、HTML 使用同一最终回执；输入内容无法被作为 HTML/命令执行。
- [ ] 接入、字段映射、价格、模型预算、数据能力和理论上限限制文档完成。

“完成”指满足以上产品与验证条件，不代表获得特定 star 数或在所有引擎/模型/GPU 上准确诊断。

## 18. 需要持续核对的风险

| 风险 | 处理决定 | 不应出现的替代做法 |
| --- | --- | --- |
| trace 没有真实依赖 | 降级为观测/局部分析，给出缺失项 | 按时间或 parent 猜出完整 DAG 后给精确收益 |
| 只能看到客户端或全局指标 | 按作用域展示，并建议补采 | 从 TTFT 直接猜出 Kernel/调度实际时间 |
| 指标/语义约定发生变动 | mapping 与 fixture 记录导出器/版本 | 静默将同名异义字段相加 |
| token/费用估算不完整 | 记录来源、已知小计与 unknown | 失败调用记零、共享资源重复收费 |
| Agent 隐式扩大工作 | 受限工具、明确预算、默认禁 probe | 自动重跑任务、修改系统或扫描配置 |
| Agent 结论好看但无证据 | 引用与数值由程序验证/渲染 | 让模型补百分比、成本或置信概率 |
| 范围膨胀 | M0～M9 完成后才进入 B1～B3 | 用通用监控/eval/调参功能替代核心验收 |

本计划已决定常规技术选择，没有因框架配置遗留的 PENDING。M0 可直接开始；真实模型验证需要可用 endpoint/凭据，真实案例验证需要可使用的 Agent trace。这些不会阻止 M0～M6 和 M7 的 mock 开发，但缺少时不能声称完成对应的真实接入验证。价格文件可选，未提供时如实保留 unpriced，不作为实现阻塞。

