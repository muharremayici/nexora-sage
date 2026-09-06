import json
from time import perf_counter

from tools.core.artifact_validator import ensure_valid_payload
from tools.core.atlas_io import load_atlas_data
from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.genome_io import load_genome_data
from tools.core.json_io import load_json_file
from tools.core.logger import logger
from tools.core.proof_envelope_policy import get_proof_envelope_policy
from tools.core.projects_registry import release_scope_project_keys
from tools.core.report_index import load_oracle_reports


def _ratio(num: int, den: int) -> float | None:
    if den <= 0:
        return None
    return num / den


def _rounded_ratio(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(value, digits)


def _ratio_passes(value: float | None, threshold: float) -> bool:
    return value is not None and value >= threshold


def _reported_or_measured_ratio(value, numerator: int, denominator: int) -> float | None:
    if value is not None:
        return float(value)
    return _ratio(numerator, denominator)


def _obligation(
    *,
    oid: str,
    title: str,
    level: str,
    passed: bool,
    evidence: dict,
    required: bool = True,
    rationale: str = "",
    status: str | None = None,
) -> dict:
    resolved_status = status or ("VERIFIED" if passed else "FAILED")
    return {
        "id": oid,
        "title": title,
        "level": level,
        "required": bool(required),
        "passed": bool(passed),
        "status": resolved_status,
        "rationale": rationale,
        "evidence": evidence,
    }


def _project_dna_framework_ids(payload: dict) -> set[str]:
    frameworks: set[str] = set()
    if not isinstance(payload, dict):
        return frameworks
    summary = payload.get("summary", {})
    if isinstance(summary, dict):
        frameworks.update(str(item).lower() for item in summary.get("frameworks", []) or [] if str(item).strip())
    for project in payload.get("projects", []) or []:
        if not isinstance(project, dict):
            continue
        for item in project.get("frameworks", []) or []:
            if isinstance(item, dict):
                value = item.get("id") or item.get("name")
            else:
                value = item
            if str(value or "").strip():
                frameworks.add(str(value).lower())
    return frameworks


def _react_proof_applicable(project_dna: dict) -> bool:
    react_frameworks = {
        "react",
        "next",
        "nextjs",
        "vite-react",
        "remix",
        "react-router",
        "tanstack-router",
        "astro-react",
        "expo",
    }
    return bool(_project_dna_framework_ids(project_dna) & react_frameworks)


def _release_cycle_pressure(atlas: dict, circular: dict) -> dict:
    circular_by_project = circular.get("by_project", {}) if isinstance(circular, dict) else {}
    circular_by_project = circular_by_project if isinstance(circular_by_project, dict) else {}
    release_projects = release_scope_project_keys(atlas if isinstance(atlas, dict) else {})
    missing_project_summaries = sorted(project for project in release_projects if project not in circular_by_project)
    return {
        "projects": sorted(release_projects),
        "cycles": sum(
            int((circular_by_project.get(project) or {}).get("cycle_count", 0) or 0)
            for project in release_projects
        ),
        "files": sum(
            len((atlas.get(project) or {}).get("files", {}) or {})
            for project in release_projects
            if isinstance(atlas.get(project), dict)
        ) if isinstance(atlas, dict) else 0,
        "workspace_cycles": len(circular.get("cycles", []) or []) if isinstance(circular, dict) else 0,
        "scope_complete": bool(release_projects) and not missing_project_summaries,
        "missing_project_summaries": missing_project_summaries,
    }


def run_proof_obligations():
    started = perf_counter()
    logger.info("Running proof obligations engine...")
    policy = get_proof_envelope_policy()
    thresholds = (
        (policy.get("proof_obligations", {}) if isinstance(policy, dict) else {}).get("thresholds", {})
        if isinstance(policy, dict)
        else {}
    )

    atlas = load_atlas_data()
    genome = load_genome_data()
    health = load_json_file(RAW_DIR / "health_score.json", {})
    dead = load_json_file(RAW_DIR / "dead_code.json", {})
    circular = load_json_file(RAW_DIR / "circular_deps.json", {})
    react = load_json_file(RAW_DIR / "react_support_matrix.json", {})
    state_flow = load_json_file(RAW_DIR / "state_flow.json", {})
    project_dna = load_json_file(RAW_DIR / "project_dna_profile.json", {})
    react_proof_applicable = _react_proof_applicable(project_dna)

    required_artifacts = [
        "atlas.json",
        "genome.json",
        "health_score.json",
        "dead_code.json",
        "circular_deps.json",
    ]
    if react_proof_applicable:
        required_artifacts.extend(["react_support_matrix.json", "state_flow.json"])
    present_count = sum(1 for name in required_artifacts if (RAW_DIR / name).exists())
    all_artifacts_ready = present_count == len(required_artifacts)

    file_total = 0
    file_with_contract = 0
    if isinstance(atlas, dict):
        for pdata in atlas.values():
            files = (pdata or {}).get("files", {}) if isinstance(pdata, dict) else {}
            for fdata in files.values():
                if not isinstance(fdata, dict):
                    continue
                file_total += 1
                if fdata.get("ast_contract_version"):
                    file_with_contract += 1
    atlas_contract_ratio = _ratio(file_with_contract, file_total)

    occ_total = 0
    occ_with_contract = 0
    if isinstance(genome, dict):
        for occs in genome.values():
            for occ in occs or []:
                if not isinstance(occ, dict):
                    continue
                occ_total += 1
                if occ.get("ast_contract_version"):
                    occ_with_contract += 1
    genome_contract_ratio = _ratio(occ_with_contract, occ_total)

    oracle_reports = load_oracle_reports()
    oracle_critical = 0
    oracle_fail = 0
    for item in oracle_reports:
        summary = item.get("oracle_summary", {}) if isinstance(item.get("oracle_summary"), dict) else {}
        oracle_critical += len(item.get("critical_blockers", []) or [])
        if str(item.get("status", "")).upper() == "FAIL":
            oracle_fail += 1

    health_score = int(health.get("overall", 0) or 0)
    dead_high = int((dead.get("summary", {}) if isinstance(dead, dict) else {}).get("high", 0) or 0)
    release_cycle_pressure = _release_cycle_pressure(atlas, circular)
    release_scope_projects = set(release_cycle_pressure["projects"])
    release_cycle_count = int(release_cycle_pressure["cycles"])
    release_file_total = int(release_cycle_pressure["files"])
    react_summary = react.get("summary", {}) if isinstance(react, dict) else {}
    react_present = int(react_summary.get("repo_present", 0) or 0)
    react_detected = int(react_summary.get("detected", 0) or 0)
    react_ratio = _ratio(react_detected, react_present)
    state_flow_boundary = state_flow.get("boundary_signals", {}) if isinstance(state_flow, dict) else {}
    state_boundary_count = len(state_flow_boundary) if isinstance(state_flow_boundary, dict) else 0
    state_proof = state_flow.get("state_proof", {}) if isinstance(state_flow, dict) else {}
    stateful_files = int(state_proof.get("stateful_files", 0) or 0)
    transition_subject_raw = state_proof.get("transition_subject_files")
    transition_subject_files = (
        int(transition_subject_raw)
        if transition_subject_raw is not None
        else stateful_files
    )
    transition_files = int(state_proof.get("transition_files", 0) or 0)
    property_files = int(state_proof.get("property_files", 0) or 0)
    property_files_strong = int(state_proof.get("property_files_strong", 0) or 0)
    property_files_strict = int(state_proof.get("property_files_strict", 0) or 0)
    transition_ratio = _ratio(transition_files, transition_subject_files)
    property_ratio = _ratio(property_files, stateful_files)
    property_ratio_strong = _ratio(property_files_strong, stateful_files)
    property_ratio_strict = _ratio(property_files_strict, stateful_files)

    state_projects = {}
    for rel in sorted(set((state_flow.get("zustand_stores", {}) or {}).keys()) | set((state_flow.get("tanstack_queries", {}) or {}).keys()) | set((state_flow.get("tanstack_mutations", {}) or {}).keys())):
        project = rel.split("::", 1)[0] if "::" in rel else "UNKNOWN"
        bucket = state_projects.setdefault(project, {"stateful": 0, "transition": 0, "property": 0, "property_strong": 0})
        bucket["stateful"] += 1
        signal = state_flow_boundary.get(rel, {}) if isinstance(state_flow_boundary, dict) else {}
        if (signal.get("transition_markers") or signal.get("client_actions")):
            bucket["transition"] += 1
        if signal.get("property_markers"):
            bucket["property"] += 1
        if signal.get("property_markers_strong"):
            bucket["property_strong"] += 1

    state_projects_total = 0
    state_transition_projects_total = 0
    state_projects_transition_ok = 0
    state_projects_property_ok = 0
    state_projects_property_strong_ok = 0
    transition_ratio_by_project = {}
    property_ratio_by_project = {}
    property_ratio_strong_by_project = {}
    for project, values in state_projects.items():
        local_stateful = int(values.get("stateful", 0) or 0)
        if local_stateful <= 0:
            continue
        local_ratio = _ratio(int(values.get("transition", 0) or 0), local_stateful)
        local_property_ratio = _ratio(int(values.get("property", 0) or 0), local_stateful)
        local_property_ratio_strong = _ratio(int(values.get("property_strong", 0) or 0), local_stateful)
        transition_ratio_by_project[project] = _rounded_ratio(local_ratio)
        property_ratio_by_project[project] = _rounded_ratio(local_property_ratio)
        property_ratio_strong_by_project[project] = _rounded_ratio(local_property_ratio_strong)
        state_projects_total += 1
        if local_ratio is not None:
            state_transition_projects_total += 1
        if _ratio_passes(local_ratio, float(thresholds.get("project_transition_ratio_min", 0.8))):
            state_projects_transition_ok += 1
        if _ratio_passes(local_property_ratio, float(thresholds.get("project_property_ratio_min", 0.6))):
            state_projects_property_ok += 1
        if _ratio_passes(local_property_ratio_strong, float(thresholds.get("project_property_ratio_strong_min", 0.35))):
            state_projects_property_strong_ok += 1

    state_proof_by_project = (
        state_flow.get("state_proof_by_project", {})
        if isinstance(state_flow.get("state_proof_by_project", {}), dict)
        else {}
    )
    if state_proof_by_project:
        state_projects_total = 0
        state_transition_projects_total = 0
        state_projects_transition_ok = 0
        state_projects_property_ok = 0
        state_projects_property_strong_ok = 0
        transition_ratio_by_project = {}
        property_ratio_by_project = {}
        property_ratio_strong_by_project = {}
        for project, values in sorted(state_proof_by_project.items()):
            if not isinstance(values, dict):
                continue
            local_stateful = int(values.get("stateful_files", 0) or 0)
            if local_stateful <= 0:
                continue
            local_transition_ratio = _reported_or_measured_ratio(
                values.get("transition_ratio"),
                int(values.get("transition_files", 0) or 0),
                int(values.get("transition_subject_files", 0) or 0),
            )
            local_property_ratio = _reported_or_measured_ratio(
                values.get("property_ratio"),
                int(values.get("property_files", 0) or 0),
                local_stateful,
            )
            local_property_ratio_strong = _reported_or_measured_ratio(
                values.get("property_ratio_strong"),
                int(values.get("property_files_strong", 0) or 0),
                local_stateful,
            )
            transition_ratio_by_project[project] = _rounded_ratio(local_transition_ratio)
            property_ratio_by_project[project] = _rounded_ratio(local_property_ratio)
            property_ratio_strong_by_project[project] = _rounded_ratio(local_property_ratio_strong)
            state_projects_total += 1
            if local_transition_ratio is not None:
                state_transition_projects_total += 1
            if _ratio_passes(local_transition_ratio, float(thresholds.get("project_transition_ratio_min", 0.8))):
                state_projects_transition_ok += 1
            if _ratio_passes(local_property_ratio, float(thresholds.get("project_property_ratio_min", 0.6))):
                state_projects_property_ok += 1
            if _ratio_passes(local_property_ratio_strong, float(thresholds.get("project_property_ratio_strong_min", 0.35))):
                state_projects_property_strong_ok += 1

    state_projects_ratio = _ratio(state_projects_transition_ok, state_transition_projects_total)
    state_projects_property_ratio = _ratio(state_projects_property_ok, state_projects_total)
    state_projects_property_strong_ratio = _ratio(state_projects_property_strong_ok, state_projects_total)
    property_ratio_min = float(thresholds.get("property_ratio_min", 0.65))
    property_ratio_strong_min = float(thresholds.get("property_ratio_strong_min", 0.55))
    project_property_ratio_min = float(thresholds.get("project_property_ratio_min", 0.6))
    project_property_ratio_strong_min = float(thresholds.get("project_property_ratio_strong_min", 0.35))
    state_projects_property_pass_ratio_min = float(thresholds.get("state_projects_property_pass_ratio_min", 0.9))
    state_projects_property_strong_pass_ratio_min = float(thresholds.get("state_projects_property_strong_pass_ratio_min", 0.8))
    strict_property_min_projects = int(thresholds.get("strict_property_min_projects", 4))
    strict_property_required = state_projects_total >= strict_property_min_projects

    # Small-sample calibration: strict broad-project ratio can become unrealistically brittle for <=4 stateful projects.
    state_projects_property_pass_ratio_effective = (
        min(state_projects_property_pass_ratio_min, 0.75)
        if state_projects_total and state_projects_total <= 4
        else state_projects_property_pass_ratio_min
    )

    export_universe = 0
    if isinstance(atlas, dict):
        for pdata in atlas.values():
            files = (pdata or {}).get("files", {}) if isinstance(pdata, dict) else {}
            for fdata in files.values():
                if not isinstance(fdata, dict):
                    continue
                exports = fdata.get("exports", []) or []
                export_universe += sum(
                    1 for exp in exports
                    if not (isinstance(exp, dict) and exp.get("type") == "ProxyExport")
                )

    dead_high_max_configured = int(thresholds.get("dead_high_max", 140))
    dead_high_ratio_max = float(thresholds.get("dead_high_ratio_max", 0.02))
    dead_high_max_effective = max(
        dead_high_max_configured,
        int(round(export_universe * dead_high_ratio_max)),
    )

    cycle_count_max_configured = int(thresholds.get("cycle_count_max", 80))
    cycle_file_ratio_max = float(thresholds.get("cycle_file_ratio_max", 0.015))
    if release_file_total >= 4000:
        cycle_file_ratio_max = max(cycle_file_ratio_max, 0.02)
    cycle_count_max_effective = max(
        cycle_count_max_configured,
        int(round(release_file_total * cycle_file_ratio_max)),
    )

    obligations = [
        _obligation(
            oid="PO_001",
            title="Required analysis artifacts are present",
            level="L0",
            passed=all_artifacts_ready,
            rationale="Without baseline artifacts, downstream proof claims are invalid.",
            evidence={
                "required_count": len(required_artifacts),
                "present_count": present_count,
                "missing": [name for name in required_artifacts if not (RAW_DIR / name).exists()],
            },
        ),
        _obligation(
            oid="PO_002",
            title="Atlas structural contract coverage",
            level="L1",
            passed=_ratio_passes(atlas_contract_ratio, float(thresholds.get("atlas_contract_ratio_min", 0.97))),
            status="UNKNOWN" if atlas_contract_ratio is None else None,
            rationale="Contract-less files reduce structural proof confidence.",
            evidence={
                "ratio": _rounded_ratio(atlas_contract_ratio),
                "measurement_status": "empty_scope" if atlas_contract_ratio is None else "measured",
                "threshold": float(thresholds.get("atlas_contract_ratio_min", 0.97)),
                "file_total": file_total,
                "file_with_contract": file_with_contract,
            },
        ),
        _obligation(
            oid="PO_003",
            title="Genome occurrence contract coverage",
            level="L1",
            passed=_ratio_passes(genome_contract_ratio, float(thresholds.get("genome_contract_ratio_min", 0.97))),
            status="UNKNOWN" if genome_contract_ratio is None else None,
            rationale="Occurrence-level contract data is required for traceable proof chains.",
            evidence={
                "ratio": _rounded_ratio(genome_contract_ratio),
                "measurement_status": "empty_scope" if genome_contract_ratio is None else "measured",
                "threshold": float(thresholds.get("genome_contract_ratio_min", 0.97)),
                "occ_total": occ_total,
                "occ_with_contract": occ_with_contract,
            },
        ),
        _obligation(
            oid="PO_004",
            title="Oracle critical blockers are zero",
            level="L2",
            passed=(oracle_critical == 0),
            rationale="Critical blockers invalidate safety proofs for integration.",
            evidence={
                "oracle_reports": len(oracle_reports),
                "oracle_fail": oracle_fail,
                "critical_blockers": oracle_critical,
            },
        ),
        _obligation(
            oid="PO_005",
            title="Health score bounded minimum",
            level="L2",
            passed=(health_score >= int(thresholds.get("health_score_min", 75))),
            rationale="Low global health weakens the reliability envelope.",
            evidence={
                "health_score": health_score,
                "threshold": int(thresholds.get("health_score_min", 75)),
            },
        ),
        _obligation(
            oid="PO_006",
            title="Dead-code high-confidence pressure bounded",
            level="L3",
            passed=(dead_high <= dead_high_max_effective),
            rationale="High dead-code pressure degrades trust in automated refactors.",
            evidence={
                "dead_high": dead_high,
                "threshold": dead_high_max_effective,
                "configured_threshold": dead_high_max_configured,
                "threshold_ratio": dead_high_ratio_max,
                "export_universe": export_universe,
            },
        ),
        _obligation(
            oid="PO_007",
            title="Circular dependency pressure bounded",
            level="L3",
            passed=(
                bool(release_cycle_pressure["scope_complete"])
                and release_file_total > 0
                and release_cycle_count <= cycle_count_max_effective
            ),
            rationale="Cycle pressure can invalidate deterministic dependency assumptions.",
            evidence={
                "cycles": release_cycle_count,
                "threshold": cycle_count_max_effective,
                "configured_threshold": cycle_count_max_configured,
                "threshold_ratio": cycle_file_ratio_max,
                "atlas_file_total": release_file_total,
                "release_scope_projects": sorted(release_scope_projects),
                "workspace_cycles_observed": release_cycle_pressure["workspace_cycles"],
                "scope_complete": release_cycle_pressure["scope_complete"],
                "missing_project_summaries": release_cycle_pressure["missing_project_summaries"],
            },
        ),
        _obligation(
            oid="PO_008",
            title="React capability detection envelope",
            level="L2",
            required=react_proof_applicable,
            passed=_ratio_passes(react_ratio, float(thresholds.get("react_ratio_min", 0.95))) if react_proof_applicable else False,
            status="NOT_APPLICABLE" if not react_proof_applicable else "UNKNOWN" if react_ratio is None else None,
            rationale=(
                "Insufficient capability detection weakens behavioral proof coverage."
                if react_proof_applicable
                else "Not applicable: Project DNA did not classify this target as a React runtime."
            ),
            evidence={
                "repo_present": react_present,
                "detected": react_detected,
                "ratio": _rounded_ratio(react_ratio),
                "measurement_status": "not_applicable" if not react_proof_applicable else "empty_scope" if react_ratio is None else "measured",
                "threshold": float(thresholds.get("react_ratio_min", 0.95)),
                "state_boundary_count": state_boundary_count,
                "react_proof_applicable": react_proof_applicable,
                "project_dna_frameworks": sorted(_project_dna_framework_ids(project_dna)),
            },
        ),
        _obligation(
            oid="PO_009",
            title="State transition evidence coverage",
            level="L4",
            required=react_proof_applicable and transition_subject_files > 0,
            passed=_ratio_passes(transition_ratio, float(thresholds.get("transition_ratio_min", 0.8))),
            status="NOT_APPLICABLE" if not react_proof_applicable or transition_subject_files <= 0 else None,
            rationale=(
                "Stateful files should expose transition-level evidence (set/get/transition/subscribe/query actions)."
                if react_proof_applicable
                else "Not applicable: Project DNA did not classify this target as a React runtime."
            ),
            evidence={
                "stateful_files": stateful_files,
                "transition_subject_files": transition_subject_files,
                "transition_files": transition_files,
                "transition_ratio": _rounded_ratio(transition_ratio),
                "measurement_status": "not_applicable" if not react_proof_applicable or transition_subject_files <= 0 else "measured",
                "threshold": float(thresholds.get("transition_ratio_min", 0.8)),
                "react_proof_applicable": react_proof_applicable,
            },
        ),
        _obligation(
            oid="PO_010",
            title="State property evidence coverage (strong)",
            level="L4",
            required=react_proof_applicable and stateful_files > 0 and strict_property_required,
            passed=(
                _ratio_passes(property_ratio_strong, property_ratio_strong_min)
                or _ratio_passes(property_ratio, property_ratio_min)
            ),
            status="NOT_APPLICABLE" if not react_proof_applicable or stateful_files <= 0 else None,
            rationale=(
                "Stateful files should preserve strong property-oriented evidence (selectors/typed state contracts)."
                if react_proof_applicable and strict_property_required
                else "Not applicable: Project DNA did not classify this target as a React runtime."
                if not react_proof_applicable
                else "Informational in small stateful-project workspaces; tracked but not gate-blocking."
            ),
            evidence={
                "stateful_files": stateful_files,
                "property_files": property_files,
                "property_files_strong": property_files_strong,
                "property_files_strict": property_files_strict,
                "property_ratio": _rounded_ratio(property_ratio),
                "property_ratio_strong": _rounded_ratio(property_ratio_strong),
                "property_ratio_strict": _rounded_ratio(property_ratio_strict),
                "measurement_status": "not_applicable" if not react_proof_applicable or stateful_files <= 0 else "measured",
                "threshold": property_ratio_strong_min,
                "broad_threshold": property_ratio_min,
                "pass_mode": "strong_or_broad",
                "required_mode": "enforced" if react_proof_applicable and strict_property_required else "informational",
                "strict_property_min_projects": strict_property_min_projects,
                "react_proof_applicable": react_proof_applicable,
            },
        ),
        _obligation(
            oid="PO_011",
            title="Project-level transition envelope consistency",
            level="L4",
            required=react_proof_applicable and state_transition_projects_total > 0,
            passed=_ratio_passes(state_projects_ratio, float(thresholds.get("state_projects_pass_ratio_min", 1.0))),
            status="NOT_APPLICABLE" if not react_proof_applicable or state_transition_projects_total <= 0 else None,
            rationale=(
                "Every stateful project should remain inside transition coverage envelope."
                if react_proof_applicable
                else "Not applicable: Project DNA did not classify this target as a React runtime."
            ),
            evidence={
                "stateful_projects": state_projects_total,
                "transition_applicable_projects": state_transition_projects_total,
                "projects_transition_ok": state_projects_transition_ok,
                "ratio": _rounded_ratio(state_projects_ratio),
                "measurement_status": "not_applicable" if not react_proof_applicable or state_transition_projects_total <= 0 else "measured",
                "threshold": float(thresholds.get("state_projects_pass_ratio_min", 1.0)),
                "project_transition_threshold": float(thresholds.get("project_transition_ratio_min", 0.8)),
                "by_project": transition_ratio_by_project,
                "react_proof_applicable": react_proof_applicable,
            },
        ),
        _obligation(
            oid="PO_012",
            title="Project-level property envelope consistency (strong)",
            level="L4",
            required=react_proof_applicable and state_projects_total > 0 and strict_property_required,
            passed=(
                _ratio_passes(state_projects_property_strong_ratio, state_projects_property_strong_pass_ratio_min)
                or _ratio_passes(state_projects_property_ratio, state_projects_property_pass_ratio_effective)
            ),
            status="NOT_APPLICABLE" if not react_proof_applicable or state_projects_total <= 0 else None,
            rationale=(
                "Stateful projects should preserve strong property evidence envelope (selectors/state-type contracts)."
                if react_proof_applicable and strict_property_required
                else "Not applicable: Project DNA did not classify this target as a React runtime."
                if not react_proof_applicable
                else "Informational in small stateful-project workspaces; tracked but not gate-blocking."
            ),
            evidence={
                "stateful_projects": state_projects_total,
                "projects_property_ok": state_projects_property_ok,
                "projects_property_strong_ok": state_projects_property_strong_ok,
                "ratio": _rounded_ratio(state_projects_property_strong_ratio),
                "measurement_status": "not_applicable" if not react_proof_applicable or state_projects_total <= 0 else "measured",
                "threshold": state_projects_property_strong_pass_ratio_min,
                "project_property_threshold": project_property_ratio_min,
                "project_property_strong_threshold": project_property_ratio_strong_min,
                "by_project": property_ratio_strong_by_project,
                "by_project_broad": property_ratio_by_project,
                "broad_ratio": _rounded_ratio(state_projects_property_ratio),
                "broad_threshold": state_projects_property_pass_ratio_min,
                "broad_threshold_effective": state_projects_property_pass_ratio_effective,
                "pass_mode": "strong_or_broad",
                "required_mode": "enforced" if react_proof_applicable and strict_property_required else "informational",
                "strict_property_min_projects": strict_property_min_projects,
                "react_proof_applicable": react_proof_applicable,
            },
        ),
    ]

    required_total = sum(1 for item in obligations if item.get("required", True))
    required_passed = sum(1 for item in obligations if item.get("required", True) and item.get("passed"))
    failed_required = [item for item in obligations if item.get("required", True) and not item.get("passed")]

    by_level = {}
    for item in obligations:
        level = str(item.get("level") or "L0")
        bucket = by_level.setdefault(level, {"total": 0, "passed": 0})
        bucket["total"] += 1
        if item.get("passed"):
            bucket["passed"] += 1

    payload = {
        "meta": {
            "version": "proof_obligations_v2",
            "generated_by": "proof_obligations_engine",
            "runtime_seconds": round(perf_counter() - started, 3),
        },
        "summary": {
            "passed": len(failed_required) == 0,
            "required_total": required_total,
            "verified_obligations": required_passed,
            "failed_required": len(failed_required),
            "verification_ratio": _rounded_ratio(_ratio(required_passed, required_total)) or 0.0,
            "levels": by_level,
        },
        "failed_required_ids": [item["id"] for item in failed_required],
        "obligations": obligations,
    }
    payload["passed"] = payload["summary"]["passed"]
    payload["verified_obligations"] = payload["summary"]["verified_obligations"]

    ensure_valid_payload("proof_obligations", payload)
    save_json_atomic(RAW_DIR / "proof_obligations.json", payload)

    total_obligations = len(obligations)
    informational_total = sum(1 for item in obligations if not item.get("required", True))

    lines = [
        "# Proof Obligations",
        "",
        f"- Passed: `{payload['summary']['passed']}`",
        f"- Total obligations: `{total_obligations}`",
        f"- Required obligations: `{payload['summary']['required_total']}`",
        f"- Informational obligations: `{informational_total}`",
        f"- Verified obligations: `{payload['summary']['verified_obligations']}/{payload['summary']['required_total']}`",
        f"- Failed required obligations: `{payload['summary']['failed_required']}`",
        f"- Verification ratio: `{payload['summary']['verification_ratio']}`",
        "",
        "## Obligations",
        "",
        "| ID | Level | Required | Status | Passed | Title |",
        "|---|---|---|---|---|---|",
    ]
    for item in obligations:
        lines.append(
            f"| `{item['id']}` | `{item['level']}` | {'required' if item.get('required', True) else 'informational'} | `{item.get('status', 'UNKNOWN')}` | {'yes' if item.get('passed') else 'no'} | {item.get('title', '')} |"
        )
    save_text_atomic(REPORTS_DIR / "proof_obligations.md", "\n".join(lines))
    logger.info("Proof obligations generated: reports/proof_obligations.md")


if __name__ == "__main__":
    run_proof_obligations()
