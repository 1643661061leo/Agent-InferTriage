# agent-InferTriage

**Where should your next week of optimization go: Agent, Prompt, cache, scheduler, or Kernel?**

An evidence-first CLI for Agent performance triage. Inspect every available round, separate
historical workload cost from diagnosis cost, and calculate conditional improvement ceilings
only when the execution graph supports them.

[中文说明](docs/README.zh-CN.md) · [Product boundaries](docs/product-boundaries.md) ·
[Evidence contract](docs/data-contract.md) · [Prometheus](docs/prometheus.md) · [Status](docs/STATUS.md)

> Alpha implementation, not a claim of universal instrumentation or validated production GPU diagnosis.
> A real single-H20 / vLLM 0.25.0 / Llama-3.1-8B run validated L0+L1 ingestion and offline
> reports. Exact business boundaries and per-step L0/L1 timing have also been exercised, with
> successful external analysis-agent completion after preserving failed/partial attempts.
> Production traces, SGLang and GPU profiles remain unvalidated. Missing evidence stays unknown.

## Try it without a GPU, API key, or model tokens

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).
Run from a checkout; this project is not yet published to PyPI.

```sh
uv sync --locked
uv run agent-infertriage demo --output-dir results/demo
open results/demo/report.html  # macOS; otherwise open this local file in your browser
```

The sample is **synthetic**, deliberately simple enough to check by hand:

| Sample fact | Expected result |
| --- | --- |
| Three rounds, each with two Kernel invocations | All six invocations remain visible |
| Task wall time | 30 seconds, synthetic |
| Inclusive input + output tokens | 13,300, synthetic |
| Historical sample charges | 0.03 DEMO, not real currency |
| Eliminate modeled queue activities | At most 1.5 seconds under fixed-DAG assumptions |
| This offline diagnosis | 0 new model tokens; measured local diagnosis time |

A mock Agent demo exercises the real loop, read-only analysis tools, budgets and accounting:

```sh
uv run agent-infertriage demo-agent --output-dir results/mock-agent
```

Its scripted responses and usage are explicitly marked `synthetic-mock`. It makes no model calls
and is not a substitute for a live endpoint smoke test.

## Observe what you can, disclose what you cannot

Observability has **five evidence capabilities**, not a required cumulative ladder:

| Capability | Evidence | Boundary |
| --- | --- | --- |
| L0 client | Opt-in client spans, explicit stream first-token event, usage | No guessed queue/prefill/decode/network split |
| L1 service | Existing Prometheus snapshots or query_range exports | Aggregates are not measurements of the target request |
| L2 request | Explicit request-associated server spans | Depends on server instrumentation and IDs |
| L3 device | Explicit imported device-profile spans | No built-in CUDA profiler or direct .nsys-rep importer yet |
| L4 correlated | Verified links/clocks plus a qualified execution DAG | No precise cross-layer benefit without these prerequisites |

Every report includes all five optimization layers, their evidence coverage, all supplied rounds
and span details, tokens/cache/cost views, evidence references, and a separate diagnosis receipt.
Service startup is kept separate from client inference calls. Instrumentation is opt-in: an
uninstrumented call cannot be observed merely by installing this package.

### Add L0 timing to your own Python Agent

For exact business boundaries, process-start timing, per-step wall-time partition,
handoff measurements and service-window diagnostics, see [L0/L1 workflow timing](docs/l0-l1-workflow.md).
These are L0/L1 features, not tasks postponed until server tracing or Kernel profiling.

```python
from agent_infertriage.recorder import Recorder

with Recorder("evidence/my-agent") as recorder:
    with recorder.startup_phase("model_load"):
        pass  # wrap your actual loading/readiness code; no service is started for you

    with recorder.model_call(agent_id="builder", round_id="1") as call:
        result = your_model_call()  # replace with your own existing call
        # For streaming, call call.first_token() on the first nonempty content token.
        call.usage(input_tokens=1000, output_tokens=100, cache_read_tokens=800)
```

Do not copy the example token counts into production: pass actual reported usage. If absent,
omit call.usage() and token/cost accounting will stay unknown. The context can wrap await calls
and concurrent async children. It does not infer causal dependencies from containment.

```sh
uv run python examples/record_agent.py --output-dir evidence/client-example
uv run agent-infertriage analyze evidence/client-example --output-dir results/client
```

That example times a local synthetic iterator, not a real inference endpoint.

## Two diagnosis paths

### Offline: deterministic, no network

```sh
uv run agent-infertriage validate examples/native
uv run agent-infertriage analyze examples/native --output-dir results/offline
uv run agent-infertriage analyze traces.jsonl --format otlp-json --all --output-dir results/otlp
```

Use --workload ID to select a single workload; multi-workload input requires --all or a selection.
OTLP parents/links are preserved but **never automatically made execution dependencies**.
To keep a reusable native package with customized attribute mappings:

```sh
uv run agent-infertriage import traces.jsonl --format otlp-json \
  --mapping examples/otlp-mapping.example.toml --output-dir evidence/imported
```

### Agent: bounded log interpretation with an explicitly configured model

Copy examples/model.example.toml to model.toml, set your endpoint and served model name,
and, if needed, configure the name of an environment variable holding the API key.

```sh
uv run agent-infertriage analyze agent.log --format raw-text --mode agent \
  --model-config model.toml --output-dir results/agent
```

**This may send selected log text to that endpoint and consume tokens/money.** Raw-text input
authorizes that one file; native packages default to metadata-only and require
content_policy: "model_allowed" to expose source text. Sanitize logs before enabling it.
There is no automatic secret redaction or full prompt-injection guarantee.

The model can read declared sources and query deterministic analysis tools. It cannot run shell
commands, discover arbitrary files, change serving configuration or launch inference probes.
Model hypotheses are separate from facts. Unknown IDs/metric references are rejected; retries,
failed/unknown usage, the final response, time and budget exhaustion are recorded.

The initial HTTP adapter uses a non-streaming /chat/completions JSON endpoint with inclusive
input/output usage semantics. It is not an adapter for every SDK/provider. Live compatibility
must be checked against your server/model. Token preflight budgets use a documented byte
heuristic, **not a hard provider token-limit guarantee**.

## Prometheus is a source, not a competitor

Reuse exporters you already have. Import .prom snapshots or Prometheus matrix JSON as native
attachments; optionally collect one snapshot/window explicitly:

```sh
uv run agent-infertriage capture-metrics http://127.0.0.1:8000/metrics \
  --output-file evidence/service.prom
uv run agent-infertriage capture-metrics http://127.0.0.1:9090 \
  --query 'rate(vllm:prompt_tokens_total[1m])' \
  --start 1792720800 --end 1792721100 --step 15s --output-file evidence/range.json
uv run agent-infertriage analyze examples/with-prometheus --output-dir results/prometheus
```

Collection is a separate, explicit GET, never an implicit part of offline analysis or a model tool.
The result preserves names, labels, version declarations, timestamps and service scope. Missing
optional metrics produce warnings without erasing valid Agent evidence. Histogram buckets cannot
be turned into individual request timings. See [the source attachment guide](docs/prometheus.md).

## Reports and accounting

Each diagnosis writes a **new** output directory and refuses to overwrite an old one:

```text
report.html                self-contained, escaped, no network assets or scripts
report.json                full analysis + independent diagnosis receipt
diagnosis_usage.json       the exact same receipt
diagnosis_events.jsonl     append-only, flushed attempt/phase journal
agent_steps.jsonl          model actions/tool/attempt records
evidence_index.json        source references
```

report.json.diagnosis and HTML/terminal share the same receipt ID. Wall time covers ingestion,
analysis, model/tool work and HTML body preparation through the final snapshot; the final receipt
encoding/file flush/terminal refresh are explicitly excluded. Missing provider usage is unknown,
not an exact zero. Offline has no model API cost; local compute is unpriced, not free hardware.

After interruption, recover durable usage without replaying any request:

```sh
uv run agent-infertriage recover results/agent
```

Pricing is optional. --price-file accepts explicit Decimal rates by provider/model/currency/
effective date. No current prices are bundled. Different currencies and alternative GPU-vs-token
cost bases are not silently added together.

## Development

```sh
uv sync --locked
uv run ruff check src tests examples scripts
uv run ruff format --check src tests examples scripts
uv run mypy src
uv run pytest -q
```

The normal suite is offline and skips live tests. To authorize a live model test explicitly:

```sh
INFERTRIAGE_RUN_LIVE=1 INFERTRIAGE_MODEL_CONFIG=/absolute/path/model.toml \
  uv run pytest -m live -q
```

A controlled real H20/vLLM experiment is recorded in STATUS; it is not broad production acceptance.
Scenario experiments stay outside the source tree, not in dedicated hardware-specific test files.
See [STATUS](docs/STATUS.md) before interpreting the roadmap as completed support.

License: Apache-2.0. This tool diagnoses and suggests next checks; it does not auto-tune or
automatically apply optimizations.
