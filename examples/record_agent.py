"""No-network instrumentation example. Replace the local iterator with your actual SDK call."""

import argparse
from pathlib import Path

from agent_infertriage.recorder import Recorder


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    with Recorder(args.output_dir, name="Locally timed synthetic client example") as recorder:
        with recorder.startup_phase("readiness"):
            pass  # Wrap actual readiness waiting here; this example starts no service.
        for round_no in range(1, 4):
            with recorder.model_call(agent_id="example", round_id=str(round_no)) as call:
                for _chunk in ("synthetic", " reply"):
                    call.first_token()  # Idempotent; call only for nonempty content tokens.
                call.usage(
                    input_tokens=100 * round_no,
                    output_tokens=2,
                    provider="synthetic",
                    model="local-iterator",
                )
    print(f"Saved client evidence to {args.output_dir}; no model or service was called.")


if __name__ == "__main__":
    main()
