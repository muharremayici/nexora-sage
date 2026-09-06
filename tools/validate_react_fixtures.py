from __future__ import annotations

import json
import shutil
import sys
from contextlib import ExitStack, contextmanager
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from datetime import datetime
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
CODE_MAPS_DIR = TOOLS_DIR.parent
if str(CODE_MAPS_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_MAPS_DIR))

from tools.core.config import CONFIG_DIR, OUTPUT_DIR, RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.evidence_status import evidence_passed
from tools.core.json_io import load_json_file, load_json_object_strict

FIXTURE_CONFIG_PATH = CONFIG_DIR / "react_fixture_matrix.json"


def _bounded_fixture_config() -> dict[str, Any]:
    config = load_json_object_strict(FIXTURE_CONFIG_PATH, label="React fixture matrix")
    fixture = config.get("bounded_fixture")
    if not isinstance(fixture, dict) or not all(fixture.get(key) for key in
            ("project", "file", "source", "decision", "artifacts", "claim_boundary")):
        raise ValueError("React bounded fixture inputs are missing")
    artifacts = fixture["artifacts"]
    if not isinstance(artifacts, list) or any(
        not isinstance(name, str) or not name.endswith(".json")
        or "/" in name or "\\" in name or ":" in name or ".." in name
        for name in artifacts
    ):
        raise ValueError("React fixture artifacts must be plain JSON filenames")
    return fixture


def react_proof_artifact_path(name: str, live_root: Path, fixture_root: Path | None) -> Path:
    if name in _bounded_fixture_config()["artifacts"]:
        if fixture_root is None:
            raise ValueError(f"Bounded React fixture was not produced: {name}")
        return fixture_root / name
    return live_root / name


@contextmanager
def bounded_react_fixture():
    """Run real producers against explicit fixture inputs, never the live Atlas.

    Patches bind acquisition and output boundaries only. Analysis, serialization,
    evidence calibration and task-pack generation remain production code.
    This synchronous validator harness must not be shared with a running pipeline.
    """
    from tools.engines import react_ecosystem_analyzer as ecosystem
    from tools.engines import react_runtime_intelligence as runtime
    from tools.engines import react_compiler_readiness as compiler
    from tools.engines import next_boundary_analyzer as boundary
    from tools.engines import ai_task_pack_generator as taskpacks

    fixture = _bounded_fixture_config()
    atlas = {fixture["project"]: {"files": {fixture["file"]: {}}}}
    with TemporaryDirectory(prefix="sage-react-proof-") as temporary, ExitStack() as stack:
        root = Path(temporary)
        raw = root / "raw"
        for module in (ecosystem, runtime, compiler, boundary, taskpacks):
            stack.enter_context(patch.object(module, "RAW_DIR", raw))
            stack.enter_context(patch.object(module, "REPORTS_DIR", root / "reports"))
            if hasattr(module, "_POLICY_CACHE"):
                stack.enter_context(patch.object(module, "_POLICY_CACHE", None))
        for module in (ecosystem, runtime, boundary):
            stack.enter_context(patch.object(module, "load_atlas_data", return_value=atlas))
            stack.enter_context(patch.object(module, "project_runtime_atlas", side_effect=lambda value: (value, {})))
            stack.enter_context(patch.object(module, "_read_project_file", return_value=fixture["source"]))
        stack.enter_context(patch.object(ecosystem, "EngineProgress", Mock()))
        stack.enter_context(patch.object(taskpacks, "TASKPACK_DIR", root / "taskpacks"))
        ecosystem.run_react_ecosystem_analyzer()
        runtime.run_react_runtime_intelligence()
        compiler.run_react_compiler_readiness()
        boundary.run_next_boundary_analyzer()
        # This is a declared upstream scenario, not a proven cockpit decision.
        save_json_atomic(raw / "merge_decision_cockpit.json", {"decisions": [fixture["decision"]]})
        taskpacks.run_ai_task_pack_generator()
        missing = [name for name in fixture["artifacts"] if not (raw / name).is_file()]
        if missing:
            raise ValueError(f"React fixture producer omitted artifacts: {missing}")
        yield raw


def _path_exists(payload: Any, dotted_path: str) -> bool:
    current = payload
    for part in dotted_path.split("."):
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return False
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return False
    return current is not None
def _normalize_path(path_value: str | None) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value)
    if path.is_absolute():
        return path
    return (CODE_MAPS_DIR / path).resolve()


def _ratio(summary: dict[str, Any]) -> float:
    present = int(summary.get("repo_present", 0) or 0)
    detected = int(summary.get("detected", 0) or 0)
    return round((detected / present), 3) if present else 0.0


def _fixture_validation_passed(payload: Any) -> bool:
    return evidence_passed(payload, default=False)


def _rehydrate_fixture_from_source(fixture: dict[str, Any], artifact_root: Path) -> str | None:
    source_root = _normalize_path(fixture.get("source_root"))
    if source_root is None:
        return None
    src_matrix = source_root / "react_support_matrix.json"
    src_validation = source_root / "react_support_validation.json"
    if not src_matrix.exists() or not src_validation.exists():
        return None

    artifact_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_matrix, artifact_root / "react_support_matrix.json")
    shutil.copy2(src_validation, artifact_root / "react_support_validation.json")
    manifest = {
        "fixture_id": fixture.get("id"),
        "rehydrated_at": datetime.now().isoformat(timespec="seconds"),
        "source_root": str(source_root),
        "artifacts": ["react_support_matrix.json", "react_support_validation.json"],
        "reason": "validator_auto_rehydrate",
    }
    save_text_atomic(artifact_root / "fixture_ingest_manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    return str(source_root)


def _react_evidence_contract_check(fixture_root: Path | None = None) -> dict[str, Any]:
    artifact_names = [
        "react_ecosystem_analysis.json",
        "react_runtime_intelligence.json",
        "react_frontier_intelligence.json",
    ]
    inspected = []
    missing = []
    for artifact_name in artifact_names:
        path = react_proof_artifact_path(artifact_name, RAW_DIR, fixture_root)
        if not path.exists():
            missing.append({"artifact": artifact_name, "reason": "missing"})
            continue
        payload = load_json_file(path, {})
        findings = payload.get("findings", []) if isinstance(payload, dict) else []
        if not findings:
            inspected.append({"artifact": artifact_name, "findings": 0, "status": "no_findings"})
            continue
        sample = findings[: min(25, len(findings))]
        missing_fields = [
            {
                "artifact": artifact_name,
                "index": index,
                "missing": [
                    field
                    for field in ("evidence_ladder", "evidence_kinds", "runtime_proof_status", "evidence_spans")
                    if field not in item
                ],
            }
            for index, item in enumerate(sample)
            if isinstance(item, dict)
            and any(field not in item for field in ("evidence_ladder", "evidence_kinds", "runtime_proof_status", "evidence_spans"))
        ]
        if missing_fields:
            missing.extend(missing_fields)
        inspected.append({"artifact": artifact_name, "findings": len(findings), "sampled": len(sample)})
    return {
        "passed": not missing,
        "inspected": inspected,
        "missing": missing[:20],
    }


def _artifact_contract_checks(config: dict[str, Any], fixture_root: Path | None = None) -> list[dict[str, Any]]:
    checks = []
    for item in config.get("artifact_contract_checks", []) if isinstance(config, dict) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("artifact") or "artifact_contract")
        artifact = str(item.get("artifact") or "")
        path = react_proof_artifact_path(artifact, RAW_DIR, fixture_root)
        if not artifact or not path.exists():
            checks.append({"name": name, "passed": False, "details": f"missing artifact {artifact}"})
            continue
        payload = load_json_file(path, {})
        missing = [field for field in item.get("required_paths", []) if not _path_exists(payload, str(field))]
        checks.append(
            {
                "name": name,
                "passed": not missing,
                "details": {"artifact": artifact, "missing_paths": missing},
            }
        )
    return checks


def run_validation() -> dict[str, Any]:
    with bounded_react_fixture() as fixture_root:
        return _run_validation(fixture_root)


def _run_validation(fixture_root: Path) -> dict[str, Any]:
    config = load_json_file(FIXTURE_CONFIG_PATH, {})
    fixtures = config.get("fixtures", []) if isinstance(config, dict) else []
    min_ratio = float(config.get("min_detected_present_ratio", 1.0) or 1.0)
    max_partial = int(config.get("max_partial_capabilities", 0) or 0)

    rows: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []

    for fixture in fixtures:
        if not isinstance(fixture, dict):
            continue
        fixture_id = str(fixture.get("id") or "unknown")
        label = str(fixture.get("label") or fixture_id)
        enabled = bool(fixture.get("enabled", True))
        required = bool(fixture.get("required", False))
        artifact_root = _normalize_path(fixture.get("artifact_root"))
        if artifact_root is None:
            artifact_root = (OUTPUT_DIR / ".fixtures" / fixture_id).resolve()
        matrix_path = artifact_root / "react_support_matrix.json"
        validation_path = artifact_root / "react_support_validation.json"
        rehydrated_from = None
        if enabled and not matrix_path.exists():
            rehydrated_from = _rehydrate_fixture_from_source(fixture, artifact_root)

        status = "pending"
        details = ""
        ratio = 0.0
        partial = 0

        if not enabled:
            status = "disabled"
            details = "fixture disabled"
        elif not matrix_path.exists():
            status = "missing"
            details = f"missing {matrix_path}"
        else:
            matrix_payload = load_json_file(matrix_path, {})
            summary = matrix_payload.get("summary", {}) if isinstance(matrix_payload, dict) else {}
            ratio = _ratio(summary if isinstance(summary, dict) else {})
            partial = int((summary or {}).get("partial", 0) or 0)
            validation_payload = load_json_file(validation_path, {}) if validation_path.exists() else {}
            validation_summary = (
                (validation_payload.get("summary") or {}) if isinstance(validation_payload, dict) else {}
            )
            if not validation_path.exists():
                status = "missing"
                details = f"missing {validation_path}"
            elif not _fixture_validation_passed(validation_payload):
                status = "fail"
                details = f"react_support_validation is not authoritative: summary={validation_summary}"
            elif ratio < min_ratio or partial > max_partial:
                status = "fail"
                details = f"ratio={ratio:.3f} partial={partial} expected ratio>={min_ratio:.3f} partial<={max_partial}"
            else:
                status = "pass"
                details = f"ratio={ratio:.3f} partial={partial}"
                if rehydrated_from:
                    details += f" rehydrated_from={rehydrated_from}"

        enforce_failure = required and enabled and status in {"missing", "fail"}
        checks.append(
            {
                "name": f"fixture_{fixture_id}",
                "passed": not enforce_failure,
                "details": details,
            }
        )
        rows.append(
            {
                "id": fixture_id,
                "label": label,
                "required": required,
                "enabled": enabled,
                "artifact_root": str(artifact_root),
                "status": status,
                "ratio": ratio,
                "partial": partial,
                "details": details,
            }
        )

    evidence_check = _react_evidence_contract_check(fixture_root)
    checks.append(
        {
            "name": "react_evidence_ladder_contract",
            "passed": evidence_check["passed"],
            "details": evidence_check,
        }
    )
    checks.extend(_artifact_contract_checks(config if isinstance(config, dict) else {}, fixture_root))

    payload = {
        "fixture_evidence": _bounded_fixture_config(),
        "summary": {
            "total_checks": len(checks),
            "passed_checks": sum(1 for c in checks if c.get("passed")),
            "failed_checks": sum(1 for c in checks if not c.get("passed")),
        },
        "policy": {
            "min_detected_present_ratio": min_ratio,
            "max_partial_capabilities": max_partial,
        },
        "rows": rows,
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "react_fixture_matrix_validation.json", payload)

    lines = [
        "# React Fixture Matrix Validation",
        "",
        f"- Min detected/present ratio: `{min_ratio:.3f}`",
        f"- Max partial capabilities: `{max_partial}`",
        f"- Total checks: `{payload['summary']['total_checks']}`",
        f"- Passed: `{payload['summary']['passed_checks']}`",
        f"- Failed: `{payload['summary']['failed_checks']}`",
        "",
        "| Fixture | Required | Status | Ratio | Partial | Details |",
        "|---|---|---|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            f"| `{row['id']}` | {'YES' if row['required'] else 'NO'} | `{row['status']}` | {row['ratio']:.3f} | {row['partial']} | {row['details']} |"
        )
    save_text_atomic(REPORTS_DIR / "react_fixture_matrix_validation.md", "\n".join(lines) + "\n")
    return payload


def main() -> int:
    payload = run_validation()
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("failed_checks", 1) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())


