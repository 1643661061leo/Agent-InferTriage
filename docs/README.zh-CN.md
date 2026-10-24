# agent-InferTriage 中文说明

目标不是再造监控面板，而是回答：**下一周的优化时间应当花在哪一层，证据是什么，收益上限多少？**

当前已有可运行 alpha。完整边界见 [产品承诺](product-boundaries.md)，验收进度见 [STATUS](STATUS.md)。
无需 hmz1、特定 Agent 框架、GPU、API Key 即可体验离线示例。

## 快速运行

需要 Python 3.12+；仓库内使用 uv 管理环境，尚未发布到 PyPI。

```sh
uv sync --locked
uv run agent-infertriage demo --output-dir results/demo
open results/demo/report.html
```

若已经准备好仓库内虚拟环境，也可直接运行：

```sh
.venv/bin/agent-infertriage demo --output-dir results/my-demo
```

输出目录必须是新的，不覆盖已有诊断。
离线 demo 的 30 秒、13,300 token 和 0.03 DEMO 费用都是合成数据，不是真实测量。
本次离线诊断新增模型 token 为 0，诊断耗时在本机实测。

```sh
uv run agent-infertriage demo-agent --output-dir results/mock-agent
```

该命令使用明确标注的 scripted mock，执行真实的分析循环，但不调用真实模型。

## 两条诊断路径

```sh
# 路径一：结构化证据离线分析，严格不联网
uv run agent-infertriage analyze examples/native --output-dir results/offline

# 也能导入 OTLP JSON/JSONL；多任务需选择 --workload 或明确 --all
uv run agent-infertriage analyze traces.jsonl --format otlp-json --all --output-dir results/otlp

# 路径二：用分析模型逐步读取日志、查询规则/计量、形成诊断
uv run agent-infertriage analyze agent.log --format raw-text --mode agent \
  --model-config model.toml --output-dir results/agent
```

先从 examples/model.example.toml 复制并填写自己授权的 endpoint 和模型。
agent 模式可能向该 endpoint 发送所选日志并消耗 token/费用；请先去敏。
普通日志只能支持引用行号的假设，不能凭自然语言恢复可信服务器内部精确耗时。
原生证据包默认 metadata，允许模型读原文需显式 content_policy: "model_allowed"。
没有自动敏感信息清洗，也不承诺完全解决 prompt injection。

模型只能读取声明来源、查询只读分析工具，不能执行命令、扫描任意文件、调整服务或发起推理探针。
每次 attempt（包括重试、失败、最后总结）都会进入本次诊断账本。
模型 usage 不完整时保留已知小计与 unknown，不记成精确零。

## 接入自己的 Agent

Recorder 是框架无关的显式 Python 包裹入口，可以包住同步调用或 await 调用。

```python
from agent_infertriage.recorder import Recorder

with Recorder("evidence/task-1") as rec:
    with rec.startup_phase("readiness"):
        wait_for_your_service()  # 你自己的服务就绪逻辑

    with rec.model_call(agent_id="builder", round_id="1") as call:
        result = your_existing_model_call()
        # 流式时，在第一条非空内容 token 到达时调用 call.first_token()
        # 用真实 usage 填写，下面仅示意字段，不是实际 token 数。
        call.usage(input_tokens=1000, output_tokens=100, cache_read_tokens=800)
```

若没有实际 usage，就不调用 usage()，结果保持 unknown。
默认不收集 Prompt 正文；未包装的调用不会被自动捕获。
冷启动按服务生命周期单列，不混成 queue/prefill/decode。

## Prometheus 如何接入

Prometheus 负责服务指标，InferTriage 负责跨来源的解释与收益计算。
支持 text 快照、query_range matrix JSON 和独立的只读采集命令：

```sh
uv run agent-infertriage capture-metrics http://127.0.0.1:8000/metrics \
  --output-file evidence/service.prom
uv run agent-infertriage analyze examples/with-prometheus --output-dir results/prometheus
```

采集文件后还要按 [Prometheus 指南](prometheus.md) 将来源加入证据 manifest；不会自动扫描磁盘。
服务 p95 和目标请求真实 queue time 是两种证据，不能替代或相减。
服务指标缺失/格式错误会列告警，已有 Agent 分析仍可进行。

## 报告怎么看

report.html 可禁网打开；report.json 提供相同结构化结果。
五个优化层面、多轮/多次调用、token、缓存与费用始终有位置；缺失显示 unknown。
L0–L4 分别表示客户端、服务指标、请求 trace、设备 profile 和完整关联能力。
不是“有一个 Prometheus endpoint，就能得到 L4”。

收益是显式完整活动图上的条件性上限；没有活动图就不给伪精确排序。
同请求关联多层时 token/cost 是不同视图，不能跨层再次相加。
Kernel 没有独立 token 费用，只能引用关联请求或明确设备计费证据。

diagnosis_usage.json 记录本次诊断，和历史任务用量分账。
终端、JSON、HTML 使用同一个 receipt_id；诊断时间计至最终用量快照，明确不含最后文件落盘尾部时间。

```sh
uv run agent-infertriage recover results/agent
```

恢复只读已持久化事件，不重发模型请求；未结束的 sending 仍是未知用量。

已完成单 H20 / vLLM 0.25.0 / Llama-3.1-8B 的受控实测：真实三轮推理、L0+L1 与离线报告通过；
早期分析 Agent 曾返回 partial，失败记录保留；后续外部模型辅助诊断已完成。
进程启动至最后本地推理的边界及分阶段计时已补测，见 [L0/L1 业务计时契约](l0-l1-workflow.md)。
生产 trace、SGLang、Kernel profile 仍未验收。
专用场景只做源码树外的实验，不新增硬件场景测试文件。实际状态见 STATUS.md。
