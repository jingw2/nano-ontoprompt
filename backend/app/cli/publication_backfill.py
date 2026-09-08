"""Resumable governed ontology identity backfill CLI.

`python -m app.cli.publication_backfill run [--ontology-id ID] [--batch-size N] [--actor USER_ID]`
loops over cursor batches until the inventory converges and prints each
batch report as JSON.
"""
import argparse
import json

from app.database import SessionLocal
from app.services.publication.backfill import run_backfill


def cmd_run(args) -> None:
    db = SessionLocal()
    try:
        cursor = None
        while True:
            report = run_backfill(
                db,
                batch_size=args.batch_size,
                after_id=cursor,
                actor_id=args.actor,
                ontology_id=args.ontology_id,
            )
            print(json.dumps(report, sort_keys=True))
            cursor = report["cursor"]
            if cursor is None:
                break
    finally:
        db.close()


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="publication_backfill")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="run the resumable identity inventory")
    run_parser.add_argument("--ontology-id", default=None)
    run_parser.add_argument("--batch-size", type=int, default=100)
    run_parser.add_argument("--actor", default=None)
    run_parser.set_defaults(func=cmd_run)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
