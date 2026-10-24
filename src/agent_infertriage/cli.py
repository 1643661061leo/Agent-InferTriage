"""CLI pipelines; all quantitative behavior lives in shared modules."""

import json
import tomllib
from pathlib import Path
from typing import Annotated, Any

import typer

from agent_infertriage.accounting import load_prices
from agent_infertriage.agent import AgentConfig, Provider, run_agent
from agent_infertriage.analysis import analyze
from agent_infertriage.demo import make_demo
from agent_infertriage.diagnosis import Session
from agent_infertriage.diagnosis import recover as recover_session
from agent_infertriage.evidence import load, load_otlp, save_native, select, validate
from agent_infertriage.models import AnalysisConfig, AnalysisResult, EvidenceSet
from agent_infertriage.reports import finish_reports, print_summary

app = typer.Typer(no_args_is_help=True, help="Cross-layer performance triage for AI agents.")


@app.command("capture-metrics")
def capture_metrics_command(
    url: str,
    output_file: Annotated[Path, typer.Option()],
    query: str | None = None,
    start: str | None = None,
    end: str | None = None,
    step: str = "15s",
) -> None:
    """Explicitly read an existing /metrics endpoint or Prometheus query_range API once."""
    from agent_infertriage.prometheus import capture

    try:
        result = capture(url, output_file, query, start, end, step)
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        typer.echo(f"Metrics capture failed: {type(exc).__name__}", err=True)
        raise typer.Exit(2) from exc


def pipeline(
    path: Path | None,
    output_dir: Path,
    mode: str = "offline",
    format_name: str = "native",
    model_config: Path | None = None,
    price_file: Path | None = None,
    rules_file: Path | None = None,
    workload_id: str | None = None,
    all_workloads: bool = False,
    demo: bool = False,
    provider: Provider | None = None,
) -> tuple[dict[str, Any], int]:
    session = Session(output_dir, mode)
    result: AnalysisResult | None = None
    agent_result: dict[str, Any] = {"status": "not_requested"}
    status, error, code = "completed", "", 0
    try:
        if mode not in ("offline", "agent"):
            raise ValueError("Mode must be offline or agent")
        session.event("phase", name="ingesting")
        evidence: EvidenceSet
        if demo:
            save_native(make_demo(), output_dir / "demo-evidence")
            evidence = load(output_dir / "demo-evidence")
        else:
            if path is None:
                raise ValueError("Input is required")
            evidence = load(path, format_name)
        known = {w.workload_id for w in evidence.manifest.workloads}
        if workload_id:
            if workload_id not in known:
                raise ValueError("Unknown workload_id")
            evidence = select(evidence, {workload_id})
        elif len(known) > 1 and not all_workloads:
            raise ValueError("Select --workload or pass --all for multiple workloads")
        config_data: dict[str, Any] = {}
        if rules_file:
            with rules_file.open("rb") as handle:
                config_data = tomllib.load(handle)
        config = AnalysisConfig.model_validate(
            config_data | {"price_file": str(price_file) if price_file else None}
        )
        session.prices = load_prices(config.price_file)
        session.check_deadline()
        session.event("phase", name="analyzing")
        result = analyze(evidence, config)
        session.check_deadline()
        if mode == "agent":
            if model_config is None:
                raise ValueError("--model-config is required for agent mode")
            agent_result = run_agent(
                evidence,
                result,
                session,
                AgentConfig.from_file(model_config),
                **({"provider": provider} if provider else {}),
            )
            status = agent_result["status"]
            if status != "completed":
                error = agent_result.get("reason", "Agent analysis incomplete")
                code = 3 if status == "partial" else 1
    except KeyboardInterrupt:
        status, error, code = "cancelled", "Diagnosis cancelled by user", 130
    except TimeoutError as exc:
        status, error, code = "partial", str(exc), 3
    except (ValueError, OSError, KeyError, TypeError) as exc:
        status, error, code = "failed", str(exc), 2
    except Exception as exc:
        status, error, code = "failed", f"Unexpected {type(exc).__name__}: {exc}", 1
    report = finish_reports(session, result, agent_result, status, error)
    return report, code


@app.command("analyze")
def analyze_command(
    path: Path,
    output_dir: Annotated[Path, typer.Option(help="A new output directory")],
    mode: str = "offline",
    format_name: Annotated[str, typer.Option("--format")] = "native",
    model_config: Path | None = None,
    price_file: Path | None = None,
    rules_file: Path | None = None,
    workload_id: Annotated[str | None, typer.Option("--workload")] = None,
    all_workloads: Annotated[bool, typer.Option("--all")] = False,
) -> None:
    try:
        report, code = pipeline(
            path,
            output_dir,
            mode,
            format_name,
            model_config,
            price_file,
            rules_file,
            workload_id,
            all_workloads,
        )
        print_summary(report, output_dir)
    except OSError as exc:
        typer.echo(f"Cannot create/write diagnosis output: {exc}", err=True)
        raise typer.Exit(2) from exc
    raise typer.Exit(code)


@app.command()
def demo(output_dir: Annotated[Path, typer.Option(help="A new output directory")]) -> None:
    """Generate a synthetic three-round, five-layer evidence/report example, offline."""
    try:
        report, code = pipeline(None, output_dir, demo=True)
        print_summary(report, output_dir)
    except OSError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    raise typer.Exit(code)


@app.command("demo-agent")
def demo_agent(output_dir: Annotated[Path, typer.Option(help="A new output directory")]) -> None:
    """Exercise the real analysis loop with a clearly labeled local mock model; no network."""
    from agent_infertriage.demo_agent import mock_provider

    # No HTTP call: this config is passed only to the explicitly injected mock provider.
    try:
        session = Session(output_dir, "agent-mock")
        evidence = make_demo()
        result = analyze(evidence)
        interpreted = run_agent(
            evidence,
            result,
            session,
            AgentConfig(
                endpoint="http://unused.invalid", model="synthetic-mock", provider="synthetic-mock"
            ),
            mock_provider(),
        )
        report = finish_reports(session, result, interpreted, interpreted["status"])
        print_summary(report, output_dir)
    except OSError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    raise typer.Exit(0 if interpreted["status"] == "completed" else 3)


@app.command("validate")
def validate_command(path: Path) -> None:
    """Validate native evidence without network or model calls."""
    try:
        evidence = load(path)
        typer.echo(
            json.dumps(
                {
                    "status": "valid",
                    "warnings": validate(evidence),
                    "import_report": evidence.import_report,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    except (ValueError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc


@app.command("import")
def import_command(
    path: Path,
    output_dir: Annotated[Path, typer.Option()],
    format_name: Annotated[str, typer.Option("--format")] = "otlp-json",
    mapping: Path | None = None,
) -> None:
    """Import OTLP JSON into a native evidence package, preserving source data."""
    try:
        if format_name != "otlp-json":
            raise ValueError("Supported import format: otlp-json")
        evidence = load_otlp(path, mapping)
        save_native(evidence, output_dir)
        typer.echo(json.dumps(evidence.import_report, ensure_ascii=False, indent=2))
    except (ValueError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc


@app.command("recover")
def recover_command(path: Path) -> None:
    """Recover durable diagnosis usage without replaying any model request."""
    try:
        result = recover_session(path)
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    if result["status"] == "incomplete":
        raise typer.Exit(3)
