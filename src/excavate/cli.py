"""Excavate CLI — M0 Resurrection Mode.

Commands and contracts: docs/specs/M0-resurrection-cli.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

from . import __version__
from .cdx import run_inventory
from .clean import run_clean
from .dig import Dig
from .pull import REQUESTS_PER_MINUTE, RateLimiter, run_pull
from .report import run_report

USER_AGENT = f"excavate/{__version__} (digital-archaeology; respectful rate budget)"
DEFAULT_DIG_BASE = "digs"


def _client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=30.0,
    )


def _dig_arg(value: str | None) -> Dig:
    return Dig(Path(value)) if value else Dig.latest(Path(DEFAULT_DIG_BASE))


def cmd_inventory(args: argparse.Namespace) -> int:
    dig = Dig.create(Path(args.output or DEFAULT_DIG_BASE), args.domain)
    summary = run_inventory(args.domain, dig, _client())
    print(
        f"inventoried {summary['domain']}: {summary['raw_rows']} raw CDX rows → "
        f"{summary['unique_urls']} unique URLs ({summary['pending']} pending, {summary['skipped']} skipped)"
    )
    print(f"dig: {dig.root}")
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    dig = _dig_arg(args.dig)
    summary = run_pull(dig, _client(), limit=args.limit)
    totals = summary["totals"]
    print(
        f"pull: {summary['fetched']} fetched, {summary['failed']} failed this run | "
        f"totals: {totals.get('fetched', 0)} fetched, {totals.get('pending', 0)} pending, "
        f"{totals.get('failed_4xx', 0) + totals.get('failed_5xx', 0)} failed "
        f"(budget {REQUESTS_PER_MINUTE}/min)"
    )
    return 0 if summary["failed"] == 0 else 1


def cmd_clean(args: argparse.Namespace) -> int:
    dig = _dig_arg(args.dig)
    summary = run_clean(dig)
    print(
        f"clean: {summary['cleaned']} files → {dig.site_dir}, "
        f"{summary['rewrites']} rewrites logged (REWRITES.md), "
        f"{len(summary['never_crawled'])} never-crawled targets"
    )
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    dig = _dig_arg(args.dig)
    summary = run_report(dig)
    print(f"report: {dig.root / 'VERIFICATION-REPORT.md'}")
    print(
        f"  {summary['raw_rows']} raw rows / {summary['unique_urls']} URLs / "
        f"{summary['fetched']} fetched / {summary['failed']} failed / "
        f"{summary['skipped']} skipped / {summary['never_crawled']} never-crawled / "
        f"{summary['rewrites']} rewrites"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="excavate", description="Digital archaeology platform — M0 Resurrection Mode")
    parser.add_argument("--version", action="version", version=f"excavate {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("inventory", help="CDX survey of a domain -> manifest (no collapse, no filters)")
    p.add_argument("domain", help="domain to inventory, e.g. magpu.com")
    p.add_argument("--output", default=None, help="dig base directory (default: digs/)")
    p.set_defaults(func=cmd_inventory)

    p = sub.add_parser("pull", help="fetch captures per manifest (resumable, rate-limited)")
    p.add_argument("--dig", default=None, help="dig directory (default: most recent under digs/)")
    p.add_argument("--limit", type=int, default=None, help="max URLs to fetch this run")
    p.set_defaults(func=cmd_pull)

    p = sub.add_parser("clean", help="de-Wayback rewrite into site/ (rewrites logged, never fake missing files)")
    p.add_argument("--dig", default=None, help="dig directory (default: most recent under digs/)")
    p.set_defaults(func=cmd_clean)

    p = sub.add_parser("report", help="emit VERIFICATION-REPORT.md (completeness accounting)")
    p.add_argument("--dig", default=None, help="dig directory (default: most recent under digs/)")
    p.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
