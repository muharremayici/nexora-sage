from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.evidence_status import normalize_evidence_status
from tools.core.json_io import load_json_file


LAYER_MATRIX_PATH = CODE_MAPS_DIR / "config" / "layer_release_matrix_contract.json"


def _layer_artifacts() -> list[tuple[str, Path]]:
    contract = load_json_file(LAYER_MATRIX_PATH, {})
    validation = contract.get("validation", {}) if isinstance(contract, dict) and isinstance(contract.get("validation"), dict) else {}
    artifact_ids = validation.get("pipeline_chain_required_artifacts", [])
    if not isinstance(artifact_ids, list):
        artifact_ids = []
    rows: list[tuple[str, Path]] = []
    for artifact_id in artifact_ids:
        name = str(artifact_id or "").strip()
        if not name:
            continue
        layer_name = name[:-5] if name.endswith(".json") else name
        if layer_name.endswith("_validation"):
            layer_name = layer_name[: -len("_validation")]
        rows.append((layer_name, RAW_DIR / f"{name.removesuffix('.json')}.json"))
    return rows


def _artifact_passed(payload: Any) -> tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, "payload is not an object"
    verdict = normalize_evidence_status(payload)
    if verdict.get("passed") is not True:
        return False, f"status={verdict.get('status')} source={verdict.get('source')}"
    return True, f"status=PASS source={verdict.get('source')}"


def _check_layer(name: str, path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "name": name,
            "passed": False,
            "details": f"missing artifact: {path.relative_to(CODE_MAPS_DIR).as_posix()}",
            "artifact": path.relative_to(CODE_MAPS_DIR).as_posix(),
        }
    payload = load_json_file(path, {})
    passed, details = _artifact_passed(payload)
    return {
        "name": name,
        "passed": passed,
        "details": details,
        "artifact": path.relative_to(CODE_MAPS_DIR).as_posix(),
    }


def run_validation() -> dict[str, Any]:
    layer_artifacts = _layer_artifacts()
    checks = [
        {
            "name": "layer_matrix_declares_pipeline_chain_required_artifacts",
            "passed": bool(layer_artifacts),
            "details": f"declared_artifacts={len(layer_artifacts)}",
            "artifact": "config/layer_release_matrix_contract.json",
        }
    ]
    checks.extend(_check_layer(name, path) for name, path in layer_artifacts)
    failed = [check for check in checks if not check["passed"]]
    payload = {
        "meta": {"kind": "pipeline_layer_chain_integrity_validation", "version": "v1"},
        "summary": {
            "total_checks": len(checks),
            "passed_checks": sum(1 for check in checks if check["passed"]),
            "failed_checks": len(failed),
            "weakest_link_status": "PASS" if not failed else "FAIL",
        },
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "pipeline_layer_chain_integrity_validation.json", payload)

    lines = [
        "# Pipeline Layer Chain Integrity Validation",
        "",
        f"- weakest_link_status: `{payload['summary']['weakest_link_status']}`",
        f"- total_checks: `{payload['summary']['total_checks']}`",
        f"- passed_checks: `{payload['summary']['passed_checks']}`",
        f"- failed_checks: `{payload['summary']['failed_checks']}`",
        "",
        "| Layer | Result | Details | Artifact |",
        "|---|---|---|---|",
    ]
    for check in checks:
        lines.append(
            f"| `{check['name']}` | {'PASS' if check['passed'] else 'FAIL'} | "
            f"{check['details']} | `{check['artifact']}` |"
        )
    save_text_atomic(REPORTS_DIR / "pipeline_layer_chain_integrity_validation.md", "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload["summary"]["failed_checks"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
