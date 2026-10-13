from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from tools import run_distribution_tests as distribution_tests
from tools import validate_distribution_test_partition_receipts as partition_receipts


def test_active_distribution_test_inventory_is_exhaustive() -> None:
    payload = distribution_tests._load_profile()
    if distribution_tests.PUBLIC_MANIFEST.is_file():
        tests, target_fixture = distribution_tests._projected_public_tests(payload)
        assert tests
        assert target_fixture
        return

    classified, test_glob = distribution_tests._canonical_classification(payload)
    discovered = {
        path.relative_to(distribution_tests.ROOT).as_posix()
        for path in distribution_tests.ROOT.glob(test_glob)
        if path.is_file()
    }
    assert set(classified) == discovered
    assert set(classified.values()) == {
        "private_maintainer",
        "public_target_repository",
    }


def test_public_profile_contains_no_private_maintainer_test() -> None:
    if distribution_tests.PUBLIC_MANIFEST.is_file():
        tests, _ = distribution_tests.resolve_tests("public_target_repository")
        assert tests
        return

    classified, _ = distribution_tests._canonical_classification(
        distribution_tests._load_profile()
    )
    tests, _ = distribution_tests.resolve_tests("public_target_repository")
    assert tests
    assert all(classified[path] == "public_target_repository" for path in tests)


def test_operational_parity_contract_remains_private_maintainer_only() -> None:
    tests, _ = distribution_tests.resolve_tests("public_target_repository")
    assert "tools/tests/test_operational_parity_contract.py" not in tests

    if distribution_tests.PUBLIC_MANIFEST.is_file():
        return

    classified, _ = distribution_tests._canonical_classification(
        distribution_tests._load_profile()
    )
    assert (
        classified["tools/tests/test_operational_parity_contract.py"]
        == "private_maintainer"
    )


def test_private_recovery_guards_are_not_shipped_but_remain_canonical() -> None:
    private_path = "tools/tests/test_reality_scope_maintainer.py"
    public, _ = distribution_tests.resolve_tests("public_target_repository")
    assert private_path not in public
    if distribution_tests.PUBLIC_MANIFEST.is_file():
        assert not (distribution_tests.ROOT / private_path).exists()
        return
    canonical, _ = distribution_tests.resolve_tests("canonical_all")
    assert private_path in canonical
    classified = distribution_tests.validate_canonical_distribution_test_inventory()
    assert classified[private_path] == "private_maintainer"
    partitions, _, _ = distribution_tests.resolve_test_partitions("canonical_all")
    assert sum(private_path in paths for paths in partitions.values()) == 1


def _assert_primary_followup_public_variant_authority():
    from tools.core.mcp_tool_profiles import PUBLIC_MCP_TOOL_PROFILES
    from tools.tests import test_agent_surface_target_visibility as visibility

    for function in (
        visibility.test_primary_followup_routes_from_actual_connection_without_expanding_profiles,
        visibility.test_primary_followup_watchdog_preserves_currentness_and_exact_root,
    ):
        profile_marks = [mark for mark in function.pytestmark
                         if mark.name == "parametrize" and mark.args[0] == "profile"]
        assert len(profile_marks) == 1
        assert profile_marks[0].args[1]
        assert set(profile_marks[0].args[1]) <= PUBLIC_MCP_TOOL_PROFILES
    assert not hasattr(visibility, "test_primary_followup_sage_self_never_routes_to_target_repository_connection")


def test_primary_followup_variant_authority_preserves_private_canonical_controls():
    _assert_primary_followup_public_variant_authority()
    if distribution_tests.PUBLIC_MANIFEST.is_file():
        assert not (distribution_tests.ROOT / "tools/tests/test_reality_scope_maintainer.py").exists()
        return
    from tools.tests import test_reality_scope_maintainer as private

    expected = [
        (private.test_private_primary_followup_routes_without_expanding_profiles, "target_root", ["", 'C:/fixture/Örnek repo; "scope"']),
        (private.test_private_primary_followup_watchdog_preserves_currentness_and_exact_root, "stale", [False, True]),
    ]
    for function, argument, values in expected:
        marks = [mark for mark in function.pytestmark
                 if mark.name == "parametrize" and mark.args[0] == argument]
        assert len(marks) == 1 and marks[0].args[1] == values
    assert callable(private.test_private_primary_followup_sage_self_never_routes_to_target_repository_connection)


def test_primary_followup_variant_guard_rejects_a_private_profile_regression(monkeypatch):
    from tools.core.execution_identity import SAGE_OPERATOR_ACTOR_PROFILE
    from tools.tests import test_agent_surface_target_visibility as visibility

    function = visibility.test_primary_followup_routes_from_actual_connection_without_expanding_profiles
    marks = [mark for mark in function.pytestmark
             if not (mark.name == "parametrize" and mark.args[0] == "profile")]
    marks.append(pytest.mark.parametrize("profile", ["target_repository_followup", SAGE_OPERATOR_ACTOR_PROFILE]).mark)
    monkeypatch.setattr(function, "pytestmark", marks)
    with pytest.raises(AssertionError):
        _assert_primary_followup_public_variant_authority()


def test_public_setup_command_reuses_the_live_source_ci_owner(tmp_path) -> None:
    from tools.core.quality_gate_ci import load_ci_execution_policy

    target = tmp_path / "target ü space"
    policy = load_ci_execution_policy(distribution_tests.ROOT)
    assert policy["commands"]["target_init"]
    assert distribution_tests._public_test_init_command(target) == [
        sys.executable, "-B", "sage.py", "init", "--skip-deps", "--target-root", str(target)
    ]


@pytest.mark.parametrize("command", [
    'node sage.py init --skip-deps --target-root "placeholder"',
    'python other.py init --skip-deps --target-root "placeholder"',
    'python sage.py run --skip-deps --target-root "placeholder"',
    'python sage.py init --target-root "placeholder"',
    'python sage.py init --skip-deps --target-root ""',
    'python sage.py init --skip-deps --target-root --analyze',
    'python sage.py init --skip-deps --target-root "placeholder" --run-pipeline',
    'python sage.py init --skip-deps --target-root "unterminated',
])
def test_public_setup_rejects_command_drift_before_execution(tmp_path, monkeypatch, command):
    from tools.core import quality_gate_ci

    monkeypatch.setattr(quality_gate_ci, "load_ci_execution_policy",
                        lambda root: {"commands": {"target_init": command}})
    with pytest.raises(ValueError):
        distribution_tests._public_test_init_command(tmp_path / "target")


@pytest.fixture
def isolated_public_test_runner(tmp_path, monkeypatch):
    from tools.core import operational_limits, quality_gate_ci

    root = tmp_path / "distribution"
    fixture = root / "tools" / "tests" / "fixtures" / "src"
    fixture.mkdir(parents=True)
    (fixture / "source.ts").write_text("export const value = 1;\n", encoding="utf-8")
    manifest = root / "PUBLIC_DISTRIBUTION_MANIFEST.json"
    manifest.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(distribution_tests, "ROOT", root)
    monkeypatch.setattr(distribution_tests, "PUBLIC_MANIFEST", manifest)
    monkeypatch.setattr(quality_gate_ci, "load_ci_execution_policy",
                        lambda actual_root: {"commands": {"target_init":
                            'python sage.py init --skip-deps --target-root "$RUNNER_TEMP/sage-ci-target"'}}
                        if actual_root == root else pytest.fail("Wrong setup contract root"))
    monkeypatch.setattr(operational_limits, "install_proof_step_timeout_seconds",
                        lambda step: 123 if step == "init" else pytest.fail("Wrong timeout owner"))
    return root, fixture


def test_public_setup_precedes_pytest_and_uses_only_the_owned_fixture_copy(
    isolated_public_test_runner, monkeypatch
):
    root, fixture = isolated_public_test_runner
    calls = []
    copied_target = []

    def execute(command, **kwargs):
        target = Path(kwargs["env"]["CODEMAPS_TARGET_ROOT"])
        assert target != fixture and target.is_dir()
        assert (target / "source.ts").read_bytes() == (fixture / "source.ts").read_bytes()
        assert kwargs["cwd"] == root and kwargs["check"] is False
        if not calls:
            assert command == [sys.executable, "-B", "sage.py", "init", "--skip-deps",
                               "--target-root", str(target)]
            assert kwargs["timeout"] == 123
            copied_target.append(target)
        else:
            assert command[:4] == [sys.executable, "-B", "-m", "pytest"]
            assert "timeout" not in kwargs and target == copied_target[0]
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(distribution_tests.subprocess, "run", execute)
    assert distribution_tests._run_pytest(["test_selected.py"],
        target_fixture=fixture.relative_to(root).as_posix(), collect_only=False,
        verbose=False, basetemp=None) == 0
    assert len(calls) == 2 and not copied_target[0].exists()
    assert not Path(calls[1][0][calls[1][0].index("--basetemp") + 1]).exists()
    assert sorted(path.name for path in fixture.iterdir()) == ["source.ts"]


@pytest.mark.parametrize("failure", ["nonzero", "timeout", "interrupt"])
def test_public_setup_failure_never_starts_pytest_and_cleans_owned_target(
    isolated_public_test_runner, monkeypatch, failure
):
    root, fixture = isolated_public_test_runner
    targets = []

    def reject(command, **kwargs):
        assert command[2:4] == ["sage.py", "init"]
        targets.append(Path(kwargs["env"]["CODEMAPS_TARGET_ROOT"]))
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        if failure == "interrupt":
            raise KeyboardInterrupt()
        return subprocess.CompletedProcess(command, 9)

    monkeypatch.setattr(distribution_tests.subprocess, "run", reject)
    kwargs = dict(target_fixture=fixture.relative_to(root).as_posix(), collect_only=False,
                  verbose=False, basetemp=None)
    if failure == "nonzero":
        assert distribution_tests._run_pytest(["test_selected.py"], **kwargs) == 9
    else:
        with pytest.raises(subprocess.TimeoutExpired if failure == "timeout" else KeyboardInterrupt):
            distribution_tests._run_pytest(["test_selected.py"], **kwargs)
    assert len(targets) == 1 and not targets[0].exists()
    assert (fixture / "source.ts").is_file()


@pytest.mark.parametrize("public,collect_only", [(True, True), (False, False), (False, True)])
def test_collection_and_canonical_execution_do_not_initialize(
    isolated_public_test_runner, monkeypatch, public, collect_only
):
    root, fixture = isolated_public_test_runner
    if not public:
        distribution_tests.PUBLIC_MANIFEST.unlink()
    calls = []

    def execute(command, **kwargs):
        assert command[:4] == [sys.executable, "-B", "-m", "pytest"]
        assert ("--collect-only" in command) == collect_only
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(distribution_tests.subprocess, "run", execute)
    assert distribution_tests._run_pytest(["test_selected.py"],
        target_fixture=fixture.relative_to(root).as_posix(), collect_only=collect_only,
        verbose=False, basetemp=None) == 0
    assert len(calls) == 1


def test_public_setup_without_owned_fixture_fails_before_subprocess(
    isolated_public_test_runner, monkeypatch
):
    monkeypatch.setattr(distribution_tests.subprocess, "run",
                        lambda *args, **kwargs: pytest.fail("No subprocess was authorized"))
    with pytest.raises(ValueError, match="owned target fixture"):
        distribution_tests._run_pytest(["test_selected.py"], target_fixture=None,
                                      collect_only=False, verbose=False, basetemp=None)


def test_unclassified_test_fails_closed() -> None:
    if distribution_tests.PUBLIC_MANIFEST.is_file():
        pytest.skip("Canonical authority classification is not shipped publicly")
    payload = copy.deepcopy(distribution_tests._load_profile())
    public_rows = payload["classification"]["overrides"]["public_target_repository"]
    public_rows.remove("tools/tests/test_distribution_test_profiles.py")
    with pytest.raises(ValueError, match="explicit authority classification"):
        distribution_tests._canonical_classification(payload)


def test_partition_is_an_exact_ordered_union_for_both_profiles() -> None:
    if distribution_tests.PUBLIC_MANIFEST.is_file():
        partitions, _, selected = distribution_tests.resolve_test_partitions(
            "public_target_repository"
        )
        tests, _ = distribution_tests.resolve_tests("public_target_repository")
        assert selected == "public_target_repository"
    else:
        partitions, _, selected = distribution_tests.resolve_test_partitions("canonical_all")
        tests, _ = distribution_tests.resolve_tests("canonical_all")
        assert selected == "canonical_all"

    flattened = [path for paths in partitions.values() for path in paths]
    assert sorted(flattened) == sorted(tests)
    assert len(flattened) == len(set(flattened))
    assert all(paths == sorted(paths) for paths in partitions.values())


def test_public_profile_has_an_explicit_nonempty_slice_in_every_family() -> None:
    partitions, _, selected = distribution_tests.resolve_test_partitions(
        "public_target_repository"
    )

    assert selected == "public_target_repository"
    assert all(partitions.values())


def test_unknown_or_multiply_owned_partition_token_fails_closed() -> None:
    if distribution_tests.PUBLIC_MANIFEST.is_file():
        pytest.skip("Canonical partition ownership is not shipped publicly")
    payload = copy.deepcopy(distribution_tests._load_profile())
    classified, _ = distribution_tests._canonical_classification(payload)
    payload["partition"]["families"]["release_distribution"]["tokens"].remove(
        "distribution"
    )
    with pytest.raises(ValueError, match="token inventory mismatch"):
        distribution_tests._validate_partition_inventory(payload, sorted(classified))

    payload = copy.deepcopy(distribution_tests._load_profile())
    payload["partition"]["families"]["target_agent"]["tokens"].append("distribution")
    with pytest.raises(ValueError, match="multiple owners"):
        distribution_tests._partition_contract(payload)


def test_partition_membership_is_independent_of_input_order() -> None:
    payload = distribution_tests._load_profile()
    tests, _ = distribution_tests.resolve_tests("public_target_repository")
    forward = distribution_tests._partition_selected_tests(
        payload, tests, "public_target_repository"
    )
    reverse = distribution_tests._partition_selected_tests(
        payload, list(reversed(tests)), "public_target_repository"
    )

    assert forward == reverse


def _passing_shard_receipts(
    payload: dict, profile_name: str = "auto"
) -> dict[str, dict]:
    partitions, _, selected_profile = (
        distribution_tests._resolve_test_partitions_from_payload(payload, profile_name)
    )
    partition_sha256 = distribution_tests._canonical_sha256(payload["partition"])
    receipts: dict[str, dict] = {}
    for family, tests in partitions.items():
        paths_sha256 = distribution_tests._canonical_sha256(tests)
        receipts[family] = {
            "schema": partition_receipts.RECEIPT_SCHEMA,
            "status": "PASS",
            "authority": partition_receipts.RECEIPT_AUTHORITY,
            "profile": selected_profile,
            "collect_only": False,
            "partition_contract_sha256": partition_sha256,
            "selected_test_union_sha256": paths_sha256,
            "selected_test_count": len(tests),
            "exact_profile_union": False,
            "shards": [
                {
                    "id": family,
                    "status": "PASS",
                    "returncode": 0,
                    "test_count": len(tests),
                    "test_paths_sha256": paths_sha256,
                    "test_paths": tests,
                }
            ],
        }
    return receipts


def test_family_receipt_join_reconstructs_the_exact_profile_union() -> None:
    payload = distribution_tests._load_profile()
    receipts = _passing_shard_receipts(payload)

    combined = partition_receipts.combine_partition_receipts(
        payload,
        "auto",
        receipts,
    )
    tests, _ = distribution_tests.resolve_tests("auto")

    assert combined["status"] == "PASS"
    assert combined["exact_profile_union"] is True
    assert combined["selected_test_count"] == len(tests)
    assert [row["id"] for row in combined["shards"]] == list(
        payload["partition"]["execution_order"]
    )
    assert sorted(
        path for row in combined["shards"] for path in row["test_paths"]
    ) == tests


def test_legacy_source_reference_does_not_invent_primary_authority() -> None:
    payload = distribution_tests._load_profile()
    receipts = _passing_shard_receipts(payload)
    sources = {family: {"path": family + ".json", "sha256": "0" * 64} for family in receipts}
    result = partition_receipts.combine_partition_receipts(payload, "auto", receipts, source_receipts=sources)
    assert all(row["truth_source"] == "not_available" for row in result["source_receipts"])


def test_family_receipt_join_rejects_missing_reordered_or_tampered_shards() -> None:
    payload = distribution_tests._load_profile()
    receipts = _passing_shard_receipts(payload)
    missing = dict(receipts)
    missing.pop("target_agent")
    with pytest.raises(ValueError, match="order/set mismatch"):
        partition_receipts.combine_partition_receipts(payload, "auto", missing)

    reordered = dict(reversed(list(receipts.items())))
    with pytest.raises(ValueError, match="order/set mismatch"):
        partition_receipts.combine_partition_receipts(payload, "auto", reordered)

    tampered = copy.deepcopy(receipts)
    tampered["governance_policy"]["shards"][0]["test_paths"].pop()
    with pytest.raises(ValueError, match="governance_policy"):
        partition_receipts.combine_partition_receipts(payload, "auto", tampered)


def test_family_receipt_join_rejects_non_pass_or_authority_drift() -> None:
    payload = distribution_tests._load_profile()
    failed = _passing_shard_receipts(payload)
    failed["runtime_platform"]["status"] = "FAIL"
    with pytest.raises(ValueError, match="runtime_platform"):
        partition_receipts.combine_partition_receipts(payload, "auto", failed)

    authority_drift = _passing_shard_receipts(payload)
    authority_drift["analysis_engines"]["authority"] = "release_authority"
    with pytest.raises(ValueError, match="analysis_engines"):
        partition_receipts.combine_partition_receipts(
            payload,
            "auto",
            authority_drift,
        )


def test_family_receipt_cli_inputs_are_confined_and_unique(
    tmp_path, monkeypatch
) -> None:
    raw_root = tmp_path / "output" / ".raw"
    raw_root.mkdir(parents=True)
    receipt = raw_root / "target.json"
    receipt.write_text("{}\n", encoding="utf-8")
    outside = tmp_path / "outside.json"
    outside.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(partition_receipts, "ROOT", tmp_path)

    parsed = partition_receipts._parse_shard_receipts(
        [f"target_agent={receipt}"]
    )
    assert parsed == {"target_agent": receipt.resolve()}

    with pytest.raises(ValueError, match="escapes output/.raw"):
        partition_receipts._parse_shard_receipts(
            [f"target_agent={outside}"]
        )
    with pytest.raises(ValueError, match="duplicate"):
        partition_receipts._parse_shard_receipts(
            [
                f"target_agent={receipt}",
                f"target_agent={receipt}",
            ]
        )


@pytest.fixture
def managed_receipt_store(tmp_path, monkeypatch):
    from tools.core import artifact_store
    from tools.core.artifact_registry import artifact_path_for_storage_root

    # Real primary storage, isolated from the current release evidence.
    raw = tmp_path / "output" / ".raw"
    store = artifact_store.ArtifactStore(raw_dir=raw)
    store.use_sqlite, store.backend = True, "hybrid_sqlite"
    store._ensure_schema()
    monkeypatch.setenv("SAGE_SYNC_SHADOW_WRITES", "1")
    monkeypatch.setattr(artifact_store, "STORE", store)
    monkeypatch.setattr(distribution_tests, "ROOT", tmp_path)
    monkeypatch.setattr(partition_receipts, "ROOT", tmp_path)
    path = artifact_path_for_storage_root(raw, "test_suite_partition_target_agent_receipt")
    return store, path


def test_managed_receipt_new_fail_replaces_old_primary_pass(managed_receipt_store):
    store, path = managed_receipt_store
    store.save_raw(path.stem, {"status": "PASS", "selected_test_count": 21})
    current = {"status": "FAIL", "selected_test_count": 50}
    distribution_tests._write_json_atomic(path, current)
    # A conflicting compatibility export cannot override current primary truth.
    path.write_text('{"status":"PASS"}', encoding="utf-8")
    payload, identity = partition_receipts._load_receipt_with_identity(path)
    assert payload == current
    assert store.load_raw(path.stem) == current
    assert identity["truth_source"] == "sqlite_primary"
    with sqlite3.connect(path.parent / "codemaps.db") as conn:
        serialized = conn.execute("SELECT payload FROM state_payloads WHERE name=?", (path.stem,)).fetchone()[0]
    assert identity["sha256"] == hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    assert identity["sha256"] != hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("damage", ["missing", "checksum", "json", "duplicate_key", "partition", "nonobject"])
def test_managed_receipt_refuses_primary_damage_despite_shadow_pass(managed_receipt_store, damage):
    store, path = managed_receipt_store
    if damage == "partition":
        store.state_payload_inline_limit_bytes = 1
    store.save_raw(path.stem, {"status": "PASS"})
    if damage == "nonobject":
        store.save_raw(path.stem, [])
    with sqlite3.connect(path.parent / "codemaps.db") as conn:
        if damage == "missing":
            conn.execute("DELETE FROM state_payloads WHERE name=?", (path.stem,))
        elif damage == "checksum":
            conn.execute("UPDATE state_payloads SET payload_sha='bad' WHERE name=?", (path.stem,))
        elif damage in {"json", "duplicate_key"}:
            content = "{" if damage == "json" else '{"status":"FAIL","status":"PASS"}'
            conn.execute("UPDATE state_payloads SET payload=?,payload_sha=? WHERE name=?",
                (content, hashlib.sha256(content.encode("utf-8")).hexdigest(), path.stem))
        elif damage == "partition":
            conn.execute("DELETE FROM state_payload_parts WHERE name=?", (path.stem,))
    path.write_text('{"status":"PASS"}', encoding="utf-8")
    with pytest.raises((ValueError, sqlite3.Error)):
        partition_receipts._load_receipt(path)


def test_json_primary_receipt_ignores_old_trace_database(managed_receipt_store):
    store, path = managed_receipt_store
    store.save_raw(path.stem, {"status": "PASS"})
    store.use_sqlite, store.backend = False, "json"
    distribution_tests._write_json_atomic(path, {"status": "FAIL"})
    payload, identity = partition_receipts._load_receipt_with_identity(path)
    assert payload == {"status": "FAIL"}
    assert identity == {"truth_source": "json_primary", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def test_managed_receipt_failed_write_does_not_publish_file_pass(managed_receipt_store, monkeypatch):
    store, path = managed_receipt_store
    store.save_raw(path.stem, {"status": "FAIL"})
    def reject(*args, **kwargs):
        raise OSError("primary unavailable")
    monkeypatch.setattr(store, "_save_payload_to_state_table", reject)
    with pytest.raises(RuntimeError, match="primary artifact write failed"):
        distribution_tests._write_json_atomic(path, {"status": "PASS"})
    assert partition_receipts._load_receipt(path) == {"status": "FAIL"}
    assert json.loads(path.read_text(encoding="utf-8")) == {"status": "FAIL"}


def test_managed_receipt_rejects_wrong_store_namespace(managed_receipt_store, monkeypatch, tmp_path):
    from tools.core import artifact_store
    _, path = managed_receipt_store
    monkeypatch.setattr(artifact_store, "STORE", artifact_store.ArtifactStore(raw_dir=tmp_path / "other"))
    with pytest.raises(ValueError, match="namespace"):
        distribution_tests._write_json_atomic(path, {"status": "PASS"})
    assert not path.exists()


def test_external_receipt_writer_is_standalone_and_dependency_light(tmp_path):
    source = Path(distribution_tests.__file__).read_bytes()
    isolated = tmp_path / "standalone"
    isolated.mkdir()
    script = isolated / "runner.py"
    script.write_bytes(source)
    receipt = tmp_path / "external.json"
    code = "import runpy,sys;from pathlib import Path;r=runpy.run_path(sys.argv[1]);r['_write_json_atomic'](Path(sys.argv[2]),{'status':'FAIL'})"
    run = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(script), str(receipt)],
        cwd=isolated, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    payload, identity = partition_receipts._load_receipt_with_identity(receipt)
    assert payload == {"status": "FAIL"}
    assert identity == {"truth_source": "explicit_json_file", "sha256": hashlib.sha256(receipt.read_bytes()).hexdigest()}


@pytest.mark.parametrize("damage", ["none", "current_fail", "missing_primary", "corrupt_primary"])
def test_receipt_join_cli_uses_current_primary_and_writes_managed_union(tmp_path, monkeypatch, damage):
    from tools.core import artifact_store
    from tools.core.artifact_registry import artifact_path_for_storage_root

    payload = distribution_tests._load_profile()
    receipts = _passing_shard_receipts(payload)
    # Materialize the declared inventory, not executable tests or borrowed results.
    paths, _ = distribution_tests.resolve_tests("auto")
    if "classification" in payload:
        paths = list(distribution_tests._canonical_classification(payload)[0])
    for relative in paths:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    raw = tmp_path / "output" / ".raw"
    store = artifact_store.ArtifactStore(raw_dir=raw)
    store.use_sqlite, store.backend = True, "hybrid_sqlite"
    monkeypatch.setenv("SAGE_SYNC_SHADOW_WRITES", "1")
    monkeypatch.setattr(artifact_store, "STORE", store)
    monkeypatch.setattr(distribution_tests, "ROOT", tmp_path)
    monkeypatch.setattr(partition_receipts, "ROOT", tmp_path)
    argv = ["join", "--profile", "auto"]
    first = None
    for family, receipt in receipts.items():
        path = artifact_path_for_storage_root(raw, "test_suite_partition_" + family + "_receipt")
        distribution_tests._write_json_atomic(path, receipt)
        first = first or path
        path.unlink()  # Primary evidence works without compatibility files.
        argv.extend(["--shard-receipt", family + "=" + str(path)])
    if damage != "none":
        if damage == "current_fail":
            receipt = copy.deepcopy(receipts[next(iter(receipts))])
            receipt["status"] = "FAIL"
            distribution_tests._write_json_atomic(first, receipt)
        else:
            with sqlite3.connect(raw / "codemaps.db") as conn:
                if damage == "missing_primary":
                    conn.execute("DELETE FROM state_payloads WHERE name=?", (first.stem,))
                else:
                    conn.execute("UPDATE state_payloads SET payload_sha='bad' WHERE name=?", (first.stem,))
        first.write_text(json.dumps(receipts[next(iter(receipts))]), encoding="utf-8")
    output = artifact_path_for_storage_root(raw, "test_suite_partition_receipt")
    argv.extend(["--receipt", str(output)])
    monkeypatch.setattr(sys, "argv", argv)
    assert partition_receipts.main() == (0 if damage == "none" else 1)
    result = partition_receipts._load_receipt(output)
    assert result["status"] == ("PASS" if damage == "none" else "FAIL")
    assert result["authority"] == partition_receipts.RECEIPT_AUTHORITY
    if damage == "none":
        assert result["exact_profile_union"] is True
        assert all(row["truth_source"] == "sqlite_primary" for row in result["source_receipts"])
        assert all(len(row["sha256"]) == 64 for row in result["source_receipts"])
    else:
        assert result["exact_profile_union"] is False and result["errors"]
