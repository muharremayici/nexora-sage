"""Validate declarative source-layer classification extensions and their live use."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import CONFIG_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.source_layer_classifier import classify_source_layer
from tools.core.source_layer_classification_policy import load_source_layer_classification_policy
from tools.core.source_layer_taxonomy import source_layer_order


RAW_PATH = RAW_DIR / "source_layer_classification_policy_validation.json"
REPORT_PATH = REPORTS_DIR / "source_layer_classification_policy_validation.md"


def _check(check_id: str, ok: bool, details: Any = None) -> dict[str, Any]:
    row = {"id": check_id, "ok": ok}
    if details is not None:
        row["details"] = details
    return row


def run() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    try:
        payload = load_source_layer_classification_policy()
        validation = payload.get("validation") if isinstance(payload.get("validation"), dict) else {}
        rules = payload.get("exact_path_rules") if isinstance(payload.get("exact_path_rules"), list) else []
        known_layers = set(source_layer_order())
        disallowed = {str(value) for value in validation.get("disallowed_layer_ids", []) if str(value)}
        paths = [str(row.get("path") or "") for row in rules if isinstance(row, dict)]
        structural_rows = [row for row in rules if isinstance(row, dict)]
        checks.extend(
            [
                _check("known_kind", payload.get("meta", {}).get("kind") == "source_layer_classification_policy"),
                _check("rules_are_object_rows", len(structural_rows) == len(rules)),
                _check("paths_are_unique", len(paths) == len(set(paths)), paths),
                _check("paths_are_relative_posix", all(path and "\\" not in path and not path.startswith("/") and ".." not in Path(path).parts for path in paths)),
                _check("layers_are_known_and_not_disallowed", all(str(row.get("layer") or "") in known_layers and str(row.get("layer") or "") not in disallowed for row in structural_rows)),
                _check("rules_have_reasons", all(str(row.get("reason") or "").strip() for row in structural_rows)),
                _check("rule_paths_exist", all((ROOT / path).is_file() for path in paths) if validation.get("require_existing_source_path") else True),
                _check("shared_classifier_consumes_each_rule", all(classify_source_layer(ROOT / path, root=ROOT)[0] == str(row.get("layer") or "") for path, row in zip(paths, structural_rows))),
            ]
        )
    except Exception as exc:
        checks.append(_check("policy_readable", False, str(exc)))
    failed = [row["id"] for row in checks if not row.get("ok")]
    return {"validator": "source_layer_classification_policy", "status": "PASS" if not failed else "FAIL", "checks": checks, "failed_checks": failed}


if __name__ == "__main__":
    result = run()
    save_json_atomic(RAW_PATH, result)
    save_text_atomic(REPORT_PATH, "# Source Layer Classification Policy Validation\n\n" + "\n".join(f"- {'PASS' if row.get('ok') else 'FAIL'}: `{row.get('id')}`" for row in result["checks"]) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] == "PASS" else 1)
