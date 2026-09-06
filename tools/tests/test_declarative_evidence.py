from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.core.artifact_validator import ArtifactValidationError
from tools.core.declarative_evidence import evaluate_evidence_contract


class DeclarativeEvidenceTests(unittest.TestCase):
    def test_current_sqlite_roadmap_requires_partition_verification(self) -> None:
        source_root = Path(__file__).resolve().parents[2]
        contract = json.loads((source_root / "config/product_reports_evidence_contract.json").read_text(encoding="utf-8"))
        check = next(row for row in contract["checks"] if row["id"] == "python_boundary_and_sqlite_roadmap_are_explicit")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sources = {key: contract["sources"][key] for key in check["requires"]}
            for source in sources.values():
                path = root / source["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((source_root / source["path"]).read_bytes())
            contract_path = root / "contract.json"
            self._write(contract_path, {"sources": sources, "checks": [check]})
            self.assertTrue(evaluate_evidence_contract(contract_path, root=root, raw_dir=root)[0]["passed"])
            doc = root / sources["sqlite_roadmap"]["path"]
            doc.write_text(doc.read_text(encoding="utf-8").replace("verify inline payload or manifest-selected ordered parts -> ", ""), encoding="utf-8")
            self.assertFalse(evaluate_evidence_contract(contract_path, root=root, raw_dir=root)[0]["passed"])

    @staticmethod
    def _write(path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_text_and_json_assertions_are_evaluated_from_declared_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = root / "raw"
            raw.mkdir()
            (root / "evidence.md").write_text("proven boundary\n", encoding="utf-8")
            self._write(raw / "result.json", {"summary": {"status": "PASS", "count": 3}})
            contract = root / "contract.json"
            self._write(
                contract,
                {
                    "sources": {
                        "doc": {"scope": "root", "path": "evidence.md"},
                        "result": {"scope": "raw", "path": "result.json"},
                    },
                    "checks": [
                        {
                            "id": "grounded",
                            "requires": ["doc", "result"],
                            "contains": {"doc": ["proven"]},
                            "not_contains": {"doc": ["unbounded"]},
                            "json_assertions": [
                                {"source": "result", "field": "summary.status", "op": "equals", "value": "PASS"},
                                {"source": "result", "field": "summary.count", "op": "gte", "value": 2},
                            ],
                        }
                    ],
                },
            )

            result = evaluate_evidence_contract(contract, root=root, raw_dir=raw)

            self.assertTrue(result[0]["passed"])
            self.assertEqual(result[0]["details"]["failures"], [])

    def test_missing_and_forbidden_evidence_fail_with_specific_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = root / "raw"
            raw.mkdir()
            (root / "evidence.md").write_text("forbidden claim\n", encoding="utf-8")
            contract = root / "contract.json"
            self._write(
                contract,
                {
                    "sources": {"doc": {"scope": "root", "path": "evidence.md"}},
                    "checks": [
                        {
                            "id": "fail_closed",
                            "requires": ["doc"],
                            "contains": {"doc": ["missing phrase"]},
                            "not_contains": {"doc": ["forbidden claim"]},
                        }
                    ],
                },
            )

            result = evaluate_evidence_contract(contract, root=root, raw_dir=raw)

            self.assertFalse(result[0]["passed"])
            self.assertEqual(len(result[0]["details"]["failures"]), 2)

    def test_duplicate_check_ids_and_path_traversal_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = root / "raw"
            raw.mkdir()
            duplicate = root / "duplicate.json"
            self._write(
                duplicate,
                {
                    "sources": {},
                    "checks": [{"id": "same", "requires": []}, {"id": "same", "requires": []}],
                },
            )
            with self.assertRaises(ValueError):
                evaluate_evidence_contract(duplicate, root=root, raw_dir=raw)

            traversal = root / "traversal.json"
            self._write(
                traversal,
                {
                    "sources": {"outside": {"scope": "root", "path": "../outside.md"}},
                    "checks": [{"id": "outside", "requires": ["outside"]}],
                },
            )
            with self.assertRaises(ValueError):
                evaluate_evidence_contract(traversal, root=root, raw_dir=raw)

    def test_numeric_assertions_fail_closed_for_boolean_or_invalid_expected_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = root / "raw"
            raw.mkdir()
            self._write(raw / "result.json", {"actual_bool": True, "actual_number": 4})
            contract = root / "contract.json"
            self._write(
                contract,
                {
                    "sources": {"result": {"scope": "raw", "path": "result.json"}},
                    "checks": [
                        {
                            "id": "numeric_types_are_strict",
                            "requires": ["result"],
                            "json_assertions": [
                                {"source": "result", "field": "actual_bool", "op": "is_type", "value": "int"},
                                {"source": "result", "field": "actual_number", "op": "gte", "value": "four"},
                            ],
                        }
                    ],
                },
            )

            result = evaluate_evidence_contract(contract, root=root, raw_dir=raw)

            self.assertFalse(result[0]["passed"])
            self.assertEqual(len(result[0]["details"]["failures"]), 2)

    def test_all_consumed_sources_are_reported_even_without_requires_duplication(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = root / "raw"
            raw.mkdir()
            (root / "evidence.md").write_text("proven\n", encoding="utf-8")
            contract = root / "contract.json"
            self._write(
                contract,
                {
                    "sources": {"doc": {"scope": "root", "path": "evidence.md"}},
                    "checks": [{"id": "reported", "requires": [], "contains": {"doc": ["proven"]}}],
                },
            )

            result = evaluate_evidence_contract(contract, root=root, raw_dir=raw)

            self.assertEqual(result[0]["details"]["sources"], ["evidence.md"])

    def test_schema_rejects_unknown_contract_fields_before_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = root / "raw"
            raw.mkdir()
            contract = root / "contract.json"
            self._write(
                contract,
                {
                    "meta": {
                        "kind": "nexora.product_reports_evidence_contract",
                        "version": "1.0.0",
                        "purpose": "test",
                        "unexpected": True,
                    },
                    "sources": {},
                    "checks": [],
                },
            )
            schema = Path(__file__).resolve().parents[2] / "config" / "schemas" / "product_reports_evidence_contract.schema.json"
            distribution_root = Path(__file__).resolve().parents[2]
            if (distribution_root / "PUBLIC_DISTRIBUTION_MANIFEST.json").is_file():
                self.assertFalse(schema.exists())
                return

            with self.assertRaises(ArtifactValidationError):
                evaluate_evidence_contract(contract, root=root, raw_dir=raw, schema_path=schema)


if __name__ == "__main__":
    unittest.main()
