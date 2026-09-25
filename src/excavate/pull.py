"""Stage 2 — rate-limited, resumable pull of raw captures.

Rules from the spec:
- fetch via /web/<timestamp>id_/<url> (unrewritten); fall back to im_ on 404
- <= 30 requests/minute sustained (observed ceiling ~60; we run at half)
- exponential backoff on 429/5xx — 3 chances per row (total, across both
  replay modes), then the row fails
- magic-byte validation on every body — content-types lie
- resumable: only fetch_state == pending rows; state persists after every row
- dedupe is keyed by URL, not disk existence — resumed runs overwrite their
  own path instead of forking -1/-2 copies

Failure buckets are semantic: failed_4xx = permanent (404, 403, ...);
failed_5xx = transient (server errors, 429-exhaustion, transport failures) —
those are the ones a later resume run should retry.
"""

from __future__ import annotations

import time

import httpx

from .dig import Dig, safe_relpath, sha1_hex

REQUESTS_PER_MINUTE = 30
BACKOFF_SCHEDULE = (30, 60, 120)  # seconds; 3 chances per row, then it fails

MAGIC_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG", "png"),
    (b"GIF8", "gif"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"%PDF", "pdf"),
    (b"OggS", "ogg"),
    (b"ID3", "mp3"),
    (b"\xff\xfb", "mp3"),
    (b"\x1f\x8b", "gzip"),
    (b"PK\x03\x04", "zip"),
)

EXTENSION_TYPES = {
    ".png": "png", ".gif": "gif", ".jpg": "jpeg", ".jpeg": "jpeg", ".pdf": "pdf",
    ".ogg": "ogg", ".oga": "ogg", ".mp3": "mp3", ".gz": "gzip", ".zip": "zip",
    ".html": "html", ".htm": "html",
}


class RateLimiter:
    """Min-interval throttle. `sleep_fn` is injectable for tests."""

    def __init__(self, per_minute: int = REQUESTS_PER_MINUTE, sleep_fn=time.sleep, clock=time.monotonic) -> None:
        if per_minute <= 0:
            raise ValueError("per_minute must be positive")
        self.min_interval = 60.0 / per_minute
        self._sleep = sleep_fn
        self._clock = clock
        self._last: float | None = None

    def wait(self) -> float:
        if self._last is not None:
            elapsed = self._clock() - self._last
            if elapsed < self.min_interval:
                self._sleep(self.min_interval - elapsed)
        waited = self._clock()
        self._last = waited
        return waited


def sniff_type(data: bytes) -> str | None:
    for signature, kind in MAGIC_SIGNATURES:
        if data.startswith(signature):
            return kind
    if data[:512].lstrip()[:1] == b"<":
        return "html"
    return None


def magic_ok(data: bytes, path: str) -> bool | None:
    """None = no expectation for this extension (check skipped)."""
    expected = EXTENSION_TYPES.get(_ext(path))
    actual = sniff_type(data)
    if expected is None or actual is None:
        return None
    return expected == actual


def _ext(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""


def _replay_urls(capture: dict) -> list[tuple[str, str]]:
    base = f"https://web.archive.org/web/{capture['timestamp']}"
    return [
        (f"{base}id_/{capture['url']}", "id_"),
        (f"{base}im_/{capture['url']}", "im_"),
    ]


def _fail(capture: dict, last_status: int | None, error: str) -> dict:
    transient = last_status is None or last_status >= 500 or last_status == 429
    capture.update(
        fetch_state="failed_5xx" if transient else "failed_4xx",
        error=error,
    )
    return capture


def fetch_one(capture: dict, client: httpx.Client, limiter: RateLimiter, sleep_fn=time.sleep) -> dict:
    """Fetch a single capture; returns the mutated capture dict (never raises).

    Retries with backoff apply to transient statuses (429, 5xx) only, on a
    budget of 3 sleeps per row shared across both replay modes. Permanent 4xx
    (404 etc.) gets no same-mode retry — it falls through to the other mode.
    """
    backoff_budget = list(BACKOFF_SCHEDULE)
    backoff_used = False
    last_status: int | None = None
    last_error = "no attempts made"
    transport_last = False  # the final attempt died before an HTTP answer

    for url, source in _replay_urls(capture):
        while True:
            limiter.wait()
            try:
                response = client.get(url)
            except httpx.HTTPError as exc:
                last_error = f"{source}: transport {exc!r}"
                transport_last = True
                break  # try the other replay mode
            transport_last = False
            last_status = response.status_code
            if response.status_code == 429 or response.status_code >= 500:
                last_error = f"{source}: {response.status_code}"
                if backoff_budget:
                    backoff_used = True
                    sleep_fn(backoff_budget.pop(0))
                    continue
                return _fail(capture, last_status, last_error)  # budget exhausted
            if response.status_code >= 400:
                last_error = f"{source}: {response.status_code}"
                break  # permanent for this mode; try the other
            capture.update(
                fetch_state="fetched",
                source=source,
                http_status=response.status_code,
                sha1_fetched=sha1_hex(response.content),
                magic_ok=magic_ok(response.content, safe_relpath(capture["url"])),
                error=None,
                _body=response.content,
            )
            return capture
    # a 4xx observed while under throttle pressure isn't trustworthy as permanent
    transient = (transport_last or last_status is None or last_status >= 500
                 or last_status == 429 or (backoff_used and last_status is not None and last_status >= 400))
    capture.update(
        fetch_state="failed_5xx" if transient else "failed_4xx",
        error=last_error,
    )
    return capture


def _claim_path(claimed: dict[str, str], rel: str, url: str) -> tuple[str, bool]:
    """Reserve `rel` for `url`. Returns (path, collision) — collision means a
    different URL already owns the canonical name and this one gets a suffix."""
    owner = claimed.get(rel)
    if owner is None or owner == url:
        claimed[rel] = url
        return rel, False
    stem, _, suffix = rel.rpartition(".")
    if not stem:
        stem, suffix = rel, ""
    else:
        suffix = "." + suffix
    for i in range(1, 1000):
        alt = f"{stem}-{i}{suffix}"
        if alt not in claimed:
            claimed[alt] = url
            return alt, True
    raise RuntimeError(f"path collision exhausted for {rel}")


def run_pull(dig: Dig, client: httpx.Client, limit: int | None = None,
             limiter: RateLimiter | None = None, sleep_fn=time.sleep) -> dict:
    limiter = limiter or RateLimiter(sleep_fn=sleep_fn)
    captures = dig.load_captures()

    # rebuild path claims from existing state — dedupe key is the URL, so a
    # resumed run overwrites its own file instead of forking -1/-2 copies
    claimed: dict[str, str] = {}
    for cap in captures:
        raw_path = cap.get("raw_path") or ""
        if raw_path.startswith("raw/"):
            claimed[raw_path[len("raw/"):]] = cap["url"]

    todo = [c for c in captures if c["fetch_state"] == "pending"]
    if limit is not None and limit > 0:
        todo = todo[:limit]
    fetched = failed = 0
    for capture in todo:
        prev_rel = capture.get("raw_path")
        prev_rel = prev_rel[len("raw/"):] if prev_rel and prev_rel.startswith("raw/") else None
        prev_collision = bool(capture.get("path_collision"))
        capture.update(sha1_fetched=None, source=None, http_status=None,
                       magic_ok=None, error=None, path_collision=None)
        result = fetch_one(capture, client, limiter, sleep_fn=sleep_fn)
        body = result.pop("_body", None)
        if result["fetch_state"] == "fetched" and body is not None:
            if prev_rel and claimed.get(prev_rel) == result["url"]:
                # resumed row: reuse its own path (overwrites in place, no forks)
                rel, collision = prev_rel, prev_collision
                claimed[rel] = result["url"]
            else:
                rel, collision = _claim_path(claimed, safe_relpath(result["url"]), result["url"])
            target = dig.raw_dir / rel
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body)
            except OSError as exc:
                # filesystem limits etc. — fail the row (transient: resume retries),
                # never wedge the run
                result.update(fetch_state="failed_5xx", error=f"local write failed: {exc}",
                              raw_path=None, path_collision=None)
                claimed.pop(rel, None)
                failed += 1
                dig.save_captures(captures)
                continue
            result["raw_path"] = f"raw/{rel}"
            result["path_collision"] = collision or None
            fetched += 1
        else:
            # failed (re-)fetch: any previously-fetched bytes no longer correspond
            # to this row's state — drop the association and the file
            if prev_rel and claimed.get(prev_rel) == result["url"]:
                old = dig.raw_dir / prev_rel
                if old.exists():
                    old.unlink()
                claimed.pop(prev_rel, None)
            result["raw_path"] = None
            result["path_collision"] = None
            failed += 1
        dig.save_captures(captures)  # persist after every row — kill-safe
    counts = {state: sum(1 for c in captures if c["fetch_state"] == state)
              for state in ("pending", "fetched", "failed_4xx", "failed_5xx", "skipped")}
    return {"attempted": len(todo), "fetched": fetched, "failed": failed, "totals": counts}
