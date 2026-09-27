"""Thin, verified wrapper around the authenticated SailPoint CLI."""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SailError(RuntimeError):
    pass


@dataclass(frozen=True)
class SailEnvironment:
    name: str
    base_url: str
    tenant_url: str


def _json_from_output(raw: str) -> Any:
    decoder = json.JSONDecoder()
    values: list[tuple[int, Any]] = []
    for index, char in enumerate(raw):
        if char not in "[{":
            continue
        try:
            value, end = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            continue
        values.append((end, value))
    if not values:
        raise SailError(f"SailPoint response contained no JSON: {raw[-600:]}")
    # The top-level API payload is normally the longest valid JSON value.
    return max(values, key=lambda item: item[0])[1]


def _run(command: list[str]) -> tuple[str, int]:
    process = subprocess.run(
        command,
        input="\n",
        text=True,
        capture_output=True,
    )
    raw = (process.stderr or "") + "\n" + (process.stdout or "")
    return raw, process.returncode


def active_environment(env: str | None = None) -> SailEnvironment:
    command = ["sail", "environment", "show"]
    if env:
        command += ["--env", env]
    raw, code = _run(command)
    if code:
        raise SailError(raw[-800:])
    value = _json_from_output(raw)
    match = re.search(r"\benv=([^\s]+)", raw)
    name = env or (match.group(1) if match else "")
    base_url = value.get("baseurl") or ""
    tenant_url = value.get("tenanturl") or ""
    if not name and base_url:
        name = base_url.removeprefix("https://").split(".", 1)[0]
    if not name or not base_url:
        raise SailError("Could not identify the active Sail CLI environment.")
    return SailEnvironment(name=name, base_url=base_url, tenant_url=tenant_url)


def api_get(path: str, *, env: str, query: list[str] | None = None) -> Any:
    command = ["sail", "api", "get", path, "--env", env, "-p"]
    for item in query or []:
        command += ["-q", item]
    raw, code = _run(command)
    if code or "Status: 4" in raw or "Status: 5" in raw:
        raise SailError(f"GET {path} failed:\n{raw[-1000:]}")
    return _json_from_output(raw)


def pull_all(env: SailEnvironment, output_root: Path) -> tuple[list[dict], list[dict], list[dict]]:
    branding = api_get("/brandings/v1", env=env.name)
    if not isinstance(branding, list):
        raise SailError("/brandings/v1 did not return a list.")

    defaults: list[dict] = []
    offset = 0
    limit = 250
    while True:
        page = api_get(
            "/notification-template-defaults/v1",
            env=env.name,
            query=[
                f"limit={limit}",
                f"offset={offset}",
                'filters=medium eq "EMAIL"',
            ],
        )
        defaults.extend(page)
        if len(page) < limit:
            break
        offset += limit
        time.sleep(0.15)

    customs: list[dict] = []
    offset = 0
    while True:
        page = api_get(
            "/notification-templates/v1",
            env=env.name,
            query=[
                f"limit={limit}",
                f"offset={offset}",
            ],
        )
        # The documented `medium eq "EMAIL"` filter currently drops valid
        # EMAIL customs on some tenants (including beta-22479). Fetch all and
        # filter locally until the service behavior is corrected.
        customs.extend(item for item in page if item.get("medium") == "EMAIL")
        if len(page) < limit:
            break
        offset += limit
        time.sleep(0.15)

    root = output_root / env.name
    root.mkdir(parents=True, exist_ok=True)
    for filename, value in (
        ("branding.json", branding),
        ("email-defaults.json", defaults),
        ("email-custom.json", customs),
    ):
        (root / filename).write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return branding, defaults, customs


COMPARISON_FIELDS = (
    "key",
    "medium",
    "locale",
    "subject",
    "body",
    "from",
    "replyTo",
)


def changed_payloads(payloads: list[dict], customs: list[dict]) -> list[dict]:
    current = {
        (item.get("key"), item.get("medium"), item.get("locale")): item
        for item in customs
    }
    changed: list[dict] = []
    for payload in payloads:
        identity = (
            payload.get("key"),
            payload.get("medium"),
            payload.get("locale"),
        )
        existing = current.get(identity)
        if not existing or any(
            (payload.get(field) or "") != (existing.get(field) or "")
            for field in COMPARISON_FIELDS
        ):
            changed.append(payload)
    return changed


def api_post_template(payload: dict, *, env: str) -> dict:
    command = [
        "sail",
        "api",
        "post",
        "/notification-templates/v1",
        "--env",
        env,
        "-b",
        json.dumps(payload, ensure_ascii=False),
        "-p",
    ]
    raw, code = _run(command)
    status = re.search(r"Status:\s+(\d{3})", raw)
    status_code = int(status.group(1)) if status else 0
    if code or status_code not in {200, 201}:
        raise SailError(
            f"POST {payload.get('key')} failed "
            f"(rc={code}, status={status_code}):\n{raw[-1000:]}"
        )
    value = _json_from_output(raw)
    if not isinstance(value, dict) or value.get("key") != payload.get("key"):
        raise SailError(
            f"POST {payload.get('key')}: unexpected response with no matching key."
        )
    return value
