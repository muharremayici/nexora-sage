from __future__ import annotations

import ast
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from tools.core import config as core_config

from tools.core.config import CODE_MAPS_DIR
from tools.validate_exception_honesty import (
    _ast_fingerprint,
    _iter_exception_handlers,
    _semantic_handler_identity,
    validate_exception_honesty,
)


class ExceptionHonestyTests(unittest.TestCase):
    def test_atomic_writers_preserve_success_and_original_failure(self):
        for writer, payload in (
            (core_config.save_text_atomic, "new"),
            (core_config.save_json_atomic, {"new": True}),
        ):
            with self.subTest(writer=writer.__name__), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "state.txt"
                writer(target, payload)
                original = target.read_bytes()
                error = OSError("replacement failed")
                with patch.object(core_config.os, "replace", side_effect=error):
                    with self.assertRaises(OSError) as caught:
                        writer(target, payload)
                self.assertIs(caught.exception, error)
                self.assertEqual(target.read_bytes(), original)
                self.assertEqual(list(Path(directory).glob("cm_tmp_*.tmp")), [])

    def test_atomic_cleanup_failure_is_visible_without_masking_write_failure(self):
        for writer, payload in (
            (core_config.save_text_atomic, "new"),
            (core_config.save_json_atomic, {"new": True}),
        ):
            with self.subTest(writer=writer.__name__), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "state.txt"
                writer(target, payload)
                original = target.read_bytes()
                error = OSError("replacement failed")
                cleanup_error = OSError("cleanup failed")
                with (
                    patch.object(core_config.os, "replace", side_effect=error),
                    patch.object(core_config.os, "remove", side_effect=cleanup_error),
                ):
                    with self.assertRaises(OSError) as caught:
                        writer(target, payload)
                self.assertIs(caught.exception, error)
                self.assertIs(caught.exception.__context__, cleanup_error)
                self.assertIn("cleanup failed", caught.exception.__notes__[0])
                self.assertEqual(target.read_bytes(), original)
                self.assertEqual(len(list(Path(directory).glob("cm_tmp_*.tmp"))), 1)

    def test_critical_generic_exceptions_are_observed_or_declared(self):
        result = validate_exception_honesty()
        self.assertEqual(result["summary"]["status"], "PASS")
        self.assertEqual(result["summary"]["blocking_unobserved_generic"], 0)
        self.assertEqual(result["summary"]["semantic_handler_policy_unmatched_or_ambiguous"], 0)
        self.assertEqual(
            result["summary"]["semantic_handler_policy_records"],
            result["summary"]["allowed_quiet"],
        )

    def test_policy_uses_exact_semantic_handler_identities(self):
        policy_path = Path(CODE_MAPS_DIR) / "config" / "exception_handling_policy.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        legacy_fields = {
            "allowed_quiet_handlers",
            "allowed_quiet_handler_notes",
            "allowed_quiet_functions",
            "allowed_quiet_pass_only_functions",
            "allowed_quiet_pass_only_function_notes",
        }
        records = policy.get("allowed_quiet_semantic_handlers")
        required = {
            "file",
            "function",
            "exception_type",
            "pass_only",
            "body_fingerprint",
            "try_context_fingerprint",
            "purpose",
        }
        self.assertIsInstance(records, list)
        self.assertFalse(legacy_fields & set(policy))
        self.assertTrue(records)
        self.assertTrue(all(isinstance(record, dict) for record in records))
        self.assertTrue(all(required <= set(record) for record in records))

    def test_fingerprint_ignores_runtime_specific_ast_fields(self):
        class SyntheticNode(ast.AST):
            _fields = ("value", "type_params")

        without_runtime_field = SyntheticNode()
        without_runtime_field.value = "stable"
        with_runtime_field = SyntheticNode()
        with_runtime_field.value = "stable"
        with_runtime_field.type_params = []
        changed_semantics = SyntheticNode()
        changed_semantics.value = "changed"
        changed_semantics.type_params = []

        self.assertEqual(
            _ast_fingerprint(without_runtime_field),
            _ast_fingerprint(with_runtime_field),
        )
        self.assertNotEqual(
            _ast_fingerprint(with_runtime_field),
            _ast_fingerprint(changed_semantics),
        )
    def test_try_context_distinguishes_same_body_handlers(self):
        tree = ast.parse(
            "def target():\n"
            "    try:\n"
            "        first()\n"
            "    except ValueError:\n"
            "        pass\n"
            "    try:\n"
            "        second()\n"
            "    except ValueError:\n"
            "        pass\n"
        )
        handlers = _iter_exception_handlers(tree)
        identities = [
            _semantic_handler_identity("synthetic.py", function, handler, context)
            for handler, function, context in handlers
        ]
        self.assertEqual(len(identities), 2)
        self.assertEqual(identities[0]["body_fingerprint"], identities[1]["body_fingerprint"])
        self.assertNotEqual(identities[0]["try_context_fingerprint"], identities[1]["try_context_fingerprint"])

    def test_identity_ignores_line_shift_but_detects_try_context_drift(self):
        base = (
            "def target():\n"
            "    try:\n"
            "        first()\n"
            "    except ValueError:\n"
            "        pass\n"
        )
        shifted = "\n\n" + base
        changed = base.replace("first()", "second()")

        def identity(source: str):
            handler, function, context = _iter_exception_handlers(ast.parse(source))[0]
            return _semantic_handler_identity("synthetic.py", function, handler, context)

        baseline = identity(base)
        self.assertEqual(baseline, identity(shifted))
        self.assertNotEqual(
            baseline["try_context_fingerprint"],
            identity(changed)["try_context_fingerprint"],
        )


if __name__ == "__main__":
    unittest.main()
