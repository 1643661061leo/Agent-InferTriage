# Evidence contract v1 (alpha)

The executable models in `src/agent_infertriage/models.py` and generated `schemas/` define field
names. Unknown fields are rejected. This is a pre-release contract, not a frozen compatibility API.
Use integer nanoseconds, explicit clock domains and Decimal-string currency values.

## Native package

```text
manifest.json          schema_version=1.0, workloads, sources, clocks, events_file, content_policy
events.jsonl           one typed record per line
source files           optional logs, metrics or original trace exports, inside this directory
```

`examples/native` is a complete synthetic package. Raw source paths may not escape the package,
including via symlinks. Source IDs use letters, digits, `_`, `.`, `-`; `events` is reserved.
The default import limit is 100 MiB total and 100,000 normalized records. Metrics also have a
100,000-series/sample ceiling. The alpha does not yet expose command-line overrides for these
limits. Inputs above limits are rejected, not silently cut into partial workloads.

## Record ownership

| record_type | Required identity | Meaning |
| --- | --- | --- |
| span | record_id, workload_id, span_id | Observed interval/operation; parent_id means containment only |
| activity | record_id, workload_id, activity_id, duration_ns | Non-duplicated execution weight for an explicit DAG |
| dependency | record_id, workload_id, from_id, to_id | Explicit causal edge, optional nonnegative min_lag_ns |
| usage | record_id, workload_id, request_id, attempt_id | One request attempt owns usage; duplicates merge or conflict |
| cache | record_id, workload_id, cache_type, scope, unit | Deltas, gauges or cumulative snapshots, never blindly mixed |
| cost | record_id, workload_id, owner_event, amount, currency, pricing_ref | Primary charge, alternative cost basis, or breakdown |
| content | record_id, workload_id, request_id, role | Optional supplied content/token IDs for deterministic rules |

IDs are unique within their documented scope; retry attempts are separate from rounds and engine
iterations. `request_refs` enables one device activity to refer to several requests without copying
the physical interval. `execution_key` detects duplicate physical activities in a proposed DAG.

Every source-derived record should carry resolvable `source_refs`; native records without that
field get `events:line:N`. Explicit synthetic fixtures use `synthetic:*` references.

## Observability and timing

`Span.observation_level` optionally declares L0, L2 or L3. L1 comes from metrics, not a fabricated
span. L4 requires additional verified workload flags and a qualified graph; it is never inferred
just from the existence of Kernel names. Imported opaque spans keep their supplied fields.

Client operations use `llm`/`chat`/`generate`/`inference`/`model`. Server phase operations are
`queue`, `prefill`, `decode` with `observation_level="L2"`, matching request/attempt IDs and
complete intervals. Repeated phase events retain occurrence/iteration IDs and use same-clock
interval union, not a sum that double-counts overlap. TTFT needs an explicit `client_ttft_ns` event.

`stages` is a view into orchestration/prompt/cache/scheduling/kernel, not an exclusive partition.
Self time requires `attributes.children_complete=true` and valid complete child intervals.
All span elapsed values remain available; union/p50/p95 are labeled with sample size/method.
Unaligned clocks prevent cross-domain exact unions. OTLP resources get separate unverified clocks.

The task boundary may include cold start; `operation="service_startup"` spans are separately
listed and never labeled prefill/decode automatically.

## Usage and money

Normalized input already includes cache-read/write subsets; normalized output includes reasoning.
Total tokens are input + output, not input + cached + output + reasoning. Unsupported semantics
must be marked unknown. Distinct attempts count separately; copies of the same attempt do not.
Conflicting normalized ownership/counts make the merged usage unknown.

Cache rates are weighted hits/queries for compatible delta groups; cumulative/gauge observations,
overlapping intervals and mixed units are not combined into a global ratio. Prefix, KV and tool
cache are separate. Rates without denominators remain unknown.

Request-owned costs use `owner_event="request:REQUEST_ID:ATTEMPT_ID"`. A billed primary charge
takes precedence over its estimate; conflicting primary charges remain unknown. Alternative GPU
pricing and Kernel breakdowns are retained but not added to the primary charge again. Currencies
remain separate. Stage/round costs are attribution views and not additive across overlapping views.

`Metric.status` is reported/derived/estimated/unknown/not_applicable. Unknown/N/A require null
values. `known_subtotal` and `unknown_count` preserve incomplete accounting. A reported zero is
different from no data.

## Execution model and ceilings

Only explicit activities/dependencies participate. `graph_complete=true` is a source assertion,
not something the importer can prove. Inferred edges, duplicates, cycles, dangling edges,
unavailable clocks or baseline fit error beyond max(1 ms, 1% observed) plus clock uncertainty
make global ceilings unknown. Small within-tolerance residuals are disclosed.

For each activity, earliest start is max(release_ns, predecessor finishes + edge lag); task duration
is the longest finish. Scenario weights are zero for elimination or rounded integer duration/s
for s >= 1. Recompute the whole graph, including critical-branch changes. No new scheduling or
Agent decisions are simulated; outcomes are conditional fixed-graph ceilings, not predictions.

L4 additionally needs `cross_layer_links_verified` and `clock_alignment_verified`, plus available
L0/L2/L3 evidence. These flags are assertions by the evidence producer, not automatic verification
of every ID. Do not set them just to enable a report label.

## OTLP JSON/JSONL adapter

Accepts OTLP/HTTP JSON `resourceSpans/scopeSpans/spans` envelopes, not arbitrary vendor JSON.
Trace/span IDs must be hexadecimal with the correct length. Float nanosecond timestamps are
rejected. Resources, scope, attributes, links/events/status and original source are preserved.
No exporter-independent GenAI convention compatibility is claimed.

Default mappings include gen_ai.operation.name, gen_ai.request.model, gen_ai.provider.name,
gen_ai.usage.input_tokens/output_tokens and gen_ai.response.id; custom infertriage attributes
carry agent/round/attempt/stages/observation_level. Override using the mapping TOML. Unsupported
attributes are preserved and listed. No request graph is inferred from span links or parents.

## Diagnosis journal and receipt

The append-only journal persists every sending/terminal attempt using a stable attempt ID.
Failed requests may have unknown usage. No hidden HTTP retries are enabled. The same final receipt
is used by terminal, JSON and escaped HTML. Recovery merges durable events and never replays
requests. Hard termination can lose the final usage update; recovered time is a lower bound.
Recovery of an incomplete journal currently has no original pricing configuration, so prices
stay unknown even if the interrupted process had a price file. Finalized receipts retain costs.

The receipt is finalized before final file I/O. A disk failure can therefore leave a durable
receipt without the complete report set; recover the journal. It cannot guarantee durable writes
when the disk itself is unavailable. The Recorder export on context exit is not a crash-safe
live streaming collector; abrupt process termination before export may lose its evidence.
