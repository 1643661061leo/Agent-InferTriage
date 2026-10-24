# Prometheus integration

Prometheus is an evidence source. InferTriage neither replaces its exporter nor continuously polls
it. The alpha imports generic Prometheus data. A real vLLM 0.25.0 smoke run captured snapshots
and checked service counter units, but is not full engine-specific semantic validation. SGLang
deployment compatibility remains unverified.

## Attach existing data

Start with a native evidence package (`manifest.json` and `events.jsonl`). Put the metrics file
inside it, then add a source to the manifest's `sources` array:

```json
{
  "source_id": "vllm-snapshot-1",
  "path": "service.prom",
  "format": "prometheus-text",
  "endpoint_id": "engine-instance-a",
  "version": "your-actual-engine-and-exporter-version",
  "captured_at_ns": 1792720800000000000
}
```

Use `prometheus-range-json` for a successful query_range matrix response, which carries its own
sample timestamps. `captured_at_ns` is required to place an untimestamped text snapshot on a
time axis; omit it if unknown. Never substitute the import time for the original capture time.
Values are serialized as decimal strings, nonfinite values as null, and epoch timestamps as exact
integer nanoseconds. Labels, HELP/TYPE metadata and declared versions are retained where present.

```sh
uv run agent-infertriage analyze evidence/task --output-dir results/task
```

The bundled `examples/with-prometheus` is an explicitly synthetic example: service queue pressure
is visible, but no per-request server queue/profile is supplied, so global benefit remains unknown.

## Explicit one-shot collection

```sh
# Existing exporter: one GET, not a new exporter/daemon
uv run agent-infertriage capture-metrics http://127.0.0.1:8000/metrics \
  --output-file evidence/task/service.prom

# Existing Prometheus: one query_range call over an explicit bounded window
uv run agent-infertriage capture-metrics http://127.0.0.1:9090 \
  --query 'rate(vllm:prompt_tokens_total[1m])' \
  --start 1792720800 --end 1792721100 --step 15s \
  --output-file evidence/task/range.json
```

Metric names are illustrative; use names from your actual exporter/version. The command prints
capture timestamp, size, series count, elapsed capture time and zero new inference tokens. Preserve
that capture timestamp in the manifest. It does **not** edit an existing manifest for you.
Collection may load your metrics service; no query-cost accounting is claimed.

The HTTP client has a timeout, a 100 MiB response limit, no hidden retries, no redirects and no
ambient proxy discovery. It refuses to overwrite a file. The alpha does not have a Prometheus
auth/tenant/config integration; for secured services use already-authorized exported files.
Do not expose a private metrics endpoint merely to use this tool.

## Interpretation rules

- A single untimestamped counter snapshot cannot produce a rate or recent-window hit ratio.
- Text counters at a declared common endpoint may yield interval rates. A negative jump is a
  reset; that interval is excluded rather than turned into negative usage or guessed throughput.
- query_range results are deliberately `untyped`: PromQL may already have transformed a counter.
  No additional rate is applied based only on the metric name.
- Sources without an endpoint identity are not merged across source IDs. Existing instance/model
  labels remain part of the series identity. Conflicting engine version/type metadata disables
  cross-snapshot rates. Do not use the same endpoint identity for different restarted instances
  unless existing labels distinguish them.
- Histogram buckets and summary quantiles remain aggregate series. No synthetic request IDs,
  queue times or per-Agent cache rates are reconstructed from them or from time proximity.
- Service metrics alone do not populate request-owned token/cost, stage elapsed time, L2/L3/L4,
  or an execution DAG. Model tools only read attached metrics; they cannot invoke collection.
- Missing/invalid optional metrics generate a warning and preserve valid trace analysis. Paths
  escaping the evidence package and oversized inputs remain validation failures.

Version-specific unit/denominator verification, time-window correlation with calibrated traces,
anomaly diagnosis, exemplar navigation, and production adapters remain follow-up work. This alpha
is a source reader and guarded presentation layer, not yet a full cross-source causal correlator.

## Upstream references

The text reader uses the [official Python client parser](https://prometheus.github.io/client_python/parser/).
The range reader follows the [Prometheus HTTP API](https://prometheus.io/docs/prometheus/latest/querying/api/).
Verify metric names and semantics using your version of
[vLLM production metrics](https://docs.vllm.ai/en/latest/usage/metrics/) or
[SGLang production metrics](https://docs.sglang.io/docs/references/production_metrics).
