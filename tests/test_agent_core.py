from __future__ import annotations

import sys
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from brand_tokens import apply_branding_tokens, tokenize_branding
from branding import Branding
from llm_translate import (
    TOKEN_RE,
    LlmTranslationError,
    _drop_unmatched_closings,
    _protect,
    _restore,
    _validate_directive_tree,
)
from agent import (
    available_master_languages,
    fallback_agent_config,
    load_agent_config,
    main,
    missing_master_keys,
    resolve_configured_languages,
    source_library_language,
    save_agent_config,
    warn_if_cursor_key_missing,
)
from masters import filter_catalog_masters, validate_masters
from sailpoint import _json_from_output
from shell import wrap_body


class VelocityProtectionTest(unittest.TestCase):
    def test_adjacent_variable_and_directive_do_not_merge(self):
        source = "$shownIds#if($remainingCount > 0), more#end"
        protected, mapping = _protect(source, "BODY")
        tokens = TOKEN_RE.findall(protected)
        self.assertEqual(3, len(tokens))
        self.assertEqual(source, _restore(protected, mapping))

    def test_end_is_braced_only_when_the_next_character_would_stick(self):
        from llm_translate import _canonicalize_simple_directives

        self.assertEqual("#end has", _canonicalize_simple_directives("#end has"))
        self.assertEqual("#end</p>", _canonicalize_simple_directives("#end</p>"))
        self.assertEqual('#end"', _canonicalize_simple_directives('#end"'))
        self.assertEqual(
            '#set($taskTasks = "#if($n == 1)task#{else}tasks#end")',
            _canonicalize_simple_directives(
                '#set($taskTasks = "#if($n == 1)task#{else}tasks#end")'
            ),
        )
        self.assertEqual("#{end}changed", _canonicalize_simple_directives("#endchanged"))
        self.assertEqual("#{else}access", _canonicalize_simple_directives("#elseaccess"))
        self.assertEqual("#elseif($x)", _canonicalize_simple_directives("#elseif($x)"))

    def test_quiet_references_are_protected(self):
        source = "$!count $!{optionalName} $normal"
        protected, mapping = _protect(source, "BODY")
        self.assertEqual(
            {"$!count", "$!{optionalName}", "$normal"},
            set(mapping.values()),
        )
        self.assertEqual(source, _restore(protected, mapping))

    def test_braced_end_is_balanced(self):
        source = "#if($value)Visible#{end}"
        protected, mapping = _protect(source, "BODY")
        protected, mapping, repairs = _drop_unmatched_closings(
            protected, mapping
        )
        self.assertEqual([], repairs)
        self.assertEqual(
            [],
            _validate_directive_tree(TOKEN_RE.findall(protected), mapping),
        )

    def test_extra_source_end_is_repaired(self):
        source = "#if($value)Visible#end#end"
        protected, mapping = _protect(source, "BODY")
        protected, mapping, repairs = _drop_unmatched_closings(
            protected, mapping
        )
        self.assertEqual(["#end"], repairs)
        self.assertEqual(
            [],
            _validate_directive_tree(TOKEN_RE.findall(protected), mapping),
        )


class BrandingTest(unittest.TestCase):
    def setUp(self):
        self.branding = Branding.from_api(
            {
                "name": "default",
                "productName": "Example",
                "navigationColor": "112233",
                "actionButtonColor": "aabbcc",
                "activeLinkColor": "445566",
                "standardLogoURL": "https://tenant.example/logo.png",
            }
        )

    def test_master_tokens_are_rendered_from_branding(self):
        source = '<h2 style="color:#112233">Title</h2>'
        master = tokenize_branding(source, self.branding)
        self.assertNotIn("#112233", master)
        self.assertEqual(source, apply_branding_tokens(master, self.branding))

    def test_shell_localizes_footer_and_uses_logo(self):
        body = wrap_body(self.branding, "<p>Hello</p>", language="en")
        self.assertIn(self.branding.logo_url, body)
        self.assertIn('padding: 20px 0 20px 20px;', body)
        self.assertIn("This is an automated email", body)
        self.assertIn(self.branding.navigation_color, body)
        self.assertIn(self.branding.action_color, body)

    def test_existing_shell_logo_gains_left_padding(self):
        from shell import pad_logo_cell

        source = '<td style="padding: 20px 0;"><img src="logo.png" /></td>'
        updated = pad_logo_cell(source)
        self.assertIn('padding: 20px 0 20px 20px;', updated)
        self.assertNotIn('padding: 20px 0;', updated)


class CatalogFilterTest(unittest.TestCase):
    def test_missing_catalog_templates_are_skipped_not_errors(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            present = directory / "known.json"
            absent = directory / "retired.json"
            present.write_text(
                '{"key":"known","subject":"Hi","body":"<p>Hi</p>","origin":"curated"}',
                encoding="utf-8",
            )
            absent.write_text(
                '{"key":"retired","subject":"Bye","body":"<p>Bye</p>","origin":"curated"}',
                encoding="utf-8",
            )
            defaults = [{"key": "known", "subject": "Hi", "body": "<p>Hi</p>"}]
            kept, skipped = filter_catalog_masters([present, absent], defaults)
            self.assertEqual([present], kept)
            self.assertEqual(["retired"], skipped)
            self.assertEqual([], validate_masters([present, absent], defaults))


class AgentConfigTest(unittest.TestCase):
    def test_init_keeps_the_library_choice_and_uploads_english(self):
        seen: list[str] = []

        def ask(default: str) -> str:
            seen.append(default)
            return "de"

        base, language = resolve_configured_languages(
            language=None,
            base_language=None,
            config=None,
            init=True,
            ask=ask,
        )
        self.assertEqual(["en"], seen)
        self.assertEqual(("de", "en"), (base, language))

    def test_explicit_language_overrides_the_english_upload_default(self):
        base, language = resolve_configured_languages(
            language="de",
            base_language=None,
            config=None,
            init=True,
            ask=lambda default: default,
        )
        self.assertEqual(("en", "de"), (base, language))

    def test_saved_language_is_the_prompt_default(self):
        seen: list[str] = []

        def ask(default: str) -> str:
            seen.append(default)
            return default

        base, language = resolve_configured_languages(
            language=None,
            base_language=None,
            config={"baseLanguage": "fr", "language": "es"},
            init=False,
            ask=ask,
        )
        self.assertEqual(["es"], seen)
        self.assertEqual(("fr", "es"), (base, language))

    def test_committed_masters_replace_a_missing_local_config(self):
        self.assertEqual(
            {"baseLanguage": "en", "language": "en"},
            fallback_agent_config(["fr", "en"]),
        )
        self.assertEqual(
            {"baseLanguage": "fr", "language": "fr"},
            fallback_agent_config(["fr"]),
        )
        self.assertIsNone(fallback_agent_config([]))

    def test_init_translates_from_english_when_it_exists(self):
        self.assertEqual("en", source_library_language("de", ["fr", "en"]))
        self.assertEqual("fr", source_library_language("en", ["fr", "en"]))
        self.assertIsNone(source_library_language("en", ["en"]))

    def test_init_only_sees_keys_that_are_not_already_masters(self):
        existing = {"alpha", "beta"}
        required = {"alpha", "beta", "gamma"}
        self.assertEqual({"gamma"}, missing_master_keys(existing, required))
        self.assertEqual(set(), missing_master_keys(existing, {"alpha", "beta"}))

    def test_available_languages_are_directories_that_contain_masters(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "en").mkdir()
            (root / "fr").mkdir()
            (root / "de").mkdir()
            (root / "en" / "one.json").write_text("{}", encoding="utf-8")
            (root / "fr" / "one.json").write_text("{}", encoding="utf-8")
            self.assertEqual(["en", "fr"], available_master_languages(root))

    def test_config_roundtrip(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent-config.json"
            save_agent_config("fr", "nl", path)
            self.assertEqual(
                {"baseLanguage": "fr", "language": "nl"},
                load_agent_config(path),
            )


class CursorKeyLaunchTest(unittest.TestCase):
    def test_missing_key_prints_a_warning(self):
        message = "No Cursor API key found."
        with (
            patch("agent.resolve_api_key", side_effect=LlmTranslationError(message)),
            patch.object(sys, "argv", ["agent", "--prepare-only"]),
        ):
            sink = StringIO()
            with patch.object(sys, "stderr", sink):
                code = main()
        self.assertEqual(1, code)
        self.assertIn(f"Warning: {message}", sink.getvalue())
        self.assertNotIn("Error:", sink.getvalue())

    def test_present_key_does_not_warn(self):
        sink = StringIO()
        with (
            patch("agent.resolve_api_key", return_value="test-key"),
            patch.object(sys, "stderr", sink),
        ):
            missing = warn_if_cursor_key_missing()
        self.assertFalse(missing)
        self.assertEqual("", sink.getvalue())


class SailOutputTest(unittest.TestCase):
    def test_top_level_array_wins_over_nested_objects(self):
        raw = (
            "INFO request\n"
            '[{"name":"default","nested":{"color":"112233"}}]\n'
            "Status: 200 OK\n"
        )
        value = _json_from_output(raw)
        self.assertIsInstance(value, list)
        self.assertEqual("default", value[0]["name"])


if __name__ == "__main__":
    unittest.main()
