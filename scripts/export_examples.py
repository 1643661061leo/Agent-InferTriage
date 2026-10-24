"""Generate canonical schemas and first-use fixtures; refuses to overwrite evidence packages."""

import json
from pathlib import Path

from pydantic import TypeAdapter

from agent_infertriage.demo import make_demo
from agent_infertriage.evidence import save_native
from agent_infertriage.models import AnalysisResult, Manifest, RecordType, Source


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    schemas = root / "schemas"
    schemas.mkdir(exist_ok=True)
    for name, schema in {
        "manifest": Manifest.model_json_schema(),
        "record": TypeAdapter(RecordType).json_schema(),
        "analysis": AnalysisResult.model_json_schema(),
    }.items():
        (schemas / f"{name}.schema.json").write_text(json.dumps(schema, indent=2) + "\n")
    native = root / "examples" / "native"
    if not native.exists():
        save_native(make_demo(), native)
    prom = root / "examples" / "with-prometheus"
    if not prom.exists():
        e = make_demo()
        e.activities.clear()
        e.dependencies.clear()
        e.manifest.workloads[0].graph_complete = False
        e.spans = [s for s in e.spans if s.operation in ("agent", "llm", "tool")]
        for span in e.spans:
            span.observation_level = "L0"
            span.attributes.pop("children_complete", None)
        e.caches.clear()
        e.manifest.sources.append(
            Source(
                source_id="service",
                path="service.prom",
                format="prometheus-text",
                captured_at_ns=1792720800000000000,
                endpoint_id="synthetic-engine",
                version="synthetic-unverified-engine",
            )
        )
        e.sources["service"] = str(root / "examples" / "service.prom")
        save_native(e, prom)


if __name__ == "__main__":
    main()
