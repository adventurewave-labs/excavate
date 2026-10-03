"""SOTA loop 12 — output-scan gate (offline).

Every secret-shaped string here is fake and assembled at runtime, so this
file itself contains nothing a secret scanner would (or should) flag.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from excavate.clean import run_clean
from excavate.cli import main
from excavate.dig import Dig
from excavate.provenance import load_events
from excavate.report import run_report
from excavate.scan import BULK_THRESHOLD, _luhn, allow, redact, run_scan, scan_status, scan_tree
from test_m0 import fresh_capture

FAKE = "FAKE"
AWS = "AK" + "IA" + FAKE * 4
GITHUB = "gh" + "p_" + "FAKEfake" * 5
PRIVATE_KEY = "-----BEGIN " + "RSA PRIVATE" + " KEY-----\nFAKEFAKEFAKE\n-----END RSA PRIVATE KEY-----\n"
CARD = "4111 1111 1111 " + "1111"          # the canonical test card number
HTPASSWD = "sysop:$" + "apr1$" + "fakesalt$" + "fakefakefakefakefakefa\n"
PASSWD_LINES = "root:x:0:0:root:/root:/bin/sh\nmarcus:x:1000:1000:Fake User:/home/marcus:/bin/sh\n"


def site(tmp: str, files: dict[str, bytes | str]) -> Dig:
    dig = Dig.create(Path(tmp), "x.com")
    caps = []
    for rel, data in files.items():
        target = dig.raw_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data.encode() if isinstance(data, str) else data)
        cap = fresh_capture(f"http://x.com/{rel}")
        cap.update(fetch_state="fetched", raw_path=f"raw/{rel}")
        caps.append(cap)
    dig.save_captures(caps)
    run_clean(dig)
    return dig


def rules(findings) -> list[str]:
    return sorted(f.rule for f in findings)


def tree(tmp: str, files: dict[str, str | bytes]) -> Path:
    root = Path(tmp)
    for rel, data in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data.encode() if isinstance(data, str) else data)
    return root


class TestPatterns(unittest.TestCase):
    def _rules(self, files: dict[str, str | bytes]) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            return rules(scan_tree(tree(tmp, files))[0])

    def test_credentials_by_shape(self):
        self.assertEqual(self._rules({"a.html": f"<!-- key {AWS} -->"}), ["aws-access-key"])
        self.assertEqual(self._rules({"a.js": f'var t = "{GITHUB}";'}), ["github-token"])
        self.assertEqual(self._rules({"notes.txt": PRIVATE_KEY}), ["private-key"])
        self.assertEqual(self._rules({"cfg.js": 'var password = "hunter2hunter2";'}), ["credential-assignment"])
        self.assertEqual(self._rules({"settings.txt": "DB_PASSWORD=notarealpassword\n"}), ["env-credential"])
        self.assertEqual(self._rules({"users.txt": HTPASSWD}), ["password-hash"])
        self.assertEqual(self._rules({"etc.txt": PASSWD_LINES}), ["passwd-file", "passwd-file"])

    def test_payment_card_needs_luhn(self):
        self.assertEqual(self._rules({"order.html": f"card: {CARD}"}), ["payment-card"])
        self.assertEqual(self._rules({"order.html": "ref: 4111 1111 1111 1112"}), [])   # fails Luhn
        self.assertEqual(self._rules({"zeros.html": "0000 0000 0000 0000"}), [])        # passes Luhn, obviously not a card

    def test_wayback_timestamps_are_not_payment_cards(self):
        # found by the golden run: 1 in 10 fourteen-digit timestamps passes Luhn
        luhn_valid = [f"2004010100{n:04d}" for n in range(200) if _luhn(f"2004010100{n:04d}")]
        self.assertGreater(len(luhn_valid), 10)
        page = "".join(f'<a href="https://web.archive.org/web/{stamp}/http://x.com/">c</a> {stamp}\n' for stamp in luhn_valid)
        self.assertEqual(self._rules({"PROVENANCE.json": page}), [])
        for real_shape in ("4111111111111111", "5555555555554444", "378282246310005", "6011111111111117", "30569309025904"):
            self.assertEqual(self._rules({"cards.txt": f"n {real_shape}"}), ["payment-card"], real_shape)

    def test_found_inside_binary_and_data_files(self):
        blob = b"\x00\x01\xff" + AWS.encode() + b"\xfe\x00"
        self.assertEqual(self._rules({"data/export.bin": blob}), ["aws-access-key"])
        self.assertEqual(self._rules({"data/users.json": json.dumps({"secret": "plaintextsecret"})}), ["credential-assignment"])

    def test_risky_names_and_directories(self):
        self.assertEqual(self._rules({".env": "X=1\n"}), ["risky-filename"])
        self.assertEqual(self._rules({"backup/dump.sql": "create table t;"}), ["risky-filename"])
        self.assertEqual(self._rules({".git/config": "[core]"}), ["risky-directory"])
        self.assertEqual(self._rules({"data/system/master.passwd-bbs-users.txt": "nothing"}), ["risky-filename"])  # velvet-bbs

    def test_uninspectable_content_is_a_finding_not_a_pass(self):
        self.assertEqual(self._rules({"files/warez.zip": b"PK\x03\x04rest"}), ["uninspected-archive"])

    def test_bulk_personal_data(self):
        emails = "\n".join(f"user{i}@example.org" for i in range(BULK_THRESHOLD))
        self.assertEqual(self._rules({"members.html": emails}), ["bulk-emails"])
        self.assertEqual(self._rules({"contact.html": "mail webmaster@example.org or sales@example.org"}), [])  # a contact page is not a leak
        phones = "\n".join(f"555-{100 + i}-{1000 + i}" for i in range(BULK_THRESHOLD))
        self.assertEqual(self._rules({"phonebook.txt": phones}), ["bulk-phones"])

    def test_ordinary_site_content_is_clean(self):
        page = ('<html><form><input type="password" name="password"></form>'
                '<p>Forgot your password? Your token of appreciation: call 555-1234.</p>'
                '<script>var apiKey = getKey(); if (password == confirm) submit();</script>'
                '<a href="mailto:webmaster@example.org">mail</a> ISBN 0-306-40615-2</html>')
        self.assertEqual(self._rules({"index.html": page, "style.css": "a{color:red}", "img/a.gif": b"GIF89a"}), [])

    def test_redaction_never_shows_the_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            finding = scan_tree(tree(tmp, {"a.txt": f"line one\nkey {AWS}\n"}))[0][0]
        self.assertEqual(finding.line, 2)
        self.assertNotIn(AWS, finding.preview)
        self.assertNotIn(AWS[8:], finding.preview)
        self.assertEqual(redact("short"), "*****")


class TestGate(unittest.TestCase):
    def test_clean_site_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = site(tmp, {"index.html": "<p>hello</p>"})
            result = run_scan(dig)
            self.assertTrue(result["clean"])
            self.assertEqual(main(["scan", "--dig", str(dig.root)]), 0)
            self.assertEqual(scan_status(dig)[0], "clean")

    def test_findings_block_until_reviewed(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = site(tmp, {"index.html": "<p>hello</p>", "admin/notes.txt": f"key {AWS}\n", "users.txt": HTPASSWD})
            self.assertEqual(main(["scan", "--dig", str(dig.root)]), 1)
            result = json.loads((dig.root / "scan.json").read_text())
            self.assertEqual((result["clean"], result["blocking"]), (False, 2))
            self.assertNotIn(AWS, (dig.root / "scan.json").read_text())  # the result file must not leak either

            prints = {f["rule"]: f["fingerprint"] for f in result["findings"]}
            self.assertEqual(main(["scan", "--dig", str(dig.root), "--allow", prints["aws-access-key"],
                                   "--reason", "example key from the site's own tutorial page"]), 1)  # one left
            self.assertEqual(main(["scan", "--dig", str(dig.root), "--allow", prints["password-hash"],
                                   "--reason", "defunct BBS, accounts gone since 1998"]), 0)
            final = json.loads((dig.root / "scan.json").read_text())
            self.assertEqual((final["clean"], final["blocking"], final["allowed"]), (True, 0, 2))
            allowlist = json.loads((dig.root / "scan-allow.json").read_text())
            self.assertEqual(sorted(e["reason"] for e in allowlist),
                             ["defunct BBS, accounts gone since 1998", "example key from the site's own tutorial page"])

    def test_allow_requires_a_reason_and_a_real_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = site(tmp, {"a.txt": f"key {AWS}"})
            fingerprint = run_scan(dig)["findings"][0]["fingerprint"]
            with self.assertRaises(ValueError):
                allow(dig, fingerprint, "   ")
            with self.assertRaises(ValueError):
                allow(dig, "0" * 16, "made up")
            with self.assertRaises(SystemExit):
                main(["scan", "--dig", str(dig.root), "--allow", fingerprint])
            self.assertFalse((dig.root / "scan-allow.json").exists())

    def test_allowance_is_tied_to_that_exact_secret_in_that_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = site(tmp, {"a.txt": f"key {AWS}"})
            allow(dig, run_scan(dig)["findings"][0]["fingerprint"], "reviewed")
            self.assertTrue(run_scan(dig)["clean"])
            other = "AK" + "IA" + "OTHR" * 4
            (dig.site_dir / "a.txt").write_text(f"key {other}")      # a different key appears in the same file
            self.assertFalse(run_scan(dig)["clean"])
            (dig.site_dir / "a.txt").write_text(f"key {AWS}")
            (dig.site_dir / "b.txt").write_text(f"key {AWS}")        # the same key appears somewhere new
            self.assertEqual(run_scan(dig)["blocking"], 1)

    def test_stale_scan_does_not_vouch_for_a_changed_site(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = site(tmp, {"index.html": "<p>hello</p>"})
            run_scan(dig)
            self.assertEqual(scan_status(dig)[0], "clean")
            (dig.site_dir / "late.txt").write_text(PRIVATE_KEY)
            self.assertEqual(scan_status(dig)[0], "stale")
            self.assertEqual(run_report(dig)["scan"], "stale")
            self.assertIn("**Stale.**", (dig.root / "VERIFICATION-REPORT.md").read_text())

    def test_report_states_the_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = site(tmp, {"index.html": "<p>hello</p>", "k.txt": PRIVATE_KEY})
            self.assertEqual(run_report(dig)["scan"], "not run")
            self.assertIn("**Not run.**", (dig.root / "VERIFICATION-REPORT.md").read_text())
            run_scan(dig)
            self.assertEqual(run_report(dig)["scan"], "findings")
            text = (dig.root / "VERIFICATION-REPORT.md").read_text()
            self.assertIn("BLOCKED — 1 unreviewed finding(s)", text)
            self.assertIn("- private-key: 1", text)
            self.assertNotIn("FAKEFAKEFAKE", text)

    def test_scan_any_directory_and_empty_is_not_clean(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as build:
            dig = site(tmp, {"index.html": "<p>hello</p>"})
            (Path(build) / ".env").write_text("API_TOKEN=notarealtoken123\n")
            self.assertEqual(main(["scan", "--dig", str(dig.root), "--path", build]), 1)
            result = json.loads((dig.root / "scan.json").read_text())
            self.assertEqual(sorted(result["by_rule"]), ["env-credential", "risky-filename"])
        with tempfile.TemporaryDirectory() as tmp:
            dig = Dig.create(Path(tmp), "x.com")
            dig.save_captures([fresh_capture()])
            self.assertFalse(run_scan(dig)["clean"])  # nothing scanned is not "clean"
            with self.assertRaises(SystemExit):
                main(["scan", "--dig", str(dig.root)])

    def test_scan_is_recorded_in_the_event_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            dig = site(tmp, {"index.html": "<p>hello</p>"})
            run_scan(dig)
            event = load_events(dig)[-1]
            self.assertEqual((event["event_type"], event["outcome"]["clean"]), ("scan", True))


if __name__ == "__main__":
    unittest.main()
