"""Excavate CLI — M0 Resurrection Mode.

Commands and contracts: docs/specs/M0-resurrection-cli.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import httpx

from . import __version__
from .cdx import MATCH_TYPES, CdxQuery, run_inventory
from .clean import run_clean
from .dig import Dig
from .pull import REQUESTS_PER_MINUTE, RateLimiter, run_pull
from .report import run_report

USER_AGENT = f"excavate/{__version__} (digital-archaeology; respectful rate budget)"
DEFAULT_DIG_BASE = "digs"
DOMAIN_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$")


def _client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=30.0,
    )


def _open_dig(value: str | None) -> Dig:
    if value:
        dig = Dig(Path(value))
        if not (dig.root / "captures.json").exists():
            raise SystemExit(f"not a dig directory (no captures.json): {value}")
        return dig
    return Dig.latest(Path(DEFAULT_DIG_BASE))


def cmd_inventory(args: argparse.Namespace) -> int:
    if not DOMAIN_RE.match(args.domain):
        raise SystemExit(f"not a plain domain: {args.domain!r} (no paths, no wildcards)")
    query = CdxQuery(args.domain, match=args.match, from_ts=args.from_ts, to_ts=args.to_ts)  # validates first
    dig = Dig.create(Path(args.output or DEFAULT_DIG_BASE), args.domain)
    try:
        with _client() as client:
            summary = run_inventory(args.domain, dig, client, query)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"CDX returned non-JSON (service degraded?): {exc}") from exc
    print(
        f"inventoried {summary['domain']}: {summary['raw_rows']} raw CDX rows "
        f"({summary['pages']} page{'s' if summary['pages'] != 1 else ''}) → "
        f"{summary['unique_urls']} unique URLs ({summary['pending']} pending, {summary['skipped']} skipped)"
    )
    print(f"dig: {dig.root}")
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    dig = _open_dig(args.dig)
    limiter = RateLimiter()  # explicit: the budget is a product feature
    with _client() as client:
        summary = run_pull(dig, client, limit=args.limit, limiter=limiter)
    totals = summary["totals"]
    failed_transient = totals.get("failed_5xx", 0)
    print(
        f"pull: {summary['fetched']} fetched, {summary['failed']} failed this run | "
        f"totals: {totals.get('fetched', 0)} fetched, {totals.get('pending', 0)} pending, "
        f"{totals.get('failed_4xx', 0)} permanent, {failed_transient} transient "
        f"(budget {REQUESTS_PER_MINUTE}/min)"
    )
    # permanent 404s are expected archaeology, transient failures resume —
    # neither is a command failure; the report carries the detail
    return 0


def cmd_clean(args: argparse.Namespace) -> int:
    dig = _open_dig(args.dig)
    summary = run_clean(dig)
    print(
        f"clean: {summary['cleaned']} files → {dig.site_dir}, "
        f"{summary['rewrites']} rewrites logged (REWRITES.md), "
        f"{summary['chrome_removed']} pieces of Wayback chrome removed, "
        f"{len(summary['never_crawled'])} never-crawled targets"
    )
    if summary["encodings"]:
        print("  charsets kept: " + ", ".join(f"{codec} ×{n}" for codec, n in summary["encodings"].items()))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    dig = _open_dig(args.dig)
    summary = run_report(dig)
    print(f"report: {dig.root / 'VERIFICATION-REPORT.md'}")
    print(
        f"  {summary['raw_rows']} raw rows / {summary['unique_urls']} URLs / "
        f"{summary['fetched']} fetched / {summary['failed_permanent']} permanent failures / "
        f"{summary['failed_transient']} transient failures / {summary['skipped']} skipped / "
        f"{summary['never_crawled']} never-crawled / {summary['rewrites']} rewrites"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="excavate", description="Digital archaeology platform — M0 Resurrection Mode")
    parser.add_argument("--version", action="version", version=f"excavate {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("inventory", help="CDX survey of a domain -> manifest (no collapse, no filters)")
    p.add_argument("domain", help="domain to inventory, e.g. magpu.com")
    p.add_argument("--output", default=None, help="dig base directory (default: digs/)")
    p.add_argument("--match", choices=MATCH_TYPES, default="prefix",
                   help="scope: prefix (default), host (exact host), domain (subdomains too)")
    p.add_argument("--from", dest="from_ts", default=None, metavar="YYYY[MMDD…]", help="earliest capture (4–14 digits)")
    p.add_argument("--to", dest="to_ts", default=None, metavar="YYYY[MMDD…]", help="latest capture (4–14 digits)")
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
    try:
        return args.func(args)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(
            f"dig state missing or corrupt ({exc!r}) — run `excavate inventory <domain>` first",
            file=sys.stderr,
        )
        return 1
    except (httpx.HTTPError, ValueError) as exc:
        print(f"network/data error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
