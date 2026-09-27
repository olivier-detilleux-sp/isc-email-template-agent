"""Tenant branding helpers for email template generation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _hex(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    value = value.strip().lstrip("#")
    if len(value) not in (3, 6):
        return fallback
    return f"#{value.lower()}"


@dataclass(frozen=True)
class Branding:
    name: str
    product_name: str
    navigation_color: str
    action_color: str
    link_color: str
    logo_url: str
    email_from_address: str | None = None

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> "Branding":
        logo = (item.get("standardLogoURL") or "").strip()
        if not logo:
            raise ValueError(
                "Branding item has empty standardLogoURL; configure the tenant logo first."
            )
        return cls(
            name=item.get("name") or "default",
            product_name=item.get("productName") or "Identity Security Cloud",
            navigation_color=_hex(item.get("navigationColor"), "#01038f"),
            action_color=_hex(item.get("actionButtonColor"), "#f5111d"),
            link_color=_hex(item.get("activeLinkColor"), "#f5111d"),
            logo_url=logo,
            email_from_address=item.get("emailFromAddress"),
        )

    def title_color(self, *, negative: bool = False) -> str:
        return self.action_color if negative else self.navigation_color
