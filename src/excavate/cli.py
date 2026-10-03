"""Excavate CLI — M0 Resurrection Mode.

Commands and contracts: docs/specs/M0-resurrection-cli.md
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
from pathlib import Path

import httpx

from . import __version__
from .bagit import build_bag, verify_bag, zip_bag
from .cdx import MATCH_TYPES, CdxQuery, parse_timestamp, run_inventory
from .clean import run_clean
from .crawl import accounting_line, run_check
from .dig import Dig
from .discover import DEFAULT_LIMIT, run_discover
from .pull import REQUESTS_PER_MINUTE, RateLimiter, run_pull
from .report import run_report
from .scan import allow, run_scan, scan_status
from .status import dig_status, eta_minutes, pull_plan

USER_AGENT = f"excavate/{__version__} (digital-archaeology; respectful rate budget)"
DEFAULT_DIG_BASE = "digs"
DOMAIN_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$")


def _client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=30.0,
    )


def _limiter() -> RateLimiter:
    return RateLimiter()  # explicit: the budget is a product feature


def _progress(args: argparse.Namespace):
    """Per-row progress on stderr (stdout stays clean for --json and pipes)."""
    if getattr(args, "quiet", False):
        return None

    def show(done: int, total: int, capture: dict) -> None:
        left = total - done
        note = f" (fallback to {capture['timestamp']})" if capture.get("fallback_from") else ""
        print(f"[{done}/{total}] {capture['fetch_state']:<10} {capture['url']}{note} — ≥{eta_minutes(left)} min left",
              file=sys.stderr)

    return show


def _open_dig(value: str | None) -> Dig:
    if value:
        dig = Dig(Path(value))
        if not dig.has_state():
            raise SystemExit(f"not a dig directory (no dig.sqlite or captures.json): {value}")
        return dig
    return Dig.latest(Path(DEFAULT_DIG_BASE))


def cmd_inventory(args: argparse.Namespace) -> int:
    if not DOMAIN_RE.match(args.domain):
        raise SystemExit(f"not a plain domain: {args.domain!r} (no paths, no wildcards)")
    query = CdxQuery(args.domain, match=args.match, from_ts=args.from_ts, to_ts=args.to_ts)  # validates first
    if args.at is not None:
        parse_timestamp(args.at)
    dig = Dig.create(Path(args.output or DEFAULT_DIG_BASE), args.domain)
    try:
        with _client() as client:
            summary = run_inventory(args.domain, dig, client, query, limiter=_limiter(), at=args.at)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"CDX returned non-JSON (service degraded?): {exc}") from exc
    args.result = {**summary, "dig": str(dig.root)}
    print(
        f"inventoried {summary['domain']}: {summary['raw_rows']} raw CDX rows "
        f"({summary['pages']} page{'s' if summary['pages'] != 1 else ''}) → "
        f"{summary['unique_urls']} unique URLs ({summary['pending']} pending, {summary['skipped']} skipped)"
    )
    print(f"selection: {'nearest to ' + args.at if args.at else 'latest'} capture per URL; dig: {dig.root}")
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    dig = _open_dig(args.dig)
    if args.dry_run:
        plan = pull_plan(dig, args.limit)
        args.result = plan
        print(f"pull (dry run): {plan['this_run']} URLs this run ({plan['pending']} pending + "
              f"{plan['transient_retries']} transient retries in the dig) ≈ at least {plan['eta_minutes']} min "
              f"at {plan['requests_per_minute']} req/min — fallbacks and backoff add to it. Nothing was fetched.")
        return 0
    with _client() as client:
        summary = run_pull(dig, client, limit=args.limit, limiter=_limiter(), progress=_progress(args))
    args.result = summary
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
    summary = run_clean(dig, twin_swaps=not args.no_twins)
    args.result = {**summary, "site": str(dig.site_dir)}
    print(
        f"clean: {summary['cleaned']} files → {dig.site_dir}, "
        f"{summary['rewrites']} rewrites logged (REWRITES.md), "
        f"{summary['chrome_removed']} pieces of Wayback chrome removed, "
        f"{summary['twin_swaps']} twin swaps, "
        f"{len(summary['never_crawled'])} never-crawled targets"
    )
    if summary["encodings"]:
        print("  charsets kept: " + ", ".join(f"{codec} ×{n}" for codec, n in summary["encodings"].items()))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    dig = _open_dig(args.dig)
    result = run_check(dig)
    args.result = result
    counts = result["counts"]
    if not counts["site_files"]:
        raise SystemExit(f"nothing to check: {dig.site_dir} is empty — run `excavate clean` first")
    print(f"check: {accounting_line(counts)}")
    if result["entry_page"] is None:
        print("  no entry page recovered — reachability not computed")
    else:
        print(f"  from {result['entry_page']}: {counts['reachable_from_entry']}/{counts['site_files']} files reachable, "
              f"{counts['orphans']} orphans")
    print(f"  detail: {dig.root / 'crawl.json'}")
    # --strict makes an incomplete site a failing exit code (for scripts / gates)
    return 1 if args.strict and counts["unresolved"] else 0


def cmd_discover(args: argparse.Namespace) -> int:
    dig = _open_dig(args.dig)
    with _client() as client:
        summary = run_discover(dig, client, limiter=_limiter(), limit=args.limit, recheck=args.recheck)
    args.result = summary
    print(
        f"discover: {summary['never_archived']} referenced files not in the manifest; "
        f"{summary['looked_up']} looked up ({summary['lookups']} CDX requests), "
        f"{summary['found']} found ({summary['fetchable']} fetchable), {summary['still_missing']} confirmed absent"
    )
    extras = [f"{summary[key]} {label}" for key, label in (("already_checked", "checked on an earlier run"),
              ("irreversible", "not reversible to a URL"), ("deferred", "left for the next run (--limit)")) if summary[key]]
    if extras:
        print("  " + ", ".join(extras))
    if summary["fetchable"]:
        print("  next: excavate pull, then clean and report")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    dig = _open_dig(args.dig)
    if args.allow:
        if not args.reason:
            raise SystemExit("--allow needs --reason \"why this is safe to publish\"")
        entry = allow(dig, args.allow, args.reason)
        print(f"allow-listed {entry['fingerprint']} ({entry['rule']} in {entry['path']}): {entry['reason']}")
    result = run_scan(dig, Path(args.path) if args.path else None)
    args.result = result
    if not result["files"]:
        raise SystemExit(f"nothing to scan: {result['scanned']} is empty — run `excavate clean` first")
    for finding in result["findings"]:
        if not finding["allowed"]:
            where = f"{finding['path']}:{finding['line']}" if finding["line"] else finding["path"]
            print(f"  [{finding['fingerprint']}] {where} — {finding['description']} ({finding['preview']})")
    verdict = "CLEAN" if result["clean"] else f"BLOCKED — {result['blocking']} unreviewed finding(s)"
    print(f"scan: {result['files']} files, {result['allowed']} allow-listed — {verdict}")
    if not result["clean"]:
        print("  review each one; remove the file, or: excavate scan --allow <fingerprint> --reason \"...\"")
    return 0 if result["clean"] else 1  # a hard gate: never 0 with unreviewed findings


def cmd_export(args: argparse.Namespace) -> int:
    if args.verify:
        errors = verify_bag(Path(args.verify))
        args.result = {"bag": args.verify, "valid": not errors, "errors": errors}
        for error in errors:
            print(f"  {error}")
        print(f"verify: {args.verify} — " + ("VALID (complete, every checksum matches)" if not errors else f"INVALID, {len(errors)} problem(s)"))
        return 1 if errors else 0
    dig = _open_dig(args.dig)
    summary = build_bag(dig, Path(args.output) if args.output else None, include_raw=not args.no_raw,
                        require_clean=args.require_clean)
    args.result = summary
    print(f"export: BagIt bag at {summary['bag']} — {summary['files']} files, {summary['size']}"
          + ("" if summary["include_raw"] else " (raw captures not included)"))
    if args.zip:
        summary["zip"] = str(zip_bag(Path(summary["bag"]), summary["date"]))
        print(f"  zip: {summary['zip']}")
    if summary["publish_gate"] != "clean":
        print(f"  note: output scan is '{summary['publish_gate']}' — recorded in bag-info.txt; this bag is not cleared for publishing")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    dig = _open_dig(args.dig)
    summary = run_report(dig)
    args.result = {**summary, "report": str(dig.root / "VERIFICATION-REPORT.md")}
    print(f"report: {dig.root / 'VERIFICATION-REPORT.md'}")
    print(
        f"  {summary['raw_rows']} raw rows / {summary['unique_urls']} URLs / "
        f"{summary['fetched']} fetched / {summary['failed_permanent']} permanent failures / "
        f"{summary['failed_transient']} transient failures / {summary['skipped']} skipped / "
        f"{summary['never_crawled']} never-crawled / {summary['rewrites']} rewrites"
    )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    dig = _open_dig(args.dig)
    status = dig_status(dig)
    args.result = status
    states = status["states"]
    print(f"{status['domain']} — {status['dig']} (selection: {status['selection']})")
    print(f"  {status['urls']} URLs: {states['fetched']} fetched, {states['pending']} pending, "
          f"{states['failed_5xx']} transient failures, {states['failed_4xx']} permanent failures, {states['skipped']} skipped")
    print(f"  site: {status['site_files']} files" + (f"; links: {accounting_line(status['crawl'])}" if status["crawl"] else ""))
    print(f"  publish gate: {status['publish_gate']}")
    if status["last_event"]:
        print(f"  last: {status['last_event']['event_type']} at {status['last_event']['timestamp']}")
    print(f"  next: {status['next']}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """inventory → pull → clean → check → scan → report, stopping at the first
    stage that fails. Each stage is the same code as its own command, so a
    run can always be continued by hand from wherever it stopped."""
    stages: dict[str, dict] = {}
    args.result = {"stages": stages}
    cmd_inventory(args)
    stages["inventory"] = args.result
    args.dig = stages["inventory"]["dig"]
    dig = _open_dig(args.dig)
    args.result = {"stages": stages}
    plan = pull_plan(dig, args.limit)
    print(f"pull: {plan['this_run']} URLs, at least {plan['eta_minutes']} min at {plan['requests_per_minute']} req/min")
    with _client() as client:
        stages["pull"] = run_pull(dig, client, limit=args.limit, limiter=_limiter(), progress=_progress(args))
        if args.discover:
            run_clean(dig, twin_swaps=not args.no_twins)
            stages["discover"] = run_discover(dig, client, limiter=_limiter())
            if stages["discover"]["fetchable"]:
                stages["pull_discovered"] = run_pull(dig, client, limiter=_limiter(), progress=_progress(args))
    stages["clean"] = run_clean(dig, twin_swaps=not args.no_twins)
    stages["check"] = run_check(dig)["counts"]
    scan = run_scan(dig)
    stages["scan"] = {key: scan[key] for key in ("files", "clean", "blocking", "allowed", "by_rule")}
    stages["report"] = run_report(dig)
    gate = scan_status(dig)[0]
    totals = stages["pull"]["totals"]
    incomplete = totals["pending"] + totals["failed_5xx"]
    args.result = {"dig": str(dig.root), "publish_gate": gate, "unresolved_links": stages["check"]["unresolved"],
                   "left_to_pull": incomplete, "stages": stages}
    print(f"run: {totals['fetched']} files recovered into {dig.site_dir}; {accounting_line(stages['check'])}")
    if incomplete:
        print(f"  {incomplete} URLs still to pull — `excavate pull --dig {dig.root}` resumes, then clean and report again")
    print(f"  publish gate: {gate}" + ("" if gate == "clean" else " — see `excavate scan`"))
    print(f"  report: {dig.root / 'VERIFICATION-REPORT.md'}")
    strict_failure = args.strict and (gate != "clean" or stages["check"]["unresolved"] or incomplete)
    return 1 if strict_failure else 0


EXIT_CODES = """exit codes:
  0  the command completed
  1  it failed, or a gate said no (scan findings, check --strict, export --verify, run --strict)
  2  usage error"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="excavate", description="Digital archaeology platform — M0 Resurrection Mode",
                                     epilog=EXIT_CODES, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", default=False,
                        help="print one JSON object (result + messages + exit code) instead of text")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    parser.add_argument("--version", action="version", version=f"excavate {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("inventory", parents=[common], help="CDX survey of a domain -> manifest (no collapse, no filters)")
    p.add_argument("domain", help="domain to inventory, e.g. magpu.com")
    p.add_argument("--output", default=None, help="dig base directory (default: digs/)")
    p.add_argument("--match", choices=MATCH_TYPES, default="prefix",
                   help="scope: prefix (default), host (exact host), domain (subdomains too)")
    p.add_argument("--from", dest="from_ts", default=None, metavar="YYYY[MMDD…]", help="earliest capture (4–14 digits)")
    p.add_argument("--at", default=None, metavar="YYYY[MMDD…]",
                   help="pick each URL's capture nearest this date (default: its latest capture)")
    p.add_argument("--to", dest="to_ts", default=None, metavar="YYYY[MMDD…]", help="latest capture (4–14 digits)")
    p.set_defaults(func=cmd_inventory)

    p = sub.add_parser("pull", parents=[common], help="fetch captures per manifest (resumable, rate-limited)")
    p.add_argument("--dig", default=None, help="dig directory (default: most recent under digs/)")
    p.add_argument("--limit", type=int, default=None, help="max URLs to fetch this run")
    p.add_argument("--dry-run", action="store_true", help="say how many URLs and how long, fetch nothing")
    p.add_argument("--quiet", action="store_true", help="no per-URL progress on stderr")
    p.set_defaults(func=cmd_pull)

    p = sub.add_parser("clean", parents=[common], help="de-Wayback rewrite into site/ (rewrites logged, never fake missing files)")
    p.add_argument("--dig", default=None, help="dig directory (default: most recent under digs/)")
    p.add_argument("--no-twins", action="store_true",
                   help="do not point dead links at surviving twins (.mp3→.ogg, case, .htm/.html); leave them as archived")
    p.set_defaults(func=cmd_clean)

    p = sub.add_parser("check", parents=[common], help="walk the cleaned site's links: recovered / missing / orphans (writes crawl.json)")
    p.add_argument("--dig", default=None, help="dig directory (default: most recent under digs/)")
    p.add_argument("--strict", action="store_true", help="exit 1 if any referenced file is not on disk")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("discover", parents=[common], help="ask CDX for files the pages link to but the inventory missed (adds them as pending)")
    p.add_argument("--dig", default=None, help="dig directory (default: most recent under digs/)")
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"max files to look up this run (default {DEFAULT_LIMIT})")
    p.add_argument("--recheck", action="store_true", help="forget earlier lookups and ask again")
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("scan", parents=[common], help="output-scan gate: credentials / bulk personal data in what would be published (exit 1 if any)")
    p.add_argument("--dig", default=None, help="dig directory (default: most recent under digs/)")
    p.add_argument("--path", default=None, help="scan this directory instead of the dig's site/ (e.g. a build output)")
    p.add_argument("--allow", default=None, metavar="FINGERPRINT", help="allow-list a reviewed finding (requires --reason)")
    p.add_argument("--reason", default=None, help="why the allow-listed finding is safe to publish")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("export", parents=[common], help="archive-grade export: a BagIt (RFC 8493) bag of site + raw captures + paper trail")
    p.add_argument("--dig", default=None, help="dig directory (default: most recent under digs/)")
    p.add_argument("--output", default=None, help="directory to create the bag in (default: <dig>/export/)")
    p.add_argument("--zip", action="store_true", help="also write <bag>.zip (deterministic)")
    p.add_argument("--no-raw", action="store_true", help="leave the as-fetched captures out of the bag")
    p.add_argument("--require-clean", action="store_true", help="refuse unless the output scan is clean")
    p.add_argument("--verify", default=None, metavar="BAG", help="verify an existing bag (directory or .zip) and exit")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("status", parents=[common], help="where the dig stands and what to run next (no network)")
    p.add_argument("--dig", default=None, help="dig directory (default: most recent under digs/)")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("run", parents=[common], help="the whole pipeline: inventory → pull → clean → check → scan → report")
    p.add_argument("domain", help="domain to resurrect, e.g. magpu.com")
    p.add_argument("--output", default=None, help="dig base directory (default: digs/)")
    p.add_argument("--match", choices=MATCH_TYPES, default="prefix", help="inventory scope (see `inventory`)")
    p.add_argument("--from", dest="from_ts", default=None, metavar="YYYY[MMDD…]", help="earliest capture")
    p.add_argument("--to", dest="to_ts", default=None, metavar="YYYY[MMDD…]", help="latest capture")
    p.add_argument("--at", default=None, metavar="YYYY[MMDD…]", help="pick captures nearest this date")
    p.add_argument("--limit", type=int, default=None, help="max URLs to pull this run (resume later with `pull`)")
    p.add_argument("--discover", action="store_true", help="also look up linked-but-missing files and pull the hits")
    p.add_argument("--no-twins", action="store_true", help="leave dead links as archived (see `clean`)")
    p.add_argument("--quiet", action="store_true", help="no per-URL progress on stderr")
    p.add_argument("--strict", action="store_true",
                   help="exit 1 unless the pull finished, every link resolves and the publish gate is clean")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("report", parents=[common], help="emit VERIFICATION-REPORT.md (completeness accounting)")
    p.add_argument("--dig", default=None, help="dig directory (default: most recent under digs/)")
    p.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    if not args.json:
        return _dispatch(args)
    # --json: the command's human lines become "messages"; its summary is "result"
    captured = io.StringIO()
    error: str | None = None
    try:
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(io.StringIO()) as errors:
            try:
                code = _dispatch(args)
            finally:
                error = errors.getvalue().strip() or None
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        error = error or (str(exc.code) if exc.code not in (None, 0) and not isinstance(exc.code, int) else None)
    payload = {"command": args.command, "exit_code": code, "result": getattr(args, "result", None),
               "messages": [line for line in captured.getvalue().splitlines() if line.strip()]}
    if error and code:
        payload["error"] = error
    print(json.dumps(payload, indent=2, default=str))
    return code


def _dispatch(args: argparse.Namespace) -> int:
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
