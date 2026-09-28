"""House style shared by access-request and approval email masters."""
from __future__ import annotations

import re

from brand_tokens import ACTION_COLOR, NAVIGATION_COLOR

ACCESS_APPROVAL_PREFIXES = ("access_request", "access_revoke", "approval_")
ACCESS_APPROVAL_KEYS = {
    "access_profile_owner_approval_notification",
    "jit_access_request_assignment",
    "pending_access_request_cancelled",
    "sod_conflicts_on_access_request_notification",
}

# Written in the house style below; used as the example given to the model.
REFERENCE_KEY = "access_request_decision_email_for_requested-for_identity"

ACCESS_APPROVAL_GUIDE = f"""\
House style for access-request and approval emails. It takes precedence over
any other layout advice. Copy the markup exactly; keep literal colors literal.
- Title: <h2 style="color:{NAVIGATION_COLOR};margin:0 0 20px 0">. When the whole
  email announces a rejection, a cancellation, or a failure, use {ACTION_COLOR}.
- Paragraphs: <p style="margin:0 0 20px 0;font-size:16px;color:#333333;line-height:1.5">.
  Names of the requested object and of identities go in <strong>.
- Information box (context or dimension attributes, request details,
  reassignment reason, items): <div style="margin-bottom:20px;padding:15px;background-color:#f9f9f9;border-left:4px solid {NAVIGATION_COLOR}">.
  Lists inside it: <ul style="margin: 0; padding-left: 20px;"> and
  <li style="font-size: 15px; color: #555; margin-bottom: 5px;">.
- Approved or successful outcome: <div style="padding:15px;background-color:#f0f7f0;border-left:4px solid #2e7d32">
  with the outcome word in <strong style="color:#2e7d32">. Always this green.
- Denied, rejected, failed, or cancelled outcome: <div style="padding:15px;background-color:#fef5f5;border-left:4px solid {ACTION_COLOR}">
  with the outcome word in <strong style="color:{ACTION_COLOR}">.
- Just-In-Time activation notice: <div style="margin-top:25px;padding:15px;background-color:#fff8e1;border-left:4px solid #ffab00">
  with the text in <strong>. Always this yellow.
- Comment or justification written by a person: <div style="margin-top:20px;padding:15px;background-color:#f9f9f9">
  (no border); the comment itself in <strong>"…"</strong> after a <br />.
- Start and end dates: <p style="margin-top:20px;font-size:14px;color:#666">.
- A link that sends the reader to review or approve becomes a button:
  <table style="margin-top: 30px;"><tbody><tr><td><a style="background-color:{NAVIGATION_COLOR};color:#ffffff;padding:12px 25px;text-decoration:none;border-radius:5px;font-weight:bold" href="…">…</a></td></tr></tbody></table>.
  Never use <button>.
- Closing: <p style="margin-top:30px;font-size:16px;color:#333">, then the
  regards line, <br />, and the team signature with the product-name token.
- Use a box only for information the source already contains. Never add an
  empty box, a new fact, or a new link.
"""

_FRENCH_HINT = re.compile(r"\b(?:Bonjour|Cordialement|L'équipe|Veuillez)\b")


def is_access_or_approval(key: str) -> bool:
    return key.startswith(ACCESS_APPROVAL_PREFIXES) or key in ACCESS_APPROVAL_KEYS


def detect_language(body: str) -> str:
    return "fr" if _FRENCH_HINT.search(body or "") else "en"


def normalize_curated_body(body: str) -> str:
    """Pin the fixed outcome colors and close directives the way ISC defaults do."""
    out = body
    out = re.sub(r"color:\s*green\b", "color:#2e7d32", out, flags=re.I)
    out = re.sub(r"color:\s*red\b", f"color:{ACTION_COLOR}", out, flags=re.I)
    out = out.replace("MySailPoint > Launchpad > ", "MySailPoint &gt; Launchpad &gt; ")
    out = re.sub(r"#\{end\}(?!\w)", "#end", out)
    return out


def style_notes(
    key: str,
    *,
    reference_body: str | None,
    current_body: str | None,
) -> str:
    """Extra prompt text that keeps a regenerated master in the existing style."""
    parts: list[str] = []
    if is_access_or_approval(key):
        parts.append(ACCESS_APPROVAL_GUIDE)
        if reference_body:
            parts.append(
                "Example written in this house style (match its markup, spacing, "
                "and colors; do not copy its wording or variables):\n"
                + reference_body
            )
    if current_body:
        parts.append(
            "Current version of this template. Keep its layout and wording "
            "wherever they still fit the source"
            + (" and the house style" if is_access_or_approval(key) else "")
            + ":\n"
            + current_body
        )
    return "\n\n".join(parts)
