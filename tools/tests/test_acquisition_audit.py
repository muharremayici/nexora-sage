import tempfile
import unittest
from pathlib import Path

from tools.core.acquisition_audit import acquisitions_from_source, repeated_acquisitions
from tools.validate_repeated_acquisition_projection import _classify_candidates


class AcquisitionAuditTests(unittest.TestCase):
    def test_groups_same_source_inside_same_function(self):
        source = """
def inspect(path):
    first = path.read_text(encoding='utf-8')
    second = path.read_text(encoding='utf-8')
    return first, second
"""
        items = acquisitions_from_source(source, file="tools/example.py", methods={"read_text"})

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].key, items[1].key)

    def test_separates_enclosing_function_freshness_boundaries(self):
        source = """
def before(path):
    return path.read_text()

def after(path):
    return path.read_text()
"""
        items = acquisitions_from_source(source, file="tools/example.py", methods={"read_text"})

        self.assertNotEqual(items[0].key, items[1].key)

    def test_separates_same_method_name_in_different_classes(self):
        source = """
class Before:
    def load(self, path):
        return path.read_text()

class After:
    def load(self, path):
        return path.read_text()
"""
        items = acquisitions_from_source(source, file="tools/example.py", methods={"read_text"})

        self.assertEqual(items[0].function, "Before.load")
        self.assertEqual(items[1].function, "After.load")
        self.assertNotEqual(items[0].key, items[1].key)

    def test_repository_scan_excludes_test_roots(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "tools"
            tests_dir = source_dir / "tests"
            tests_dir.mkdir(parents=True)
            (source_dir / "real.py").write_text("def f(p):\n p.read_text(); p.read_text()\n", encoding="utf-8")
            (tests_dir / "fixture.py").write_text("def f(p):\n p.read_text(); p.read_text()\n", encoding="utf-8")

            candidates, errors = repeated_acquisitions(
                root,
                scan_roots=["tools"],
                exclude_path_prefixes=["tools/tests"],
                methods={"read_text"},
                minimum_occurrences=2,
            )

        self.assertFalse(errors)
        self.assertEqual([item["file"] for item in candidates], ["tools/real.py"])

    def test_stale_independent_read_declaration_is_visible(self):
        candidates = [{"key": "live", "classification": ""}]

        classified, unused = _classify_candidates(
            candidates,
            {"live": {"reason": "freshness boundary"}, "stale": {"reason": "old code"}},
        )

        self.assertEqual(classified[0]["classification"], "declared_independent")
        self.assertEqual(unused, ["stale"])


if __name__ == "__main__":
    unittest.main()
