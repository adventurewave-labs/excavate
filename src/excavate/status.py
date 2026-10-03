"""Where a dig stands, and what to do next — without touching the network."""

from __future__ import annotations

import json
import math

from .dig import Dig
from .provenance import SITE_PROVENANCE, load_events
from .pull import REQUESTS_PER_MINUTE
from .scan import scan_status


def eta_minutes(requests: int) -> int:
    """Lower bound: the rate budget alone. Fallback captures, im_ retries and
    backoff only ever add to it."""
    return math.ceil(requests / REQUESTS_PER_MINUTE) if requests > 0 else 0


def pull_plan(dig: Dig, limit: int | None = None) -> dict:
    counts = dig.state_counts()
    to_fetch = counts["pending"] + counts["failed_5xx"]
    this_run = min(to_fetch, limit) if limit is not None and limit > 0 else to_fetch
    return {
        "pending": counts["pending"],
        "transient_retries": counts["failed_5xx"],
        "to_fetch": to_fetch,
        "this_run": this_run,
        "requests_per_minute": REQUESTS_PER_MINUTE,
        "eta_minutes": eta_minutes(this_run),
    }


def dig_status(dig: Dig) -> dict:
    counts = dig.state_counts()
    meta = dig.meta()
    site_files = (sum(1 for p in dig.site_dir.rglob("*") if p.is_file() and p.name != SITE_PROVENANCE)
                  if dig.site_dir.exists() else 0)
    gate, _ = scan_status(dig)
    crawl_path = dig.root / "crawl.json"
    crawl = json.loads(crawl_path.read_text())["counts"] if crawl_path.exists() else None
    events = load_events(dig)
    plan = pull_plan(dig)

    if plan["to_fetch"]:
        next_step = f"excavate pull   # {plan['to_fetch']} URLs left, at least {plan['eta_minutes']} min at {REQUESTS_PER_MINUTE} req/min"
    elif not counts["fetched"]:
        next_step = "nothing was recoverable — see `excavate report` for why"
    elif not site_files or (events and _stage_after(events, "pull", "clean")):
        next_step = "excavate clean"
    elif gate in ("not run", "stale"):
        next_step = "excavate scan   # the publish gate has not seen this site"
    elif gate == "findings":
        next_step = "excavate scan   # review the findings; the gate is blocking"
    else:
        next_step = "excavate report, then excavate export — the dig is complete and the gate is clean"

    return {
        "domain": meta["domain"],
        "dig": str(dig.root),
        "selection": (meta.get("selection") or {}).get("at") or "latest",
        "inventoried_at": meta.get("inventoried_at"),
        "urls": sum(counts.values()),
        "states": counts,
        "pull": plan,
        "site_files": site_files,
        "crawl": crawl,
        "publish_gate": gate,
        "last_event": ({"event_type": events[-1]["event_type"], "timestamp": events[-1]["timestamp"]} if events else None),
        "next": next_step,
    }


def _stage_after(events: list[dict], earlier: str, later: str) -> bool:
    """True if the most recent `earlier` event happened after the most recent
    `later` one (e.g. a pull since the last clean → the site is out of date)."""
    last = {name: max((i for i, e in enumerate(events) if e["event_type"] == name), default=-1) for name in (earlier, later)}
    return last[earlier] > last[later]
