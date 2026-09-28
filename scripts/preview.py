#!/usr/bin/env python3
"""Render a template payload to static HTML for visual review.

Velocity is resolved with a naive sample context; this is a preview helper,
not a Velocity engine.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SAMPLE = {
    "user.name": "Marie Dupont",
    "requesterName": "Paul Martin",
    "requestedForIdentityName": "Claire Bernard",
    "requestedRoleName": "Gestionnaire Finance",
    "accessProfileName": "Finance - Lecture SAP",
    "startDate": "1 octobre 2026",
    "removeDate": "31 décembre 2026",
    "PRODUCT_NAME": "DAR x SailPoint",
    "emailRecipientName": "Marie Dupont",
    "cancelerName": "Paul Martin",
    "cancelComment": "Demande en double.",
    "__recipient.name": "Marie Dupont",
    "__global.productName": "DAR x SailPoint",
}

# Conditions considered true in the preview.
TRUE_CONDITIONS = ("startDate", "removeDate", "cancelComment", "approved")


def render(body: str) -> str:
    out = body

    def keep_if(match: re.Match[str]) -> str:
        condition = match.group(1)
        return "" if any(flag in condition for flag in TRUE_CONDITIONS) else ""

    # Resolve #if(...) blocks: keep content when the condition looks "true".
    def resolve_blocks(text: str) -> str:
        pattern = re.compile(r"#if\s*\(([^)]*)\)(.*?)(?:#\{end\}|#end\b)", re.S)
        while True:
            match = pattern.search(text)
            if not match:
                return text
            condition, content = match.group(1), match.group(2)
            keep = any(flag in condition for flag in TRUE_CONDITIONS)
            if "!=" in condition and "requesterName" in condition:
                keep = True
            if "#{else}" in content:
                yes, no = content.split("#{else}", 1)
                content = yes if keep else no
                keep = True
            text = text[: match.start()] + (content if keep else "") + text[match.end() :]

    out = resolve_blocks(out)
    out = re.sub(r"#set\s*\([^)]*\)", "", out)

    for name, value in SAMPLE.items():
        out = out.replace("${%s}" % name, value).replace("$%s" % name, value)
    out = re.sub(r"#\{else\}|#\{end\}|#else(?!if)\b|#end\b", "", out)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("payload", help="Path to a generated payload JSON")
    parser.add_argument("--out", required=True, help="Output HTML path")
    args = parser.parse_args()

    payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
    subject = render(payload.get("subject", ""))
    body = render(payload.get("body", ""))

    html = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>{payload.get('key', '')}</title></head>"
        '<body style="margin:0;padding:24px;background:#f4f4f4;'
        'font-family:Arial,Helvetica,sans-serif">'
        '<div style="max-width:700px;margin:0 auto">'
        '<p style="font-size:13px;color:#666;margin:0 0 4px 0">Objet :</p>'
        f'<p style="font-size:17px;color:#111;margin:0 0 16px 0"><strong>{subject}</strong></p>'
        f"{body}"
        "</div></body></html>"
    )
    Path(args.out).write_text(html, encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
