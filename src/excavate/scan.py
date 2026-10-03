"""Output-scan gate (PRD FR-E5b) — scan what gets PUBLISHED, not what was read.

The justifying incident: velvet-bbs shipped 266 users' plaintext passwords in
its output repo while its README said they stayed private. The corpus had
been handled carefully; the generated output leaked. So this scans the build
directory itself — every file, dotfiles and data files included — right
before anything is pushed or deployed.

It is a hard gate, not a linter: the scan is clean only when every finding
is gone or has been explicitly reviewed and allow-listed with a reason. What
could not be inspected (an archive, an oversized file) is itself a finding —
unscanned content is never reported as clean.

Deterministic patterns only; no model, no network. It is triage: it catches
the well-known shapes of credentials and bulk personal data, and a human
still decides. Findings never echo the secret — only a redacted preview and
a fingerprint.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .dig import Dig, _atomic_write
from .provenance import record_event, tree_hash

MAX_BYTES = 20 * 1024 * 1024
BULK_THRESHOLD = 20  # distinct emails / phone numbers in ONE file before it reads as a list of people
ALLOW_FILE = "scan-allow.json"
RESULT_FILE = "scan.json"

ARCHIVE_SUFFIXES = (".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".lzh", ".arj", ".arc")

# file names that are a finding by themselves
RISKY_NAMES: tuple[tuple[str, str], ...] = (
    (".env", "environment file"), (".env.*", "environment file"), (".htpasswd", "password file"),
    ("id_rsa", "SSH private key"), ("id_dsa", "SSH private key"), ("id_ecdsa", "SSH private key"),
    ("id_ed25519", "SSH private key"), ("*.pem", "key/certificate file"), ("*.key", "key file"),
    ("*.pfx", "key store"), ("*.p12", "key store"), ("*.kdbx", "password database"),
    ("master.passwd*", "password file"), ("passwd", "password file"), ("shadow", "password file"),
    ("wp-config.php", "application config with credentials"), ("*.sql", "database dump"),
    (".npmrc", "package-registry credentials"), (".netrc", "stored logins"), ("credentials", "credentials file"),
)
RISKY_DIRS = (".git", ".svn", ".ssh", ".aws")

_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("private-key", "private key block",
     re.compile(r"(?a)-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----")),
    ("aws-access-key", "AWS access key id", re.compile(r"(?a)\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token", "GitHub token", re.compile(r"(?a)\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack-token", "Slack token", re.compile(r"(?a)\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("google-api-key", "Google API key", re.compile(r"(?a)\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("stripe-key", "Stripe live key", re.compile(r"(?a)\b[sr]k_live_[0-9A-Za-z]{24,}\b")),
    ("password-hash", "password hash (htpasswd / crypt)",
     re.compile(r"(?am)^[^\s:#]{1,64}:\$(?:apr1|1|2[aby]|5|6)\$[^\s:]{8,}")),
    ("passwd-file", "unix passwd-format line",
     re.compile(r"(?am)^[a-z_][a-z0-9_-]{0,31}:[^:\n]*:\d+:\d+:[^:\n]*:[^:\n]*:[^:\n]*$")),
    ("credential-assignment", "credential assigned in clear text",
     re.compile(r"""(?ai)\b(?:pass(?:word|wd)?|secret|api[_-]?key|auth[_-]?token|access[_-]?token)\b["']?\s*[:=]\s*["']([^"'\s<>]{6,})["']""")),
    ("env-credential", "credential in a KEY=value line",
     re.compile(r"(?am)^[A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API_KEY|PRIVATE_KEY)[A-Z0-9_]*=(\S{6,})$")),
    ("us-ssn", "US social security number", re.compile(r"(?a)\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")),
)
_CARD = re.compile(r"(?a)\b(?:\d[ -]?){12,18}\d\b")
_EMAIL = re.compile(r"(?a)\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?a)(?<![\w.])(?:\+?\d{1,3}[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?![\w.])")


@dataclass(frozen=True)
class Finding:
    rule: str
    description: str
    path: str
    line: int          # 1-based; 0 = about the file as a whole
    preview: str       # redacted — never the secret itself
    fingerprint: str
    allowed: bool = False
    reason: str | None = None


def _fingerprint(rule: str, path: str, matched: str) -> str:
    return hashlib.sha256(f"{rule}\0{path}\0{matched}".encode("utf-8", "surrogateescape")).hexdigest()[:16]


def redact(text: str) -> str:
    flat = " ".join(text.split())
    if len(flat) <= 8:
        return "*" * len(flat)
    return f"{flat[:4]}…{'*' * 6} ({len(flat)} chars)"


def _luhn(digits: str) -> bool:
    total = 0
    for index, ch in enumerate(reversed(digits)):
        n = int(ch)
        if index % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def _card_shaped(digits: str) -> bool:
    """Issuer prefix + length of a real card scheme. Luhn alone is a 1-in-10
    coincidence: without this, every tenth 14-digit Wayback timestamp
    (20040101000000…) would be reported as a payment card."""
    n = len(digits)
    two, four = int(digits[:2]), int(digits[:4])
    return (
        (digits[0] == "4" and n in (13, 16, 19))                       # Visa
        or (n == 16 and (51 <= two <= 55 or 2221 <= four <= 2720))     # Mastercard
        or (n == 15 and two in (34, 37))                               # American Express
        or (n == 16 and (four == 6011 or two == 65))                   # Discover
        or (n == 14 and (two in (36, 38) or 300 <= int(digits[:3]) <= 305))  # Diners Club
        or (n == 16 and 3528 <= four <= 3589)                          # JCB
    )


def _scan_text(text: str, rel: str) -> list[Finding]:
    found: list[Finding] = []

    def add(rule: str, description: str, start: int, matched: str, preview: str | None = None) -> None:
        line = text.count("\n", 0, start) + 1
        found.append(Finding(rule, description, rel, line, preview if preview is not None else redact(matched),
                             _fingerprint(rule, rel, matched)))

    for rule, description, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            add(rule, description, match.start(), match.group(match.lastindex or 0))
    for match in _CARD.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19 and len(set(digits)) > 1 and _card_shaped(digits) and _luhn(digits):
            add("payment-card", "payment card number (issuer prefix + Luhn check)", match.start(), digits)
    for rule, description, pattern in (("bulk-emails", "email addresses", _EMAIL), ("bulk-phones", "phone numbers", _PHONE)):
        distinct = sorted({m.group(0).lower() for m in pattern.finditer(text)})
        if len(distinct) >= BULK_THRESHOLD:
            found.append(Finding(rule, f"{len(distinct)} distinct {description} in one file (a list of people?)", rel, 0,
                                 f"{len(distinct)} distinct values", _fingerprint(rule, rel, "\n".join(distinct))))
    return found


def _scan_name(rel: str) -> list[Finding]:
    found = []
    parts = rel.split("/")
    name = parts[-1].lower()
    for folder in parts[:-1]:
        if folder.lower() in RISKY_DIRS:
            found.append(Finding("risky-directory", f"file inside a `{folder}` directory", rel, 0, folder,
                                 _fingerprint("risky-directory", rel, folder)))
            break
    for pattern, description in RISKY_NAMES:
        if fnmatch.fnmatch(name, pattern):
            found.append(Finding("risky-filename", f"{description} by name", rel, 0, parts[-1],
                                 _fingerprint("risky-filename", rel, name)))
            break
    return found


def scan_tree(root: Path) -> tuple[list[Finding], int]:
    """Every finding under `root`, plus the number of files looked at."""
    findings: list[Finding] = []
    files = sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []
    for path in files:
        rel = path.relative_to(root).as_posix()
        findings.extend(_scan_name(rel))
        size = path.stat().st_size
        if rel.lower().endswith(ARCHIVE_SUFFIXES):
            findings.append(Finding("uninspected-archive", "archive — contents were not inspected", rel, 0,
                                    f"{size} bytes", _fingerprint("uninspected-archive", rel, str(size))))
        if size > MAX_BYTES:
            findings.append(Finding("uninspected-large-file", f"larger than {MAX_BYTES // (1024 * 1024)} MB — not scanned",
                                    rel, 0, f"{size} bytes", _fingerprint("uninspected-large-file", rel, str(size))))
            continue
        # latin-1 maps every byte to a character: patterns match inside any file, text or not
        # (the patterns are ASCII-mode, so a neighbouring high byte is not mistaken for a letter)
        findings.extend(_scan_text(path.read_bytes().decode("latin-1"), rel))
    return findings, len(files)


def load_allowlist(dig: Dig) -> dict[str, dict]:
    path = dig.root / ALLOW_FILE
    if not path.exists():
        return {}
    return {entry["fingerprint"]: entry for entry in json.loads(path.read_text())}


def allow(dig: Dig, fingerprint: str, reason: str) -> dict:
    """Record a reviewed finding as acceptable. A reason is mandatory — the
    allow-list is the record of who decided what could be published."""
    if not reason.strip():
        raise ValueError("an allow-list entry needs a reason")
    result_path = dig.root / RESULT_FILE
    known = json.loads(result_path.read_text())["findings"] if result_path.exists() else []
    finding = next((f for f in known if f["fingerprint"] == fingerprint), None)
    if finding is None:
        raise ValueError(f"no finding with fingerprint {fingerprint} in the last scan — run `excavate scan` first")
    entries = load_allowlist(dig)
    entries[fingerprint] = {"fingerprint": fingerprint, "rule": finding["rule"], "path": finding["path"],
                            "reason": reason.strip()}
    _atomic_write(dig.root / ALLOW_FILE, json.dumps(sorted(entries.values(), key=lambda e: (e["path"], e["rule"])), indent=2))
    return entries[fingerprint]


def run_scan(dig: Dig, path: Path | None = None) -> dict:
    """Scan the site (or `path`) and write scan.json. `clean` is True only
    when nothing is left un-allow-listed."""
    root = Path(path) if path is not None else dig.site_dir
    findings, files = scan_tree(root)
    allowlist = load_allowlist(dig)
    resolved = [Finding(**{**asdict(f), "allowed": f.fingerprint in allowlist,
                           "reason": allowlist.get(f.fingerprint, {}).get("reason")}) for f in findings]
    blocking = [f for f in resolved if not f.allowed]
    by_rule: dict[str, int] = {}
    for finding in blocking:
        by_rule[finding.rule] = by_rule.get(finding.rule, 0) + 1
    site_hash = tree_hash(root)
    result = {
        "scanned": str(root),
        "tree_hash": site_hash,
        "files": files,
        "clean": not blocking and files > 0,
        "blocking": len(blocking),
        "allowed": len(resolved) - len(blocking),
        "by_rule": dict(sorted(by_rule.items())),
        "findings": [asdict(f) for f in resolved],
    }
    _atomic_write(dig.root / RESULT_FILE, json.dumps(result, indent=2))
    record_event(dig, "scan", site_hash,
                 {key: result[key] for key in ("files", "clean", "blocking", "allowed", "by_rule")})
    return result


def scan_status(dig: Dig) -> tuple[str, dict | None]:
    """("not run" | "stale" | "clean" | "findings", last result). A scan of a
    site that has since changed is stale — it vouches for nothing."""
    path = dig.root / RESULT_FILE
    if not path.exists():
        return "not run", None
    result = json.loads(path.read_text())
    if result["scanned"] == str(dig.site_dir) and result["tree_hash"] != tree_hash(dig.site_dir):
        return "stale", result
    return ("clean" if result["clean"] else "findings"), result
