# 产品承诺与观测边界

当前交付为可运行 alpha，不是全部计划已验收的生产版本。

## 准确的承诺

agent-InferTriage 面向不同 Agent 推理场景提供统一接入协议和计时入口。
**接入或导入有效证据后**，在可观测边界内分析；证据不足时明确显示 unknown。
不能承诺“安装后自动观测所有 Agent”，也不能承诺“所有场景都有完整五层精确计时”。

产品问题始终是：下一周应当优先调查 Agent 编排、Prompt、缓存、调度还是 Kernel？
回答必须说明证据、缺口、观测级别、条件性收益上限和下一项验证。
没有合格活动图时，不输出伪精确的全局投入排序。

## 五级观测能力

L0–L4 是五项可独立具备的能力，不是发现 L3 就自动具备 L0/L2。
observed 只表示存在该类证据，不表示覆盖全部请求或全部阶段。

| 级别 | 来源 | 可报告内容 | 不可推断内容 |
| --- | --- | --- | --- |
| L0 客户端 | 显式 Recorder 或导入客户端 span | 调用 wall time、客户端首内容 token、attempt、usage | queue/prefill/decode/network 精确拆分 |
| L1 服务指标 | Prometheus 快照 / query_range | 指标窗口、labels、counter 分段速率/重置 | 目标请求排队时间、某轮因果关系 |
| L2 请求追踪 | 明确标注 L2 的服务器 span | 有 request/attempt 关联的 queue/prefill/decode 多次事件 | 不存在的 phase、共享 batch 的独占耗时 |
| L3 设备 | 明确标注 L3 的规范化 profile span | 已导入 kernel/launch/通信/显存事件 | 无计数器时的 compute/memory-bound 判定 |
| L4 跨层关联 | 明确依赖图、关联 ID、校准时钟 | 合格固定图的 critical path 和条件性上限 | 对新调度、动态 Agent 行为的无条件预测 |

当前没有自动扫描 Prometheus/trace/profile 的发现服务；由用户显式附加或采集。
当前没有直接读取 Nsight SQLite、.nsys-rep、CUPTI 二进制的适配器；须先归一化到协议。
已完成单 H20 / vLLM 0.25.0 / Llama-3.1-8B 的受控实测，验证 L0+L1 导入与离线报告；
早期分析 Agent 因无效输出返回 partial；后续外部模型辅助诊断已完成，partial 记录保留。
不能推广为其他版本、SGLang 或设备 profile 已验收。
总计时边界、Agent 初始化/文档/决策/健康探测/交接等属于 L0/L1，已前移细化；
详见 [L0/L1 业务计时契约](l0-l1-workflow.md)，不等待 L2–L4 才实施。

## 冷启动不是一次推理

Recorder 的 startup_phase() 单独记录 process_start、model_load、weights_load、cuda_init、
compile、cuda_graph_capture、warmup、readiness。
这些名称由用户包装实际代码，不意味着工具能够从外层 40 秒自动还原内部阶段。
Agent 任务总 wall time 可以包含冷启动；每次 model_call 的客户端延迟单独列示。
不从 readiness latency 推导权重加载或 CUDA 初始化时间。

## 时间与收益

父子和并行 span 的 elapsed、inclusive 累计值、active interval union 分开呈现。
只有完整子事件声明下才计算父 self time；跨时钟未对齐时并集未知。
workflow round、attempt、engine iteration 和 kernel invocation 保留各自 ID，不互相替代。

完整执行图必须显式声明完整性、无重复物理活动、依赖合法、时钟可用，且拟合任务实测时长。
计算“指定活动权重变为零/除以加速倍数后”的最长路径，保留固定释放和等待约束。
因此分支切换后收益可以为零；单项上限不能相加。
固定图的理想上限既不是收益保证，也不是“下一周能实现”的工期承诺。

## 只读诊断与费用

offline 不联网、不调用模型；agent 只访问用户明确配置的分析模型和只读工具。
Prometheus 的显式采集不产生推理请求，但查询本身可能有基础设施成本，不宣称完全无开销。
主动负载、前缀实验、自动参数修改都尚未实现，且不得因启用 agent 而隐式执行。

历史 workload token/cost 与本次 diagnosis token/time/cost 分账。
有 token 无价格时为 unpriced；可能已发送但响应缺 usage 的 attempt 为 unknown，不写精确零。
流式首 token 只在客户端明确收到非空内容时记录，不等同于服务器 TTFT。
