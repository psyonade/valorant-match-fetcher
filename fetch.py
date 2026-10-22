"""Command-line frontend for the Valorant match export service."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from api import APIError
from service import FetchOptions, run_fetch


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch and export HenrikDev Valorant match data.")
    parser.add_argument("--name", required=True, help="Riot ID name (without #tag)")
    parser.add_argument("--tag", required=True, help="Riot ID tag")
    parser.add_argument("--region", default="ap", help="HenrikDev affinity (default: ap)")
    parser.add_argument("--act", default="current", help="Act code from metadata.season.short, or current")
    parser.add_argument("--mode", help="Queue filter, such as competitive; default: all")
    parser.add_argument("--limit", type=int, default=100, help="Maximum matches to include (default: 100)")
    parser.add_argument("--csv", action="store_true", help="Write flattened match data to matches.csv")
    parser.add_argument("--jsonl", action="store_true", help="Write flattened match data to matches.jsonl")
    parser.add_argument("--digest", action="store_true", help="Write compact LLM digest Markdown")
    parser.add_argument("--rounds", action="store_true", help="Write kill-level events to kills.csv")
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help=argparse.SUPPRESS)
    return parser


def run(args: argparse.Namespace) -> int:
    options = FetchOptions(
        name=args.name,
        tag=args.tag,
        region=args.region,
        act=args.act,
        mode=args.mode,
        limit=args.limit,
        rounds=args.rounds,
        export_csv=args.csv,
        export_jsonl=args.jsonl,
        export_digest=args.digest,
        data_dir=args.data_dir,
    )
    result = run_fetch(options)
    print(f"Exported {result.match_count} match(es) for act {result.act} to {args.data_dir}.")
    if result.null_performance_scores is not None:
        print(f"Matches with null Performance Score: {result.null_performance_scores}")
    if args.csv:
        print("CSV: matches.csv")
    if args.jsonl:
        print("JSONL: matches.jsonl")
    if args.rounds:
        print(f"Exported {result.kill_count} kill event(s) to kills.csv.")
    if args.digest:
        print("Digest: " + ", ".join(str(path) for path in result.digest_paths))
    return 0


def main() -> int:
    args = _parser().parse_args()
    try:
        return run(args)
    except (APIError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
