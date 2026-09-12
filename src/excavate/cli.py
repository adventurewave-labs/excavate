"""Excavate CLI — M0 skeleton.

Each subcommand states its contract and exits non-zero until implemented.
Contracts live in docs/specs/M0-resurrection-cli.md; this file is the
entry-point those contracts hang off, not an implementation.
"""

from __future__ import annotations

import argparse
import sys

from excavate import __version__

CDX_API = "https://web.archive.org/cdx/search/cdx"
REPLAY_BASE = "https://web.archive.org/web"
# Observed ceiling ~60 req/min post-Oct-2024; M0 runs at half (spec constraint).
RATE_LIMIT_PER_MIN = 30
USER_AGENT = f"excavate/{__version__} (digital-archaeology; respectful rate budget)"


def _not_implemented(command: str, contract: str) -> int:
    print(f"excavate {command}: not implemented yet (M0 in progress)\n  contract: {contract}", file=sys.stderr)
    return 2


def cmd_inventory(args: argparse.Namespace) -> int:
    return _not_implemented(
        "inventory",
        f"CDX survey of {args.domain!r} -> manifest.csv/json (no collapse, no mimetype filter)",
    )


def cmd_pull(args: argparse.Namespace) -> int:
    return _not_implemented(
        "pull",
        f"fetch raw captures per manifest in {args.dig!r} via id_/im_ (<= {RATE_LIMIT_PER_MIN}/min, resumable)",
    )


def cmd_clean(args: argparse.Namespace) -> int:
    return _not_implemented(
        "clean",
        f"de-Wayback rewrite of {args.dig!r} into site/ (rewrites logged, never fake missing files)",
    )


def cmd_report(args: argparse.Namespace) -> int:
    return _not_implemented(
        "report",
        f"completeness accounting for {args.dig!r} -> VERIFICATION-REPORT.md",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="excavate", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"excavate {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("inventory", help="CDX survey of a domain -> manifest")
    p.add_argument("domain", help="domain to inventory, e.g. magpu.com")
    p.add_argument("--output", default=None, help="dig directory (default: digs/<domain>/)")
    p.set_defaults(func=cmd_inventory)

    p = sub.add_parser("pull", help="fetch captures per manifest (resumable, rate-limited)")
    p.add_argument("--dig", default=None, help="dig directory (default: most recent)")
    p.add_argument("--limit", type=int, default=None, help="max URLs to fetch this run")
    p.set_defaults(func=cmd_pull)

    p = sub.add_parser("clean", help="de-Wayback rewrite into site/")
    p.add_argument("--dig", default=None, help="dig directory (default: most recent)")
    p.set_defaults(func=cmd_clean)

    p = sub.add_parser("report", help="emit VERIFICATION-REPORT.md")
    p.add_argument("--dig", default=None, help="dig directory (default: most recent)")
    p.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
