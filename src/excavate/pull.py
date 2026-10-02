"""Stage 2 — rate-limited, resumable pull of raw captures.

Rules from the spec:
- fetch via /web/<timestamp>id_/<url> (unrewritten); fall back to im_ on 404
- <= 30 requests/minute sustained (observed ceiling ~60; we run at half)
- exponential backoff on 429/5xx — 3 chances per row (total, across both
  replay modes), then the row fails
- when the wanted capture is permanently gone, fall back through the URL's
  other captures (nearest-first) before calling the file lost — disclosed
- magic-byte validation on every body — content-types lie
- CDX digest verification on every `id_` body — proof the bytes on disk are
  the bytes IA archived (unverifiable cases are recorded as such, with reason)
- resumable: pending rows, then transient failures (failed_5xx); permanent
  failures and fetched rows are never re-requested; state persists after every row
- dedupe is keyed by URL, not disk existence — resumed runs overwrite their
  own path instead of forking -1/-2 copies

Failure buckets are semantic: failed_4xx = permanent (404, 403, ...);
failed_5xx = transient (server errors, 429-exhaustion, transport failures) —
those are the ones a later resume run should retry.
"""

from __future__ import annotations

import time

import httpx

from .dig import Dig, safe_relpath, sha1_base32, sha1_hex
from .provenance import record_event, selection_hash

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


_BASE32_SHA1 = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")


def verify_digest(capture: dict, body: bytes, source: str, headers: httpx.Headers) -> tuple[bool | None, str | None]:
    """Compare the fetched body with the CDX digest. Returns (digest_ok, note).

    True/False is only claimed when the comparison is meaningful: `id_` replay
    returns the archived payload unmodified, so its SHA-1 must equal the CDX
    digest. Everything else is None with the reason — an unverifiable body is
    disclosed as unverifiable, never passed off as verified or as corrupt:
    - `im_` replay is rewritten by Wayback, so it can never match;
    - a content-encoded payload was digested in its encoded form, but the
      HTTP client hands us decoded bytes;
    - a CDX row with a missing/odd digest gives nothing to compare with.
    """
    expected = str(capture.get("digest") or "").strip().upper()
    if len(expected) != 32 or not set(expected) <= _BASE32_SHA1:
        return None, "no usable CDX digest"
    if source != "id_":
        return None, f"{source} replay is rewritten by Wayback"
    if sha1_base32(body) == expected:
        return True, None
    encoding = headers.get("content-encoding") or headers.get("x-archive-orig-content-encoding")
    if encoding and encoding.lower() != "identity":
        return None, f"payload was content-encoded ({encoding}); digest covers the encoded bytes"
    return False, f"sha1 {sha1_base32(body)} != CDX {expected}"


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
            digest_ok, digest_note = verify_digest(capture, response.content, source, response.headers)
            capture.update(
                fetch_state="fetched",
                digest_ok=digest_ok,
                digest_note=digest_note,
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


def _fall_back(capture: dict, client: httpx.Client, limiter: RateLimiter, sleep_fn) -> dict:
    """The wanted capture is permanently gone (4xx in both replay modes): work
    down the URL's alternate captures before declaring the file lost.

    Only a PERMANENT failure triggers this — a transient one keeps the wanted
    capture and is retried on resume. On success the row records which capture
    was actually used (`timestamp`) and which was wanted (`fallback_from`);
    on exhaustion it reverts to the wanted capture and lists every attempt.
    """
    alternates = capture.get("alternates") or []
    if capture["fetch_state"] != "failed_4xx" or not alternates:
        return capture
    wanted = {field: capture.get(field) for field in ("timestamp", "digest", "mimetype")}
    attempts = [f"{capture['timestamp']} ({capture.get('error')})"]
    while capture["fetch_state"] == "failed_4xx" and alternates:
        alternate = alternates.pop(0)
        capture.update(timestamp=alternate["timestamp"], digest=alternate.get("digest", ""),
                       mimetype=alternate.get("mimetype") or capture.get("mimetype"))
        fetch_one(capture, client, limiter, sleep_fn=sleep_fn)
        if capture["fetch_state"] == "fetched":
            capture["fallback_from"] = wanted["timestamp"]
            capture["alternates"] = alternates
            return capture
        attempts.append(f"{capture['timestamp']} ({capture.get('error')})")
        if capture["fetch_state"] == "failed_5xx":
            # throttled or flaky mid-chain: put this alternate back and let resume continue
            alternates.insert(0, alternate)
            break
    capture.update(**wanted, alternates=alternates, fallback_from=None)
    capture["error"] = f"{len(attempts)} captures tried: " + "; ".join(attempts)
    return capture


def _claim_path(claimed: dict[str, str], claimed_ci: dict[str, str], rel: str, url: str) -> tuple[str, bool]:
    """Reserve `rel` for `url`. Returns (path, collision).

    Case-insensitive filesystems (default APFS) treat Foo.html and foo.html
    as the same file, so claims are tracked case-insensitively — a URL whose
    path differs only in case from an existing claim gets a suffix."""
    if claimed_ci.get(rel.lower()) is None:
        claimed[rel] = url
        claimed_ci[rel.lower()] = url
        return rel, False
    if claimed_ci[rel.lower()] == url:
        return rel, False  # our own claim, any case
    stem, _, suffix = rel.rpartition(".")
    if not stem:
        stem, suffix = rel, ""
    else:
        suffix = "." + suffix
    for i in range(1, 1000):
        alt = f"{stem}-{i}{suffix}"
        if claimed_ci.get(alt.lower()) is None:
            claimed[alt] = url
            claimed_ci[alt.lower()] = url
            return alt, True
    raise RuntimeError(f"path collision exhausted for {rel}")


def run_pull(dig: Dig, client: httpx.Client, limit: int | None = None,
             limiter: RateLimiter | None = None, sleep_fn=time.sleep, progress=None) -> dict:
    """`progress(done, total, capture)` is called after every row is persisted."""
    asked = selection_hash(dig.load_captures())
    summary: dict | None = None
    try:
        summary = _run_pull(dig, client, limit, limiter, sleep_fn, progress)
        return summary
    finally:
        # rows are committed one by one as they finish; the readable export is
        # refreshed once per run — including a run cut short by Ctrl-C or an error
        if dig.db_path.exists():
            dig.export_captures_json()
        record_event(dig, "pull", asked, {"completed": summary is not None, "totals": dig.state_counts(),
                                           **({k: summary[k] for k in ("attempted", "fetched", "failed")} if summary else {})})


def _run_pull(dig: Dig, client: httpx.Client, limit: int | None,
              limiter: RateLimiter | None, sleep_fn, progress=None) -> dict:
    limiter = limiter or RateLimiter(sleep_fn=sleep_fn)
    captures = dig.load_captures()

    # rebuild path claims from existing state — dedupe key is the URL, so a
    # resumed run overwrites its own file instead of forking -1/-2 copies.
    # claimed_ci is the case-insensitive view (APFS-style filesystems).
    claimed: dict[str, str] = {}
    claimed_ci: dict[str, str] = {}
    for cap in captures:
        raw_path = cap.get("raw_path") or ""
        if raw_path.startswith("raw/"):
            rel = raw_path[len("raw/"):]
            claimed[rel] = cap["url"]
            claimed_ci[rel.lower()] = cap["url"]

    # pending rows first, then transient failures (the "resume retries these"
    # contract): permanent 4xx and skipped rows are never re-requested
    todo = ([c for c in captures if c["fetch_state"] == "pending"]
            + [c for c in captures if c["fetch_state"] == "failed_5xx"])
    if limit is not None and limit > 0:
        todo = todo[:limit]
    fetched = failed = 0
    done = 0

    def persist(row: dict) -> None:
        nonlocal done
        dig.save_capture(row)  # one row, after every row — kill-safe at any dig size
        done += 1
        if progress is not None:
            progress(done, len(todo), row)

    for capture in todo:
        prev_rel = capture.get("raw_path")
        prev_rel = prev_rel[len("raw/"):] if prev_rel and prev_rel.startswith("raw/") else None
        prev_collision = bool(capture.get("path_collision"))
        prev_sha1 = capture.get("sha1_fetched")
        prev_source = capture.get("source")
        prev_http = capture.get("http_status")
        prev_magic = capture.get("magic_ok")
        prev_digest = (capture.get("digest_ok"), capture.get("digest_note"))
        capture.update(sha1_fetched=None, source=None, http_status=None,
                       magic_ok=None, error=None, path_collision=None,
                       digest_ok=None, digest_note=None)
        result = fetch_one(capture, client, limiter, sleep_fn=sleep_fn)
        result = _fall_back(result, client, limiter, sleep_fn)
        body = result.pop("_body", None)
        if result["fetch_state"] == "fetched" and body is not None:
            if prev_rel and claimed.get(prev_rel) == result["url"]:
                # resumed row: reuse its own path (overwrites in place, no forks)
                rel, collision = prev_rel, prev_collision
                claimed[rel] = result["url"]
                claimed_ci[rel.lower()] = result["url"]
            else:
                rel, collision = _claim_path(claimed, claimed_ci, safe_relpath(result["url"]), result["url"])
            target = dig.raw_dir / rel
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body)
            except OSError as exc:
                # filesystem limits etc. — never wedge the run, never lose
                # previously-fetched bytes
                if prev_rel and claimed.get(prev_rel) == result["url"]:
                    result.update(fetch_state="fetched", raw_path=f"raw/{prev_rel}",
                                  path_collision=prev_collision or None, sha1_fetched=prev_sha1,
                                  source=prev_source, http_status=prev_http, magic_ok=prev_magic,
                                  digest_ok=prev_digest[0], digest_note=prev_digest[1],
                                  error=f"re-fetch write failed (will retry on next pull): {exc}")
                else:
                    result.update(fetch_state="failed_5xx", error=f"local write failed: {exc}",
                                  raw_path=None, path_collision=None)
                    claimed.pop(rel, None)
                    claimed_ci.pop(rel.lower(), None)
                failed += 1
                persist(result)
                continue
            result["raw_path"] = f"raw/{rel}"
            result["path_collision"] = collision or None
            fetched += 1
        else:
            # a failed (re-)fetch never destroys previously-fetched bytes — they
            # are the best-known capture. The attempt is recorded, state stays
            # honest, and the report discloses it.
            if prev_rel and claimed.get(prev_rel) == result["url"]:
                result["error"] = f"re-fetch failed ({result['fetch_state']}): {result.get('error')}"
                result.update(fetch_state="fetched", raw_path=f"raw/{prev_rel}",
                              path_collision=prev_collision or None, sha1_fetched=prev_sha1,
                              source=prev_source, http_status=prev_http, magic_ok=prev_magic,
                              digest_ok=prev_digest[0], digest_note=prev_digest[1])
            else:
                result.update(raw_path=None, path_collision=None)
            failed += 1
        persist(result)
    dig.write_manifest_json(captures)  # keep the spec manifest in step with fetch states
    counts = {state: sum(1 for c in captures if c["fetch_state"] == state)
              for state in ("pending", "fetched", "failed_4xx", "failed_5xx", "skipped")}
    return {"attempted": len(todo), "fetched": fetched, "failed": failed, "totals": counts}
