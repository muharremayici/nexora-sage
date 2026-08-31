from __future__ import annotations

import json
import sys
from fnmatch import fnmatch
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.release_proof_steps import load_release_proof_command_names, load_release_proof_step_ids, load_release_proof_steps


REGISTRY_PATH = ROOT / "config" / "governance_registry.json"
CONTRIBUTOR_EXTENSION_CONTRACT_PATH = ROOT / "config" / "contributor_extension_contract.json"
SCHEMA_PATH = ROOT / "config" / "schemas" / "governance_registry.schema.json"
RAW_OUTPUT_PATH = RAW_DIR / "governance_registry_validation.json"
REPORT_OUTPUT_PATH = REPORTS_DIR / "governance_registry_validation.md"

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _check(name: str, passed: bool, details: Any) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value if str(item)}


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _path_matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch(path, pattern) for pattern in patterns)


def _is_prefix_excluded(path: str, prefixes: set[str]) -> bool:
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in prefixes)


def _contributor_owned_paths() -> tuple[set[str], list[str]]:
    contract = _load_json(CONTRIBUTOR_EXTENSION_CONTRACT_PATH)
    surfaces = contract.get("extension_surfaces", []) if isinstance(contract.get("extension_surfaces"), list) else []
    exact_paths: set[str] = set()
    patterns: list[str] = []
    for row in surfaces:
        if not isinstance(row, dict):
            continue
        for field in ("registry_or_manifest", "canonical_location"):
            value = str(row.get(field) or "").replace("\\", "/").strip()
            if not value:
                continue
            if "*" in value:
                patterns.append(value)
            elif value.endswith("/"):
                patterns.append(f"{value}**/*.json")
                patterns.append(f"{value}*.json")
            elif value.endswith(".json"):
                exact_paths.add(value)
    return exact_paths, patterns


def _governance_like_config_inventory(
    registry: dict[str, Any],
    surface_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    validation = registry.get("validation", {}) if isinstance(registry.get("validation"), dict) else {}
    discovery = (
        validation.get("governance_like_config_discovery", {})
        if isinstance(validation.get("governance_like_config_discovery"), dict)
        else {}
    )
    include_root = str(discovery.get("include_root") or "config").strip("/")
    suffixes = {str(item).lower() for item in discovery.get("include_suffixes", []) if str(item)}
    prefixes = {str(item).replace("\\", "/").strip("/") for item in discovery.get("exclude_path_prefixes", []) if str(item)}
    tokens = [str(item).lower() for item in discovery.get("candidate_path_tokens", []) if str(item)]
    owner_patterns: list[dict[str, Any]] = []
    for row in discovery.get("owner_patterns", []) if isinstance(discovery.get("owner_patterns"), list) else []:
        if isinstance(row, dict):
            owner_patterns.append(row)

    registered_sources = {str(row.get("source") or "").replace("\\", "/") for row in surface_rows if row.get("source")}
    contributor_exact, contributor_patterns = _contributor_owned_paths()

    candidates: list[dict[str, Any]] = []
    unowned: list[dict[str, Any]] = []
    root_path = ROOT / include_root
    if not root_path.exists():
        return {
            "enabled": bool(discovery),
            "candidates": [],
            "unowned": [{"path": include_root, "reason": "include_root_missing"}],
            "owner_pattern_count": len(owner_patterns),
        }

    for path in sorted(root_path.rglob("*")):
        if not path.is_file() or (suffixes and path.suffix.lower() not in suffixes):
            continue
        rel_path = _rel(path)
        if _is_prefix_excluded(rel_path, prefixes):
            continue
        if tokens and not any(token in rel_path.lower() for token in tokens):
            continue

        owner = ""
        owner_basis = ""
        if rel_path in registered_sources:
            owner = "governance_surface"
            owner_basis = "governance_registry.source"
        elif rel_path in contributor_exact or _path_matches_any(rel_path, contributor_patterns):
            owner = "contributor_extension_contract"
            owner_basis = "contributor_extension_contract.registry_or_manifest_or_canonical_location"
        else:
            for pattern_row in owner_patterns:
                patterns = [str(item) for item in pattern_row.get("patterns", []) if str(item)]
                if _path_matches_any(rel_path, patterns):
                    owner = str(pattern_row.get("owner") or "owner_pattern")
                    owner_basis = "governance_like_config_discovery.owner_patterns"
                    break

        candidate = {"path": rel_path, "owner": owner, "owner_basis": owner_basis}
        candidates.append(candidate)
        if not owner:
            unowned.append({"path": rel_path, "reason": "governance_like_config_without_declared_owner"})

    return {
        "enabled": bool(discovery),
        "candidates": candidates,
        "unowned": unowned,
        "owner_pattern_count": len(owner_patterns),
        "contributor_exact_paths": sorted(contributor_exact),
        "contributor_patterns": sorted(contributor_patterns),
    }


def build_validation() -> dict[str, Any]:
    registry = _load_json(REGISTRY_PATH)
    release_proof_step_ids = load_release_proof_step_ids()
    release_proof_command_names = load_release_proof_command_names()
    release_proof_steps = {str(step.get("id") or ""): step for step in load_release_proof_steps() if step.get("id")}
    validation = registry.get("validation", {}) if isinstance(registry.get("validation"), dict) else {}
    required_surface_fields = _string_set(validation.get("required_surface_fields"))
    required_surface_ids = _string_set(validation.get("required_surface_ids"))
    valid_seal_scope_kinds = _string_set(validation.get("seal_scope_kinds"))
    expected_seal_scope_kind_by_surface = (
        validation.get("expected_seal_scope_kind_by_surface", {})
        if isinstance(validation.get("expected_seal_scope_kind_by_surface"), dict)
        else {}
    )
    surfaces = registry.get("governance_surfaces", []) if isinstance(registry, dict) else []
    surface_rows = [row for row in surfaces if isinstance(row, dict)] if isinstance(surfaces, list) else []
    surface_ids = [str(row.get("id") or "") for row in surface_rows]
    duplicate_ids = sorted(item for item in set(surface_ids) if item and surface_ids.count(item) > 1)
    governance_like_inventory = _governance_like_config_inventory(registry, surface_rows)

    missing_fields: list[dict[str, Any]] = []
    missing_files: list[dict[str, str]] = []
    unregistered_validators: list[dict[str, str]] = []
    unregistered_steps: list[dict[str, str]] = []
    validator_step_mismatches: list[dict[str, str]] = []
    invalid_boundaries: list[dict[str, str]] = []
    invalid_seal_scopes: list[dict[str, str]] = []

    for row in surface_rows:
        surface_id = str(row.get("id") or "<missing-id>")
        missing = sorted(field for field in required_surface_fields if not row.get(field))
        if missing:
            missing_fields.append({"surface": surface_id, "missing": missing})

        for field in ("source", "validator"):
            rel = str(row.get(field) or "")
            if rel and "*" not in rel:
                path = ROOT / rel
                if not path.exists():
                    missing_files.append({"surface": surface_id, "field": field, "path": rel})

        validator = str(row.get("validator") or "")
        if validator and Path(validator).name not in release_proof_command_names:
            unregistered_validators.append({"surface": surface_id, "validator": validator})

        step = str(row.get("release_proof_step") or "")
        if step and step not in release_proof_step_ids:
            unregistered_steps.append({"surface": surface_id, "release_proof_step": step})
        elif step and validator:
            command = release_proof_steps.get(step, {}).get("command", [])
            command_names = {Path(str(part)).name for part in command if str(part).strip()}
            if Path(validator).name not in command_names:
                validator_step_mismatches.append(
                    {
                        "surface": surface_id,
                        "validator": validator,
                        "release_proof_step": step,
                        "release_proof_command": " ".join(str(part) for part in command),
                    }
                )

        claim_impact = str(row.get("claim_impact") or "")
        human_seal_policy = str(row.get("human_seal_policy") or "")
        if "claim" in claim_impact and "hard" not in human_seal_policy:
            invalid_boundaries.append(
                {
                    "surface": surface_id,
                    "reason": "claim-affecting surface must declare a hard human seal path",
                }
            )
        seal_scope_kind = str(row.get("seal_scope_kind") or "")
        expected_seal_scope_kind = str(expected_seal_scope_kind_by_surface.get(surface_id) or "")
        if seal_scope_kind not in valid_seal_scope_kinds:
            invalid_seal_scopes.append(
                {
                    "surface": surface_id,
                    "seal_scope_kind": seal_scope_kind,
                    "reason": "unknown_or_missing_seal_scope_kind",
                }
            )
        elif expected_seal_scope_kind and seal_scope_kind != expected_seal_scope_kind:
            invalid_seal_scopes.append(
                {
                    "surface": surface_id,
                    "seal_scope_kind": seal_scope_kind,
                    "expected": expected_seal_scope_kind,
                    "reason": "seal_scope_kind_does_not_match_surface_boundary",
                }
            )

    checks = [
        _check(
            "governance_registry_exists_and_has_known_kind",
            REGISTRY_PATH.exists() and registry.get("_meta", {}).get("kind") == "nexora.governance_registry",
            {"path": "config/governance_registry.json", "kind": registry.get("_meta", {}).get("kind")},
        ),
        _check(
            "governance_registry_has_explicit_schema",
            SCHEMA_PATH.exists() and "nexora.governance_registry" in _load_json(SCHEMA_PATH).get("properties", {}).get("_meta", {}).get("properties", {}).get("kind", {}).get("const", ""),
            {"schema": "config/schemas/governance_registry.schema.json"},
        ),
        _check(
            "governance_registry_declares_required_surfaces",
            required_surface_ids.issubset(set(surface_ids)) and not duplicate_ids,
            {"required": sorted(required_surface_ids), "present": sorted(surface_ids), "duplicates": duplicate_ids},
        ),
        _check(
            "governance_registry_validation_contract_declared",
            bool(required_surface_fields)
            and bool(required_surface_ids)
            and bool(valid_seal_scope_kinds)
            and bool(expected_seal_scope_kind_by_surface),
            {
                "required_surface_fields": sorted(required_surface_fields),
                "required_surface_ids": sorted(required_surface_ids),
                "seal_scope_kinds": sorted(valid_seal_scope_kinds),
                "expected_seal_scope_surfaces": sorted(expected_seal_scope_kind_by_surface),
            },
        ),
        _check("governance_surfaces_have_required_fields", not missing_fields, missing_fields),
        _check("governance_surface_sources_and_validators_exist", not missing_files, missing_files),
        _check("governance_surface_validators_are_release_proof_steps", not unregistered_validators, unregistered_validators),
        _check("governance_surface_release_steps_are_registered", not unregistered_steps, unregistered_steps),
        _check(
            "governance_surface_validator_matches_declared_release_step",
            not validator_step_mismatches,
            validator_step_mismatches,
        ),
        _check("claim_affecting_surfaces_declare_hard_hitl_path", not invalid_boundaries, invalid_boundaries),
        _check(
            "human_seal_scope_kinds_keep_sage_self_target_repo_and_agent_surfaces_separate",
            not invalid_seal_scopes,
            invalid_seal_scopes,
        ),
        _check(
            "doctrine_and_principle_boundaries_remain_separate",
            any(row.get("id") == "doctrine_registry" and "binding" in str(row.get("binding_level")) for row in surface_rows)
            and any(row.get("id") == "principle_registry" and str(row.get("binding_level")) == "advisory_only" for row in surface_rows),
            {"doctrine_and_principle_surface_ids": ["doctrine_registry", "principle_registry"]},
        ),
        _check(
            "governance_like_config_files_have_declared_owner",
            governance_like_inventory["enabled"] and not governance_like_inventory["unowned"],
            {
                "candidate_count": len(governance_like_inventory["candidates"]),
                "unowned": governance_like_inventory["unowned"][:50],
                "owner_pattern_count": governance_like_inventory["owner_pattern_count"],
            },
        ),
    ]
    failures = [row for row in checks if not row["passed"]]
    payload = {
        "meta": {
            "kind": "governance_registry_validation",
            "version": "1.0.0",
            "generated_at": _utc_now(),
            "generator": "tools.validate_governance_registry",
            "source": "config/governance_registry.json",
        },
        "summary": {
            "status": "PASS" if not failures else "FAIL",
            "surfaces": len(surface_rows),
            "checks": len(checks),
            "passed": sum(1 for row in checks if row["passed"]),
            "failed": len(failures),
            "seal_scope_kinds": sorted({str(row.get("seal_scope_kind") or "") for row in surface_rows if row.get("seal_scope_kind")}),
        },
        "checks": checks,
        "governance_like_config_inventory": governance_like_inventory,
    }
    return payload


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Governance Registry Validation",
        "",
        f"- status: `{summary.get('status')}`",
        f"- surfaces: `{summary.get('surfaces')}`",
        f"- checks: `{summary.get('checks')}`",
        f"- failed: `{summary.get('failed')}`",
        "",
        "## Checks",
        "",
        "| Check | Result | Details |",
        "|---|---|---|",
    ]
    for check in payload.get("checks", []) or []:
        details = check.get("details")
        if not isinstance(details, str):
            details = json.dumps(details, ensure_ascii=False)
        escaped_details = details.replace("|", "\\|")
        lines.append(f"| `{check.get('name')}` | {'PASS' if check.get('passed') else 'FAIL'} | {escaped_details} |")
    return "\n".join(lines) + "\n"


def run() -> dict[str, Any]:
    payload = build_validation()
    save_json_atomic(RAW_OUTPUT_PATH, payload)
    save_text_atomic(REPORT_OUTPUT_PATH, render_report(payload))
    return payload


def main() -> int:
    payload = run()
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
