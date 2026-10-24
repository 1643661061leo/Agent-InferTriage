"""Self-contained, escaped HTML and one shared accounting receipt."""

import hashlib
import json
from pathlib import Path
from typing import Any

from jinja2 import Environment
from rich.console import Console
from rich.table import Table

from agent_infertriage.diagnosis import Session, atomic_json
from agent_infertriage.models import AnalysisResult

TEMPLATE = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy"
content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>agent-InferTriage report</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{background:#10151d;color:#dce5f0;
font:15px/1.6 system-ui;margin:0 auto;max-width:1200px;padding:32px}h1{font-size:30px}
h2{margin-top:36px;color:#a9d5ff}table{width:100%;border-collapse:collapse;margin:16px 0}
td,th{text-align:left;vertical-align:top;padding:9px;border-bottom:1px solid #334052}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.6 ui-monospace;background:#192230;
padding:16px;border-radius:8px}details{padding:10px;background:#151e2a;margin:8px 0}
summary{cursor:pointer}.muted{color:#99aabc}.unknown{color:#ffc479}.observed{color:#87d9ba}
.bar{height:14px;background:#6aacf0;border-radius:3px;min-width:1px}
.notice{border-left:3px solid #ffc479;padding:12px;background:#292419}
</style>
<h1>agent-InferTriage</h1>
<p class="muted">Historical workload / independent diagnosis accounting</p>
{% if error %}<p class="notice">{{error}}</p>{% endif %}
{% for warning in analysis.warnings %}<p class="notice">{{warning}}</p>{% endfor %}
{% for w in analysis.workloads %}
<h2>{{w.workload.name or w.workload.workload_id}}</h2>
<p>ID: {{w.workload.workload_id}} · Graph: {{w.graph.status}}</p>
{% if w.workflow_timing %}
<h3>Business stopwatch: boundaries and observed contributors</h3>
<pre>{{pretty(w.workflow_timing.boundaries)}}</pre>
<p>Total: {{w.workflow_timing.total_ns}} ns · Observed interval fraction:
{{w.workflow_timing.observed_fraction}} (not instrumentation completeness)</p>
<p class="muted">Most-specific observed wall intervals, not CPU self time or causal savings.
Model API wait is not pure thinking. Empty gaps are not handoffs.
Parallel work is not added twice.</p>
<table><tr><th>Step kind</th><th>Observed seconds</th><th>Wall fraction</th></tr>
{% for c in w.workflow_timing.contributors %}<tr><td>{{c.kind}}</td>
<td>{{'%.6f'|format(c.observed_ns / 1000000000)}}</td><td>{{c.wall_fraction}}</td></tr>
{% endfor %}</table>
<details><summary>All timed steps / rounds / attempts</summary>
<p>Inclusive times below overlap parent phases; do not sum this table.</p>
<table><tr><th>Step</th><th>Kind</th><th>Agent / round</th>
<th>Elapsed ms</th><th>Input / output tokens</th></tr>
{% for s in w.workflow_timing.steps %}<tr><td>{{s.name}}</td><td>{{s.kind}}</td>
<td>{{s.agent_id}} / {{s.round_id}}</td><td>{{'%.3f'|format(s.inclusive_ns / 1000000)}}</td>
<td>{{s.input_tokens}} / {{s.output_tokens}}</td></tr>{% endfor %}</table>
<details><summary>Raw step records and source references</summary>
<pre>{{pretty(w.workflow_timing.steps)}}</pre></details>
</details>
<details><summary>Startup phase envelopes (overlap child steps; not additive)</summary>
<pre>{{pretty(w.workflow_timing.phase_envelopes)}}</pre></details>
<details><summary>Bottleneck candidates (observations, not proven causes)</summary>
<pre>{{pretty(w.workflow_timing.candidates)}}</pre></details>
<details><summary>Unattributed gaps / parallel interval partition</summary>
<pre>{{pretty(w.workflow_timing.partition)}}</pre></details>
<details><summary>L1 service windows (not request latency)</summary>
<pre>{{pretty(w.service_windows)}}</pre></details>
{% endif %}
<details open><summary>Observability L0–L4 · available evidence,
not a completeness guarantee</summary>
<pre>{{pretty(w.observability)}}</pre></details>
<details><summary>Cold start / service lifecycle (separate from inference)</summary>
<pre>{{pretty(w.service_lifecycle)}}</pre></details>
<details><summary>Per-request client latency / TTFT and server phases</summary>
<pre>{{pretty(w.request_timings)}}</pre></details>
<details open><summary>Workload tokens, cache and cost (not diagnosis usage)</summary>
<pre>{{ pretty({'tokens':w.tokens, 'caches':w.caches, 'cost':w.cost}) }}</pre></details>
<table><thead><tr><th>Layer</th><th>Coverage</th><th>Activities</th>
<th>Time, tokens, cache, cost</th></tr></thead>
<tbody>{% for layer, state in w.coverage.items() %}<tr><td>{{layer}}</td>
<td class="{{state.status}}">{{state.status}}<br><small>{{state.collection_hint}}</small></td>
<td>{{w.stages[layer].span_count}}</td><td><details><summary>Inspect measurements</summary>
<pre>{{ pretty(w.stages[layer]) }}</pre></details></td></tr>{% endfor %}</tbody></table>
<h3>All rounds and invocations</h3>
{% for round in w.rounds %}<details><summary>Agent {{round.agent_id or 'unknown'}} ·
Round {{round.round_id or 'unknown'}} · {{round.span_ids|length}} spans</summary>
<pre>{{pretty(round)}}</pre>
{% for s in w.spans if s.span_id in round.span_ids %}<details><summary>{{s.name or s.span_id}}
· stage {{s.stages|join(', ')}} · request {{s.request_id or 'not associated'}}</summary>
<pre>{{pretty(s)}}</pre></details>{% endfor %}</details>{% endfor %}
<h3>Observed timeline</h3>
<p class="muted">Width is elapsed time. Nested and parallel rows are not additive.</p>
{% for row in timelines[w.workload.workload_id] %}<div>{{row.label}}
<div style="margin-left:{{row.left}}%;width:{{row.width}}%" class="bar"></div></div>
{% else %}<p>Timeline placement unavailable: missing intervals or multiple clock domains.
Inspect the individual span timings; no cross-clock alignment is inferred.</p>{% endfor %}
<details><summary>Execution dependencies / conditional layer upper bounds</summary>
<pre>{{pretty({'graph':w.graph,'dependencies':w.dependencies,'layer_bounds':w.layer_bounds})}}</pre>
</details>
<p>Unknown improvement space: {{w.unknown_layers|join(', ') or 'none'}}.
Individual upper bounds overlap and must not be summed.</p>
<h3>Where to investigate next</h3>
<p class="muted">Order by conditional modeled ceiling, not guaranteed achievable benefit.
Data quality, implementation effort and correctness risk still need review.</p>
<ol>{% for item in w.priority %}<li>{{item.layer}} · at most
{{'%.3f'|format(item.benefit.saved_ns / 1000000000)}}s under the fixed-DAG assumptions</li>
{% else %}<li>No supported cross-layer ranking. Collect missing request/graph evidence.</li>
{% endfor %}</ol>
{% endfor %}
<h2>Evidence-backed findings</h2>
{% for f in analysis.findings %}<details open><summary>{{f.rule_id}} · {{f.title}}</summary>
<pre>{{pretty(f)}}</pre></details>{% else %}
<p>No supported rule findings. This does not prove optimal performance.</p>{% endfor %}
<details><summary>Metric registry and source references</summary>
<pre>{{pretty(analysis.metrics)}}</pre><pre>{{pretty(analysis.evidence_index)}}</pre></details>
<h2>Agent-assisted interpretation</h2><p class="muted">Model hypotheses are not measured facts.</p>
<pre>{{pretty(agent)}}</pre>
<h2>Prometheus service evidence</h2>
<p class="muted">Service/window aggregates.
They do not replace per-request traces or Kernel profiles.</p>
{% for series in analysis.service_metrics %}<details>
<summary>{{series.name}} · {{series.scope}}</summary>
<pre>{{pretty(series)}}</pre></details>{% else %}<p>No Prometheus evidence supplied.</p>{% endfor %}
<h2>This diagnosis: token, time and cost receipt</h2>
<p class="muted">Includes retries and final model response; excludes historical workload tokens.</p>
<pre id="diagnosis-receipt">__INFERTRIAGE_RECEIPT_SLOT_42D9__</pre>
<p>Generated locally. No external scripts, fonts, images or analytics.</p></html>"""


def pretty(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def prepare_html(report: dict[str, Any]) -> str:
    timelines: dict[str, list[dict[str, Any]]] = {}
    for workload in report["analysis"].get("workloads", []):
        spans = workload["spans"]
        known = [s for s in spans if s["start_ns"] is not None and s["end_ns"] is not None]
        if len({s["clock_domain"] for s in known}) > 1:
            known = []  # Do not imply an alignment that was not preserved in this view.
        begin = min((s["start_ns"] for s in known), default=0)
        duration = max((s["end_ns"] for s in known), default=begin) - begin
        timelines[workload["workload"]["workload_id"]] = [
            {
                "label": s["name"] or s["span_id"],
                "left": round((s["start_ns"] - begin) / max(1, duration) * 100, 3),
                "width": round((s["end_ns"] - s["start_ns"]) / max(1, duration) * 100, 3),
            }
            for s in known
        ]
    env = Environment(autoescape=True)
    return env.from_string(TEMPLATE).render(**report, pretty=pretty, timelines=timelines)


def finish_reports(
    session: Session,
    result: AnalysisResult | None,
    agent: dict[str, Any],
    status: str,
    error: str = "",
) -> dict[str, Any]:
    from html import escape

    analysis = (
        result.model_dump(mode="json")
        if result
        else {"workloads": [], "findings": [], "metrics": {}, "warnings": [], "evidence_index": {}}
    )
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "analysis": analysis,
        "agent": agent,
        "error": error,
    }
    html = prepare_html(
        report
    )  # Account for expensive report-body rendering before freezing usage.
    session.config_digest = result.config_digest if result else ""
    receipt = session.finalize(
        status, [w["workload"]["workload_id"] for w in analysis["workloads"]], error
    )
    digest = hashlib.sha256(pretty(receipt).encode()).hexdigest()
    report["diagnosis"] = receipt
    report["receipt_digest"] = digest
    atomic_json(session.output_dir / "report.json", report)
    atomic_json(session.output_dir / "evidence_index.json", analysis["evidence_index"])
    html = html.replace(
        '<pre id="diagnosis-receipt">__INFERTRIAGE_RECEIPT_SLOT_42D9__</pre>',
        '<pre id="diagnosis-receipt">' + escape(pretty(receipt)) + "</pre>",
        1,
    )
    html += f"\n<!-- receipt-sha256:{digest} -->\n"
    temporary = session.output_dir / "report.html.tmp"
    temporary.write_text(html, encoding="utf-8")
    temporary.replace(session.output_dir / "report.html")
    steps = [
        e
        for e in session.events
        if e["type"] in ("agent_round", "agent_action", "tool_finished", "model_attempt")
    ]
    with (session.output_dir / "agent_steps.jsonl").open("w", encoding="utf-8") as handle:
        for step in steps:
            handle.write(json.dumps(step, ensure_ascii=False) + "\n")
    return report


def print_summary(report: dict[str, Any], output_dir: Path) -> None:
    console = Console()
    console.print("agent-InferTriage", style="bold cyan")
    for workload in report["analysis"].get("workloads", []):
        workflow = workload.get("workflow_timing", {})
        if workflow.get("total_ns") is not None:
            console.print(
                f"Business stopwatch: {workflow['total_ns'] / 1e9:.6f}s "
                f"· {workflow['boundaries']['start']} → {workflow['boundaries']['end']}"
            )
            console.print(
                "Observed contributors: "
                + ", ".join(
                    f"{c['kind']}={c['observed_ns'] / 1e9:.3f}s"
                    for c in workflow["contributors"][:5]
                )
            )
        console.print(
            "Observability: "
            + " · ".join(
                f"{level} {state['status']}" for level, state in workload["observability"].items()
            )
        )
        console.print(
            f"Request attempts: {len(workload['request_timings'])}; "
            "client latency/TTFT and startup details are in the report"
        )
        table = Table(title=workload["workload"]["workload_id"])
        table.add_column("Layer")
        table.add_column("Coverage")
        table.add_column("Conditional saving upper bound")
        for layer, coverage in workload["coverage"].items():
            benefit = next(b["benefit"] for b in workload["layer_bounds"] if b["layer"] == layer)
            saved = benefit.get("saved_ns")
            table.add_row(
                layer,
                coverage["status"],
                f"{saved / 1e9:.3f}s (fixed DAG)" if saved is not None else "unknown",
            )
        console.print(table)
    if report["analysis"].get("service_metrics"):
        console.print(
            f"Prometheus: {len(report['analysis']['service_metrics'])} service-level series; "
            "not per-request measurements"
        )
    receipt = report["diagnosis"]
    console.print(f"Diagnosis status: {receipt['status']} · mode: {receipt['mode']}")
    token_label = (
        "Synthetic mock tokens (not consumed)" if receipt.get("is_mock") else "New model tokens"
    )
    console.print(
        f"{token_label}: {receipt['total_tokens']} "
        f"(known subtotal: {receipt['known_token_subtotal']}; "
        f"unknown calls: {receipt['unknown_usage_call_count']})"
    )
    console.print(
        f"Diagnosis wall time: {receipt['wall_time_ms']:.2f} ms; "
        f"model attempts: {receipt['model_call_count']}"
    )
    cost = receipt["cost"]
    console.print(
        "Diagnosis cost: "
        + json.dumps(
            {
                "status": cost["status"],
                "known_subtotal_by_currency": cost["known_subtotal_by_currency"],
                "unpriced_item_count": len(cost["unpriced_items"]),
            },
            ensure_ascii=False,
        )
    )
    console.print("Receipt ID: " + receipt["receipt_id"])
    console.print("Report: " + str(output_dir / "report.html"), markup=False)
    if report.get("error"):
        console.print("Error: " + report["error"], markup=False)
