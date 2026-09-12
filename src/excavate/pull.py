"""Stage 2 — rate-limited, resumable pull of raw captures.

Rules from the spec:
- fetch via /web/<timestamp>id_/<url> (unrewritten); fall back to im_ on 404
- <= 30 requests/minute sustained (observed ceiling ~60; we run at half)
- exponential backoff on HTTP 429, fail the row after 3 attempts
- magic-byte validation on every body — content-types lie
- resumable: only fetch_state == pending rows; state persists after every row
"""

from __future__ import annotations

import time

import httpx

from .dig import Dig, safe_relpath, sha1_hex

REQUESTS_PER_MINUTE = 30
BACKOFF_SCHEDULE = (30, 60, 120)  # seconds; then the row fails

# signature -> human type; first match wins (None = unknown/plain text)
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
}


class RateLimiter:
    """Min-interval throttle. `sleep_fn` is injectable for tests."""

    def __init__(self, per_minute: int = REQUESTS_PER_MINUTE, sleep_fn=time.sleep, clock=time.monotonic) -> None:
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
    head = data[:512].lstrip()
    if head[:1] == b"<":
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


def _unique_path(directory, rel: str):
    candidate = directory / rel
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for i in range(1, 1000):
        alt = candidate.with_name(f"{stem}-{i}{suffix}")
        if not alt.exists():
            return alt
    raise RuntimeError(f"path collision exhausted for {rel}")


def _replay_urls(capture: dict) -> list[tuple[str, str]]:
    base = f"https://web.archive.org/web/{capture['timestamp']}"
    return [
        (f"{base}id_/{capture['url']}", "id_"),
        (f"{base}im_/{capture['url']}", "im_"),
    ]


def fetch_one(capture: dict, client: httpx.Client, limiter: RateLimiter, sleep_fn=time.sleep) -> dict:
    """Fetch a single capture; returns the mutated capture dict (never raises)."""
    last_error = None
    for url, source in _replay_urls(capture):
        for attempt, backoff in enumerate((*BACKOFF_SCHEDULE, None)):
            limiter.wait()
            try:
                response = client.get(url)
            except httpx.HTTPError as exc:
                last_error = f"{source}: transport {exc!r}"
                break  # transport error → try the other replay mode
            if response.status_code == 429:
                last_error = f"{source}: 429 (attempt {attempt + 1})"
                if backoff is not None:
                    sleep_fn(backoff)
                    continue
                break  # backoff exhausted → other mode / give up
            if response.status_code >= 500:
                last_error = f"{source}: {response.status_code}"
                break  # IA 5xx is rare and transient; try the other replay mode
            if response.status_code >= 400:
                last_error = f"{source}: {response.status_code}"
                break  # 404 etc → try the other replay mode
            # success
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
        # 429-exhausted or 5xx on this mode; fall through to the other mode
    capture.update(fetch_state="failed_4xx" if "429" in (last_error or "") else "failed_5xx",
                   error=last_error)
    return capture


def run_pull(dig: Dig, client: httpx.Client, limit: int | None = None,
             limiter: RateLimiter | None = None, sleep_fn=time.sleep) -> dict:
    limiter = limiter or RateLimiter(sleep_fn=sleep_fn)
    captures = dig.load_captures()
    todo = [c for c in captures if c["fetch_state"] == "pending"]
    if limit is not None:
        todo = todo[:limit]
    fetched = failed = 0
    for capture in todo:
        result = fetch_one(capture, client, limiter, sleep_fn=sleep_fn)
        body = result.pop("_body", None)
        if result["fetch_state"] == "fetched" and body is not None:
            target = _unique_path(dig.raw_dir, safe_relpath(result["url"]))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            result["raw_path"] = str(target.relative_to(dig.root))
            fetched += 1
        else:
            failed += 1
        dig.save_captures(captures)  # persist after every row — kill-safe
    counts = {state: sum(1 for c in captures if c["fetch_state"] == state)
              for state in ("pending", "fetched", "failed_4xx", "failed_5xx", "skipped")}
    return {"attempted": len(todo), "fetched": fetched, "failed": failed, "totals": counts}
