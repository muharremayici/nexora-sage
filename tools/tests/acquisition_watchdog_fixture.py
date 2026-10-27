"""Real bounded Watchdog acquisition probe; never execute target code."""
from contextlib import closing
from copy import deepcopy
import functools
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading
import time

def paired_watchdog_runs(request, tmp_path_factory, record_testsuite_property, *, repository_root, inherited_env):
    retained = os.environ.get("SAGE_TEST_ACQUISITION_RETAINED_BASELINE")
    if retained:
        import ast
        proof = json.loads(Path(retained).read_text(encoding="utf-8"))
        for path, identity in proof["g1_report"]["source_sha256"].items():
            if path != "tools/tests/test_acquisition_differential_acceptance.py":
                assert hashlib.sha256((repository_root / path).read_bytes()).hexdigest() == identity, path
        functions = {n.name: hashlib.sha256(ast.dump(n, include_attributes=False).encode()).hexdigest()
                     for n in ast.parse((repository_root / "tools/tests/test_acquisition_differential_acceptance.py").read_text(encoding="utf-8")).body
                     if isinstance(n, ast.FunctionDef)}
        assert all(functions[k] == v for k, v in proof["g1_function_ast_sha256"].items())
        seeds = {}
        for mode, archived in proof["g1_report"]["archives"].items():
            path = repository_root / archived["archive"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == archived["archive_sha256"]
            seeds[mode] = json.loads(path.read_text(encoding="utf-8"))
    else:
        # Same module-scoped acquisition as G1/G2/G3, not a second base campaign.
        seeds = request.getfixturevalue("differential_runs")
    base = tmp_path_factory.mktemp("watchdog-differential")
    env = {k: v for k, v in os.environ.items() if k not in inherited_env}
    env.update(SAGE_SYNC_SHADOW_WRITES="1", PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8",
               SAGE_MCP_TOOL_PROFILE="target_repository_default", SAGE_ACTOR_PROFILE="target_repository_default",
               CODEMAPS_PIPELINE_WORKERS="1")
    results = {}
    for mode in ("normal", "external"):
        work = base / mode
        work.mkdir()
        seed_path = work / "seed.json"
        seed_path.write_text(json.dumps(seeds[mode]), encoding="utf-8")
        child = subprocess.run([sys.executable, "-B", str(repository_root / "tools/tests/test_acquisition_differential_acceptance.py"),
                                "--native-watchdog", str(seed_path), str(work), mode], cwd=repository_root,
                               env=env, capture_output=True, text=True, encoding="utf-8", timeout=180)
        (work / "driver.log").write_text(child.stdout + child.stderr, encoding="utf-8")
        assert child.returncode == 0, f"{mode}: {work / 'driver.log'}\n{child.stderr[-6000:]}\n{child.stdout[-1500:]}"
        path = work / "result.json"
        results[mode] = json.loads(path.read_text(encoding="utf-8"))
        record_testsuite_property(f"watchdog_{mode}_evidence", str(path))
    return results


def run_native_watchdog(seed, work, mode, *, repository_root, bind_runtime):
    import tempfile
    from tools.core.unmanaged_atomic_io import native_filesystem_path
    scratch = work / "scratch"
    Path(native_filesystem_path(scratch)).mkdir()
    tempfile.tempdir = str(scratch)
    os.environ.update(TMP=str(scratch), TEMP=str(scratch), TMPDIR=str(scratch))
    from tools.core import config
    source_raw = Path(seed["raw_dir"])
    source_work = source_raw.parent.parent if mode == "normal" else source_raw.parents[4]
    compiled = json.loads((source_work / "runtime.json").read_text(encoding="utf-8"))
    root = (repository_root / compiled["workspace_root"]).resolve()
    assert source_raw.is_dir() and root.is_dir()
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
    primary = atlas_io.load_atlas_data_with_identity()
    assert primary.primary_verified and primary.source == "sqlite_primary"
    assert lineage.load_atlas_commit(config.RAW_DIR) == seed["commit"]
    for name in ("audit_report", "quality_gate"):
        assert lineage.receipt_binding(raw_dir=config.RAW_DIR, artifact_id=name,
            artifact_payload=artifact_store.STORE.load_raw(name, {}),
            expected_snapshot_id=seed["commit"]["snapshot_id"])[0] == "BOUND"
    canonical_audit = artifact_store.STORE.load_raw("audit_report", {})
    canonical_gate = artifact_store.STORE.load_raw("quality_gate", {})

    from tools.orchestrators import watchdog, orchestrator
    from tools.engines import generate_atlas
    # Only the isolated fixture installation's coordination endpoints move;
    # lock acquisition, policy, scheduler and producer decisions remain native.
    lock = orchestrator.pipeline_lock_policy()
    orchestrator._PIPELINE_LOCK_PATH = work / lock["endpoint"]
    orchestrator._PIPELINE_LOCK_DIAGNOSTIC_PATH = work / lock["diagnostic_endpoint"]
    calls, jobs, commands, writes, rejected_writes = [], [], [], set(), []
    original_generate = generate_atlas.generate_atlas

    @functools.wraps(original_generate)
    def observed_generate(*args, **kwargs):
        calls.append(deepcopy(kwargs))
        return original_generate(*args, **kwargs)

    generate_atlas.generate_atlas = observed_generate

    def profile(frame, event, arg):
        if event == "call" and frame.f_code.co_name == "build_project_atlas" and Path(frame.f_code.co_filename).resolve() == Path(generate_atlas.__file__).resolve():
            local = frame.f_locals
            jobs.append({"project": local["pkey"], "root": str(local["ppath"]),
                         "surgical_files": sorted(local.get("surgical_rel_paths") or [])})

    observing = True

    def check_write(path):
        if isinstance(path, int) or not isinstance(path, (str, bytes, os.PathLike)):
            return
        value = os.fsdecode(path)
        if value.startswith("\\\\?\\"):
            value = value[4:]
        resolved = Path(value).resolve()
        if not resolved.is_relative_to(work):
            rejected_writes.append(str(resolved))
            raise PermissionError(f"Probe refuses writes outside its fixture: {resolved}")
        writes.add(str(resolved.relative_to(work)))

    def audit(event, args):
        if not observing:
            return
        if event == "subprocess.Popen":
            commands.append({"executable": str(args[0]) if args[0] is not None else None,
                             "argv": list(args[1]) if isinstance(args[1], (list, tuple)) else str(args[1]),
                             "cwd": str(args[2] or "")})
        elif event == "sqlite3.connect" and str(args[0]) != ":memory:" and "mode=ro" not in str(args[0]):
            check_write(args[0])
        elif event == "open":
            mode, flags = args[1], args[2]
            if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
                check_write(args[0])
        elif event in {"os.rename", "os.replace"}:
            check_write(args[0])
            check_write(args[1])
        elif event in {"os.remove", "os.mkdir", "os.rmdir"}:
            check_write(args[0])

    sys.addaudithook(audit)
    threading.setprofile(profile)
    started = time.perf_counter()
    try:
        selection = watchdog.collect_watch_input_selection(root / "page.tsx")
        code = watchdog.run_watchdog_once(root / "page.tsx", 0.0, mode="ADVISE")
        elapsed = time.perf_counter() - started
        session = artifact_store.STORE.load_raw("watchdog_session", {})
        scoped_audit = artifact_store.STORE.load_raw("watchdog_audit_report", {})
        scoped_graph = artifact_store.STORE.load_raw("watchdog_graph_advisories", {})
        signals = artifact_store.STORE.load_raw("signals", {})
        pulse_commit = lineage.load_atlas_commit(config.RAW_DIR)
        after_atlas = atlas_io.load_atlas_data_with_identity()
        receipt = artifact_store.STORE.load_raw("pipeline_run_receipt", {})
        metrics = json.loads((config.REPORTS_DIR / "pipeline_metrics.json").read_text(encoding="utf-8"))
        calls_before_reject = len(calls)
        writes_before_reject = set(writes)
        rejected = watchdog.run_watchdog_once([root / "page.tsx", work / "outside.tsx"], 0.0, mode="ADVISE")
        rejection_writes = sorted(writes - writes_before_reject)
    finally:
        observing = False
        threading.setprofile(None)
        generate_atlas.generate_atlas = original_generate
    assert artifact_store.STORE.load_raw("audit_report", {}) == canonical_audit
    assert artifact_store.STORE.load_raw("quality_gate", {}) == canonical_gate
    after = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in root.rglob("*") if p.is_file()}
    assert before == after
    result = {"mode": mode, "root": str(root), "raw_dir": str(config.RAW_DIR), "work": str(work),
        "selection": selection, "code": code, "elapsed_seconds": elapsed, "atlas_calls": calls, "atlas_jobs": jobs,
        "subprocess_commands": commands, "written_paths": sorted(writes), "rejected_writes": rejected_writes,
        "session": session, "scoped_audit": scoped_audit, "scoped_graph": scoped_graph, "signals": signals,
        "seed_commit": seed["commit"], "pulse_commit": pulse_commit, "pipeline_receipt": receipt, "pipeline_metrics": metrics,
        "unchanged_unselected_project": after_atlas.data["WIDGET"] == primary.data["WIDGET"],
        "subject_source_unchanged": before == after, "canonical_audit_and_gate_unchanged": True,
        "rejected_change_set": {"code": rejected, "extra_atlas_calls": len(calls) - calls_before_reject, "extra_writes": rejection_writes},
        "boundary": "Existing-file explicit rescan; not a real edit, target execution, OS observer delivery test, subprocess write sandbox or large-repository benchmark."}
    return result
