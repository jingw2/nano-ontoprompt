"""CLI entrypoint for the business-journey acceptance runner.

Exactly two phases, kept structurally separate so a CI job cannot silently
combine them:

* ``--phase prepare`` builds the pre-browser baseline via `prepare_journey`/
  `prepare_all_journeys`. It accepts `--model-id` (must be the one official
  `MODEL_ID` — anything else is rejected before any HTTP call is made).
* ``--phase verify`` reads persisted post-browser evidence via
  `verify_journey`/`verify_all_journeys`. It never calls the model, and
  supplying `--model-id` alongside it is a parser-level error, not a
  silently-ignored flag.

Supports exactly ``--phase``, ``--journey``, ``--model-id`` (prepare only),
``--api-base``, ``--output``, and ``--run-id`` — no ``--api-key``/model
endpoint flag exists on this CLI at all. `DEEPSEEK_API_KEY` (the provider
key `prepare_journey` reads directly, per its own contract) and the
application credential (`orchestrator.APPLICATION_API_KEY_ENV_VAR`) are both
read from the environment rather than a command-line flag, so neither secret
is ever visible in argv/process listings/shell history — the same
discipline `DeepSeekVisionClient` already applies to the provider key.
`verify_journey`'s own function signature has no `api_key` parameter either
(consistent with the brief's exact interface), but it internally
authenticates by reading the SAME `APPLICATION_API_KEY_ENV_VAR` this module
also reads for `prepare_journey` — every endpoint it reads requires a
signed-in caller.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .artifacts import assemble_journey_evidence
from .contracts import MODEL_ID
from .orchestrator import (
    APPLICATION_API_KEY_ENV_VAR,
    JourneyAcceptanceError,
    prepare_all_journeys,
    prepare_journey,
    read_run_manifest,
    verify_all_journeys,
    verify_journey,
)

JOURNEY_CHOICES = ("supply_chain", "finance", "credit", "all")

# Where the CI gate's scanner (`evals.business_journeys.artifacts scan
# --staging ...`) expects one closed-schema `ArtifactAllowlist` JSON file per
# journey, written only after `--phase verify --journey all` has itself
# passed (`orchestrator.STAGING_RELATIVE_PATH`'s own parent directory).
_STAGING_RELATIVE_DIR = Path("business_journeys") / "staging"


def _write_staging_evidence(output_dir: Path, run_id: str, verifications: list) -> None:
    """Assemble and write the scanner's real input: one `ArtifactAllowlist`
    per journey, projected from the persisted prepare (`run.json`) and the
    just-computed verify records. Never touches `run.json` itself -- this is
    an additive write next to it."""
    run_manifest = read_run_manifest(output_dir)
    preparations_by_journey = {p["journey_id"]: p for p in run_manifest["preparations"]}
    staging_dir = Path(output_dir) / _STAGING_RELATIVE_DIR
    staging_dir.mkdir(parents=True, exist_ok=True)
    for verification in verifications:
        journey_id = verification.journey_id
        preparation = preparations_by_journey.get(journey_id)
        if preparation is None:
            raise JourneyAcceptanceError(f"PREPARATION_MISSING_FOR_VERIFICATION: {journey_id}")
        evidence = assemble_journey_evidence(
            run_id=run_id, journey_id=journey_id,
            preparation=preparation, verification=verification.to_dict(),
        )
        (staging_dir / f"{journey_id}.json").write_text(
            evidence.model_dump_json(indent=2), encoding="utf-8",
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.business_journeys.run",
        description="Prepare or verify the real-model business-journey acceptance baseline.",
    )
    parser.add_argument("--phase", required=True, choices=("prepare", "verify"))
    parser.add_argument("--journey", required=True, choices=JOURNEY_CHOICES)
    parser.add_argument("--api-base", required=True, help="Application-under-test base URL.")
    parser.add_argument("--output", required=True, type=Path, help="Artifact output directory.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--model-id", default=None,
        help="Prepare-phase only: must be the exact official MODEL_ID if supplied.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.phase == "verify" and args.model_id is not None:
        parser.error("--model-id is only accepted with --phase prepare")
    if not args.run_id.strip():
        # An empty run_id fails closed here rather than silently: it would
        # otherwise reach `app.tasks.agent_turn._resolve_business_journey`'s
        # `if not run_id` truthiness check, taking the turn quietly down the
        # generic (non-journey) path with no error raised anywhere.
        parser.error("--run-id must not be empty")

    try:
        if args.phase == "prepare":
            model_id = args.model_id or MODEL_ID
            api_key = os.environ.get(APPLICATION_API_KEY_ENV_VAR) or ""
            if not api_key:
                raise JourneyAcceptanceError(
                    f"APPLICATION_API_KEY_REQUIRED: set {APPLICATION_API_KEY_ENV_VAR}"
                )
            if args.journey == "all":
                preparations = prepare_all_journeys(
                    api_base=args.api_base, api_key=api_key, output_dir=args.output,
                    run_id=args.run_id, model_id=model_id,
                )
                document = {"preparations": [p.to_dict() for p in preparations]}
            else:
                preparation = prepare_journey(
                    args.journey, api_base=args.api_base, api_key=api_key,
                    output_dir=args.output, run_id=args.run_id, model_id=model_id,
                )
                document = preparation.to_dict()
        else:
            if args.journey == "all":
                verifications = verify_all_journeys(
                    api_base=args.api_base, output_dir=args.output, run_id=args.run_id,
                )
                _write_staging_evidence(args.output, args.run_id, verifications)
                document = {"verifications": [v.to_dict() for v in verifications]}
            else:
                verification = verify_journey(
                    args.journey, api_base=args.api_base, output_dir=args.output, run_id=args.run_id,
                )
                document = verification.to_dict()
    except JourneyAcceptanceError as exc:
        print(f"FAIL {args.phase} {args.journey}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(document, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
