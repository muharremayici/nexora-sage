"""Bounded native-producer comparisons, not target execution or release proof.

Each acquisition runs in a fresh interpreter: bind the temporary runtime before
importing producers whose output paths are captured at import. No producer,
parser, receipt admission, lineage writer or consumer verdict is mocked.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


REPO = Path(__file__).resolve().parents[2]
# End with a genuinely produced positive chain for the remaining comparisons;
# negative evidence stays in results, not as the current successful namespace.
CASES = ("missing_scope", "tampered_audit", "bounded", "full")
INHERITED_ENV = (
    "CODEMAPS_TARGET_ROOT", "CODEMAPS_TARGET_PROJECTS", "CODEMAPS_EXTERNAL_RUN_ID",
    "CODEMAPS_TARGET_PREFLIGHT_RECEIPT", "CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256",
    "CODEMAPS_TOPOLOGY_MODE", "CODEMAPS_EXPECTED_FAILURE_DRILL", "SAGE_TENANT_ID",
)


def _bind_fixture_runtime(config, root, work, mode):
    """Test environment only; central doctrine/schema/policy owners stay real."""
    config.CONFIG_FILE = work / "runtime.json"
    config.DISCOVERY_FILE = work / "discovery.json"
    config.OVERRIDES_FILE = work / "overrides.json"
    config.ROOT = config.MAIN_PROJECT_ROOT = config.SRC = config.DEFAULT_WATCH_PATH = root
    config.DYNAMIC_CONFIG = {}
    config.OUTPUT_DIR = (
        work / "external_targets" / config.external_target_output_slug(str(root))
        / "generations" / "differential-fixture"
        if mode == "external" else work / "output"
    )
    for name, suffix in {
        "RAW_DIR": ".raw", "REPORTS_DIR": "reports", "LOGS_DIR": "logs",
        "SANCTUARY_DIR": ".sanctuary", "SCRIPTS_DIR": "scripts", "SNAPSHOTS_DIR": ".snapshots",
    }.items():
        setattr(config, name, config.OUTPUT_DIR / suffix)
    config.ensure_output_dir()


def _run_native_subject(root, work, mode):
    # Keep these imports late: pytest's parent may have a different runtime.
    sys.path.insert(0, str(REPO))
    from tools.core import config
    _bind_fixture_runtime(config, root, work, mode)
    assert "tools.core.artifact_store" not in sys.modules
    # Discovery resolves its explicit target at import, independently of config.
    # Bind only this discovery phase; normal acquisition must not gain an
    # external-receipt override from its bootstrap environment.
    os.environ["CODEMAPS_TARGET_ROOT"] = str(root)
    from tools import config_compiler
    from tools.orchestrators import discovery
    assert discovery.ROOT_DIR == root.resolve()
    proposal = discovery.run_discovery(output_path=config.DISCOVERY_FILE, write_reports=False)
    del os.environ["CODEMAPS_TARGET_ROOT"]
    assert json.loads(config.DISCOVERY_FILE.read_text(encoding="utf-8")) == proposal
    config.save_json_atomic(config.OVERRIDES_FILE, {})
    config.save_json_atomic(config.CONFIG_FILE, {})
    config_compiler.DISCOVERY_PATH = config.DISCOVERY_FILE
    config_compiler.OVERRIDES_PATH = config.OVERRIDES_FILE
    config_compiler.CONFIG_PATH = config.CONFIG_FILE
    compiled = config_compiler.compile_runtime_config()
    compiled["use_sqlite"] = True
    config.save_json_atomic(config.CONFIG_FILE, compiled)
    config.DYNAMIC_CONFIG.update(compiled)
    # Registry-backed runtime globals, not hand-written policy thresholds.
    config.SKIP_DIRS = set(compiled["skip_dirs"])
    config.SOURCE_EXTENSIONS = config._resolve_source_extensions(compiled)
    config.ARCH_CONFIG = compiled["architecture"]
    config.ENVIRONMENT = compiled["environment"]
    config.PLUGINS = set(compiled.get("plugins", []))
    config.PROJECT_FILTER = []
    assert "tools.core.artifact_store" not in sys.modules

    runtime_by_selection = {}

    def select(projects):
        key = tuple(projects or [])
        if key in runtime_by_selection:
            config.DYNAMIC_CONFIG.clear()
            config.DYNAMIC_CONFIG.update(deepcopy(runtime_by_selection[key]))
            config.PROJECT_FILTER = projects or []
            return config.DYNAMIC_CONFIG
        runtime = deepcopy(compiled)
        if mode == "external":
            from tools import external_target_preflight as preflight
            preflight.EXTERNAL_TARGETS_DIR = work / "external_targets"
            os.environ["CODEMAPS_EXTERNAL_RUN_ID"] = "differential-fixture"
            payload = preflight.build_preflight(root, projects=",".join(projects or []))
            assert Path(payload["target"]["output_dir"]) == config.OUTPUT_DIR
            persisted = preflight.persist_preflight(payload)
            transport = preflight.preflight_receipt_transport(persisted)
            os.environ.update({
                "CODEMAPS_TARGET_ROOT": str(root),
                "CODEMAPS_TARGET_PROJECTS": ",".join(projects or []),
                "CODEMAPS_TARGET_PREFLIGHT_RECEIPT": transport["path"],
                "CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256": transport["sha256"],
            })
            assert config.load_external_target_preflight_receipt(root, require_freshness=True) == persisted
            runtime = config._apply_target_root_override(runtime)
            assert runtime["_target_root_override"]["observation_source"] == "external_target_preflight_receipt"
        config.DYNAMIC_CONFIG.clear()
        config.DYNAMIC_CONFIG.update(runtime)
        config.PROJECT_FILTER = projects or []
        runtime_by_selection[key] = deepcopy(runtime)
        return config.DYNAMIC_CONFIG

    select(None)
    from tools.core import analysis_snapshot_lineage as lineage
    from tools.core import artifact_store, atlas_io, runtime_project_scope
    from tools.core import repository_observation as observation
    from tools.core.analysis_scope_authority import runtime_scope_authority
    from tools.core.atlas_integrity import validate_atlas_commit
    from tools.core.audit_report import invalidate_audit_report_cache
    from tools.engines import architecture_oracle as oracle, generate_atlas, nuclear_processor
    from tools.engines import audit, quality_gate
    from tools.orchestrators import orchestrator
    store = artifact_store.STORE
    store.initialize_schema()
    assert store.use_sqlite and store.db_manager.db_path.is_relative_to(work)
    atlas, _, _ = generate_atlas.generate_atlas()
    commit = lineage.load_atlas_commit(config.RAW_DIR)
    assert commit["state"] == "complete"
    assert all(row["passed"] for row in validate_atlas_commit(atlas, commit))
    primary = atlas_io.load_atlas_data_with_identity()
    assert primary.primary_verified and primary.source == "sqlite_primary" and primary.data == atlas
    assert {key: sorted(row["files"]) for key, row in atlas.items() if key != "symbols"} == {
        "MAIN": ["page.tsx"], "WIDGET": ["value.ts"],
    }
    assert all(f["parser_evidence"]["status"] == "observed"
               for p, row in atlas.items() if p != "symbols" for f in row["files"].values())
    nuclear_processor.run_nuclear(use_cache=False)
    assert lineage.load_lineage_receipt(config.RAW_DIR, "genome")["status"] == "COMPLETE"
    results = {}
    observations = {}
    for case in CASES:
        projects = ["WIDGET"] if case == "bounded" else None
        runtime = select(projects)
        runtime_project_scope.set_runtime_project_filter(projects)
        fresh = None
        if mode == "normal":
            selected = {k: v for k, v in compiled["variations"].items() if not projects or k in projects}
            key = tuple(projects or [])
            if key not in observations:
                observations[key] = observation.observe_current_repository(
                    root, compiled["_repository_topology"], projects=selected, run_id="differential")
            fresh = observations[key]
        authority = runtime_scope_authority(
            dynamic_config=runtime, projects=projects, atlas=atlas, raw_dir=config.RAW_DIR,
            project_observation=fresh, observation_run_id="differential" if fresh else None)
        scope_artifact = orchestrator._scope_authority_artifact(authority, stage="POST_ATLAS")
        config.save_json_atomic(config.RAW_DIR / "analysis_scope_authority.json", scope_artifact)
        scope_receipt = lineage.write_current_atlas_lineage(
            artifact_id="analysis_scope_authority", producer="tools.orchestrators.orchestrator",
            artifact_payload=scope_artifact, atlas=atlas)
        assert scope_receipt["status"] == "COMPLETE"
        oracle.run_architecture_oracle()
        if case == "missing_scope":
            # Remove authority without forging a matching successful receipt.
            config.save_json_atomic(config.RAW_DIR / "analysis_scope_authority.json", {})
        assert audit.analyze_project() is True
        actual_audit = store.load_raw("audit_report", {})
        if case == "tampered_audit":
            actual_audit["audit_scope"]["scope_authority"]["scope_authority_id"] = "tampered-after-production"
            config.save_json_atomic(config.RAW_DIR / "audit_report.json", actual_audit)
            invalidate_audit_report_cache()
        quality_gate.run_quality_gates()
        actual_gate = store.load_raw("quality_gate", {})
        results[case] = {
            "audit": actual_audit, "quality_gate": actual_gate,
            "scope": authority,
            "audit_receipt": lineage.load_lineage_receipt(config.RAW_DIR, "audit_report"),
            "quality_receipt": lineage.load_lineage_receipt(config.RAW_DIR, "quality_gate"),
        }
    assert store.load_raw("audit_report", {}) == results["full"]["audit"]
    assert lineage.load_lineage_receipt(config.RAW_DIR, "quality_gate")["status"] == "COMPLETE"
    return {
        "mode": mode, "pid": os.getpid(), "raw_dir": str(config.RAW_DIR),
        "db_path": str(store.db_manager.db_path), "commit": commit, "cases": results,
        "native_ast_file_count": 2, "subject_code_executed": False,
    }


@pytest.fixture(scope="module")
def differential_runs(tmp_path_factory, record_testsuite_property):
    base = tmp_path_factory.mktemp("acquisition-differential")
    root = base / "subject"
    root.mkdir()
    for name, payload in {
        "package.json": {"workspaces": ["packages/*"], "dependencies": {"react": "19", "vite": "6"},
                         "scripts": {"dev": "never execute this"}},
        "packages/widget/package.json": {"exports": {".": "./value.ts"}},
    }.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    # Deliberate real LOC finding, not a fabricated Audit output or weakened policy.
    page = "export function largeFunction() {\n" + "".join(
        f"  const v{i} = {i};\n" for i in range(165)) + "  return v164;\n}\n"
    (root / "page.tsx").write_text(page, encoding="utf-8")
    (root / "packages/widget/value.ts").write_text("", encoding="utf-8")
    before = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in root.rglob("*") if p.is_file()}
    env = {k: v for k, v in os.environ.items() if k not in INHERITED_ENV}
    env.update(SAGE_SYNC_SHADOW_WRITES="1", PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    runs = {}
    for mode in ("normal", "external"):
        work = base / mode
        work.mkdir()
        run = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()),
                              "--native-subject", str(root), str(work), mode],
                             cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8", timeout=180)
        (work / "driver.log").write_text(run.stdout + run.stderr, encoding="utf-8")
        assert run.returncode == 0, f"{mode}: {work / 'driver.log'}\n{run.stdout[-2000:]}\n{run.stderr[-5000:]}"
        runs[mode] = json.loads((work / "result.json").read_text(encoding="utf-8"))
        record_testsuite_property(f"{mode}_evidence", str(work / "result.json"))
        record_testsuite_property(f"{mode}_snapshot", runs[mode]["commit"]["snapshot_id"])
    after = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in root.rglob("*") if p.is_file()}
    assert after == before
    assert runs["normal"]["pid"] != runs["external"]["pid"]
    assert runs["normal"]["db_path"] != runs["external"]["db_path"]
    return runs


def _scope_semantics(scope, origin):
    """Only two validated acquisition-specific provenance fields may differ."""
    result = deepcopy(scope)
    observation_id = result.pop("project_observation_identity", None)
    threat = result.pop("target_repository_threat_boundary", None)
    if observation_id is not None:
        assert origin.get("project_observation_identity") == observation_id
        assert observation_id.startswith("sha256:") and len(observation_id) == 71
    if threat is not None:
        assert origin["target_repository_threat_boundary"] == threat
        assert threat["analysis_admission"] == "allowed_with_explicit_boundaries"
        assert threat["target_code_execution"] == threat["target_dependency_installation"] == "forbidden"
        assert threat["repository_content_trust"] == "untrusted_repository_data_not_instruction"
        assert threat["path_boundary"]["escaping_path_count"] == 0
        assert threat["path_boundary"]["status"] == "pass_contained"
    return result


def _audit_semantics(payload, snapshot, origin):
    result = deepcopy(payload)
    identity = result["artifact_identity"]
    assert identity == {"status": "BOUND", "atlas_snapshot_id": snapshot, "errors": []}
    identity["atlas_snapshot_id"] = "<verified-current-snapshot>"
    for location in (result, result["summary"]):
        for row in location["architecture_policy_application"]["projects"].values():
            # Never erase a stale or foreign policy binding in the comparison.
            assert row["expected_snapshot_id"] == row["observed_snapshot_id"] == snapshot
            row["expected_snapshot_id"] = row["observed_snapshot_id"] = "<verified-current-snapshot>"
        authority = location["audit_scope"]["scope_authority"]
        location["audit_scope"]["scope_authority"] = _scope_semantics(authority, origin)
    result["summary"].pop("profile_timings")
    return result


@pytest.mark.parametrize("case", CASES)
def test_actual_audit_and_quality_semantics_match_acquisition(differential_runs, case):
    normal, external = (differential_runs[mode]["cases"][case] for mode in ("normal", "external"))
    audits, gates = [], []
    for mode, row in (("normal", normal), ("external", external)):
        audits.append(_audit_semantics(row["audit"], differential_runs[mode]["commit"]["snapshot_id"], row["scope"]))
        gate = deepcopy(row["quality_gate"])
        gate["analysis_scope_authority"] = _scope_semantics(gate["analysis_scope_authority"], row["scope"])
        gates.append(gate)
    assert audits[0] == audits[1]
    # Entire gate, including every check, reason, missing layer and FAIL, is compared.
    assert gates[0] == gates[1]


@pytest.mark.parametrize("mode", ["normal", "external"])
def test_actual_positive_outputs_are_bound_nonvacuous_and_scope_limited(differential_runs, mode):
    result = differential_runs[mode]
    for case, projects in (("full", ["MAIN", "WIDGET"]), ("bounded", ["WIDGET"])):
        row = result["cases"][case]
        audit = row["audit"]
        assert audit["audited_projects"] == projects
        assert audit["artifact_identity"] == {
            "status": "BOUND", "atlas_snapshot_id": result["commit"]["snapshot_id"], "errors": [],
        }
        for receipt, payload in ((row["audit_receipt"], audit), (row["quality_receipt"], row["quality_gate"])):
            assert receipt["status"] == "COMPLETE"
            assert receipt["atlas_snapshot_id"] == result["commit"]["snapshot_id"]
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            assert receipt["artifact_sha256"] == hashlib.sha256(encoded).hexdigest()
            assert receipt["errors"] == []
            assert all(dep["snapshot_binding"] == "BOUND" for dep in receipt["dependencies"])
        assert row["quality_gate"]["scope_gate_status"] == "PASS"
        # Missing pipeline producers remain a FAIL; this is not a target health proof.
        assert row["quality_gate"]["passed"] is False
        assert row["quality_gate"]["release_gate_status"] == "FAIL"
        assert row["scope"]["full_repository_claim_eligible"] is (case == "full")
        if mode == "normal":
            assert row["scope"]["project_observation_identity"].startswith("sha256:")
        else:
            assert row["scope"]["target_repository_threat_boundary"]["target_code_execution"] == "forbidden"
    assert any(v["project"] == "MAIN" and v["rule"] == "loc_limits_symbol"
               for v in result["cases"]["full"]["audit"]["violations"])
    assert result["cases"]["bounded"]["audit"]["violations"] == []


@pytest.mark.parametrize("mode", ["normal", "external"])
@pytest.mark.parametrize("case", ["missing_scope", "tampered_audit"])
def test_actual_quality_rejects_missing_or_changed_scope(differential_runs, mode, case):
    row = differential_runs[mode]["cases"][case]
    assert row["quality_gate"]["scope_gate_status"] == "INCOMPLETE_EVIDENCE"
    assert row["quality_gate"]["passed"] is False
    check = next(c for c in row["quality_gate"]["checks"] if c["name"] == "analysis_scope_authority")
    assert check["actual"] == 0 and check["passed"] is False
    assert row["quality_receipt"]["status"] != "COMPLETE"
    dependency = "analysis_scope_authority" if case == "missing_scope" else "audit_report"
    assert f"dependency:{dependency}:artifact_content_hash_mismatch" in row["quality_receipt"]["errors"]


@pytest.mark.parametrize("mode", ["normal", "external"])
def test_comparison_cannot_hide_stale_policy_or_scope(differential_runs, mode):
    result = differential_runs[mode]
    row = result["cases"]["full"]
    changed = deepcopy(row["audit"])
    changed["architecture_policy_application"]["projects"]["MAIN"]["observed_snapshot_id"] = "foreign"
    with pytest.raises(AssertionError):
        _audit_semantics(changed, result["commit"]["snapshot_id"], row["scope"])
    changed = deepcopy(row["scope"])
    changed["scope_authority_id"] = "foreign"
    assert _scope_semantics(changed, row["scope"]) != _scope_semantics(row["scope"], row["scope"])


def test_comparison_cannot_hide_activated_external_execution(differential_runs):
    origin = deepcopy(differential_runs["external"]["cases"]["full"]["scope"])
    origin["target_repository_threat_boundary"]["target_code_execution"] = "allowed"
    with pytest.raises(AssertionError):
        _scope_semantics(origin, origin)


def _run_native_packets(seed, work, mode):
    """Reuse a verified base chain; run only the additional packet producers."""
    import shutil
    import sqlite3
    from contextlib import closing

    sys.path.insert(0, str(REPO))
    from tools.core import config
    from tools.core.unmanaged_atomic_io import native_filesystem_path
    source_raw = Path(seed["raw_dir"])
    # Runtime config belongs to the original tiny subject, not the SAGE source tree.
    source_work = source_raw.parents[0].parent if mode == "normal" else source_raw.parents[4]
    compiled = json.loads((source_work / "runtime.json").read_text(encoding="utf-8"))
    root = (REPO / compiled["workspace_root"]).resolve()
    if mode == "external":
        from tools.core.external_target_generation import begin_external_target_generation
        target_dir = work / "output" / "external_targets" / config.external_target_output_slug(str(root))
        begin_external_target_generation(target_dir, "differential-fixture", root)
    _bind_fixture_runtime(config, root, work / "output" if mode == "external" else work, mode)
    for name, source in (("CONFIG_FILE", source_work / "runtime.json"),
                         ("DISCOVERY_FILE", source_work / "discovery.json"),
                         ("OVERRIDES_FILE", source_work / "overrides.json")):
        shutil.copy2(native_filesystem_path(source), native_filesystem_path(getattr(config, name)))
    # Copy compatibility exports with their original times, but use SQLite's
    # online backup for one consistent primary read snapshot (never copy WAL).
    shutil.copytree(native_filesystem_path(source_raw), native_filesystem_path(config.RAW_DIR), dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("codemaps.db", "codemaps.db-wal", "codemaps.db-shm"))
    with closing(sqlite3.connect(native_filesystem_path(source_raw / "codemaps.db"))) as src:
        with closing(sqlite3.connect(native_filesystem_path(config.RAW_DIR / "codemaps.db"))) as dst:
            src.backup(dst)
    config.DYNAMIC_CONFIG.update(compiled)
    config.SKIP_DIRS = set(compiled["skip_dirs"])
    config.SOURCE_EXTENSIONS = config._resolve_source_extensions(compiled)
    config.ARCH_CONFIG = compiled["architecture"]
    config.ENVIRONMENT = compiled["environment"]
    config.PLUGINS = set(compiled.get("plugins", []))
    config.PROJECT_FILTER = []
    # Keep query telemetry in this fixture, without relocating central policy.
    config.PRODUCT_OPERATIONAL_DIR = work / "output" / ".operational"
    config.MCP_OPERATIONAL_DIR = config.PRODUCT_OPERATIONAL_DIR / "mcp"
    config.MCP_CALL_TELEMETRY_DB = config.MCP_OPERATIONAL_DIR / "calls.db"
    config.MCP_HONESTY_TELEMETRY_FILE = config.MCP_OPERATIONAL_DIR / "honesty.json"
    if mode == "external":
        from tools import external_target_preflight as preflight
        preflight.EXTERNAL_TARGETS_DIR = work / "output" / "external_targets"
        os.environ["CODEMAPS_EXTERNAL_RUN_ID"] = "differential-fixture"
        persisted = preflight.persist_preflight(preflight.build_preflight(root))
        transport = preflight.preflight_receipt_transport(persisted)
        os.environ.update(CODEMAPS_TARGET_ROOT=str(root), CODEMAPS_TARGET_PROJECTS="",
                          CODEMAPS_TARGET_PREFLIGHT_RECEIPT=transport["path"],
                          CODEMAPS_TARGET_PREFLIGHT_RECEIPT_SHA256=transport["sha256"])
        assert config.load_external_target_preflight_receipt(root, require_freshness=True) == persisted
        runtime = config._apply_target_root_override(deepcopy(compiled))
        config.DYNAMIC_CONFIG.clear()
        config.DYNAMIC_CONFIG.update(runtime)

    from tools.core import analysis_snapshot_lineage as lineage, artifact_store, atlas_io
    from tools.core.pipeline_run_receipts import start_pipeline_run_receipt
    from tools.engines import audit, quant_engine, circular_dependency_finder
    from tools.engines import dead_code_detector, clone_detector, live_surface_analyzer
    from tools import generate_nexora_brief, generate_nexora_agent_contract, generate_nexora_operator_packet
    from tools.mcp import server
    # The fixture installation boundary changes; native target slug/current
    # resolution, trust, parser, packet builders and profile checks do not.
    server.BASE_DIR = work
    server.OUTPUT_DIR = work / "output"
    server.RAW_DIR = server.OUTPUT_DIR / ".raw"
    server.REPORTS_DIR = server.OUTPUT_DIR / "reports"
    server.SCRIPTS_DIR = server.OUTPUT_DIR / "scripts"
    assert server._active_mcp_actor_profile() == "target_repository_default"
    target_arg = str(root) if mode == "external" else ""
    primary = atlas_io.load_atlas_data_with_identity()
    assert primary.primary_verified and primary.source == "sqlite_primary"
    assert lineage.load_atlas_commit(config.RAW_DIR) == seed["commit"]
    for name in ("audit_report", "quality_gate"):
        assert lineage.receipt_binding(raw_dir=config.RAW_DIR, artifact_id=name,
            artifact_payload=artifact_store.STORE.load_raw(name, {}),
            expected_snapshot_id=seed["commit"]["snapshot_id"])[0] == "BOUND"
    recorder = start_pipeline_run_receipt(
        command_profile="bounded_packet_differential_fixture", scope="SAGE_ON_REPOSITORY",
        projects=["MAIN", "WIDGET"], run_id="differential-fixture",
        context={"acquisition_mode": "EXPLICIT_TARGET" if mode == "external" else "DEFAULT",
                 "claim_boundary": "Existing packet producer closure only; not full analysis or release proof."},
        db_path=config.RAW_DIR / "codemaps.db", shadow_path=config.RAW_DIR / "pipeline_run_receipt.json")
    assert recorder is not None
    completed = []
    circular_dependency_finder.CircularDependencyFinder().run()
    completed.append("Circular Dependencies")
    dead_code_detector.DeadCodeDetector().run()
    completed.append("Dead Code")
    assert clone_detector.run_clone_detector() is True
    completed.append("Clone Detector")
    live_surface_analyzer.run_live_surface_analyzer()
    completed.append("Live Surface Priority Pack")
    for name in ("circular_deps", "dead_code", "clone_detector", "live_surface_priority_pack"):
        assert lineage.load_lineage_receipt(config.RAW_DIR, name)["status"] == "COMPLETE", name
    assert audit.analyze_project(changed_files=["MAIN::page.tsx"]) is True
    completed.append("scoped Audit for packet focus")
    assert quant_engine.run_quant_engine(focus_files=["MAIN::page.tsx"]) is True
    completed.append("ContextOS Signals")
    generate_nexora_brief.run()
    completed.append("Nexora Brief")
    generate_nexora_agent_contract.run()
    completed.append("Nexora Agent Contract")
    generated = generate_nexora_operator_packet.run()
    completed.append("Nexora Operator Packet")
    assert artifact_store.flush_shadow_writes()
    recorder.progress("evidence_closeout", completed_steps=completed)
    recorder.progress("process_exit_ready")
    recorder.terminal(terminal_status="PASS", exit_code=0, completed_steps=completed,
                      governance_verdict="TARGET_QUALITY_GATE_REMAINS_FAIL")
    generation = None
    if mode == "external":
        from tools.core.external_target_generation import finalize_external_target_generation
        generation = finalize_external_target_generation(target_dir, "differential-fixture", exit_code=0)
        assert generation["state"] == "VALIDATED", generation
    assert server._raw_dir_for_target(target_arg).resolve() == config.RAW_DIR.resolve()
    query = lambda: {
        "operator": json.loads(server.get_operator_packet(format="json", target_root=target_arg)),
        "surgical": json.loads(server.get_surgical_operation_packet(format="json", target_root=target_arg)),
    }
    ready = query()
    signals = artifact_store.STORE.load_raw("signals", {})
    bad = deepcopy(signals)
    bad["summary"]["candidate_files"] += 1
    artifact_store.STORE.save_raw("signals", bad)
    tampered_signal = json.loads(server.get_surgical_operation_packet(format="json", target_root=target_arg))
    # Restore by its real producer, not by rewriting the failing receipt.
    assert quant_engine.run_quant_engine(focus_files=["MAIN::page.tsx"]) is True
    restored = query()
    foreign_packet = deepcopy(generated)
    foreign_packet["target_repository_agent_surface"]["scope_authority"]["scope_authority_id"] = "foreign-cached-scope"
    artifact_store.STORE.save_raw("nexora_operator_packet", foreign_packet)
    mismatched_packet = json.loads(server.get_operator_packet(format="json", target_root=target_arg))
    generate_nexora_operator_packet.run()
    scope = artifact_store.STORE.load_raw("analysis_scope_authority", {})
    changed_scope = deepcopy(scope)
    changed_scope["scope_authority"]["scope_authority_id"] = "changed-after-packet-production"
    artifact_store.STORE.save_raw("analysis_scope_authority", changed_scope)
    stale_operator = json.loads(server.get_operator_packet(format="json", target_root=target_arg))
    # Keep the observed stale query distinct from the existing producer's refusal.
    rebuilt_operator = generate_nexora_operator_packet.run()["target_repository_agent_surface"]
    unknown = work / "unknown-subject"
    unknown.mkdir()
    wrong_target = {
        "operator": server.get_operator_packet(format="json", target_root=str(unknown)),
        "surgical": json.loads(server.get_surgical_operation_packet(format="json", target_root=str(unknown))),
    }
    return {"mode": mode, "raw_dir": str(config.RAW_DIR), "root": str(root),
            "seed_commit": seed["commit"], "generation": generation,
            "generated_agent_surface": generated["target_repository_agent_surface"],
            "signals": signals, "ready": ready, "tampered_signal": tampered_signal,
            "restored": restored, "stale_operator": stale_operator,
            "mismatched_packet": mismatched_packet,
            "rebuilt_operator": rebuilt_operator, "wrong_target": wrong_target}


@pytest.fixture(scope="module")
def packet_runs(request, tmp_path_factory, record_testsuite_property):
    retained = os.environ.get("SAGE_TEST_ACQUISITION_RETAINED_BASELINE")
    if retained:
        import ast
        proof = json.loads(Path(retained).read_text(encoding="utf-8"))
        for path, identity in proof["g1_report"]["source_sha256"].items():
            if path != "tools/tests/test_acquisition_differential_acceptance.py":
                assert hashlib.sha256((REPO / path).read_bytes()).hexdigest() == identity, path
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        functions = {n.name: hashlib.sha256(ast.dump(n, include_attributes=False).encode()).hexdigest()
                     for n in tree.body if isinstance(n, ast.FunctionDef)}
        assert all(functions[name] == identity for name, identity in proof["g1_function_ast_sha256"].items())
        seeds = {}
        for mode, archived in proof["g1_report"]["archives"].items():
            path = REPO / archived["archive"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == archived["archive_sha256"]
            seeds[mode] = json.loads(path.read_text(encoding="utf-8"))
    else:
        # A normal distribution run acquires once for both G1 and G2 in this module.
        seeds = request.getfixturevalue("differential_runs")
    base = tmp_path_factory.mktemp("packet-differential")
    env = {k: v for k, v in os.environ.items() if k not in INHERITED_ENV}
    env.update(SAGE_SYNC_SHADOW_WRITES="1", PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8",
               SAGE_MCP_TOOL_PROFILE="target_repository_default", SAGE_ACTOR_PROFILE="target_repository_default")
    results = {}
    for mode in ("normal", "external"):
        work = base / mode
        work.mkdir()
        seed_path = work / "seed.json"
        seed_path.write_text(json.dumps(seeds[mode]), encoding="utf-8")
        child = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()),
                                "--native-packets", str(seed_path), str(work), mode],
                               cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8", timeout=180)
        (work / "driver.log").write_text(child.stdout + child.stderr, encoding="utf-8")
        assert child.returncode == 0, f"{mode}: {work / 'driver.log'}\n{child.stderr[-5000:]}\n{child.stdout[-1000:]}"
        path = work / "result.json"
        results[mode] = json.loads(path.read_text(encoding="utf-8"))
        record_testsuite_property(f"packet_{mode}_evidence", str(path))
    return results


@pytest.mark.parametrize("mode", ["normal", "external"])
def test_packet_real_mcp_ready_chain_is_nonvacuous(packet_runs, mode):
    row = packet_runs[mode]
    assert row["ready"]["operator"]["status"] == "READY"
    assert row["ready"]["operator"]["directives"]
    assert row["ready"]["surgical"]["status"] == "ACCEPTED", row["ready"]["surgical"]
    assert row["ready"]["surgical"]["l1_focus"]
    assert row["ready"]["surgical"]["source_grounding"]["drift_check_status"] == "match"
    assert row["restored"]["surgical"]["status"] == "ACCEPTED"


@pytest.mark.parametrize("mode", ["normal", "external"])
def test_packet_real_mcp_rejects_tampered_signals_and_wrong_target(packet_runs, mode):
    row = packet_runs[mode]
    assert row["tampered_signal"]["status"] == "INVALID_CONTEXT", row["tampered_signal"]
    assert "status: missing_required_target_artifacts" in row["wrong_target"]["operator"]
    assert row["wrong_target"]["surgical"]["status"] == "INVALID_CONTEXT"


@pytest.mark.parametrize("mode", ["normal", "external"])
def test_packet_operator_cached_scope_cannot_remain_ready_after_drift(packet_runs, mode):
    row = packet_runs[mode]
    assert row["rebuilt_operator"]["status"] == "INCOMPLETE_EVIDENCE"
    assert row["stale_operator"]["status"] != "READY", row["stale_operator"]
    assert row["mismatched_packet"]["status"] == "INVALID_CONTEXT"


def _packet_semantics(row, name):
    result = deepcopy(row["ready"][name])
    snapshot = row["seed_commit"]["snapshot_id"]
    assert result["analysis_root"] == row["root"]
    if name == "operator":
        route = result["primary_tool_followup"]
        assert route["target_root"] == (row["root"] if row["mode"] == "external" else "")
        assert route["execution_authority"] == "not_granted"
        route["target_root"] = "<same-subject-bound-route>"
    else:
        path_evidence = result["capability_activation"]["input_evidence"]
        assert path_evidence["source_artifact"] == str(Path(row["raw_dir"]) / "capability_activation_plan.json")
        path_evidence["source_artifact"] = "<own-raw>/capability_activation_plan.json"
        blueprint = result["architecture_governance_context"]["repository_blueprint"]
        assert blueprint["proposal_identity_status"] == "BOUND"
        assert blueprint["evidence"]["snapshot_binding"]["status"] == "BOUND"
        assert blueprint["evidence"]["snapshot_binding"]["atlas_snapshot_id"] == snapshot
        assert blueprint["proposal_identity"].startswith("sha256:") and len(blueprint["proposal_identity"]) == 71
        # This source-produced proposal includes its own Atlas ID. Policy/HITL
        # state, scope and all classification facts remain in the comparison.
        blueprint["proposal_identity"] = "<own-snapshot-bound-advisory-proposal>"

    def current_ids(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"atlas_snapshot_id", "dependency_snapshot_id", "expected_snapshot_id", "bound_snapshot_id"} and child is not None:
                    assert child == snapshot, key
                    value[key] = "<verified-own-snapshot>"
                else:
                    current_ids(child)
        elif isinstance(value, list):
            for child in value:
                current_ids(child)
    current_ids(result)
    return result


@pytest.mark.parametrize("name", ["operator", "surgical"])
def test_packet_real_mcp_facts_focus_scope_and_directives_match(packet_runs, name):
    assert _packet_semantics(packet_runs["normal"], name) == _packet_semantics(packet_runs["external"], name)


def test_packet_comparison_cannot_erase_foreign_snapshot(packet_runs):
    changed = deepcopy(packet_runs["normal"])
    changed["ready"]["surgical"]["l1_focus"][0]["dependency_snapshot_id"] = "foreign"
    with pytest.raises(AssertionError):
        _packet_semantics(changed, "surgical")


def test_packet_unknown_system_scope_cannot_request_a_weaker_trust_profile(tmp_path):
    from tools.core.artifact_trust import build_artifact_trust_summary
    with pytest.raises(ValueError, match="recognized execution system scope"):
        build_artifact_trust_summary(tmp_path, system_scope="UNKNOWN")


@pytest.mark.parametrize("packet_time", [0, 1])
def test_packet_cached_projection_requires_a_current_validation_epoch(tmp_path, monkeypatch, packet_time):
    from tools.mcp import server
    scope = {"scope_authority_id": "epoch-predicate-unit-fixture", "evidence_status": "COMPLETE_REPOSITORY",
             "claim_scope": "supported_source_repository", "full_repository_claim_eligible": True}
    trust = {"status": "PASS", "checks": [], "failures": [], "scope": scope,
             "freshness": {"atlas_mtime": 2}}
    before = deepcopy(trust)
    # Only the epoch predicate is under test; native admission is proved above.
    monkeypatch.setattr(server, "_ensure_agent_artifact_chain_current", lambda *a, **k: trust)
    monkeypatch.setattr(server, "artifact_state_meta", lambda *a, **k: {"validation_mtime": packet_time})
    result = json.loads(server._current_operator_agent_surface(
        {"analysis_root": str(tmp_path), "scope_authority": scope, "directives": []},
        raw_dir=tmp_path, target_root=str(tmp_path), requested_format="json"))
    assert result["status"] == "INVALID_CONTEXT"
    assert any(row["name"] == "operator_packet:not_older_than_canonical_inputs"
               for row in result["artifact_trust"]["failures"])
    assert trust == before


def _run_native_leases(seed, work, mode):
    sys.path.insert(0, str(REPO))
    from tools.tests.acquisition_lease_fixture import run_native_leases
    return run_native_leases(seed, work, mode, repository_root=REPO, bind_runtime=_bind_fixture_runtime)


@pytest.fixture(scope="module")
def lease_runs(request, tmp_path_factory, record_testsuite_property):
    retained = os.environ.get("SAGE_TEST_ACQUISITION_RETAINED_BASELINE")
    if retained:
        import ast
        proof = json.loads(Path(retained).read_text(encoding="utf-8"))
        for path, identity in proof["g1_report"]["source_sha256"].items():
            if path != "tools/tests/test_acquisition_differential_acceptance.py":
                assert hashlib.sha256((REPO / path).read_bytes()).hexdigest() == identity, path
        functions = {n.name: hashlib.sha256(ast.dump(n, include_attributes=False).encode()).hexdigest()
                     for n in ast.parse(Path(__file__).read_text(encoding="utf-8")).body
                     if isinstance(n, ast.FunctionDef)}
        assert all(functions[k] == v for k, v in proof["g1_function_ast_sha256"].items())
        seeds = {}
        for mode, archived in proof["g1_report"]["archives"].items():
            path = REPO / archived["archive"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == archived["archive_sha256"]
            seeds[mode] = json.loads(path.read_text(encoding="utf-8"))
    else:
        seeds = request.getfixturevalue("differential_runs")
    base = tmp_path_factory.mktemp("lease-differential")
    env = {k: v for k, v in os.environ.items() if k not in INHERITED_ENV}
    env.update(SAGE_SYNC_SHADOW_WRITES="1", PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8",
               SAGE_MCP_TOOL_PROFILE="target_repository_followup", SAGE_ACTOR_PROFILE="target_repository_followup")
    results = {}
    for mode in ("normal", "external"):
        work = base / mode
        work.mkdir()
        seed_path = work / "seed.json"
        seed_path.write_text(json.dumps(seeds[mode]), encoding="utf-8")
        child = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()),
                                "--native-leases", str(seed_path), str(work), mode],
                               cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
        (work / "driver.log").write_text(child.stdout + child.stderr, encoding="utf-8")
        assert child.returncode == 0, f"{mode}: {work / 'driver.log'}\n{child.stderr[-5000:]}\n{child.stdout[-1000:]}"
        path = work / "result.json"
        results[mode] = json.loads(path.read_text(encoding="utf-8"))
        record_testsuite_property(f"lease_{mode}_evidence", str(path))
    return results


@pytest.mark.parametrize("mode", ["normal", "external"])
def test_lease_real_mcp_owner_busy_release_and_handoff(lease_runs, mode):
    row = lease_runs[mode]
    outcomes = row["outcomes"]
    assert {k: outcomes[k]["status"] for k in ("acquire", "busy", "not_owner", "renew", "release", "released_again", "handoff", "handoff_release")} == {
        "acquire": "acquired", "busy": "busy", "not_owner": "not_owner", "renew": "renewed",
        "release": "released", "released_again": "not_found", "handoff": "acquired", "handoff_release": "released"}
    first = outcomes["acquire"]["lease"]
    assert first["analysis_root"] == row["root"] and first["target_file"] == "page.tsx"
    assert first["actor_id"] == "agent-a" and first["source_snapshot_hash"] == row["expected_source_hash"]
    assert outcomes["busy"]["lease"]["lease_key"] == outcomes["handoff"]["lease"]["lease_key"] == first["lease_key"]
    assert outcomes["busy"]["lease"]["actor_id"] == outcomes["not_owner"]["lease"]["actor_id"] == "agent-a"
    assert row["final_lease"] == {"status": "available", "lease": None}
    assert row["subject_source_unchanged"] and row["subject_code_executed"] is False


@pytest.mark.parametrize("mode", ["normal", "external"])
def test_lease_real_mcp_rejects_unavailable_and_stale_grounding(lease_runs, mode):
    outcomes = lease_runs[mode]["outcomes"]
    for case in ("unindexed", "outside", "wrong_target", "tampered_snapshot", "live_drift", "missing_snapshot"):
        assert outcomes[case]["status"] == "not_grounded", (case, outcomes[case])
        assert not all(outcomes[case]["required_source_grounding"].values())
        assert "lease" not in outcomes[case]
    assert outcomes["tampered_snapshot"]["target_path_status"]["source_snapshot_status"] == "content_mismatch"
    assert outcomes["live_drift"]["target_path_status"]["source_snapshot_status"] == "ok"
    assert outcomes["live_drift"]["target_path_status"]["drift_check_status"] == "mismatch"
    assert outcomes["missing_snapshot"]["target_path_status"]["source_snapshot_status"] == "missing"


def _lease_semantics(row):
    result = deepcopy(row["outcomes"])
    for payload in result.values():
        assert payload["analysis_root"] == row["root"] or payload["status"] == "not_grounded"
        if payload.get("lease"):
            lease = payload["lease"]
            assert lease["analysis_root"] == row["root"] and lease["target_file"] == "page.tsx"
            assert lease["acquired_at_epoch"] <= lease["renewed_at_epoch"] < lease["expires_at_epoch"]
            assert 0 < lease["remaining_seconds"] <= 300
            # Compare the effective TTL rather than hiding a route-specific TTL
            # under independently valid absolute acquisition timestamps.
            lease["checked_ttl_seconds"] = lease["expires_at_epoch"] - lease["renewed_at_epoch"]
            for key in ("acquired_at_epoch", "renewed_at_epoch", "expires_at_epoch", "remaining_seconds"):
                lease[key] = "<checked-lease-clock>"
        if payload.get("retry_after_seconds"):
            assert payload["status"] == "busy" and 0 < payload["retry_after_seconds"] <= 300
            payload["retry_after_seconds"] = "<checked-busy-wait>"
        if payload["analysis_root"] != row["root"]:
            # Unknown-target refusal must stay bound to that exact requested root.
            assert payload is result["wrong_target"]
            assert payload["analysis_root"] == row["unknown_target"]
            payload["analysis_root"] = "<explicit-unindexed-target>"
    return result


def test_lease_real_mcp_full_decisions_and_grounding_match(lease_runs):
    assert _lease_semantics(lease_runs["normal"]) == _lease_semantics(lease_runs["external"])
    assert lease_runs["normal"]["db_path"] != lease_runs["external"]["db_path"]


@pytest.mark.parametrize("field,value", [("actor_id", "foreign-owner"), ("source_snapshot_hash", "foreign-source")])
def test_lease_comparison_cannot_hide_a_foreign_owner_or_source(lease_runs, field, value):
    original = _lease_semantics(lease_runs["normal"])
    changed = deepcopy(lease_runs["normal"])
    changed["outcomes"]["busy"]["lease"][field] = value
    assert _lease_semantics(changed) != original


@pytest.fixture(scope="module")
def watchdog_runs(request, tmp_path_factory, record_testsuite_property):
    from tools.tests.acquisition_watchdog_fixture import paired_watchdog_runs
    return paired_watchdog_runs(request, tmp_path_factory, record_testsuite_property,
                                repository_root=REPO, inherited_env=INHERITED_ENV)


@pytest.mark.parametrize("module", ["acquisition_lease_fixture", "acquisition_watchdog_fixture"])
def test_acquisition_helpers_do_not_import_acceptance_driver(module):
    # Fresh interpreters catch the back-edge even if pytest already loaded this
    # module. Helpers must not import pytest or bind the production runtime.
    script = """
import importlib
import inspect
import sys
module = importlib.import_module(sys.argv[1])
assert 'pytest' not in sys.modules
assert 'tools.core.config' not in sys.modules
assert not any(name.endswith('test_acquisition_differential_acceptance') for name in sys.modules)
for name in ('run_native_leases', 'run_native_watchdog', 'paired_watchdog_runs'):
    function = getattr(module, name, None)
    if function is not None:
        try:
            inspect.signature(function).bind(None, None, None)
        except TypeError:
            pass  # Context must be explicit, never silently imported/fallback.
        else:
            raise AssertionError(name + ' accepts missing fixture context')
"""
    child = subprocess.run([sys.executable, "-B", "-c", script, "tools.tests." + module],
                           cwd=REPO, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert child.returncode == 0, child.stdout + child.stderr


@pytest.mark.parametrize("mode", ["normal", "external"])
def test_watchdog_real_pulse_is_file_bounded_and_namespace_bound(watchdog_runs, mode):
    row = watchdog_runs[mode]
    assert row["code"] == 0, row["session"]
    assert row["atlas_calls"] == [{"stale_projects": ["MAIN"], "dry_run": False, "surgical_files": ["MAIN::page.tsx"]}]
    assert row["atlas_jobs"] == [{"project": "MAIN", "root": row["root"], "surgical_files": ["page.tsx"]}]
    assert row["unchanged_unselected_project"] and row["canonical_audit_and_gate_unchanged"]
    assert row["subject_source_unchanged"] and not row["rejected_writes"]
    session = row["session"]
    assert session["summary"]["analysis_status"] == "completed" and session["summary"]["not_release_proof"] is True
    assert session["scoped_audit_validation"]["status"] == "valid"
    assert session["scoped_audit_validation"]["scope_match"] is True
    assert session["path_contract"]["analysis_root"] == row["root"]
    assert row["pipeline_receipt"]["status"] == "PASS"
    assert row["pulse_commit"]["generation_transition"]["parent_snapshot_id"] == row["seed_commit"]["snapshot_id"]
    assert row["pulse_commit"]["generation_transition"]["changed_files"] == ["MAIN::page.tsx"]
    assert row["written_paths"]
    for command in row["subprocess_commands"]:
        import shlex
        # Windows Popen emits a command-line string with executable=None;
        # POSIX emits argv. Neither representation proves binary authenticity.
        argv = shlex.split(command["argv"], posix=False) if isinstance(command["argv"], str) else command["argv"]
        assert Path(argv[0].strip('"')).name.lower() in {"node", "node.exe"}
        assert Path(argv[1].strip('"')).resolve() == REPO / "tools/engines/ast_sequencer.cjs"
    if mode == "external":
        assert not (Path(row["work"]) / "output/.raw").exists()


@pytest.mark.parametrize("mode", ["normal", "external"])
def test_watchdog_mixed_escape_rejects_without_partial_pulse(watchdog_runs, mode):
    assert watchdog_runs[mode]["rejected_change_set"] == {"code": 1, "extra_atlas_calls": 0, "extra_writes": []}


def _watchdog_semantics(row):
    # Compare the whole target-facing decision body, not clock/proof advice for
    # different acquisition connections. Each own binding is checked above.
    session = row["session"]
    result = {name: deepcopy(session[name]) for name in (
        "summary", "input_origin", "input_files", "changed_files", "sample_files", "change_events", "acquisition", "violations")}
    elapsed = result["summary"].pop("elapsed_seconds")
    assert 0 <= elapsed <= row["elapsed_seconds"] + 1
    return result


def test_watchdog_target_decisions_and_workload_match(watchdog_runs):
    assert _watchdog_semantics(watchdog_runs["normal"]) == _watchdog_semantics(watchdog_runs["external"])
    assert [r["name"] for r in watchdog_runs["normal"]["pipeline_metrics"]["steps"]] == [
        r["name"] for r in watchdog_runs["external"]["pipeline_metrics"]["steps"]]


def test_watchdog_comparison_cannot_hide_a_changed_scope_or_finding(watchdog_runs):
    row = deepcopy(watchdog_runs["normal"])
    row["session"]["input_files"] = ["packages/widget/value.ts"]
    assert _watchdog_semantics(row) != _watchdog_semantics(watchdog_runs["normal"])
    row = deepcopy(watchdog_runs["normal"])
    row["session"]["violations"] = []
    assert row["session"]["summary"]["violation_count"] > 0
    assert _watchdog_semantics(row) != _watchdog_semantics(watchdog_runs["normal"])


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--native-watchdog":
        sys.path.insert(0, str(REPO))
        from tools.tests.acquisition_watchdog_fixture import run_native_watchdog
        watchdog_seed = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        watchdog_work = Path(sys.argv[3])
        watchdog_result = run_native_watchdog(watchdog_seed, watchdog_work, sys.argv[4],
                                              repository_root=REPO, bind_runtime=_bind_fixture_runtime)
        (watchdog_work / "result.json").write_text(json.dumps(watchdog_result, ensure_ascii=False, indent=2), encoding="utf-8")
        raise SystemExit(0)
    if len(sys.argv) == 5 and sys.argv[1] == "--native-leases":
        lease_seed = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        lease_work = Path(sys.argv[3])
        lease_result = _run_native_leases(lease_seed, lease_work, sys.argv[4])
        (lease_work / "result.json").write_text(json.dumps(lease_result, ensure_ascii=False, indent=2), encoding="utf-8")
        raise SystemExit(0)
    if len(sys.argv) == 5 and sys.argv[1] == "--native-packets":
        packet_seed = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        packet_work = Path(sys.argv[3])
        packet_result = _run_native_packets(packet_seed, packet_work, sys.argv[4])
        (packet_work / "result.json").write_text(json.dumps(packet_result, ensure_ascii=False, indent=2), encoding="utf-8")
        raise SystemExit(0)
    assert len(sys.argv) == 5 and sys.argv[1] == "--native-subject"
    subject, work_dir, acquisition = Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
    assert acquisition in {"normal", "external"}
    result = _run_native_subject(subject, work_dir, acquisition)
    (work_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
