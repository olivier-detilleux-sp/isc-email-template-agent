"""Interactive end-to-end SailPoint email template agent."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from branding import Branding  # noqa: E402
from llm_translate import LlmTranslationError  # noqa: E402
from masters import (  # noqa: E402
    assemble_payloads,
    build_base_masters,
    filter_catalog_masters,
    load_json,
    translate_masters,
    validate_masters,
    validate_payloads,
)
from sailpoint import (  # noqa: E402
    SailError,
    active_environment,
    api_post_template,
    changed_payloads,
    pull_all,
)
from shell import pad_logo_cell  # noqa: E402


LANGUAGES = {
    "1": ("fr", "French"),
    "2": ("en", "English"),
    "3": ("de", "German"),
    "4": ("es", "Spanish"),
    "5": ("it", "Italian"),
    "6": ("nl", "Dutch"),
    "7": ("pt", "Portuguese"),
}
LANGUAGE_ALIASES = {
    "français": "fr",
    "francais": "fr",
    "french": "fr",
    "english": "en",
    "anglais": "en",
    "allemand": "de",
    "german": "de",
    "espagnol": "es",
    "spanish": "es",
    "italien": "it",
    "italian": "it",
    "néerlandais": "nl",
    "neerlandais": "nl",
    "dutch": "nl",
    "portugais": "pt",
    "portuguese": "pt",
}


CONFIG_PATH = ROOT / "data" / "agent-config.json"
DEFAULT_BASE_LANGUAGE = "en"
DEFAULT_PUBLICATION_LANGUAGE = "en"

INIT_INTRO = (
    "Style library language. Masters are rebuilt in this language.\n"
    "Press Enter for English. Uploaded templates use this library when "
    "you do not pass a different --language."
)
RUN_INTRO = "Language for uploaded email subjects and bodies:"


def ask_language(default: str = "en", *, intro: str) -> str:
    print(intro)
    for number, (code, label) in LANGUAGES.items():
        print(f"  {number}. {label} ({code})")
    answer = input(f"Choice [{default}]: ").strip() or default
    return normalize_language(answer)


def load_agent_config(path: Path = CONFIG_PATH) -> dict[str, str] | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return None
    base = data.get("baseLanguage")
    language = data.get("language")
    if not isinstance(base, str) or not isinstance(language, str):
        return None
    return {
        "baseLanguage": normalize_language(base),
        "language": normalize_language(language),
    }


def save_agent_config(
    base_language: str,
    language: str,
    path: Path = CONFIG_PATH,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"baseLanguage": base_language, "language": language},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def resolve_configured_languages(
    *,
    language: str | None,
    base_language: str | None,
    config: dict[str, str] | None,
    init: bool,
    ask,
) -> tuple[str, str]:
    """Resolve the style-library language and the publication language."""
    language = normalize_language(language) if language else None
    base_language = normalize_language(base_language) if base_language else None
    if init:
        if base_language is None and language is None:
            base_language = ask(DEFAULT_BASE_LANGUAGE)
        elif base_language is None:
            base_language = DEFAULT_BASE_LANGUAGE
        if language is None:
            language = DEFAULT_PUBLICATION_LANGUAGE
        return base_language, language
    if config is None:
        raise ValueError("The agent has not been initialized.")
    base_language = base_language or config["baseLanguage"]
    if language is None:
        language = ask(config["language"])
    return base_language, language


def normalize_language(value: str) -> str:
    cleaned = value.strip().lower()
    if cleaned in LANGUAGES:
        return LANGUAGES[cleaned][0]
    if cleaned in LANGUAGE_ALIASES:
        return LANGUAGE_ALIASES[cleaned]
    if len(cleaned) in {2, 5}:
        return cleaned.split("-", 1)[0]
    raise ValueError(f"Unrecognized language: {value}")


def select_default_branding(items: list[dict]) -> Branding:
    item = next((item for item in items if item.get("name") == "default"), None)
    if item is None:
        if len(items) != 1:
            raise ValueError(
                "No branding named 'default', and more than one branding is available."
            )
        item = items[0]
    return Branding.from_api(item)


def logo_padding_payload(custom: dict) -> dict | None:
    """Update only the logo inset on a template already published with our shell."""
    body = custom.get("body") or ""
    padded = pad_logo_cell(body)
    if padded == body:
        return None
    return {
        "key": custom["key"],
        "name": custom.get("name") or custom["key"],
        "medium": "EMAIL",
        "locale": custom.get("locale") or "en",
        "subject": custom.get("subject") or "",
        "body": padded,
        "from": custom.get("from") or "$__global.emailFromAddress",
        "replyTo": custom.get("replyTo") or "$__global.emailFromAddress",
        "description": custom.get("description"),
        "header": None,
        "footer": None,
        "slackTemplate": None,
        "teamsTemplate": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare, translate, brand, and publish ISC EMAIL templates."
    )
    parser.add_argument(
        "--env",
        help="Sail CLI environment. Default: the active environment.",
    )
    parser.add_argument(
        "--language",
        "--lang",
        help=(
            "Language written into uploaded templates "
            "(fr, en, de, es, it, nl, pt). Default: en."
        ),
    )
    parser.add_argument("--model", default="auto", help="Cursor SDK model")
    parser.add_argument(
        "--base-language",
        help="Language of the style library. Default: the language saved by --init.",
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help=(
            "Set the default language and rebuild the style library "
            "from the tenant catalog. Does not publish."
        ),
    )
    parser.add_argument(
        "--rebuild-masters",
        action="store_true",
        help="Rebuild the base styles with the LLM",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Generate and validate without offering to publish",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Publish without an interactive confirmation",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        help="Limit the run to one template key (repeatable)",
    )
    args = parser.parse_args()
    if args.init and args.only:
        print(
            "--init rebuilds the whole style library. Remove --only.",
            file=sys.stderr,
        )
        return 1

    config = load_agent_config()
    if not args.init and config is None:
        print("This agent is not initialized.", file=sys.stderr)
        print(
            "Set the default language and rebuild the style library first:",
            file=sys.stderr,
        )
        print("  ./agent --init", file=sys.stderr)
        return 1

    def ask(default: str) -> str:
        if args.yes or not sys.stdin.isatty():
            return default
        intro = INIT_INTRO if args.init else RUN_INTRO
        return ask_language(default, intro=intro)

    try:
        base_language, language = resolve_configured_languages(
            language=args.language,
            base_language=args.base_language,
            config=config,
            init=args.init,
            ask=ask,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    only = set(args.only) or None

    try:
        environment = active_environment(args.env)
        print(
            f"Tenant CLI: {environment.name} ({environment.base_url})",
            flush=True,
        )
        print(f"Uploaded content language: {language}", flush=True)
        if language != base_language:
            print(
                f"Style library: {base_language}. "
                f"Uploads are translated into {language}.",
                flush=True,
            )

        branding_items, defaults, customs = pull_all(
            environment, ROOT / "data/pull"
        )
        branding = select_default_branding(branding_items)
        print(
            "Branding: "
            f"{branding.product_name}, {branding.navigation_color}, "
            f"{branding.action_color}, logo={branding.logo_url}",
            flush=True,
        )

        curated_path = ROOT / "data/curated-keys.json"
        curated_keys = set(load_json(curated_path)) if curated_path.exists() else set()
        base_dir = ROOT / "data/masters" / base_language
        default_keys = {
            item["key"] for item in defaults if not only or item["key"] in only
        }
        existing_keys = {path.stem for path in base_dir.glob("*.json")}
        need_base = (
            args.init
            or args.rebuild_masters
            or not default_keys.issubset(existing_keys)
        )
        if need_base:
            build_base_masters(
                defaults=defaults,
                customs=customs,
                curated_keys=curated_keys,
                branding=branding,
                target_language=base_language,
                model=args.model,
                output_dir=base_dir,
                cache_dir=ROOT / "data/llm-cache",
                only=only,
                force=args.init or args.rebuild_masters,
            )
        if args.init:
            save_agent_config(base_language, language)
            print(
                f"Initialized. Style library: {base_language}. "
                f"Default publication language: {language}.",
                flush=True,
            )
            print("Next, prepare without publishing:", flush=True)
            print("  ./agent --prepare-only", flush=True)
            return 0

        if language == base_language:
            master_paths = sorted(base_dir.glob("*.json"))
            if only:
                master_paths = [p for p in master_paths if p.stem in only]
        else:
            translated_dir = ROOT / "data/translations" / language
            master_paths = translate_masters(
                source_dir=base_dir,
                target_dir=translated_dir,
                source_language=base_language,
                target_language=language,
                model=args.model,
                cache_dir=ROOT / "data/llm-cache",
                only=default_keys,
            )

        master_paths, skipped = filter_catalog_masters(master_paths, defaults)
        if skipped:
            print(
                f"{len(skipped)} template(s) missing from the tenant catalog, skipped:",
                flush=True,
            )
            for key in skipped:
                print(f"  - {key}", flush=True)
        if only:
            missing_only = sorted(
                only - {path.stem for path in master_paths} - set(skipped)
            )
            if missing_only:
                print(
                    "--only key(s) missing from the catalog or the masters, skipped:",
                    flush=True,
                )
                for key in missing_only:
                    print(f"  - {key}", flush=True)

        master_errors = validate_masters(master_paths, defaults)
        if master_errors:
            invalid_keys = {
                error.split(":", 1)[0].split("/", 1)[0] for error in master_errors
            }
            print(
                f"{len(invalid_keys)} template(s) not regenerated "
                "(they differ from the current catalog):",
                flush=True,
            )
            for error in master_errors:
                print(f"  - {error}", flush=True)
            master_paths = [
                path for path in master_paths if path.stem not in invalid_keys
            ]
        else:
            invalid_keys = set()

        payload_dir = ROOT / "data/payloads" / environment.name / language
        payloads = assemble_payloads(
            master_paths=master_paths,
            branding=branding,
            output_dir=payload_dir,
        )
        preserved: list[str] = []
        if invalid_keys and language != base_language:
            print(
                f"{len(invalid_keys)} template(s) will not be uploaded. "
                f"They could not be produced in {language}:",
                flush=True,
            )
            for key in sorted(invalid_keys):
                print(f"  - {key}", flush=True)
        else:
            for custom in customs:
                key = custom.get("key")
                if key not in invalid_keys:
                    continue
                if only and key not in only:
                    continue
                payload = logo_padding_payload(custom)
                if payload is None:
                    preserved.append(key)
                    continue
                payloads.append(payload)
                output = payload_dir / f"{key}.json"
                output.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        if preserved:
            print(
                "Logo inset not applied; the existing shell was not found:",
                flush=True,
            )
            for key in preserved:
                print(f"  - {key}", flush=True)
        errors = validate_payloads(payloads, branding=branding)
        if errors:
            print("Blocking validation errors:", file=sys.stderr)
            for error in errors:
                print(f"  - {error}", file=sys.stderr)
            return 2

        changed = changed_payloads(payloads, customs)
        print(
            f"Validation OK: {len(payloads)} templates, "
            f"{len(changed)} to update, "
            f"{len(payloads) - len(changed)} unchanged.",
            flush=True,
        )
        print(
            "Translated `subject` and `body` fields are present in every payload.",
            flush=True,
        )
        print(f"Payloads: {payload_dir}", flush=True)

        if args.prepare_only or not changed:
            return 0
        if not args.yes:
            answer = input(
                f"Type PUSH to publish {len(changed)} {language} templates "
                f"to {environment.name}: "
            ).strip()
            if answer != "PUSH":
                print("Publication cancelled.")
                return 0

        results: list[dict] = []
        failures: list[str] = []
        for index, payload in enumerate(changed, 1):
            key = payload["key"]
            print(f"[{index}/{len(changed)}] POST {key}", flush=True)
            try:
                response = api_post_template(payload, env=environment.name)
                results.append(
                    {
                        "key": key,
                        "success": True,
                        "id": response.get("id"),
                        "modified": response.get("modified"),
                    }
                )
            except SailError as exc:
                failures.append(key)
                results.append({"key": key, "success": False, "error": str(exc)})
            time.sleep(0.2)

        report_dir = ROOT / "data/reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"{environment.name}-{language}-{int(time.time())}.json"
        report_path.write_text(
            json.dumps(
                {
                    "environment": environment.name,
                    "language": language,
                    "total": len(changed),
                    "success": len(changed) - len(failures),
                    "failures": failures,
                    "results": results,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(
            f"Publication finished: {len(changed) - len(failures)} succeeded, "
            f"{len(failures)} failed. Report: {report_path}",
            flush=True,
        )
        return 1 if failures else 0
    except (SailError, ValueError, LlmTranslationError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
