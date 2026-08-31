from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.core.json_io import load_json_file, load_json_object_strict


SUPPORTED_OPERATORS = {
    "equals",
    "gte",
    "has_keys",
    "in",
    "is_type",
    "list_contains",
}
TYPE_NAMES = {"dict": dict, "list": list, "str": str, "int": int, "float": float, "bool": bool}


def _safe_path(base: Path, relative: str) -> Path:
    candidate = (base / relative).resolve()
    candidate.relative_to(base.resolve())
    return candidate


def _source_path(source: dict[str, Any], *, root: Path, raw_dir: Path) -> Path:
    scope = str(source.get("scope") or "root")
    base = raw_dir if scope == "raw" else root
    return _safe_path(base, str(source.get("path") or ""))


def _nested(payload: Any, dotted_path: str) -> tuple[bool, Any]:
    current = payload
    for segment in [part for part in dotted_path.split(".") if part]:
        if not isinstance(current, dict) or segment not in current:
            return False, None
        current = current[segment]
    return True, current


def _json_assertion(payload: Any, assertion: dict[str, Any]) -> tuple[bool, str]:
    field = str(assertion.get("field") or "")
    operator = str(assertion.get("op") or "")
    if operator not in SUPPORTED_OPERATORS:
        return False, f"unsupported_operator:{operator}"
    found, actual = _nested(payload, field)
    if not found:
        return False, f"missing_field:{field}"
    expected = assertion.get("value")
    if operator == "equals":
        passed = actual == expected
    elif operator == "gte":
        passed = (
            isinstance(actual, (int, float))
            and not isinstance(actual, bool)
            and isinstance(expected, (int, float))
            and not isinstance(expected, bool)
            and actual >= expected
        )
    elif operator == "has_keys":
        passed = isinstance(actual, dict) and all(str(key) in actual for key in (expected or []))
    elif operator == "in":
        passed = actual in (expected or [])
    elif operator == "is_type":
        expected_type = TYPE_NAMES.get(str(expected))
        passed = expected_type is not None and isinstance(actual, expected_type)
        if expected_type in {int, float} and isinstance(actual, bool):
            passed = False
    else:
        passed = isinstance(actual, list) and expected in actual
    return bool(passed), "" if passed else f"assertion_failed:{field}:{operator}:actual={actual!r}"


def evaluate_evidence_contract(
    contract_path: Path,
    *,
    root: Path,
    raw_dir: Path,
    schema_path: Path | None = None,
) -> list[dict[str, Any]]:
    contract = load_json_object_strict(contract_path, label="Declarative evidence contract")
    if schema_path is not None:
        from tools.core.artifact_validator import ensure_against_schema

        ensure_against_schema(schema_path, contract_path.stem, contract)
    sources = contract.get("sources")
    checks = contract.get("checks")
    if not isinstance(sources, dict) or not isinstance(checks, list):
        raise ValueError("Declarative evidence contract requires object sources and list checks")
    duplicate_ids = [
        check_id
        for check_id in {str(row.get("id") or "") for row in checks if isinstance(row, dict)}
        if sum(1 for row in checks if isinstance(row, dict) and str(row.get("id") or "") == check_id) > 1
    ]
    if duplicate_ids:
        raise ValueError(f"Declarative evidence contract has duplicate check ids: {sorted(duplicate_ids)}")

    cache: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    for row in checks:
        if not isinstance(row, dict) or not str(row.get("id") or ""):
            raise ValueError("Declarative evidence contract check rows require id")
        failures: list[str] = []
        used_source_ids = {
            *(str(value) for value in row.get("requires", [])),
            *(str(value) for value in (row.get("contains") or {})),
            *(str(value) for value in (row.get("not_contains") or {})),
            *(str(value.get("source") or "") for value in row.get("json_assertions", []) if isinstance(value, dict)),
        }
        used_sources: list[str] = []
        for source_id in sorted(used_source_ids - {""}):
            source = sources.get(source_id)
            if not isinstance(source, dict):
                continue
            path = _source_path(source, root=root, raw_dir=raw_dir)
            used_sources.append(path.relative_to(root).as_posix() if root in path.parents else f"output/.raw/{path.name}")
        for source_id in [str(value) for value in row.get("requires", [])]:
            source = sources.get(source_id)
            if not isinstance(source, dict):
                failures.append(f"unknown_source:{source_id}")
                continue
            path = _source_path(source, root=root, raw_dir=raw_dir)
            cache.setdefault(source_id, {"path": path, "exists": path.exists()})
            if not path.exists():
                failures.append(f"missing_source:{source_id}:{path}")

        for source_id, phrases in (row.get("contains") or {}).items():
            source = sources.get(source_id)
            if not isinstance(source, dict):
                failures.append(f"unknown_source:{source_id}")
                continue
            path = _source_path(source, root=root, raw_dir=raw_dir)
            text = cache.setdefault(source_id, {"path": path, "exists": path.exists()}).setdefault(
                "text", path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
            )
            for phrase in phrases:
                if str(phrase) not in text:
                    failures.append(f"missing_phrase:{source_id}:{phrase}")

        for source_id, phrases in (row.get("not_contains") or {}).items():
            source = sources.get(source_id)
            if not isinstance(source, dict):
                failures.append(f"unknown_source:{source_id}")
                continue
            path = _source_path(source, root=root, raw_dir=raw_dir)
            text = cache.setdefault(source_id, {"path": path, "exists": path.exists()}).setdefault(
                "text", path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
            )
            for phrase in phrases:
                if str(phrase) in text:
                    failures.append(f"forbidden_phrase:{source_id}:{phrase}")

        for assertion in row.get("json_assertions", []):
            source_id = str(assertion.get("source") or "")
            source = sources.get(source_id)
            if not isinstance(source, dict):
                failures.append(f"unknown_source:{source_id}")
                continue
            path = _source_path(source, root=root, raw_dir=raw_dir)
            payload = cache.setdefault(source_id, {"path": path, "exists": path.exists()}).setdefault(
                "json", load_json_file(path, {}) if path.exists() else {}
            )
            passed, failure = _json_assertion(payload, assertion)
            if not passed:
                failures.append(f"{source_id}:{failure}")

        results.append(
            {
                "name": str(row["id"]),
                "passed": not failures,
                "details": {"sources": sorted(set(used_sources)), "failures": failures},
            }
        )
    return results
