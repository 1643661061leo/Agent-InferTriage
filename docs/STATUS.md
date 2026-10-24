# Implementation status

2026-10-24 policy update: production-like scenario runs are ad-hoc experiments outside the
project, not dedicated test scripts or scenario-specific test cases. The earlier H20 launcher,
its nine mock scenario tests and its runbook have been withdrawn from the source tree; ordinary
project-function tests remain. Real experiment results are recorded separately when available.

As of 2026-10-24: **0.1.0a1 alpha, not full v0.1 acceptance**.

## L0/L1 business stopwatch refinement: 2026-10-24

See [the timing contract and L2–L4 follow-up split](l0-l1-workflow.md).
New generic functions include same-host parent monotonic origin (including process initialization),
explicit finish at a completed inference span, step taxonomy, non-additive inclusive envelopes,
exclusive observed-interval partition with parallel/gap buckets, L1 counter windows and candidate
diagnostics. Model API duration is never called pure thinking; growth is not a proven cause.
Read-only tools expose compact references while full report provenance is preserved.

The same real H20/vLLM/Llama scenario was run again using external controller
`deepseek-v4-flash-0731`, starting the timer immediately before the Agent subprocess was created
and sealing it at the third local inference return. Business time 95.320589443 s, 152 timed spans,
readiness envelope 78.126502366 s, three local calls 1.754153/2.511919/1.234350 s.
Explicit in-process handoff packing/queue/unpacking summed to 0.809654 ms; this does not validate
remote handoff timing. Unattributed interval 50.181860 ms; interval partition sums exactly to total.
Observed interval fraction 99.947% is not proof of complete internal instrumentation.

Offline replay: completed, 0 new tokens, 161.992 ms. Initial external diagnosis was partial on
invalid output, 51,454 tokens; compact tool references plus a bounded concise output request
were then validated against the same saved evidence without restarting the service.
Exact `deepseek-v4.1-flash` recheck completed, 49,416 inclusive tokens, 29.917 s, five model
attempts and three read-only tools. Every partial/recheck receipt remains separate; costs unpriced.
GPU returned to baseline and the owned container was stopped. No scenario test script was added.
Artifacts: `/home/ubuntu/sihao/experiments/agent-infertriage-20261024-l0l1-01`.

Current local checks: **94 passed, 1 live test skipped**, Ruff/format and strict mypy passed.
This is controlled L0/L1 verification, not L2/L3/L4 or production load acceptance.

## Real controlled experiment: 2026-10-24

Code deployed to `/home/ubuntu/sihao/Agent-InferTriage`; artifacts kept separately under
`/home/ubuntu/sihao/experiments/agent-infertriage-20261024-live02`.
One idle H20 was used with an existing vLLM 0.25.0 image and local Llama-3.1-8B-Instruct weights.
No Claude/cloud model or new model download was used. All experiment code ran ad hoc through
stdin; no scenario-specific test file was added. Other projects/services were left untouched.

- Process cold start/readiness: 112.257 s; three client calls: 1.010/1.259/1.163 s.
- Three workload calls: 4,645 input + 543 output = 5,188 tokens, provider reported.
- Offline diagnosis: completed, 0 new model tokens, 79.931 ms wall time.
- Real analysis-agent diagnosis: partial, 3 completed HTTP model calls + 1 tool call,
  4,962 tokens and 3,396.218 ms. Final model output used invalid evidence/metric references;
  the validator rejected them instead of admitting unsupported claims.
- L0+L1 observed; L2/L3/L4 unknown. No request-trace/profile-based global benefit was claimed.
- Service-scope counter delta: 2,848 prefix hits / 4,645 queried tokens. This does not fill
  missing per-request cache usage or imply billing discounts.
- GPU pricing was not supplied, so monetary costs remain unpriced, not zero.
- Both experiment containers were stopped after completion; logs and failure records retained.

The preceding `live01` attempt failed on a removed vLLM CLI flag and is preserved separately.
These are controlled real-service experiments, not a customer production incident or throughput
benchmark. They verify the real runtime path but do not complete all live acceptance criteria.

## Implemented and locally exercised

- Python package/CLI, uv lockfile, strict core type checks, lint/format checks, generated schemas.
- Native evidence, raw text, OTLP JSON/JSONL, explicit source paths and input limits.
- Opt-in Python L0 Recorder, async child contexts, explicit client TTFT, separate cold-start phases.
- Five-layer coverage, all supplied rounds/occurrences, request/attempt/iteration/kernel IDs.
- Inclusive usage, duplicate/conflict handling, weighted cache views, Decimal prices/currencies.
- Explicit-DAG longest path, fixed releases/lags, baseline qualification, recomputed scenarios.
- Context growth, repeated tool/retry, tool-result amplification and prefix risk/reuse-gap rules.
- Deterministic offline analysis and bounded Agent loop, typed actions, read-only tools, budgets.
- Explicit analysis-model HTTP adapter, no hidden retries; failed/missing usage stays unknown.
- Independent diagnosis journal/token/time/cost, partial/failure/cancel paths, no-replay recovery.
- Escaped, self-contained HTML/JSON/terminal outputs sharing one final receipt.
- Prometheus text and query_range imports, service-scoped presentation, counter/reset handling,
  explicit one-shot read-only acquisition. No aggregate-to-request latency conversion.
- Five observability capabilities, with unknown phases/levels and collection requirements.

Automated tests include hand-checkable serial/parallel/equal-branch/release examples, property
tests for nonnegative DAG weight reduction and interval union, accounting, read-only boundaries,
mock Agent loops, offline network prohibition, interrupted recovery, hostile HTML escaping,
Prometheus resets/query-range parsing, SDK-produced trace projection and Recorder concurrency.
Run `uv run pytest -q` for current counts. Live tests are explicitly opt-in and normally skipped.

Initial local verification: 79 tests passed, 1 live test skipped, on macOS / Python 3.14.
Ruff, formatting, strict mypy and offline lockfile checks passed. Offline, mock-Agent,
Prometheus-attachment and L0-recorder examples were executed locally. This is not remote CI
or production-service validation.

## Validation not yet performed

| Area | Current evidence | Acceptance still required |
| --- | --- | --- |
| Live analysis model | Local partial and external v4.1 completed runs; full receipts retained | Broader providers, robust output reliability and semantic grounding |
| Production Agent trace | Controlled real L0 workflow plus hand/SDK fixtures | Sanitized production exporter/Agent trace and exporter version |
| vLLM / SGLang | vLLM 0.25.0 serving + real Prometheus snapshots | Full metric/trace semantic mapping; SGLang still unvalidated |
| CUDA / Kernel | Synthetic normalized device intervals | Real Nsight/CUPTI export adapter, calibrated request links |
| L4 causality | Explicit hand-modeled complete graphs | Real ID linkage, clock calibration and residual audit |
| Platform matrix | macOS Python 3.14 checks; Linux Python 3.12 real runtime | Full Linux CI matrix; workflow has not been run remotely |
| Scale/performance | Functional/property tests | Large traces and bounded-memory/runtime benchmarks |

## Scope changes from the original plan

The user's subsequent instructions move **generic Prometheus source ingestion** forward from B1
into this alpha and add opt-in L0 instrumentation plus L0–L4 capability labeling and startup
separation. This does not imply completion of B1's production engine integration or B2/B3 probes.
The original draft/plan remain design references, not claims of implementation coverage.

## Follow-up order

1. Capture a sanitized real Agent trace; validate round/attempt ownership, clocks and elapsed time.
2. Improve analysis-agent citation/reference reliability and complete a successful grounded live run.
3. Pin one vLLM/SGLang version, map its metric units and request phase semantics, add real fixtures.
4. Add a real profile-export adapter and audited request/batch/device linkage, then validate L4.
5. Add opt-in active probes only with separate consent, token/time/cost budgets and failure receipts.

No auto-tuner, automatic configuration changes, daemon/dashboard, profiler implementation,
universal proxy/SDK middleware, secret redaction or default paid-model/probe traffic is included.
