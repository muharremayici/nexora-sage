"""Optional loaded-graph navigation, never source freshness or executed coverage."""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from tools.core.atlas_integrity import payload_sha256
from tools.core.artifact_validator import ArtifactValidationError
from tools.core.test_impact_profiles import dependency_witness_policy, static_candidate_evidence


def optional_witness_policy() -> dict | None:
    try:
        return dependency_witness_policy()
    except (ArtifactValidationError, ValueError, TypeError, KeyError, OSError):
        # Only optional navigation is withheld; original candidate decisions survive.
        return None


def unavailable_witness(reason: str) -> dict:
    return {"status": "unavailable", "reason": reason, "runtime_execution": "not_established",
            "changed_behavior": "not_established", "mutation_authority": "none"}


def canonical_node(value: Any) -> bool:
    if not isinstance(value, str) or value.count("::") != 1:
        return False
    project, path = value.split("::")
    return (bool(project) and not any(c in project for c in "/\\:") and bool(path)
            and not any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value)
            and not any(c in path for c in "\\:")
            and all(part and part not in (".", "..") for part in path.split("/")))


class DependencyWitnessTrace:
    """Bounded parents retained by the existing reachability walk, not a second BFS."""
    def __init__(self, target: str, policy: dict):
        self.target, self.policy = target, policy
        self.parents = {target: None}
        self.omitted = False

    def observe(self, child: str, parent: str) -> None:
        if child in self.parents:
            return
        if len(self.parents) >= self.policy["max_trace_nodes"] or parent not in self.parents:
            self.omitted = True
            return
        self.parents[child] = parent

    def path(self, candidate: str, reverse: dict) -> list[str] | None:
        path = [candidate]
        while path[-1] != self.target:
            parent = self.parents.get(path[-1])
            if (parent is None or parent in path or path[-1] not in reverse.get(parent, [])
                    or len(path) >= self.policy["max_path_nodes"]):
                return None
            path.append(parent)
        return path


def _display_ref(atlas: dict, node: str, context, project_indexes: dict) -> str | None:
    if not canonical_node(node):
        return None
    project, relative = node.split("::")
    data = atlas.get(project)
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict) or not isinstance(files.get(relative), dict):
        return None
    if project not in project_indexes:
        # One request-local in-memory index per shown project; no target/SQLite reads.
        counts = {}
        for path, meta in files.items():
            if not isinstance(meta, dict):
                continue
            public = meta.get("workspace_rel")
            if not public and data.get("root_path", ".") in (".", ""):
                public = path
            if isinstance(public, str):
                counts[public] = counts.get(public, 0) + 1
        project_indexes[project] = counts
    projected = context(atlas, project, relative)
    public = projected.get("repo_relative_path")
    if project_indexes[project].get(public) != 1 or not canonical_node(project + "::" + str(public)):
        return None
    return project + "::" + public


def attach_dependency_witnesses(tests: list[dict], *, atlas: dict, target: str,
                               reverse: dict, trace: DependencyWitnessTrace | None, context) -> dict:
    policy = trace.policy if trace is not None else optional_witness_policy()
    indexes, used_bytes = {}, 0
    summary = {"status": "retained" if policy else "unavailable", "retained_candidates": 0,
               "omitted_candidates": len(tests), "observed_paths": 0,
               "reason": "bounded_optional_navigation" if policy else "optional_policy_unavailable"}
    if policy is None:
        return summary
    for index, row in enumerate(tests):
        if index >= policy["max_candidates"]:
            break
        row["dependency_witness"] = unavailable_witness("loaded_graph_trace_unavailable")
        summary["retained_candidates"] += 1
        summary["omitted_candidates"] -= 1
        relation = static_candidate_evidence(row)["relation"]
        if relation not in ("direct_import", "transitive_dependency"):
            row["dependency_witness"] = unavailable_witness("no_static_dependency_relation")
            continue
        if trace is None:
            continue
        nodes = trace.path(row.get("atlas_node", ""), reverse)
        if not nodes or len(nodes) < 2 or nodes[-1] != target:
            row["dependency_witness"] = unavailable_witness("path_not_retained_or_over_budget")
            continue
        refs = [_display_ref(atlas, node, context, indexes) for node in nodes]
        if (any(ref is None for ref in refs) or any(len(n) > policy["max_reference_characters"] for n in nodes)
                or any(len(ref) > policy["max_reference_characters"] for ref in refs)):
            row["dependency_witness"] = unavailable_witness("path_identity_unavailable_or_over_budget")
            continue
        view = {"status": "observed", "version": policy["version"], "policy_sha256": payload_sha256(policy),
                "scope": "loaded_static_dependency_graph_only", "nodes": nodes, "target_refs": refs,
                "trace_retention": "partial" if trace.omitted else "complete",
                "runtime_execution": "not_established", "changed_behavior": "not_established",
                "mutation_authority": "none", "proof_boundary": policy["proof_boundary"]}
        size = len(json.dumps(view, ensure_ascii=False).encode("utf-8"))
        if used_bytes + size > policy["max_group_bytes"]:
            row["dependency_witness"] = unavailable_witness("serialized_navigation_budget")
            continue
        used_bytes += size
        row["dependency_witness"] = view
        summary["observed_paths"] += 1
    summary["observed_carrier_bytes"] = used_bytes
    return summary


def project_dependency_witness(row: dict, payload: dict) -> dict:
    """Carrier/item consistency only; not source/graph reverification or authentication."""
    policy, value = optional_witness_policy(), row.get("dependency_witness")
    if policy is None:
        return unavailable_witness("optional_policy_unavailable")
    if (isinstance(value, dict) and set(value) == set(unavailable_witness(""))
            and value.get("status") == "unavailable" and isinstance(value.get("reason"), str)
            and 0 < len(value["reason"]) <= 128
            and all(c in "abcdefghijklmnopqrstuvwxyz_" for c in value["reason"])):
        return unavailable_witness(value["reason"])
    if not isinstance(value, dict) or value.get("status") != "observed":
        return unavailable_witness("navigation_not_observed")
    expected = {"status", "version", "policy_sha256", "scope", "nodes", "target_refs", "trace_retention",
                "runtime_execution", "changed_behavior", "mutation_authority", "proof_boundary"}
    nodes, refs = value.get("nodes"), value.get("target_refs")
    if (set(value) != expected or value.get("version") != policy["version"]
            or value.get("policy_sha256") != payload_sha256(policy)
            or value.get("scope") != "loaded_static_dependency_graph_only"
            or value.get("runtime_execution") != "not_established" or value.get("changed_behavior") != "not_established"
            or value.get("mutation_authority") != "none" or value.get("proof_boundary") != policy["proof_boundary"]
            or value.get("trace_retention") not in ("partial", "complete")
            or not isinstance(nodes, list) or not 2 <= len(nodes) <= policy["max_path_nodes"]
            or not isinstance(refs, list) or len(refs) != len(nodes)
            or any(not canonical_node(n) or len(n) > policy["max_reference_characters"] for n in nodes + refs)
            or len(set(nodes)) != len(nodes) or len(set(refs)) != len(refs)
            or len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > policy["max_group_bytes"]):
        return unavailable_witness("navigation_carrier_inconsistent")
    relation = static_candidate_evidence(row)["relation"]
    project = row.get("project")
    public = row.get("repo_relative_path") or row.get("file")
    if (relation not in ("direct_import", "transitive_dependency")
            or (relation == "direct_import") != (len(nodes) == 2)
            or row.get("source") == "live_bounded_sibling_scan"
            or nodes[0] != row.get("atlas_node") or nodes[-1] != payload.get("target")
            or refs[0] != str(project) + "::" + str(public)
            or refs[-1] != payload.get("target_ref")
            or any(n.split("::")[0] != ref.split("::")[0] for n, ref in zip(nodes, refs))):
        return unavailable_witness("navigation_item_identity_or_relation_mismatch")
    return deepcopy(value)


def normalize_dependency_navigation(payload: dict) -> dict:
    """Recount only current projected carriers; never inherit producer summary claims."""
    tests = payload.get("impacted_tests", [])
    if "dependency_navigation" not in payload and not any("dependency_witness" in r for r in tests):
        return payload
    result = dict(payload)
    rows = [dict(row) for row in tests]
    result["impacted_tests"] = rows
    policy = optional_witness_policy()
    retained = observed = used_bytes = 0
    for index, row in enumerate(rows):
        supplied = row.pop("dependency_witness", None)
        if policy is None or supplied is None or index >= policy["max_candidates"]:
            continue
        projected = project_dependency_witness({**row, "dependency_witness": supplied}, payload)
        if projected["status"] == "observed":
            size = len(json.dumps(projected, ensure_ascii=False).encode("utf-8"))
            if used_bytes + size > policy["max_group_bytes"]:
                projected = unavailable_witness("serialized_navigation_budget")
            else:
                used_bytes += size
                observed += 1
        row["dependency_witness"] = projected
        retained += 1
    result["dependency_navigation"] = {"scope": "current_candidate_projection",
        "status": "retained" if policy else "unavailable", "retained_candidates": retained,
        "observed_paths": observed, "omitted_candidates": len(rows) - retained,
        "observed_carrier_bytes": used_bytes,
        "reason": "bounded_optional_navigation" if policy else "optional_policy_unavailable"}
    return result
