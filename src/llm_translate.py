"""High-quality, Velocity-safe translation through the Cursor SDK."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from cursor_sdk import (
    Agent,
    AgentOptions,
    CursorAgentError,
    LocalAgentOptions,
    LocalAgentStoreConfig,
)


TOKEN_RE = re.compile(r"@@ISC_PROTECTED_[A-Z_]+_\d{4}@@")
VELOCITY_RE = re.compile(
    r"#\{(?:else|end|stop|break)\}|#(?:else|end|stop|break)\b|"
    r"#(?:set|if|elseif|foreach|macro|parse|include|evaluate)\b|"
    r"\$!?\{[^}]+\}|\$!?[A-Za-z_][A-Za-z0-9_]*"
    r"(?:\.[A-Za-z_][A-Za-z0-9_]*|\([^)]*\))*",
    re.I,
)
URL_RE = re.compile(r"https?://[^\s\"'<>]+")
HTML_TAG_RE = re.compile(r"</?([A-Za-z][A-Za-z0-9]*)\b")


GLOSSARY = """\
- Terminology when the target language is French: access request → demande d'accès ;
  entitlement → droit ; access profile → profil d'accès ; role → rôle ;
  identity → identité ; source → source ; account → compte ;
  certification → certification ; reviewer → réviseur ; owner → propriétaire ;
  approval → approbation ; provisioning → provisionnement.
- Keep product names and technical acronyms unchanged (ISC, API, MFA, and so on).
- Use "vous", in a professional and inclusive tone, when writing French.
- Text inside Velocity directives is protected or technical: do not rewrite it."""

LITERAL_INSTRUCTIONS = f"""\
You are a SailPoint Identity Security Cloud expert and a professional writer.
Translate an ISC notification template from English into natural, professional,
idiomatic French.

Mandatory constraints:
- Reply with a single valid JSON object: {{"subject":"...","body":"..."}}.
- Keep every @@ISC_PROTECTED_*@@ token exactly once, unchanged, in the right place.
- Do not translate, edit, delete, or duplicate any protected token.
- Preserve the HTML structure, attributes, styles, and links.
- Do not add any functional information that is absent from the original.
{GLOSSARY}
"""

REDESIGN_INSTRUCTIONS = f"""\
You are a SailPoint Identity Security Cloud expert, a professional writer, and
an HTML email integrator.

You receive an English ISC notification template whose Velocity expressions and
URLs have been replaced by @@ISC_PROTECTED_*@@ tokens. Produce a rewritten and
reformatted French version.

Mandatory constraints:
- Reply with a single valid JSON object: {{"subject":"...","body":"..."}}.
- `body` contains only the inner email content: no logo, no footer, no wrapping
  <table>, and no <html>/<head>/<body> tags. Branding is added around your reply.
- Reuse every @@ISC_PROTECTED_*@@ token that stands for a variable at least once.
  Never invent a new token.
- Never write in plain text a value that already exists as a token (product name,
  user name, role name, and so on). Reuse the matching token. The signature must
  use the product-name token.
- Every token must sit inside a coherent sentence or HTML element. No token may
  be left isolated, especially at the end of the message.
- Keep every directive token (#if, #foreach, #end, #else, and so on), correctly
  nested: each opening directive keeps its closing directive, and conditional
  content stays inside its block.
- The functional meaning must stay identical. Do not add information, actions,
  or links that are not in the original.

Expected layout (compatible with email clients):
- A short, explicit <h2> title at the top.
- Short <p> paragraphs, inline style `margin:0 0 16px 0;font-size:16px;color:#333333`.
- Key facts (start/end dates, reason, granted items) in a <div> with a light
  background and a colored left border, or in a readable <ul>/<li> list.
- A closing line: « Cordialement,<br />L'équipe … ».
- Inline styles only. No CSS classes and no <style> tag.
{GLOSSARY}
"""

LANGUAGE_LABELS = {
    "fr": "French",
    "en": "English",
    "de": "German",
    "es": "Spanish",
    "it": "Italian",
    "nl": "Dutch",
    "pt": "Portuguese",
}


def _instructions(
    *,
    style: str,
    source_language: str,
    target_language: str,
    palette: dict[str, str] | None,
) -> str:
    language = LANGUAGE_LABELS.get(target_language, target_language)
    source = LANGUAGE_LABELS.get(source_language, source_language)
    glossary = GLOSSARY if target_language == "fr" else (
        "- Use standard SailPoint Identity Security Cloud terminology "
        f"in the target language ({language}).\n"
        "- Keep product names and technical acronyms unchanged (ISC, API, MFA, and so on)."
    )
    if style == "literal":
        return f"""\
You are a SailPoint Identity Security Cloud expert and a professional writer.
Translate the template subject and body from {source} into {language}.

Mandatory constraints:
- Reply with a single valid JSON object: {{"subject":"...","body":"..."}}.
- Translate both the subject and the body, not only the body.
- Keep every @@ISC_PROTECTED_*@@ token exactly once, unchanged, in the right place.
- Do not translate, edit, delete, or duplicate any protected token.
- Preserve the HTML structure, attributes, styles, and links.
- Do not add any functional information that is absent from the original.
{glossary}
"""

    colors = palette or {}
    return f"""\
You are a SailPoint Identity Security Cloud expert, a professional writer, and
an HTML email integrator.

Rewrite the template in natural, professional {language} and improve its layout.
Translate both the subject and the body.

Mandatory constraints:
- Reply with a single valid JSON object: {{"subject":"...","body":"..."}}.
- `body` contains only the inner email content: no logo, no footer, no wrapping
  <table>, and no <html>/<head>/<body> tags.
- Reuse every @@ISC_PROTECTED_*@@ token that stands for a variable at least once.
  Never invent a new token.
- Never write in plain text a value that already exists as a token.
- No token may be left isolated, especially at the end of the message.
- Keep every #if/#foreach/#else/#end directive correctly nested, with the same
  functional meaning.
- Do not add information, actions, or links that are absent from the original.

Layout:
- A short, explicit <h2> title at the top.
- Short paragraphs with inline styles.
- Key facts in callout boxes or readable lists.
- A closing line that uses the product-name token.
- Primary color: {colors.get("navigation", "__ISC_BRAND_NAVIGATION_COLOR__")}
- Accent color: {colors.get("action", "__ISC_BRAND_ACTION_COLOR__")}
- If a color above is an `__ISC_BRAND_*__` token, copy it exactly, including
  both leading and trailing underscores.
- Inline styles only.
{glossary}
"""


class LlmTranslationError(RuntimeError):
    """Raised when an LLM response cannot safely replace the source template."""


KEYCHAIN_SERVICE = "cursor-sdk"
KEYCHAIN_ACCOUNT = "CURSOR_API_KEY"


def _read_env_file(path: Path) -> str | None:
    if not path.exists():
        return None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        name, separator, value = line.partition("=")
        if not separator or name.strip() != "CURSOR_API_KEY":
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        return value or None
    return None


def _read_macos_keychain() -> str | None:
    if sys.platform != "darwin":
        return None
    try:
        completed = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                KEYCHAIN_SERVICE,
                "-a",
                KEYCHAIN_ACCOUNT,
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def resolve_api_key() -> str:
    """Find the Cursor key without ever requiring it on the command line."""
    sources = (
        os.environ.get("CURSOR_API_KEY"),
        _read_env_file(Path(__file__).resolve().parents[1] / ".env"),
        _read_macos_keychain(),
    )
    for candidate in sources:
        api_key = (candidate or "").strip()
        if api_key:
            os.environ["CURSOR_API_KEY"] = api_key
            return api_key
    raise LlmTranslationError(
        "No Cursor API key found. Create a User API key at "
        "https://cursor.com/dashboard/api, then store it with one of these "
        "methods (do not paste it into a chat):\n"
        "  1. export CURSOR_API_KEY='...' in your shell\n"
        "  2. a local .env file (ignored by Git) containing CURSOR_API_KEY=...\n"
        "  3. macOS keychain: security add-generic-password "
        f"-s {KEYCHAIN_SERVICE} -a {KEYCHAIN_ACCOUNT} -w"
    )


def _protect(text: str, prefix: str) -> tuple[str, dict[str, str]]:
    mapping: dict[str, str] = {}
    index = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal index
        token = f"@@ISC_PROTECTED_{prefix}_{index:04d}@@"
        mapping[token] = match.group(0)
        index += 1
        return token

    protected = _protect_directives(text, replace)
    protected = VELOCITY_RE.sub(replace, protected)
    protected = URL_RE.sub(replace, protected)
    return protected, mapping


def _protect_directives(text: str, replace) -> str:
    """Protect complete Velocity directives, including balanced conditions."""
    output: list[str] = []
    index = 0
    while index < len(text):
        if text[index] != "#":
            output.append(text[index])
            index += 1
            continue

        tail = text[index:]
        simple = re.match(
            r"#\{(?:else|end|stop|break)\}|"
            r"#(?:else(?!if)\b|end\b|stop\b|break\b)",
            tail,
            re.I,
        )
        if simple:
            output.append(replace(simple))
            index += simple.end()
            continue

        directive = re.match(
            r"#(?:set|if|elseif|foreach|macro|parse|include|evaluate)\b",
            tail,
            re.I,
        )
        if not directive:
            output.append(text[index])
            index += 1
            continue

        end = index + directive.end()
        while end < len(text) and text[end].isspace():
            end += 1
        if end < len(text) and text[end] == "(":
            depth = 0
            quote: str | None = None
            escaped = False
            while end < len(text):
                char = text[end]
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif quote:
                    if char == quote:
                        quote = None
                elif char in {"'", '"'}:
                    quote = char
                elif char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth == 0:
                        end += 1
                        break
                end += 1
        match = re.match(r"[\s\S]+", text[index:end])
        if match:
            output.append(replace(match))
        index = end
    return "".join(output)


def _restore(text: str, mapping: dict[str, str]) -> str:
    out = text
    for token, original in mapping.items():
        out = out.replace(token, original)
    return _canonicalize_simple_directives(out)


def _canonicalize_simple_directives(value: str) -> str:
    """Brace #else/#end only when the next character would be absorbed.

    `#endchanged` is not a directive, so it must become `#{end}changed`.
    `#end`, `#end.`, `#end</p>` and `#end"` stay unbraced: that is the form
    used by the stock ISC templates, and a braced closer inside a #set string
    is printed literally.
    """
    value = re.sub(r"#else(?!if|\})(?=\w)", "#{else}", value, flags=re.I)
    value = re.sub(r"#end(?!\})(?=\w)", "#{end}", value, flags=re.I)
    return value


def _drop_unmatched_closings(
    protected: str, mapping: dict[str, str]
) -> tuple[str, dict[str, str], list[str]]:
    """Drop unmatched source #end tokens while preserving valid block structure."""
    depth = 0
    dropped: list[str] = []
    output = protected
    clean_mapping = dict(mapping)
    for token in TOKEN_RE.findall(protected):
        original = mapping.get(token, "")
        kind = _directive_kind(original)
        if kind in {"if", "foreach", "macro"}:
            depth += 1
        elif kind in {"else", "elseif"}:
            # An unmatched else is unsafe to repair automatically.
            if depth == 0:
                raise LlmTranslationError(
                    f"Source directive has no open block: {mapping[token]}"
                )
        elif kind == "end":
            if depth == 0:
                output = output.replace(token, "", 1)
                clean_mapping.pop(token, None)
                dropped.append(mapping[token])
            else:
                depth -= 1
    if depth:
        raise LlmTranslationError(
            f"Incomplete source template: {depth} Velocity block(s) left open."
        )
    return output, clean_mapping, dropped


def _parse_json(raw: str) -> dict[str, str]:
    value = raw.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
        value = re.sub(r"\s*```$", "", value)
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", value)
        if not match:
            raise LlmTranslationError("The Cursor response does not contain JSON.")
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise LlmTranslationError("The JSON returned by Cursor is invalid.") from exc
    if not isinstance(data, dict) or not isinstance(data.get("subject"), str) or not isinstance(
        data.get("body"), str
    ):
        raise LlmTranslationError("Cursor must return string fields subject and body.")
    return {"subject": data["subject"], "body": data["body"]}


def _visible_text(value: str) -> str:
    value = TOKEN_RE.sub("", value)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip().casefold()


def _validate_protected(source: str, translated: str, mapping: dict[str, str]) -> list[str]:
    errors: list[str] = []
    expected = Counter(mapping.keys())
    actual = Counter(TOKEN_RE.findall(translated))
    if actual != expected:
        missing = list((expected - actual).elements())
        extra = list((actual - expected).elements())
        if missing:
            errors.append(f"missing tokens: {missing[:8]}")
        if extra:
            errors.append(f"duplicate or unknown tokens: {extra[:8]}")

    source_tags = Counter(tag.lower() for tag in HTML_TAG_RE.findall(source))
    result_tags = Counter(tag.lower() for tag in HTML_TAG_RE.findall(translated))
    if source_tags != result_tags:
        errors.append("HTML tag structure changed")
    return errors


def _is_directive(original: str) -> bool:
    return original.lstrip().startswith("#")


def _directive_kind(original: str) -> str | None:
    match = re.match(
        r"#(?:\{)?(set|if|elseif|else|foreach|macro|parse|include|evaluate|"
        r"end|stop|break)(?:\})?",
        original.lstrip(),
        re.I,
    )
    return match.group(1).lower() if match else None


def _validate_redesign(translated: str, mapping: dict[str, str]) -> list[str]:
    """Allow restructuring, but keep every variable and a valid directive tree."""
    errors: list[str] = []
    found = TOKEN_RE.findall(translated)

    unknown = sorted({token for token in found if token not in mapping})
    if unknown:
        errors.append(f"unknown tokens: {unknown[:8]}")

    directives = {t: v for t, v in mapping.items() if _is_directive(v)}
    values = {t: v for t, v in mapping.items() if not _is_directive(v)}

    counts = Counter(found)
    missing_directives = [t for t in directives if counts[t] != 1]
    if missing_directives:
        errors.append(
            f"missing or duplicate directives: {missing_directives[:8]}"
        )

    used_values = {mapping[t] for t in found if t in values}
    lost_values = sorted({v for v in values.values()} - used_values)
    if lost_values:
        errors.append(f"lost variables: {lost_values[:8]}")

    errors.extend(_validate_directive_tree(found, mapping))
    errors.extend(_validate_no_orphan_token(translated, mapping))
    return errors


def _validate_no_orphan_token(translated: str, mapping: dict[str, str]) -> list[str]:
    """Reject tokens dumped outside any sentence, typically at the very end."""
    tail = translated.rstrip()
    matches = list(TOKEN_RE.finditer(tail))
    if not matches:
        return []
    match = matches[-1]
    trailing = tail[match.end() :].strip()
    if trailing in {"", "</p>", "</div>", "<br />", "<br/>"}:
        original = mapping.get(match.group(0), "")
        before = tail[: match.start()].rstrip()
        if before.endswith(">") and not _is_directive(original):
            return [
                "isolated token at the end of the message: "
                f"{original} must be part of a sentence"
            ]
    return []


def _validate_directive_tree(found: list[str], mapping: dict[str, str]) -> list[str]:
    """Check that #if/#foreach blocks stay balanced after restructuring."""
    depth = 0
    for token in found:
        original = mapping.get(token, "")
        if not _is_directive(original):
            continue
        kind = _directive_kind(original)
        if kind in {"if", "foreach", "macro"}:
            depth += 1
        elif kind in {"else", "elseif"}:
            if depth == 0:
                return ["#else or #elseif outside a conditional block"]
        elif kind == "end":
            depth -= 1
            if depth < 0:
                return ["#end without a matching open block"]
    if depth != 0:
        return [f"unclosed Velocity blocks (final depth {depth})"]
    return []


def _cache_path(
    cache_dir: Path,
    key: str,
    subject: str,
    body: str,
    model: str,
    style: str,
    source_language: str,
    target_language: str,
    notes: str = "",
) -> Path:
    digest = hashlib.sha256(
        json.dumps(
            {
                "key": key,
                "subject": subject,
                "body": body,
                "model": model,
                "style": style,
                "sourceLanguage": source_language,
                "targetLanguage": target_language,
                "tokenFormatVersion": 3,
                **({"styleNotes": notes} if notes else {}),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()[:16]
    suffix = "" if style == "literal" else f"-{style}"
    return cache_dir / target_language / f"{key}-{digest}{suffix}.json"


def translate_with_cursor(
    *,
    key: str,
    name: str,
    description: str | None,
    subject: str,
    body: str,
    model: str = "auto",
    cache_dir: str | Path = "data/llm-cache",
    retries: int = 2,
    style: str = "literal",
    palette: dict[str, str] | None = None,
    source_language: str = "en",
    target_language: str = "en",
    style_notes: str = "",
) -> tuple[str, str]:
    """Translate one ISC template with Cursor and strict preservation checks."""
    api_key = resolve_api_key()

    if style not in {"literal", "redesign"}:
        raise LlmTranslationError(f"Unknown generation style: {style}")

    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    cached = _cache_path(
        cache,
        key,
        subject,
        body,
        model,
        style,
        source_language,
        target_language,
        style_notes,
    )
    cached.parent.mkdir(parents=True, exist_ok=True)
    if cached.exists():
        data = json.loads(cached.read_text(encoding="utf-8"))
        return (
            _canonicalize_simple_directives(data["subject"]),
            _canonicalize_simple_directives(data["body"]),
        )

    safe_subject, subject_map = _protect(subject, "SUBJECT")
    safe_body, body_map = _protect(body, "BODY")
    safe_subject, subject_map, subject_repairs = _drop_unmatched_closings(
        safe_subject, subject_map
    )
    safe_body, body_map, body_repairs = _drop_unmatched_closings(
        safe_body, body_map
    )

    instructions = _instructions(
        style=style,
        source_language=source_language,
        target_language=target_language,
        palette=palette,
    )

    if style_notes:
        instructions = f"{instructions}\n{style_notes}\n"

    base_prompt = f"""\
{instructions}

Template context:
- key: {key}
- name: {name}
- description: {description or ""}

Subject:
{safe_subject}

HTML/Velocity body:
{safe_body}
"""
    feedback = ""
    last_error = ""
    for attempt in range(retries + 1):
        prompt = base_prompt + feedback
        try:
            store_dir = Path(cache_dir).resolve().parent / ".sdk-agent-store"
            store_dir.mkdir(parents=True, exist_ok=True)
            result = Agent.prompt(
                prompt,
                AgentOptions(
                    api_key=api_key,
                    model=model,
                    local=LocalAgentOptions(
                        cwd=os.getcwd(),
                        setting_sources=[],
                        store=LocalAgentStoreConfig(
                            type="sqlite",
                            root_dir=str(store_dir),
                        ),
                    ),
                    name=f"translate-{key}",
                    tools=[],
                ),
            )
        except CursorAgentError as exc:
            retryable = getattr(exc, "is_retryable", False)
            if retryable and attempt < retries:
                retry_after = getattr(exc, "retry_after", None)
                delay = float(retry_after) if isinstance(retry_after, (int, float)) else 2.0
                time.sleep(min(max(delay, 1.0), 30.0))
                continue
            raise LlmTranslationError(
                f"Cursor SDK failed to start: {exc}; retryable={retryable}"
            ) from exc

        status = getattr(result.status, "value", str(result.status)).lower()
        if status != "finished":
            raise LlmTranslationError(
                f"Cursor run {result.id} finished with status {result.status}."
            )
        try:
            translated = _parse_json(result.result)
        except LlmTranslationError as exc:
            last_error = str(exc)
            feedback = (
                "\nYour previous reply was not a usable JSON object "
                f"({last_error}). Reply only with "
                '{"subject":"...","body":"..."} and no markdown.\n'
            )
            continue
        if style == "redesign":
            errors = _validate_redesign(
                translated["subject"], subject_map
            ) + _validate_redesign(translated["body"], body_map)
        else:
            errors = _validate_protected(
                safe_subject, translated["subject"], subject_map
            ) + _validate_protected(safe_body, translated["body"], body_map)
        if (
            source_language != target_language
            and len(_visible_text(safe_subject)) >= 4
            and _visible_text(translated["subject"]) == _visible_text(safe_subject)
        ):
            errors.append("the subject was not translated")
        if not errors:
            final_subject = _restore(translated["subject"], subject_map)
            final_body = _restore(translated["body"], body_map)
            payload = {
                "key": key,
                "model": model,
                "targetLanguage": target_language,
                "style": style,
                "runId": result.id,
                "sourceRepairs": subject_repairs + body_repairs,
                "subject": final_subject,
                "body": final_body,
            }
            cached.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return final_subject, final_body

        last_error = "; ".join(errors)
        feedback = (
            "\nYour previous reply was rejected for these reasons: "
            f"{last_error}. Start again from the source text and follow every "
            "token and tag constraint strictly.\n"
        )

    raise LlmTranslationError(
        f"Translation rejected after {retries + 1} attempts: {last_error}"
    )
