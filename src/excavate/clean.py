"""Stage 3 — de-Wayback clean: raw/ → site/.

Every rewrite is logged to REWRITES.md (disclosed-deviations model, never
silent). Genuinely missing files stay missing — they surface in the report.
site/ mirrors raw/ exactly (paths derive from raw_path, not from re-deriving
the URL), and the tree is wiped before each full clean so no orphans linger.

One mapping function (rewrite_target / local_target → safe_relpath) serves
both the rewriter and the link-graph, so a link's written form and its
analysis target can never disagree.
"""

from __future__ import annotations

import posixpath
import re
import shutil
from pathlib import Path
from urllib.parse import urljoin

from .chrome import strip_chrome
from .dig import Dig, safe_relpath
from .encoding import decode_document, encode_document
from .rewrite import scan
from .twins import Twin, TwinIndex

# /web/<timestamp><mode>/ prefix inside im_-sourced or stale-linked content —
# absolute and path-relative forms, any case (real pages vary)
WAYBACK_PREFIX = re.compile(r"https?://web\.archive\.org/web/\d{6,14}(?:[a-z]{2}_)?/", re.IGNORECASE)
WAYBACK_PREFIX_REL = re.compile(r"^/web/\d{6,14}(?:[a-z]{2}_)?/", re.IGNORECASE)
WAYBACK_PREFIX_PROTOREL = re.compile(r"^//web\.archive\.org/web/\d{6,14}(?:[a-z]{2}_)?/", re.IGNORECASE)

# URL discovery lives in rewrite.scan (a span tokenizer) — one scanner feeds
# both the rewriter and the link graph
TEXT_SUFFIXES = (".html", ".htm", ".css")
_SKIP_PREFIXES = ("#", "mailto:", "tel:", "javascript:", "data:")
# any other scheme (ftp:, news:, irc:, aim:, gopher: ...) is a link OUT of the
# site — never a local file, never "never-crawled"
_OTHER_SCHEME = re.compile(r"(?!https?:)[A-Za-z][A-Za-z0-9+.\-]*:", re.IGNORECASE)

# a "rewrite" proper: `before` → `after` — chrome strips, collision notes and
# ERROR lines are disclosed in the ledger but counted separately
REWRITE_LINE = re.compile(r"^- `.*` → `.*` \(")
CHROME_LINE = re.compile(r"^- wayback chrome removed \(")
TWIN_LINE = re.compile(r"^- `.*` → `.*` \(.*; twin: ")


def same_site_host(host: str, domain: str) -> bool:
    """www. variants of the inventory domain are the same site."""
    return host.lower().removeprefix("www.") == domain.lower().removeprefix("www.")


def _strip_wayback(url: str) -> str:
    stripped = WAYBACK_PREFIX.sub("", url)
    stripped = WAYBACK_PREFIX_PROTOREL.sub("", stripped)
    return WAYBACK_PREFIX_REL.sub("", stripped)


def _classify(domain: str, url: str) -> tuple[str, str] | None:
    """(kind, path) for a URL, or None if not a local reference.

    kind is "absolute" (http(s) same-site, incl. www variant) or "root"
    (starts with /). Page-relative URLs return None from here — the rewriter
    leaves them untouched; the link-graph resolves them against base_dir.
    """
    url = url.strip()
    if not url or url.lower().startswith(_SKIP_PREFIXES):
        return None
    stripped = _strip_wayback(url)
    if stripped != url:
        url = stripped
    match = re.match(r"(?i:https?)://([^/?#]+)([/?#].*)?$", url)
    if match:
        if not same_site_host(match.group(1), domain):
            return None  # external link
        rest = match.group(2) or "/"
    elif url.startswith("//"):
        return None  # protocol-relative external
    elif _OTHER_SCHEME.match(url):
        return None  # non-http scheme: external by definition
    elif not url.startswith("/"):
        return ("relative", url)
    else:
        rest = url
    if rest.startswith(("#", "?")):
        return None
    return ("local", rest)


def rewrite_target(domain: str, url: str) -> str | None:
    """Local path for absolute/wayback/root-absolute links; None = leave alone.

    Page-relative links return None here — they keep working unchanged in the
    mirrored tree.
    """
    classified = _classify(domain, url)
    if classified is None or classified[0] == "relative":
        return None
    return safe_relpath(f"http://{domain}{classified[1]}")


def local_target(domain: str, url: str, base_dir: str = "") -> str | None:
    """Local path for ANY link (the link-graph mapping), resolving
    page-relative URLs against base_dir the way a browser would."""
    classified = _classify(domain, url)
    if classified is None:
        return None
    kind, value = classified
    if kind == "relative":
        joined = posixpath.join(base_dir, value) if base_dir else value
        value = "/" + posixpath.normpath(joined)
    return safe_relpath(f"http://{domain}{value}")


def page_relative(site_path: str, page_rel: str) -> str:
    """Express a site-root path relative to the page that links to it.

    Rewritten links must work from any page depth, from file:// and from a
    sub-path deploy — so they are page-relative, never root-absolute.
    """
    return posixpath.relpath(site_path, posixpath.dirname(page_rel) or ".")


def _href_escape(path: str) -> str:
    """A local path as written into a link. File names on disk are
    percent-DEcoded, so a literal %, # or ? in one must be re-encoded or the
    browser reads it as an escape, a fragment or a query."""
    return path.replace("%", "%25").replace("#", "%23").replace("?", "%3F")


def _rewrite(domain: str, url: str, rel_path: str, base_abs: str | None = None,
             twins: TwinIndex | None = None) -> tuple[str | None, Twin | None]:
    """(rewritten form of `url` as written into `rel_path`, twin used) —
    (None, None) = leave alone.

    The #fragment survives the rewrite (it is not part of the file mapping).
    With a `<base href>` in play, every URL is first resolved against it —
    the base tag itself is removed, so relative links must stop depending on it.
    With a twin index, a link whose target was never recovered is pointed at
    its surviving twin when one is on disk (page-relative links included —
    otherwise those are left exactly as written).
    """
    if base_abs is not None:
        stripped = _strip_wayback(url.strip())
        if not stripped or stripped.lower().startswith(_SKIP_PREFIXES) or _OTHER_SCHEME.match(stripped):
            return None, None
        url = urljoin(base_abs, stripped)
    base, sep, fragment = url.partition("#")
    local = rewrite_target(domain, base)
    page_relative_link = False
    if local is None:
        if base_abs is not None:
            return url, None  # external (absolutized because it leaned on <base>)
        if twins is None:
            return None, None
        local = local_target(domain, base, posixpath.dirname(rel_path))
        if local is None:
            return None, None  # external, anchor, mailto, ...
        page_relative_link = True
    twin = twins.find(local) if twins is not None else None
    if twin is not None:
        local = twin.path
    elif page_relative_link:
        return None, None  # a working (or honestly missing) relative link is never touched
    return _href_escape(page_relative(local, rel_path)) + sep + fragment, twin


def _base_url(domain: str, rel_path: str, base_href: str | None) -> str | None:
    """Absolute URL of the document's <base href>, or None if it has none."""
    if not base_href:
        return None
    return urljoin(f"http://{domain}/{rel_path}", _strip_wayback(base_href))


def clean_text(text: str, domain: str, rel_path: str, is_css: bool,
               twins: TwinIndex | None = None) -> tuple[str, list[str]]:
    """Rewrite one HTML/CSS document; returns (new_text, log_lines)."""
    log: list[str] = []

    # chrome first: its markup carries /_static/ URLs that must never be
    # mistaken for site links by the rewriter or the link graph
    text, removed = strip_chrome(text, is_css)
    log.extend(f"- wayback chrome removed ({rel_path}): {item}" for item in removed)
    document = scan(text, is_css)
    base_abs = _base_url(domain, rel_path, document.base_href)

    def rewrite(url: str, flavor: str) -> str | None:
        new, twin = _rewrite(domain, url, rel_path, base_abs, twins)
        if new is not None and new != url:
            note = f"; twin: {twin.reason} — the linked file was never recovered" if twin else ""
            log.append(f"- `{url}` → `{new}` ({rel_path}{flavor}{note})")
        return new

    text = document.apply(rewrite, base_replacement="<!-- base href removed: resolved into each link -->")
    if base_abs is not None:
        log.append(f"- <base href=`{document.base_href}`> removed ({rel_path}): links resolved against it individually")
    return text, log


def _decode(data: bytes, is_css: bool = False) -> str:
    return decode_document(data, is_css).text


def internal_targets(text: str, domain: str, page_rel: str = "") -> set[str]:
    """Local link targets in a document, resolved the way a browser would."""
    base_dir = posixpath.dirname(page_rel)
    document = scan(text, is_css=page_rel.lower().endswith(".css"))
    base_abs = _base_url(domain, page_rel, document.base_href)
    targets: set[str] = set()
    for url in document.urls():
        if base_abs is not None:
            stripped = _strip_wayback(url)
            if not stripped or stripped.lower().startswith(_SKIP_PREFIXES) or _OTHER_SCHEME.match(stripped):
                continue
            url = urljoin(base_abs, stripped)
        target = local_target(domain, url.partition("#")[0], base_dir)
        if target is not None:
            targets.add(target)
    return targets


def external_hosts(text: str, domain: str, page_rel: str = "") -> list[str]:
    """Host of every http(s) link in a document that leaves the site (one
    entry per link) — what the site leaned on that this dig does not hold."""
    document = scan(text, is_css=page_rel.lower().endswith(".css"))
    base_abs = _base_url(domain, page_rel, document.base_href)
    hosts: list[str] = []
    for url in document.urls():
        url = _strip_wayback(url.strip())
        if base_abs is not None and url and not url.lower().startswith(_SKIP_PREFIXES):
            url = urljoin(base_abs, url)
        match = re.match(r"(?i)(?:https?:)?//([^/?#:]+)", url)
        if match and not same_site_host(match.group(1), domain):
            hosts.append(match.group(1).lower())
    return hosts


def site_rel(capture: dict) -> str:
    """Site path mirrors the raw tree — derived from raw_path when present."""
    raw_path = capture.get("raw_path") or ""
    if raw_path.startswith("raw/"):
        try:
            return Path(raw_path).relative_to("raw").as_posix()
        except ValueError:
            pass  # malformed — fall through to URL-derived path (reads are still gated)
    return safe_relpath(capture["url"])


def _resolve_raw(dig: Dig, capture: dict) -> Path | None:
    """Resolve the raw file, refusing paths that escaped the dig directory."""
    raw_file = (dig.root / capture["raw_path"]).resolve()
    if not raw_file.is_relative_to(dig.root.resolve()):
        return None
    return raw_file


def effective_pulled(dig: Dig, captures: list[dict]) -> tuple[set[str], list[dict], list[str]]:
    """Paths actually present in raw/ for fetched captures.

    Shared by clean and report so both agree: only captures with a real,
    in-dig raw file count as pulled. Returns (pulled_paths, valid_captures,
    error_lines).
    """
    valid: list[dict] = []
    errors: list[str] = []
    for capture in captures:
        if capture.get("fetch_state") != "fetched" or not capture.get("raw_path"):
            continue
        raw_file = _resolve_raw(dig, capture)
        if raw_file is not None and raw_file.is_file():
            valid.append(capture)
        else:
            errors.append(f"- ERROR: raw file missing or outside dig for `{capture['url']}` — skipped")
    return {site_rel(c) for c in valid}, valid, errors


def run_clean(dig: Dig, twin_swaps: bool = True) -> dict:
    """De-Wayback raw/ into site/. `twin_swaps=False` leaves every link to a
    never-recovered file exactly as the site wrote it (no surviving-twin
    substitution) — for a strictly as-archived rebuild."""
    domain = dig.domain
    captures = dig.load_captures()
    all_log: list[str] = []
    internal: set[str] = set()
    encodings: dict[str, int] = {}

    pulled, valid, errors = effective_pulled(dig, captures)
    all_log.extend(errors)

    if not valid:
        # nothing cleanable — do NOT wipe a previously cleaned site/, and do
        # not erase its rewrite history either: append, never overwrite
        ledger = dig.root / "REWRITES.md"
        prior = ledger.read_text() if ledger.exists() else (
            f"# REWRITES — {dig.domain}\n\nEvery URL rewrite applied during clean. Nothing silent.\n\n")
        ledger.write_text(prior.rstrip() + "\n\n" + "\n".join(all_log)
                          + "\nno cleanable captures — site/ and prior ledger left untouched\n",
                          encoding="utf-8")
        return {"cleaned": 0, "rewrites": 0, "never_crawled": [], "encodings": {}, "chrome_removed": 0, "twin_swaps": 0}

    twins = TwinIndex(pulled) if twin_swaps else None

    # full re-clean: wipe stale output so no orphan survives from earlier runs
    if dig.site_dir.exists():
        shutil.rmtree(dig.site_dir)
    dig.site_dir.mkdir(parents=True, exist_ok=True)

    for capture in valid:
        raw_file = _resolve_raw(dig, capture)
        if raw_file is None:  # effective_pulled already filtered these
            continue
        rel = site_rel(capture)
        target = dig.site_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if rel.lower().endswith(TEXT_SUFFIXES):
            is_css = rel.lower().endswith(".css")
            data = raw_file.read_bytes()
            doc = decode_document(data, is_css)
            text, log = clean_text(doc.text, domain, rel, is_css=is_css, twins=twins)
            # no rewrite → the original bytes, untouched; otherwise re-encode in
            # the page's own charset so its declaration stays true
            target.write_bytes(data if text == doc.text else encode_document(text, doc, is_css))
            encodings[doc.codec] = encodings.get(doc.codec, 0) + 1
            if doc.note:
                all_log.append(f"- encoding: `{rel}` — {doc.note}; kept as `{doc.codec}`")
            all_log.extend(log)
            internal |= internal_targets(text, domain, page_rel=rel)
        else:
            shutil.copyfile(raw_file, target)

        if capture.get("path_collision"):
            canonical = safe_relpath(capture["url"])
            all_log.append(
                f"- path collision: `{capture['url']}` stored at `{rel}` "
                f"(in-page links resolve to the first capture at `{canonical}`)"
            )

    (dig.root / "REWRITES.md").write_text(
        f"# REWRITES — {domain}\n\nEvery URL rewrite applied during clean. Nothing silent.\n\n"
        + "\n".join(all_log) + "\n",
        encoding="utf-8",
    )
    never_crawled = sorted(t for t in internal if t not in pulled and t != "")
    rewrites = sum(1 for line in all_log if REWRITE_LINE.match(line))
    return {"cleaned": len(valid), "rewrites": rewrites, "never_crawled": never_crawled,
            "chrome_removed": sum(1 for line in all_log if CHROME_LINE.match(line)),
            "twin_swaps": sum(1 for line in all_log if TWIN_LINE.match(line)),
            "encodings": dict(sorted(encodings.items()))}
