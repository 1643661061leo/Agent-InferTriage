# L0/L1：Agent 业务秒表与瓶颈候选

这是原计划 R02/R03/R05 的前移细化，不等待 L2–L4。L0/L1 的目标是可靠回答
“从启动 Agent 到最后一次推理结束，一共多久、可观测时间花在哪里”，不是假装能看到服务内部。

## 总计时边界

- 普通接入从 Recorder.__enter__ 开始，必须如实标记 recorder_enter，不能叫进程启动。
- 需要进程级起点时，父进程在创建 Agent 子进程前取得 monotonic_ns，传给
  Recorder(origin_ns=..., start_boundary="agent_process_spawn")。必须同一主机/同一单调时钟；
  工具不自动验证跨机器时钟。进程创建、解释器启动、导入和初始化计为 agent_init。
- 最后一次目标推理调用闭合后，使用 finish("last_inference_completed", end_span_id=...)。
  终点取该调用的实际结束时间，不取后来导出报告、控制 Agent 再总结或清理容器的时间。
- finish 后不能继续追加 span；未 finish 的异常/取消按实际退出点落盘，标记 failed/cancelled，
  不能伪装为成功的最后推理结束。诊断、采集后快照、导出、清理另行记录，不计业务时间。
- 最后一次推理是哪个调用由工作流明确指定；工具不会自行猜测还会不会有下一轮。

## 步骤分类

| 分类 | 典型内容 | 不应误称为 |
| --- | --- | --- |
| agent_init | 进程创建、导入和初始化 | 模型加载 |
| document_read | 文档/文件读取 | 推理 |
| prompt_build | 上下文组装、裁剪、模板 | GPU prefill |
| agent_decision | 控制模型的一次客户端请求等待 | 纯思考或 reasoning 计算时间 |
| tool_dispatch | 工具路由、参数验证 | 工具全部执行时间 |
| service_launch | 创建进程/容器 | 完整冷启动 |
| readiness_wait | 等待可用的包围区间 | 精确的模型加载时间 |
| health_check / poll_wait | 一次 HTTP 探测 / 两次探测间休眠 | 推理排队或交接 |
| inference | 每个请求/轮次/attempt 的客户端调用 | 服务端 GPU 计算 |
| response_parse | 响应解析、校验 | 网络传输 |
| handoff_serialize / transfer / deserialize | 交接打包、可观测传输等待、接收解析 | 下一阶段模型的整个等待 |
| retry_backoff | 重试前明确记录的休眠 | 无依据的故障推断 |
| metrics_capture | 读取已有监控数据 | 免费、零开销的旁路 |

step(kind, name, agent_id=..., round_id=..., attributes=...) 可标记 payload_bytes、http_status、
重试编号等。模型调用用 model_call(attributes={"step_kind":"agent_decision"}) 或 inference。
不启用全局网络/文件 monkeypatch：只覆盖接入点明确包装的操作。未记录的流程仍是未观测时间。

## 时间如何对账

保留每个步骤 inclusive 持续时间与嵌套关系；另按边界切片生成总 wall time 的完整分区：
有子 span 时使用最具体的已记录活动；并行叶活动同时存在记 parallel_overlap；无活动记
unattributed。所有分区严格相加等于总耗时，但“已记录区间占比”不是所有内部步骤都完整的证明。
父步骤剩余区间也不是 CPU self time。冷启动包围区间单列，与其健康探测/休眠子项不能相加。

## 瓶颈分类与证据强度

1. 冷启动/生命周期等待：显示 readiness 包围耗时、探测耗时、轮询休眠；实际 ready 时刻
   可能位于最后失败与首次成功探测之间，不能凭轮询推导精确加载耗时。
2. Agent 决策/业务模型等待：按 agent、轮次、请求、attempt 展示耗时、TTFT、token。
   外部模型无服务端证据，纯思考时间仍 unknown。
3. 上下文增长、重复工具/重试、前缀风险：沿用确定性规则，明确为模式候选；
   输入增长不证明内容无用，也不证明是时延增长原因。不能给出未经验证的裁剪收益。
4. 工具/文档 I/O、prompt 组装、响应解析：依实测耗时排序，不因重复就断言很慢。
5. 交接开销：仅使用明确的交接三阶段事件与 payload_bytes；未记录交接不能显示已知 0，
   也不能把两个阶段之间的空白默认当作交接慢。跨主机传输需额外关联和时钟证据。
6. 重试和 backoff：只统计有证据的 attempt/休眠，保留失败用量 unknown。
7. 并行重叠、未观测空白、时钟/边界缺口：首先报告证据质量，不构造虚假的优化原因。

报告的 contributors 是实测占时调查顺序，不是可节省时间排名。每个候选附来源、下一项核查，
收益上限在没有合格依赖图时保持 unknown。不能通过逐项相加得到整体优化收益。

## L1 服务窗口

读取现有 Prometheus；展示队列/running、KV 使用、prefix cache、preemption 和阶段累计量。
同 endpoint、labels、版本/类型一致、多个递增时间戳的 counter/histogram sum/count 可求差；
观测到重置、样本不足或元数据冲突时不输出精确 delta。
窗口与来源保持可查，不把服务累计量分配到某次请求。稀疏快照会漏掉瞬时拥塞、采样间重启，
不能用“两个快照等待请求都为零”证明从未拥塞。指标含义/单位仍需固定引擎版本核对。

## L2–L4 后续任务

- L2：服务端逐请求 queue/prefill/decode、调度迭代和缓存关联；不是补做文档读取等客户端计时。
- L3：真实设备 timeline 的转换适配和短窗口采集；不是从 GPU utilization 猜 kernel。
- L4：跨层 ID、共享 batch、时钟、明确等待依赖与完整性审计，才能计算条件 critical path 和收益。
- 外部 API 的内部阶段始终受提供方证据限制，不因本地链路完整而宣称外部 Kernel 可见。
