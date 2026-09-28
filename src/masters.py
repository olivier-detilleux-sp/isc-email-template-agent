"""Build and translate reusable, tenant-independent styled templates."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Iterable

from brand_tokens import (
    ACTION_COLOR,
    NAVIGATION_COLOR,
    apply_branding_tokens,
    normalize_brand_tokens,
    tokenize_branding,
)
from branding import Branding
from llm_translate import (
    TOKEN_RE,
    LlmTranslationError,
    _directive_kind,
    _drop_unmatched_closings,
    _protect,
    _validate_directive_tree,
    translate_with_cursor,
)
from shell import apply_branding_to_existing, strip_existing_shell, wrap_body
from style_guide import (
    REFERENCE_KEY,
    detect_language,
    normalize_curated_body,
    style_notes,
)


def _digest(*values: str) -> str:
    return hashlib.sha256("\0".join(values).encode()).hexdigest()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def build_base_masters(
    *,
    defaults: list[dict],
    customs: list[dict],
    curated_keys: set[str],
    branding: Branding,
    target_language: str,
    model: str,
    output_dir: Path,
    cache_dir: Path,
    only: set[str] | None = None,
    force: bool = False,
) -> list[Path]:
    """Create styled masters from defaults; preserve explicitly curated content.

    Curated keys come from the tenant's customized template, in whatever
    language it is written, and are translated literally into the target
    language so the layout survives. Other keys are redesigned from the stock
    default, guided by the current master and the house style.
    """
    custom_by_key = {item["key"]: item for item in customs}
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    failures: list[str] = []

    selected = [d for d in defaults if not only or d["key"] in only]
    # Curated masters first: they provide the style reference for the others.
    selected.sort(key=lambda d: d["key"] not in curated_keys)
    for index, default in enumerate(selected, 1):
        key = default["key"]
        path = output_dir / f"{key}.json"
        source_digest = _digest(
            default.get("subject") or "", default.get("body") or ""
        )
        current = load_json(path) if path.exists() else None
        if current and not force:
            if (
                current.get("sourceDigest") == source_digest
                and current.get("language") == target_language
            ):
                print(f"[{index}/{len(selected)}] master {key} (cached)", flush=True)
                written.append(path)
                continue
        if (
            current
            and key in curated_keys
            and key not in custom_by_key
            and current.get("origin") == "curated"
        ):
            print(
                f"[{index}/{len(selected)}] master {key} "
                "(curated, no tenant custom: kept)",
                flush=True,
            )
            written.append(path)
            continue
        print(f"[{index}/{len(selected)}] master {key}", flush=True)

        try:
            if key in curated_keys and key in custom_by_key:
                custom = custom_by_key[key]
                subject = custom.get("subject") or default.get("subject") or ""
                branded = apply_branding_to_existing(custom.get("body") or "", branding)
                inner_body = strip_existing_shell(branded)
                inner_body = normalize_curated_body(
                    tokenize_branding(inner_body, branding)
                )
                subject = normalize_curated_body(subject)
                custom_language = detect_language(inner_body)
                if custom_language != target_language:
                    subject, inner_body = translate_with_cursor(
                        key=key,
                        name=default.get("name") or key,
                        description=default.get("description"),
                        subject=subject,
                        body=inner_body,
                        model=model,
                        cache_dir=cache_dir,
                        style="literal",
                        source_language=custom_language,
                        target_language=target_language,
                    )
                origin = "curated"
            else:
                reference_path = output_dir / f"{REFERENCE_KEY}.json"
                reference = (
                    load_json(reference_path).get("body")
                    if reference_path.exists() and key != REFERENCE_KEY
                    else None
                )
                subject, inner_body = translate_with_cursor(
                    key=key,
                    name=default.get("name") or key,
                    description=default.get("description"),
                    subject=default.get("subject") or "",
                    body=default.get("body") or "",
                    model=model,
                    cache_dir=cache_dir,
                    style="redesign",
                    source_language=default.get("locale") or "en",
                    target_language=target_language,
                    palette={
                        "navigation": NAVIGATION_COLOR,
                        "action": ACTION_COLOR,
                    },
                    style_notes=style_notes(
                        key,
                        reference_body=reference,
                        current_body=(current or {}).get("body"),
                    ),
                )
                origin = "cursor-redesign"
        except LlmTranslationError as exc:
            message = f"{key}: {exc}"
            failures.append(message)
            print(f"  FAILED {message}", flush=True)
            continue

        master = {
            "key": key,
            "name": default.get("name") or key,
            "description": default.get("description"),
            "medium": "EMAIL",
            # ISC's available default template locale identifies the override.
            "tenantLocale": default.get("locale") or "en",
            "language": target_language,
            "from": default.get("from") or "$__global.emailFromAddress",
            "replyTo": default.get("replyTo") or "$__global.emailFromAddress",
            "subject": subject,
            "body": normalize_brand_tokens(inner_body),
            "origin": origin,
            "sourceDigest": source_digest,
        }
        path.write_text(
            json.dumps(master, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        written.append(path)
    if failures:
        failure_path = output_dir / "_generation-failures.txt"
        failure_path.write_text("\n".join(failures) + "\n", encoding="utf-8")
        raise LlmTranslationError(
            f"{len(failures)} template(s) failed; details: {failure_path}"
        )
    failure_path = output_dir / "_generation-failures.txt"
    failure_path.unlink(missing_ok=True)
    return written


def translate_masters(
    *,
    source_dir: Path,
    target_dir: Path,
    source_language: str,
    target_language: str,
    model: str,
    cache_dir: Path,
    only: set[str] | None = None,
) -> list[Path]:
    """Translate styled masters without changing their HTML layout."""
    target_dir.mkdir(parents=True, exist_ok=True)
    sources = sorted(source_dir.glob("*.json"))
    if only is not None:
        sources = [path for path in sources if path.stem in only]
    written: list[Path] = []
    for index, path in enumerate(sources, 1):
        master = load_json(path)
        key = master["key"]
        print(f"[{index}/{len(sources)}] translate master {key}", flush=True)
        subject, body = translate_with_cursor(
            key=key,
            name=master.get("name") or key,
            description=master.get("description"),
            subject=master["subject"],
            body=master["body"],
            model=model,
            cache_dir=cache_dir,
            style="literal",
            source_language=source_language,
            target_language=target_language,
        )
        translated = dict(master)
        translated.update(
            {
                "language": target_language,
                "subject": subject,
                "body": body,
                "origin": f"cursor-translation-from-{source_language}",
            }
        )
        output = target_dir / path.name
        output.write_text(
            json.dumps(translated, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        written.append(output)
    return written


def assemble_payloads(
    *,
    master_paths: Iterable[Path],
    branding: Branding,
    output_dir: Path,
) -> list[dict]:
    """Apply live branding and create API-ready TemplateDto payloads."""
    output_dir.mkdir(parents=True, exist_ok=True)
    payloads: list[dict] = []
    for path in master_paths:
        master = load_json(path)
        inner = apply_branding_tokens(master["body"], branding)
        body = wrap_body(
            branding,
            inner,
            language=master.get("language") or "en",
        )
        payload = {
            "key": master["key"],
            "name": master["name"],
            "medium": "EMAIL",
            "locale": master.get("tenantLocale") or "en",
            "subject": master["subject"],
            "body": body,
            "from": master.get("from") or "$__global.emailFromAddress",
            "replyTo": master.get("replyTo") or "$__global.emailFromAddress",
            "description": master.get("description"),
            "header": None,
            "footer": None,
            "slackTemplate": None,
            "teamsTemplate": None,
        }
        output = output_dir / path.name
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        payloads.append(payload)
    return payloads


def _velocity_signature(value: str, prefix: str):
    protected, mapping = _protect(value or "", prefix)
    protected, mapping, repairs = _drop_unmatched_closings(protected, mapping)
    ordered_tokens = TOKEN_RE.findall(protected)
    directives = [
        _directive_kind(mapping[token])
        for token in ordered_tokens
        if _directive_kind(mapping[token])
    ]
    references = {
        original
        for original in mapping.values()
        if original.startswith("$") or original.startswith("http")
    }
    tree_errors = _validate_directive_tree(ordered_tokens, mapping)
    return directives, references, repairs, tree_errors


def filter_catalog_masters(
    master_paths: Iterable[Path],
    defaults: list[dict],
) -> tuple[list[Path], list[str]]:
    """Keep masters that exist in the tenant default catalog; skip the rest."""
    catalog = {item["key"] for item in defaults}
    kept: list[Path] = []
    skipped: list[str] = []
    for path in master_paths:
        key = path.stem
        if key in catalog:
            kept.append(path)
        else:
            skipped.append(key)
    return kept, skipped


def validate_masters(master_paths: Iterable[Path], defaults: list[dict]) -> list[str]:
    """Fail closed on Velocity, URL and link regressions before branding/upload."""
    errors: list[str] = []
    defaults_by_key = {item["key"]: item for item in defaults}
    for path in master_paths:
        master = load_json(path)
        key = master.get("key") or path.stem
        default = defaults_by_key.get(key)
        if not default:
            continue
        if not (master.get("subject") or "").strip():
            errors.append(f"{key}: empty master subject")
        if not (master.get("body") or "").strip():
            errors.append(f"{key}: empty master body")

        body = master.get("body") or ""
        if re.search(r"<(?:html|head|body|style|script|button)\b", body, re.I):
            errors.append(f"{key}: forbidden HTML tag in the master")
        if "@@ISC_PROTECTED_" in body or "@@ISC_PROTECTED_" in (
            master.get("subject") or ""
        ):
            errors.append(f"{key}: protected token was not restored")

        # Curated templates are deliberately allowed to differ semantically
        # from the stock defaults, but their own Velocity tree must be valid.
        for field in ("subject", "body"):
            try:
                actual = _velocity_signature(
                    master.get(field) or "", f"MASTER_{field.upper()}"
                )
            except Exception as exc:
                errors.append(f"{key}/{field}: invalid Velocity: {exc}")
                continue
            if actual[2]:
                errors.append(
                    f"{key}/{field}: orphaned Velocity closing tag in the master"
                )
            if actual[3]:
                errors.extend(f"{key}/{field}: {item}" for item in actual[3])

            if master.get("origin") == "curated":
                continue
            try:
                expected = _velocity_signature(
                    default.get(field) or "", f"SOURCE_{field.upper()}"
                )
            except Exception as exc:
                errors.append(f"{key}/{field}: invalid source Velocity: {exc}")
                continue
            if actual[0] != expected[0]:
                errors.append(
                    f"{key}/{field}: Velocity directive sequence changed"
                )
            missing_references = expected[1] - actual[1]
            new_references = actual[1] - expected[1]
            if missing_references:
                errors.append(
                    f"{key}/{field}: lost variables/URLs: "
                    f"{sorted(missing_references)[:8]}"
                )
            if new_references:
                errors.append(
                    f"{key}/{field}: invented variables/URLs: "
                    f"{sorted(new_references)[:8]}"
                )

        source_links = len(re.findall(r"<a\b[^>]*\bhref=", default.get("body") or "", re.I))
        master_links = len(re.findall(r"<a\b[^>]*\bhref=", body, re.I))
        if master.get("origin") != "curated" and master_links < source_links:
            errors.append(
                f"{key}: lost link(s), source={source_links}, master={master_links}"
            )
    return errors


def validate_payloads(
    payloads: list[dict],
    *,
    branding: Branding,
    protected_prefix: str = "@@ISC_PROTECTED_",
) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for payload in payloads:
        key = payload.get("key", "<missing>")
        if key in seen:
            errors.append(f"{key}: duplicate key")
        seen.add(key)
        if not (payload.get("subject") or "").strip():
            errors.append(f"{key}: empty subject")
        if not (payload.get("body") or "").strip():
            errors.append(f"{key}: empty body")
        body = payload.get("body") or ""
        subject = payload.get("subject") or ""
        if protected_prefix in body or protected_prefix in subject:
            errors.append(f"{key}: unrestored protected token")
        if "__ISC_BRAND_" in body:
            errors.append(f"{key}: unresolved branding token")
        if branding.logo_url not in body:
            errors.append(f"{key}: tenant logo missing")
        if branding.navigation_color not in body:
            errors.append(f"{key}: navigation color missing")
        if branding.action_color not in body:
            errors.append(f"{key}: action color missing")
    return errors
