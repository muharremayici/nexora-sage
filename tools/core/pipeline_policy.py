from __future__ import annotations

from copy import deepcopy

from tools.core.config import ARCH_CONFIG, CONFIG_DIR, DYNAMIC_CONFIG
from tools.core.json_io import load_json_object_strict_cached


AUDIT_POLICY_FILE = CONFIG_DIR / "audit_policy.json"


def get_audit_policy():
    return load_json_object_strict_cached(AUDIT_POLICY_FILE, label="Audit policy")


def get_audit_config():
    return DYNAMIC_CONFIG.get("audit", {})


def get_audit_loc_limits():
    audit_cfg = get_audit_config()
    loc_cfg = audit_cfg.get("loc_limits", {})
    default_limits = get_audit_policy().get("loc_limits", {})
    return {
        key: int(loc_cfg.get(key, default_value))
        for key, default_value in default_limits.items()
        if isinstance(default_value, (int, float))
    }


def get_loc_finding_contract():
    contract = get_audit_policy().get("loc_finding_contract")
    if not isinstance(contract, dict):
        raise ValueError("Audit policy loc_finding_contract must be an object.")
    generic_rule = str(contract.get("generic_rule") or "").strip()
    kind_rule_map = contract.get("kind_rule_map")
    required_fields = contract.get("required_evidence_fields")
    if not generic_rule or not isinstance(kind_rule_map, dict) or not isinstance(required_fields, list):
        raise ValueError("Audit policy loc_finding_contract is incomplete.")
    return {
        "generic_rule": generic_rule,
        "kind_rule_map": {
            str(kind).strip().lower(): str(rule).strip()
            for kind, rule in kind_rule_map.items()
            if str(kind).strip() and str(rule).strip()
        },
        "required_evidence_fields": [str(field).strip() for field in required_fields if str(field).strip()],
    }


def resolve_loc_finding_semantics(
    symbol_kind: str,
    observed_loc: int,
    *,
    symbol_name: str = "unknown",
    start_line: int | None = None,
    end_line: int | None = None,
):
    kind = str(symbol_kind or "unknown").strip().lower() or "unknown"
    limits = get_audit_loc_limits()
    threshold_kind = kind if kind in limits else "default"
    limit = int(limits[threshold_kind])
    contract = get_loc_finding_contract()
    finding = {
        "rule": contract["kind_rule_map"].get(kind, contract["generic_rule"]),
        "symbol_name": str(symbol_name or "unknown"),
        "symbol_kind": kind,
        "threshold_kind": threshold_kind,
        "start_line": int(start_line) if start_line is not None else None,
        "end_line": int(end_line) if end_line is not None else None,
        "observed_loc": int(observed_loc),
        "limit": limit,
    }
    missing_fields = [field for field in contract["required_evidence_fields"] if field not in finding]
    if missing_fields:
        raise ValueError(f"LOC finding contract fields are not produced: {missing_fields}")
    return finding


def get_api_entry_filenames():
    from tools.core.config import DOCTRINE

    configured = (
        DOCTRINE.get("architectural_integrity_rules", {})
        .get("api_entry_filenames")
    )
    if isinstance(configured, list) and configured:
        return [str(item) for item in configured if str(item or "").strip()]

    configured_policy = get_audit_policy().get("api_entry_filenames", [])
    return [str(item) for item in configured_policy if str(item or "").strip()]


def get_audit_report_sections():
    audit_cfg = get_audit_config()
    profile = ARCH_CONFIG.get("detected_profile", "MODULAR_FLAT")
    policy = get_audit_policy()

    configured_sections = audit_cfg.get("report_sections")
    sections = configured_sections if configured_sections else policy.get("report_sections", [])
    sections = deepcopy(sections)

    for override in policy.get("profile_report_section_overrides", {}).get(profile, []):
        try:
            replace_index = int(override.get("replace_index"))
            section = override.get("section")
        except (TypeError, ValueError):
            continue
        if isinstance(section, dict) and 0 <= replace_index < len(sections):
            sections[replace_index] = section

    return sections


def get_quality_gates():
    return DYNAMIC_CONFIG.get("quality_gates", {})


def get_module_root_name():
    from tools.core.fractal_policy import get_module_container
    return get_module_container()
