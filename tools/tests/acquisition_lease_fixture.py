"""Native paired MCP lease fixture; not target execution or release proof."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys

def run_native_leases(seed, work, mode, *, repository_root, bind_runtime):
    """Exercise the real MCP lease route, without writing any subject source."""
    import shutil
    import sqlite3
    from contextlib import closing

    sys.path.insert(0, str(repository_root))
    from tools.core import config
    from tools.core.unmanaged_atomic_io import native_filesystem_path
    source_raw = Path(seed["raw_dir"])
    source_work = source_raw.parent.parent if mode == "normal" else source_raw.parents[4]
    compiled = json.loads((source_work / "runtime.json").read_text(encoding="utf-8"))
    root = (repository_root / compiled["workspace_root"]).resolve()
    assert root.is_dir() and source_raw.is_dir()
    before = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in root.rglob("*") if p.is_file()}
    if mode == "external":
        from tools.core.external_target_generation import begin_external_target_generation
        target_dir = work / "output" / "external_targets" / config.external_target_output_slug(str(root))
        begin_external_target_generation(target_dir, "differential-fixture", root)
    bind_runtime(config, root, work / "output" if mode == "external" else work, mode)
    for name, source in (("CONFIG_FILE", source_work / "runtime.json"),
                         ("DISCOVERY_FILE", source_work / "discovery.json"),
                         ("OVERRIDES_FILE", source_work / "overrides.json")):
        shutil.copy2(native_filesystem_path(source), native_filesystem_path(getattr(config, name)))
    shutil.copytree(native_filesystem_path(source_raw), native_filesystem_path(config.RAW_DIR), dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("codemaps.db", "codemaps.db-wal", "codemaps.db-shm"))
    db = config.RAW_DIR / "codemaps.db"
    with closing(sqlite3.connect(native_filesystem_path(source_raw / "codemaps.db"))) as src:
        with closing(sqlite3.connect(native_filesystem_path(db))) as dst:
            src.backup(dst)
    config.DYNAMIC_CONFIG.update(compiled)
    config.PROJECT_FILTER = []
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
    from tools.core.target_write_lease import inspect_target_write_lease
    from tools.mcp import server
    server.BASE_DIR = work
    server.OUTPUT_DIR = work / "output"
    server.RAW_DIR = server.OUTPUT_DIR / ".raw"
    server.REPORTS_DIR = server.OUTPUT_DIR / "reports"
    server.SCRIPTS_DIR = server.OUTPUT_DIR / "scripts"
    assert server._active_mcp_actor_profile() == "target_repository_followup"
    target_arg = str(root) if mode == "external" else ""
    primary = atlas_io.load_atlas_data_with_identity()
    assert primary.primary_verified and primary.source == "sqlite_primary"
    assert lineage.load_atlas_commit(config.RAW_DIR) == seed["commit"]
    for name in ("audit_report", "quality_gate"):
        assert lineage.receipt_binding(raw_dir=config.RAW_DIR, artifact_id=name,
            artifact_payload=artifact_store.STORE.load_raw(name, {}),
            expected_snapshot_id=seed["commit"]["snapshot_id"])[0] == "BOUND"
    assert artifact_store.STORE.load_raw("quality_gate", {})["release_gate_status"] == "FAIL"
    # This records only checked base reuse, not a newly executed analysis suite.
    recorder = start_pipeline_run_receipt(
        command_profile="bounded_lease_base_admission_fixture", scope="SAGE_ON_REPOSITORY",
        projects=["MAIN", "WIDGET"], run_id="differential-fixture",
        context={"claim_boundary": "Verified retained Atlas/Audit/Quality base only; no analysis replay or release proof."},
        db_path=db, shadow_path=config.RAW_DIR / "pipeline_run_receipt.json")
    assert recorder is not None
    completed = ["verified SQLite primary", "own Atlas commit", "bound Audit and Quality receipts"]
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
    assert Path(server._analysis_root_display(target_arg)).resolve() == root
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM target_write_leases").fetchone()[0] == 0

    def call(action, actor, file="MAIN::page.tsx", *, target=target_arg):
        return json.loads(server.manage_target_write_lease(
            action, file, actor, target_root=target, format="json"))

    outcomes = {}
    outcomes["acquire"] = call("acquire", "agent-a")
    outcomes["busy"] = call("acquire", "agent-b")
    outcomes["not_owner"] = call("release", "agent-b")
    outcomes["renew"] = call("acquire", "agent-a")
    outcomes["release"] = call("release", "agent-a")
    outcomes["released_again"] = call("release", "agent-a")
    outcomes["handoff"] = call("acquire", "agent-b", "page.tsx")
    outcomes["handoff_release"] = call("release", "agent-b", "page.tsx")
    outcomes["unindexed"] = call("acquire", "agent-a", "not-indexed.tsx")
    outcomes["outside"] = call("acquire", "agent-a", "../outside.tsx")
    unknown = work / "unknown-target"
    unknown.mkdir()
    outcomes["wrong_target"] = call("acquire", "agent-a", target=str(unknown))
    with closing(sqlite3.connect(db)) as conn:
        original = conn.execute(
            "SELECT content, content_hash, file_id FROM source_snapshots WHERE project_key='MAIN' AND rel_path='page.tsx'"
        ).fetchone()
        assert original is not None
        # Faults live only in this disposable SQLite projection. Subject bytes,
        # the original seed and its receipts are never edited or reissued.
        conn.execute("UPDATE source_snapshots SET content=content || '\n// tampered snapshot' WHERE file_id=?", (original[2],))
        conn.commit()
    outcomes["tampered_snapshot"] = call("acquire", "agent-a")
    from tools.core.source_snapshot_integrity import source_text_hash
    older = original[0] + "\n// coherent older captured source"
    older_hash = source_text_hash(older, original[1])
    assert older_hash and older_hash != original[1]
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("UPDATE source_snapshots SET content=?, content_hash=? WHERE file_id=?", (older, older_hash, original[2]))
        conn.execute("UPDATE files SET hash=? WHERE file_id=?", (older_hash, original[2]))
        conn.commit()
    outcomes["live_drift"] = call("acquire", "agent-a")
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("DELETE FROM source_snapshots WHERE file_id=?", (original[2],))
        conn.commit()
    outcomes["missing_snapshot"] = call("acquire", "agent-a")
    final_lease = inspect_target_write_lease(db, analysis_root=root, target_file="page.tsx")
    after = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in root.rglob("*") if p.is_file()}
    assert after == before
    if mode == "normal":
        assert not (work / "output" / "external_targets").exists()
    else:
        assert not server.RAW_DIR.exists()
    return {"mode": mode, "root": str(root), "raw_dir": str(config.RAW_DIR), "db_path": str(db),
            "seed_snapshot_id": seed["commit"]["snapshot_id"], "generation": generation,
            "unknown_target": str(unknown), "expected_source_hash": original[1],
            "outcomes": outcomes, "final_lease": final_lease,
            "subject_source_unchanged": after == before, "subject_code_executed": False,
            "final_namespace": "fault_injected_source_projection_not_ready"}
