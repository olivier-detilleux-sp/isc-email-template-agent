"""Tenant-independent placeholders used by styled template masters."""
from __future__ import annotations

from branding import Branding

NAVIGATION_COLOR = "__ISC_BRAND_NAVIGATION_COLOR__"
ACTION_COLOR = "__ISC_BRAND_ACTION_COLOR__"
LINK_COLOR = "__ISC_BRAND_LINK_COLOR__"


def normalize_brand_tokens(value: str) -> str:
    """Repair harmless LLM punctuation changes around known brand tokens."""
    output = value
    for name, token in (
        ("NAVIGATION_COLOR", NAVIGATION_COLOR),
        ("ACTION_COLOR", ACTION_COLOR),
        ("LINK_COLOR", LINK_COLOR),
    ):
        # Models occasionally omit the final double underscore before a CSS
        # semicolon. Accept only the three known names; never a generic token.
        output = output.replace(f"__ISC_BRAND_{name}", token)
        output = output.replace(token + "__", token)
    return output


def tokenize_branding(value: str, branding: Branding) -> str:
    """Replace current tenant colors with reusable placeholders."""
    replacements = {
        branding.navigation_color.lower(): NAVIGATION_COLOR,
        branding.action_color.lower(): ACTION_COLOR,
        branding.link_color.lower(): LINK_COLOR,
    }
    output = value
    # Action/link often have the same value. Prefer ACTION for visible accents.
    if branding.action_color.lower() == branding.link_color.lower():
        replacements.pop(branding.link_color.lower(), None)
        replacements[branding.action_color.lower()] = ACTION_COLOR
    if branding.navigation_color.lower() == branding.link_color.lower():
        replacements[branding.navigation_color.lower()] = NAVIGATION_COLOR
    for old, token in replacements.items():
        output = output.replace(old, token).replace(old.upper(), token)
    return output


def apply_branding_tokens(value: str, branding: Branding) -> str:
    """Render placeholders using the live tenant branding."""
    return (
        normalize_brand_tokens(value)
        .replace(NAVIGATION_COLOR, branding.navigation_color)
        .replace(ACTION_COLOR, branding.action_color)
        .replace(LINK_COLOR, branding.link_color)
    )
