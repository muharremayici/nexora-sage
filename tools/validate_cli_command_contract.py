from __future__ import annotations

import ast
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.core.config import RAW_DIR, REPORTS_DIR, save_json_atomic, save_text_atomic
from tools.core.json_io import load_json_file


CONTRACT_PATH = ROOT / "config" / "cli_command_contract.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(name: str, passed: bool, details: Any = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": details}


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value if str(item)}


def _classifier_pattern_errors(classifiers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for row in classifiers:
        pattern = str(row.get("pattern") or "")
        if not pattern:
            continue
        try:
            re.compile(pattern)
        except re.error as exc:
            errors.append({"id": str(row.get("id") or "<missing-id>"), "pattern": pattern, "error": str(exc)})
    return errors


def _bootstrap_mode_branch_calls(source: str, mode: str) -> set[str]:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        compare = node.test
        if len(compare.ops) != 1 or not isinstance(compare.ops[0], ast.Eq) or len(compare.comparators) != 1:
            continue
        left = compare.left
        right = compare.comparators[0]
        if not (
            isinstance(left, ast.Attribute)
            and isinstance(left.value, ast.Name)
            and left.value.id == "args"
            and left.attr == "mode"
            and isinstance(right, ast.Constant)
            and right.value == mode
        ):
            continue
        branch = ast.Module(body=node.body, type_ignores=[])
        return {
            call.func.id
            for call in ast.walk(branch)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        }
    return set()


def _cli_surface_snapshot(*, public_distribution: bool) -> dict[str, Any]:
    import codemaps

    original_detector = codemaps.is_public_distribution
    codemaps.is_public_distribution = lambda: public_distribution
    try:
        parser = codemaps.build_parser()
        subparsers = next(
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        return {
            "commands": sorted(subparsers.choices),
            "help": parser.format_help(),
        }
    finally:
        codemaps.is_public_distribution = original_detector


def _help_surface_errors(
    surface_id: str,
    surface: dict[str, Any],
    snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    commands = set(snapshot.get("commands", []))
    help_text = str(snapshot.get("help") or "")
    normalized_help = " ".join(help_text.split())
    required_commands = _string_set(surface.get("required_commands"))
    forbidden_commands = _string_set(surface.get("forbidden_commands"))
    required_phrases = _string_set(surface.get("required_phrases"))
    forbidden_phrases = _string_set(surface.get("forbidden_phrases"))
    errors: list[dict[str, Any]] = []
    if not required_commands:
        errors.append({"surface": surface_id, "reason": "required_commands is empty"})
    if not required_phrases:
        errors.append({"surface": surface_id, "reason": "required_phrases is empty"})
    if missing := sorted(required_commands - commands):
        errors.append({"surface": surface_id, "missing_commands": missing})
    if exposed := sorted(forbidden_commands & commands):
        errors.append({"surface": surface_id, "forbidden_commands_exposed": exposed})
    if missing := sorted(
        phrase
        for phrase in required_phrases
        if " ".join(phrase.split()) not in normalized_help
    ):
        errors.append({"surface": surface_id, "missing_help_phrases": missing})
    if exposed := sorted(
        phrase
        for phrase in forbidden_phrases
        if " ".join(phrase.split()) in normalized_help
    ):
        errors.append({"surface": surface_id, "forbidden_help_phrases_exposed": exposed})
    return errors


def validate_cli_command_contract() -> dict[str, Any]:
    contract = load_json_file(CONTRACT_PATH, {})
    if not isinstance(contract, dict):
        contract = {}
    target_proof_contract = load_json_file(
        ROOT / "config" / "target_repository_proof_contract.json",
        {},
    )
    cli_source = (ROOT / "codemaps.py").read_text(encoding="utf-8", errors="replace")
    bootstrap_source = (ROOT / "tools" / "core" / "bootstrap_env.py").read_text(encoding="utf-8", errors="replace")
    discovery_source = (ROOT / "tools" / "orchestrators" / "discovery.py").read_text(
        encoding="utf-8",
        errors="replace",
    )
    installation_proof_source = (ROOT / "tools" / "generate_installation_proof.py").read_text(encoding="utf-8", errors="replace")
    validation = contract.get("validation", {})
    if not isinstance(validation, dict):
        validation = {}
    required_command_ids = _string_set(validation.get("required_command_ids"))
    allowed_pipeline_step_template_command_ids = _string_set(
        validation.get("allowed_pipeline_step_template_command_ids")
    )
    valid_execution_modes = _string_set(validation.get("valid_execution_modes"))
    help_surfaces = validation.get("help_surfaces", {})
    help_surfaces = help_surfaces if isinstance(help_surfaces, dict) else {}
    visibility = validation.get("top_level_command_visibility", {})
    visibility = visibility if isinstance(visibility, dict) else {}
    visibility_classes = visibility.get("classes", {})
    visibility_classes = visibility_classes if isinstance(visibility_classes, dict) else {}
    required_init_help_phrases = _string_set(validation.get("required_init_help_phrases"))
    init_target_boundary = (
        validation.get("init_target_boundary", {})
        if isinstance(validation.get("init_target_boundary"), dict)
        else {}
    )
    init_target_cli_tokens = _string_set(init_target_boundary.get("cli_required_tokens"))
    init_target_bootstrap_tokens = _string_set(init_target_boundary.get("bootstrap_required_tokens"))
    init_target_discovery_tokens = _string_set(init_target_boundary.get("discovery_required_tokens"))
    generated_smoke_probe_command = str(validation.get("generated_smoke_probe_command") or "")
    commands = [row for row in contract.get("commands", []) if isinstance(row, dict)]
    classifiers = [row for row in contract.get("agent_command_classifiers", []) if isinstance(row, dict)]
    command_ids = [str(row.get("id") or "") for row in commands]
    command_id_set = set(command_ids)
    unknown_pipeline_step_template_command_ids = sorted(
        allowed_pipeline_step_template_command_ids - command_id_set
    )
    duplicate_ids = sorted({item for item in command_ids if command_ids.count(item) > 1 and item})
    unknown_modes = sorted(
        {
            str(row.get("execution_mode") or "")
            for row in commands
            if str(row.get("execution_mode") or "") not in valid_execution_modes
        }
    )
    incomplete_commands = [
        row.get("id")
        for row in commands
        if not row.get("surface")
        or not row.get("intent")
        or not row.get("execution_mode")
        or not row.get("canonical_use")
    ]
    incomplete_classifiers = [
        row.get("id")
        for row in classifiers
        if not row.get("id")
        or not row.get("pattern")
        or not row.get("execution_scope")
        or not row.get("proof_boundary")
        or not row.get("agent_note")
    ]
    classifier_pattern_errors = _classifier_pattern_errors(classifiers)
    guard_errors = []
    for guard in contract.get("ambiguity_guards", []) or []:
        if not isinstance(guard, dict):
            guard_errors.append({"guard": guard, "reason": "guard is not an object"})
            continue
        missing = [item for item in guard.get("commands", []) or [] if item not in command_id_set]
        if missing:
            guard_errors.append({"guard": guard.get("id"), "missing_commands": missing})
        if not guard.get("rule"):
            guard_errors.append({"guard": guard.get("id"), "reason": "missing rule"})

    by_id = {str(row.get("id")): row for row in commands if row.get("id")}
    init_execution = contract.get("init_execution", {}) if isinstance(contract.get("init_execution"), dict) else {}
    init_modes = init_execution.get("modes", {}) if isinstance(init_execution.get("modes"), dict) else {}
    installation_init_modes = (
        init_execution.get("installation_proof_init_modes", {})
        if isinstance(init_execution.get("installation_proof_init_modes"), dict)
        else {}
    )
    init_mode_errors = []
    for mode_id, mode in init_modes.items():
        if not isinstance(mode, dict):
            init_mode_errors.append({"mode": mode_id, "reason": "mode is not an object"})
            continue
        missing = [
            field
            for field in (
                "public_flag",
                "bootstrap_mode",
                "analysis_profile",
                "cost_tier",
                "telemetry_identifier",
                "generated_scope",
                "operator_guidance",
            )
            if field not in mode
        ]
        command_id = f"init_{mode_id}"
        if missing or command_id not in by_id:
            init_mode_errors.append({"mode": mode_id, "missing_fields": missing, "missing_command": command_id not in by_id})
    setup_only_mode = init_modes.get("setup_only", {}) if isinstance(init_modes.get("setup_only"), dict) else {}
    setup_only_flag = str(setup_only_mode.get("public_flag") or "")
    default_init_mode = str(init_execution.get("default_mode") or "")
    installation_modes_valid = bool(installation_init_modes) and all(
        str(mode_id) in init_modes for mode_id in installation_init_modes.values()
    )
    installation_proof_uses_contract = (
        "installation_proof_init_mode(level)" in installation_proof_source
        and "init_mode_contract(installation_proof_init_mode(level))" in installation_proof_source
        and 'public_flag = str(init_mode.get("public_flag") or "")' in installation_proof_source
    )
    setup_only_calls = _bootstrap_mode_branch_calls(bootstrap_source, str(setup_only_mode.get("bootstrap_mode") or ""))
    required_setup_calls = _string_set(setup_only_mode.get("required_bootstrap_calls"))
    forbidden_setup_calls = _string_set(setup_only_mode.get("forbidden_bootstrap_calls"))
    setup_only_source_boundary = (
        default_init_mode == "setup_only"
        and 'mode = "full" if bool(getattr(args, "full", False)) else "setup_only"' in cli_source
        and bool(required_setup_calls)
        and required_setup_calls.issubset(setup_only_calls)
        and not forbidden_setup_calls.intersection(setup_only_calls)
    )
    full_force = by_id.get("run_full_force", {})
    release_deep = by_id.get("run_release_deep", {})
    entrypoint_roles_ok = bool(by_id.get("sage_cli", {}).get("canonical")) and "codemaps_cli" not in by_id
    force_distinct_ok = (
        full_force.get("execution_mode") == release_deep.get("execution_mode") == "release_deep"
        and full_force.get("intent") != release_deep.get("intent")
        and bool(full_force.get("distinct_from", {}).get("run_release_deep"))
    )
    release_bundle_distinct_ok = (
        by_id.get("release_check_all", {}).get("intent") != by_id.get("release_proof_bundle", {}).get("intent")
    )
    list_steps_distinct_ok = (
        by_id.get("run_list_steps", {}).get("intent") == "registry_refresh_and_step_inventory"
        and by_id.get("run_list_steps", {}).get("execution_mode") == "explicit_step"
        and by_id.get("run_list_steps", {}).get("intent") != by_id.get("run_full", {}).get("intent")
    )
    explicit_step_contract_ok = (
        by_id.get("run_explicit_step", {}).get("intent") == "operator_requested_step_closure"
        and by_id.get("run_explicit_step", {}).get("execution_mode") == "explicit_step"
        and by_id.get("run_explicit_step", {}).get("requires_contract") == "pipeline_step_registry.invocation_contract.explicit_step_closure"
        and "dependency closure" in str(by_id.get("run_explicit_step", {}).get("canonical_use") or "")
    )
    target_proof_cli = (
        target_proof_contract.get("public_cli", {})
        if isinstance(target_proof_contract, dict)
        else {}
    )
    target_proof_cli_ok = (
        by_id.get("target_repository_proof", {}).get("intent")
        == "bounded_target_repository_proof"
        and target_proof_cli.get("refresh_policies")
        == ["current", "if-missing", "always"]
        and target_proof_cli.get("default_refresh_policy") == "current"
        and target_proof_cli.get("default_mode") == "baseline"
        and target_proof_cli.get("successful_verdicts")
        == ["PASS", "REVIEW_REQUIRED"]
        and "def target_repository_proof_cli_contract()" in cli_source
        and 'profile=str(cli_contract["refresh_execution_profile"])' in cli_source
        and "proof_artifact_not_refreshed" in cli_source
        and "def cmd_target_proof(args):" in cli_source
        and "resolve_current_external_target_generation" in cli_source
        and "TARGET_REPOSITORY_PROOF_GENERATOR" in cli_source
        and 'args.refresh_policy == "if-missing"' in cli_source
        and 'args.refresh_policy == "always"' in cli_source
    )
    help_surface_errors: list[dict[str, Any]] = []
    help_surface_snapshots: dict[str, dict[str, Any]] = {}
    for surface_id, public_distribution in (("development", False), ("public", True)):
        surface = help_surfaces.get(surface_id, {})
        if not isinstance(surface, dict):
            help_surface_errors.append({"surface": surface_id, "reason": "surface contract is not an object"})
            continue
        snapshot = _cli_surface_snapshot(public_distribution=public_distribution)
        help_surface_snapshots[surface_id] = {
            "commands": snapshot["commands"],
            "help_length": len(snapshot["help"]),
        }
        help_surface_errors.extend(_help_surface_errors(surface_id, surface, snapshot))
    help_surfaces_ok = set(help_surfaces) >= {"development", "public"} and not help_surface_errors
    visibility_errors: list[dict[str, Any]] = []
    declared_top_level: list[str] = []
    declared_public: set[str] = set()
    for class_id, row in visibility_classes.items():
        if not isinstance(row, dict):
            visibility_errors.append({"class": class_id, "reason": "class is not an object"})
            continue
        class_commands = row.get("commands")
        if not isinstance(class_commands, list) or not all(
            isinstance(item, str) and item for item in class_commands
        ):
            visibility_errors.append({"class": class_id, "reason": "commands are invalid"})
            continue
        declared_top_level.extend(class_commands)
        if row.get("public_distribution") is True:
            declared_public.update(class_commands)
        elif row.get("public_distribution") is not False:
            visibility_errors.append(
                {"class": class_id, "reason": "public_distribution must be boolean"}
            )
    duplicate_top_level = sorted(
        {name for name in declared_top_level if declared_top_level.count(name) > 1}
    )
    development_commands = set(help_surface_snapshots.get("development", {}).get("commands", []))
    public_commands = set(help_surface_snapshots.get("public", {}).get("commands", []))
    if duplicate_top_level:
        visibility_errors.append({"duplicate_commands": duplicate_top_level})
    if development_commands != set(declared_top_level):
        visibility_errors.append(
            {
                "development_unclassified": sorted(development_commands - set(declared_top_level)),
                "declared_without_parser": sorted(set(declared_top_level) - development_commands),
            }
        )
    if public_commands != declared_public:
        visibility_errors.append(
            {
                "unexpected_public_commands": sorted(public_commands - declared_public),
                "missing_public_commands": sorted(declared_public - public_commands),
            }
        )
    visibility_ok = bool(visibility_classes) and not visibility_errors
    init_help_discloses_heavy_path = (
        bool(required_init_help_phrases)
        and all(phrase in cli_source for phrase in required_init_help_phrases)
    )
    explicit_init_target_boundary = (
        bool(init_target_cli_tokens)
        and bool(init_target_bootstrap_tokens)
        and bool(init_target_discovery_tokens)
        and all(token in cli_source for token in init_target_cli_tokens)
        and all(token in bootstrap_source for token in init_target_bootstrap_tokens)
        and all(token in discovery_source for token in init_target_discovery_tokens)
    )
    from tools.core.agent_command_contracts import command_contract_for_agent

    generated_smoke_contract = command_contract_for_agent(
        generated_smoke_probe_command
    )
    generated_smoke_classified = (
        bool(generated_smoke_probe_command)
        and
        generated_smoke_contract.get("execution_scope") == "ui_smoke_playwright"
        and generated_smoke_contract.get("proof_boundary") == "generated_ui_smoke_spec_evidence"
    )
    validation_execution = contract.get("validation_execution", {})
    validation_execution = validation_execution if isinstance(validation_execution, dict) else {}
    required_profile_fields = _string_set(validation_execution.get("required_profile_fields"))
    runtime_truth_modes = _string_set(validation_execution.get("runtime_truth_modes"))
    artifact_refresh_modes = _string_set(validation_execution.get("artifact_refresh_modes"))
    execution_profiles = [
        row for row in validation_execution.get("profiles", []) if isinstance(row, dict)
    ]
    profile_ids = [str(row.get("id") or "") for row in execution_profiles]
    profile_flags = [str(row.get("public_flag") or "") for row in execution_profiles if str(row.get("public_flag") or "")]
    profile_errors = [
        {
            "id": str(row.get("id") or "<missing-id>"),
            "missing_fields": sorted(required_profile_fields - set(row)),
            "runtime_truth_mode": row.get("runtime_truth_mode"),
            "artifact_refresh_mode": row.get("artifact_refresh_mode"),
        }
        for row in execution_profiles
        if required_profile_fields - set(row)
        or str(row.get("runtime_truth_mode") or "") not in runtime_truth_modes
        or str(row.get("artifact_refresh_mode") or "") not in artifact_refresh_modes
    ]
    validator_sets = validation_execution.get("validator_sets", {})
    validator_sets = validator_sets if isinstance(validator_sets, dict) else {}
    profile_set_ids = {str(row.get("validator_set_id") or "") for row in execution_profiles}
    invalid_validator_paths = sorted(
        {
            str(path)
            for paths in validator_sets.values()
            if isinstance(paths, list)
            for path in paths
            if not str(path).strip() or not (ROOT / str(path)).is_file()
        }
    )
    source_clean_forbidden_paths = _string_set(
        validation_execution.get("source_clean_forbidden_validator_paths")
    )
    source_clean_profile = next(
        (row for row in execution_profiles if str(row.get("id") or "") == "source_clean"),
        {},
    )
    source_clean_paths = set(
        validator_sets.get(str(source_clean_profile.get("validator_set_id") or ""), [])
        if isinstance(source_clean_profile, dict)
        else []
    )
    doctor_contract = validation_execution.get("doctor", {})
    doctor_contract = doctor_contract if isinstance(doctor_contract, dict) else {}
    doctor_required_fields = _string_set(doctor_contract.get("required_profile_fields"))
    doctor_profiles = doctor_contract.get("profiles", {})
    doctor_profiles = doctor_profiles if isinstance(doctor_profiles, dict) else {}
    doctor_profile_errors = {
        str(scope): sorted(doctor_required_fields - set(profile))
        for scope, profile in doctor_profiles.items()
        if not isinstance(profile, dict) or doctor_required_fields - set(profile)
    }
    doctor_set_ids = {
        str(profile.get("validator_set_id") or "")
        for profile in doctor_profiles.values()
        if isinstance(profile, dict)
    }
    repository_doctor_profile = doctor_profiles.get("SAGE_ON_REPOSITORY", {})
    repository_doctor_paths = set(
        validator_sets.get(str(repository_doctor_profile.get("validator_set_id") or ""), [])
        if isinstance(repository_doctor_profile, dict)
        else []
    )
    repository_doctor_forbidden_paths = _string_set(
        doctor_contract.get("repository_forbidden_validator_paths")
    )
    validate_profile_consumer = (
        "resolve_validate_execution_profile(args)" in cli_source
        and "validation_profile_options()" in cli_source
        and "validator_commands_for_profile(profile)" in cli_source
        and "optional_validator_options()" in cli_source
    )

    checks = [
        _check("contract_file_exists", CONTRACT_PATH.exists(), str(CONTRACT_PATH)),
        _check("validation_declares_required_command_ids", bool(required_command_ids), sorted(required_command_ids)),
        _check("validation_declares_valid_execution_modes", bool(valid_execution_modes), sorted(valid_execution_modes)),
        _check("required_commands_declared", required_command_ids.issubset(command_id_set), sorted(command_id_set)),
        _check(
            "pipeline_step_template_exceptions_are_declared_and_reference_known_commands",
            bool(allowed_pipeline_step_template_command_ids)
            and not unknown_pipeline_step_template_command_ids,
            {
                "allowed_command_ids": sorted(allowed_pipeline_step_template_command_ids),
                "unknown_command_ids": unknown_pipeline_step_template_command_ids,
            },
        ),
        _check("command_ids_are_unique", not duplicate_ids, duplicate_ids),
        _check("commands_have_required_fields", not incomplete_commands, incomplete_commands),
        _check("agent_command_classifiers_declared", bool(classifiers), [row.get("id") for row in classifiers]),
        _check("agent_command_classifiers_have_required_fields", not incomplete_classifiers, incomplete_classifiers),
        _check("agent_command_classifier_patterns_compile", not classifier_pattern_errors, classifier_pattern_errors),
        _check(
            "generated_playwright_smoke_command_is_classified",
            generated_smoke_classified,
            generated_smoke_contract,
        ),
        _check("commands_use_known_execution_modes", not unknown_modes, unknown_modes),
        _check(
            "validation_execution_profiles_are_complete_and_known",
            bool(required_profile_fields)
            and bool(runtime_truth_modes)
            and bool(artifact_refresh_modes)
            and bool(execution_profiles)
            and not profile_errors
            and len(profile_ids) == len(set(profile_ids))
            and len(profile_flags) == len(set(profile_flags))
            and str(validation_execution.get("default_profile_id") or "") in set(profile_ids),
            {
                "profile_ids": profile_ids,
                "profile_flags": profile_flags,
                "errors": profile_errors,
                "default_profile_id": validation_execution.get("default_profile_id"),
            },
        ),
        _check(
            "validation_execution_profiles_reference_existing_validator_sets",
            bool(validator_sets)
            and profile_set_ids.issubset(set(validator_sets))
            and not invalid_validator_paths,
            {
                "profile_set_ids": sorted(profile_set_ids),
                "validator_set_ids": sorted(validator_sets),
                "invalid_validator_paths": invalid_validator_paths,
            },
        ),
        _check(
            "source_clean_profile_excludes_declared_engine_exercising_validators",
            bool(source_clean_forbidden_paths)
            and not source_clean_forbidden_paths.intersection(source_clean_paths),
            {
                "forbidden_paths": sorted(source_clean_forbidden_paths),
                "source_clean_paths": sorted(source_clean_paths),
            },
        ),
        _check(
            "doctor_profiles_are_scope_complete_and_reference_validator_sets",
            doctor_required_fields == {"id", "validator_set_id", "claim_boundary"}
            and set(doctor_profiles) == {"SAGE_ON_REPOSITORY", "SAGE_ON_SAGE"}
            and not doctor_profile_errors
            and doctor_set_ids.issubset(set(validator_sets)),
            {
                "scopes": sorted(doctor_profiles),
                "set_ids": sorted(doctor_set_ids),
                "errors": doctor_profile_errors,
            },
        ),
        _check(
            "repository_doctor_excludes_sage_self_evidence_validators",
            bool(repository_doctor_forbidden_paths)
            and not repository_doctor_forbidden_paths.intersection(repository_doctor_paths),
            {
                "forbidden_paths": sorted(repository_doctor_forbidden_paths),
                "repository_doctor_paths": sorted(repository_doctor_paths),
            },
        ),
        _check(
            "validate_cli_consumes_profile_contract",
            validate_profile_consumer,
            "cmd_validate/parser must resolve profiles, parser flags, central validator sets and optional validators through the CLI contract.",
        ),
        _check("init_modes_are_complete_and_command_backed", bool(init_modes) and not init_mode_errors, init_mode_errors),
        _check(
            "installation_proof_init_modes_reference_known_modes",
            installation_modes_valid,
            installation_init_modes,
        ),
        _check(
            "installation_proof_consumes_init_mode_contract",
            installation_proof_uses_contract,
            {"setup_only_flag": setup_only_flag, "mapping": installation_init_modes},
        ),
        _check(
            "setup_only_init_has_no_analysis_pulse",
            setup_only_source_boundary,
            {
                "public_flag": setup_only_flag,
                "bootstrap_mode": setup_only_mode.get("bootstrap_mode"),
                "observed_calls": sorted(setup_only_calls),
                "required_calls": sorted(required_setup_calls),
                "forbidden_calls": sorted(forbidden_setup_calls),
            },
        ),
        _check("ambiguity_guards_reference_known_commands", not guard_errors, guard_errors),
        _check("sage_is_canonical_product_entrypoint", entrypoint_roles_ok, {"sage_cli": by_id.get("sage_cli"), "codemaps_cli": by_id.get("codemaps_cli")}),
        _check("full_force_is_distinct_from_release_deep_profile", force_distinct_ok, {"run_full_force": full_force, "run_release_deep": release_deep}),
        _check("release_check_is_distinct_from_proof_bundle", release_bundle_distinct_ok, {"release_check_all": by_id.get("release_check_all"), "release_proof_bundle": by_id.get("release_proof_bundle")}),
        _check("list_steps_is_registry_refresh_not_analysis_run", list_steps_distinct_ok, {"run_list_steps": by_id.get("run_list_steps"), "run_full": by_id.get("run_full")}),
        _check("explicit_step_declares_dependency_closure_contract", explicit_step_contract_ok, {"run_explicit_step": by_id.get("run_explicit_step")}),
        _check(
            "target_repository_proof_cli_uses_current_generation_and_canonical_builder",
            target_proof_cli_ok,
            {
                "command": by_id.get("target_repository_proof"),
                "public_cli": target_proof_cli,
            },
        ),
        _check(
            "development_and_public_help_surfaces_preserve_authority_boundary",
            help_surfaces_ok,
            {
                "snapshots": help_surface_snapshots,
                "errors": help_surface_errors,
            },
        ),
        _check(
            "top_level_cli_visibility_is_exhaustive_and_public_is_exact",
            visibility_ok,
            {
                "classes": sorted(visibility_classes),
                "development_commands": sorted(development_commands),
                "public_commands": sorted(public_commands),
                "errors": visibility_errors,
            },
        ),
        _check(
            "init_help_discloses_heavy_first_run_and_lightweight_smoke",
            init_help_discloses_heavy_path,
            sorted(required_init_help_phrases),
        ),
        _check(
            "public_init_requires_explicit_analyzed_repository_boundary",
            explicit_init_target_boundary,
            {
                "cli_required_tokens": sorted(init_target_cli_tokens),
                "bootstrap_required_tokens": sorted(init_target_bootstrap_tokens),
                "discovery_required_tokens": sorted(init_target_discovery_tokens),
            },
        ),
    ]
    status = "PASS" if all(check["passed"] for check in checks) else "FAIL"
    payload = {
        "meta": {
            "kind": "cli_command_contract_validation",
            "version": "v1",
            "generated_at": _utc_now(),
            "generator": "tools.validate_cli_command_contract",
        },
        "summary": {
            "status": status,
            "commands": len(commands),
            "checks": len(checks),
            "passed": sum(1 for check in checks if check["passed"]),
            "canonical_commands": sum(1 for row in commands if row.get("canonical")),
            "agent_command_classifiers": len(classifiers),
        },
        "principles": contract.get("principles", {}),
        "commands": commands,
        "checks": checks,
    }
    save_json_atomic(RAW_DIR / "cli_command_contract_validation.json", payload)
    save_text_atomic(REPORTS_DIR / "cli_command_contract_validation.md", render_report(payload))
    return payload


def render_report(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# CLI Command Contract Validation",
        "",
        "Validates command intent boundaries so analysis, release, proof, and compatibility surfaces do not drift into ambiguous duplicates.",
        "",
        f"- status: `{summary.get('status')}`",
        f"- commands: `{summary.get('commands')}`",
        f"- canonical_commands: `{summary.get('canonical_commands')}`",
        "",
        "| Command | Intent | Execution Mode | Canonical |",
        "|---|---|---|---|",
    ]
    for command in payload.get("commands", []):
        lines.append(
            f"| `{command.get('id')}` | `{command.get('intent')}` | `{command.get('execution_mode')}` | `{command.get('canonical')}` |"
        )
    lines.extend(["", "## Checks", "", "| Check | Passed |", "|---|---|"])
    for check in payload.get("checks", []):
        lines.append(f"| `{check.get('name')}` | `{check.get('passed')}` |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    payload = validate_cli_command_contract()
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    return 0 if payload.get("summary", {}).get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
