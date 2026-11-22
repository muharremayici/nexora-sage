"""Pure v2 diagnostic input projections; no reads, execution or evidence admission."""
from __future__ import annotations

import hashlib
import json
import re
import tomllib


INPUT_AUTHORITY = "diagnostic_only_no_release_reuse"
PERSISTED_POLICY_SCHEMA = "nexora_sage_persisted_test_unit_reuse_v1"
PERSISTED_AUTHORITY = "diagnostic_test_execution_reuse_only"
UNIT_CACHE_DIRECTORY = "output/.operational/distribution_units"
CANONICAL_POLICY_SCHEMA = "nexora_sage_canonical_test_unit_admission_v1"
TEST_EVIDENCE_AUTHORITY = "test_evidence_only_no_release_or_publication_authority"

IDENTITY_SCHEMA = "nexora_sage_diagnostic_unit_execution_identity_v2"
PROVENANCE_SCHEMA = "nexora_sage_diagnostic_unit_input_capture_v2"
BASELINE_SCHEMA = "nexora_sage_diagnostic_test_unit_pilot_v2"
BINDING_SCHEMA = "nexora_sage_diagnostic_unit_parent_binding_v2"
OUTPUT_SCHEMA = "nexora_sage_diagnostic_unit_output_observation_v2"
REVIEW_ALGORITHM = "sha256_utf8_lf_project_version_scalar_mask_v2"
METADATA_PROJECTION = {"kind": "pyproject_project_version_scalar_span_v2",
                       "max_bytes": 65536, "max_version_characters": 128}
_INITIALIZERS = {"tools/__init__.py", "tools/core/__init__.py", "tools/tests/__init__.py"}


def project_roles(spec: dict) -> dict[str, str]:
    """Interpret an explicitly reviewed descriptor, never infer a source closure."""
    rows = spec.get("project_inputs")
    test = spec.get("test_path")
    if (not isinstance(rows, dict) or not 6 <= len(rows) <= 32
            or not isinstance(test, str) or not re.fullmatch(r"tools/tests/test_[a-z0-9_]+\.py", test)):
        raise ValueError("Unsupported project input role contract")
    roles = {}
    for path, row in rows.items():
        if not isinstance(path, str) or not isinstance(row, dict):
            raise ValueError("Unsupported project input role contract")
        role = row.get("role")
        expected = ("test_source" if path == test else "package_initializer" if path in _INITIALIZERS
                    else "version_masked_unit_metadata" if path == "pyproject.toml" else None)
        if expected is not None:
            if role != expected:
                raise ValueError("Unsupported project input role contract")
        elif (not re.fullmatch(r"tools/core/[a-z0-9_]+\.(py|cjs)", path)
              or path.endswith("/__init__.py") or role not in {"executable_source", "observed_source"}):
            raise ValueError("Unsupported project input role contract")
        roles[path] = role
    if (not (_INITIALIZERS | {test, "pyproject.toml"}) <= set(roles)
            or "executable_source" not in roles.values()):
        raise ValueError("Incomplete reviewed project input roles")
    return roles


def unit_schema_projection(schema: dict, unit: str) -> dict:
    """Bind this unit's section and only its transitively referenced definitions."""
    try:
        section = schema["properties"]["diagnostic_test_unit_pilots"]["properties"][unit]
        definitions = schema.get("$defs", {})
    except (KeyError, TypeError) as exc:
        raise ValueError("Unsupported diagnostic input schema") from exc
    selected = {}

    def visit(node):
        if isinstance(node, dict):
            if "$dynamicRef" in node or "$recursiveRef" in node:
                raise ValueError("Unsupported dynamic unit schema reference")
            if "$ref" in node:
                ref = node["$ref"]
                if not isinstance(ref, str) or not re.fullmatch(r"#/[\$]defs/[A-Za-z0-9_]+", ref):
                    raise ValueError("Unsupported unit schema reference")
                name = ref.split("/")[-1]
                if name not in definitions:
                    raise ValueError("Missing unit schema definition")
                if name not in selected:
                    selected[name] = definitions[name]
                    visit(definitions[name])
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(section)
    return {"$defs": {name: selected[name] for name in sorted(selected)}, **section}


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def project_metadata(content: bytes) -> tuple[bytes, str]:
    """Mask only the validated scalar's inner bytes; retain quotes, comments and EOL.

    Unsupported TOML layouts refuse. In particular, rejecting multiline strings
    prevents apparent headers/assignments inside strings from being mislocated.
    TOML parsing and span location must independently agree about the value.
    """
    if not isinstance(content, bytes) or len(content) > METADATA_PROJECTION["max_bytes"]:
        raise ValueError("Metadata byte limit or type")
    text = content.decode("utf-8", errors="strict")
    if "\"\"\"" in text or "'''" in text:
        raise ValueError("Unsupported multiline metadata layout")
    parsed = tomllib.loads(text)
    project = parsed.get("project")
    version = project.get("version") if isinstance(project, dict) else None
    if (not isinstance(version, str) or not 0 < len(version) <= METADATA_PROJECTION["max_version_characters"]
            or any(ord(c) < 32 or ord(c) == 127 for c in version)):
        raise ValueError("Missing or unsupported project version")
    in_project, headers, spans, offset = False, 0, [], 0
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip(" \t")
        if stripped.startswith("["):
            # All table headers in this narrow layout must have plain bare keys.
            header = re.fullmatch(r"\[([A-Za-z0-9_.-]+)\][ \t]*(?:#[^\r\n]*)?(?:\r?\n)?", stripped)
            if header is None:
                raise ValueError("Unsupported or ambiguous metadata table")
            in_project = header[1] == "project"
            headers += int(in_project)
        if in_project:
            assignment = re.fullmatch(r"[ \t]*version[ \t]*=[ \t]*([\"'])([^\r\n]*?)\1[ \t]*(?:#[^\r\n]*)?(?:\r?\n)?", line)
            if assignment:
                value = assignment[2]
                if "\\" in value or assignment[1] in value or value != version:
                    raise ValueError("Unsupported or mismatching version scalar")
                start, end = assignment.span(2)
                spans.append((offset + start, offset + end))
        offset += len(line)
    if headers != 1 or len(spans) != 1:
        raise ValueError("Missing or ambiguous plain project.version scalar")
    start, end = spans[0]
    masked = (text[:start] + "<SAGE_PROJECT_VERSION_V2>" + text[end:]).encode("utf-8")
    return masked, version


def project_facts(unit: str, spec: dict, files: dict[str, bytes], policy: bytes) -> tuple[str, dict]:
    """Derive review, execution and origin facts from one held declared capture."""
    roles = project_roles(spec)
    if (not isinstance(unit, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", unit)
            or set(files) != set(roles)
            or spec["metadata_projection"] != METADATA_PROJECTION):
        raise ValueError("Unsupported project input role contract")
    masked, version = project_metadata(files["pyproject.toml"])
    projected = {}
    for path, role in roles.items():
        row = spec["project_inputs"][path]
        algorithm = REVIEW_ALGORITHM if path == "pyproject.toml" else "sha256_utf8_lf_v1"
        if (set(row) != {"role", "review_digest_algorithm", "review_sha256"}
                or row["role"] != role or row["review_digest_algorithm"] != algorithm):
            raise ValueError("Unsupported project input role or review algorithm")
        content = masked if path == "pyproject.toml" else files[path]
        review = sha256(content.decode("utf-8").replace("\r\n", "\n").encode("utf-8"))
        if review != row["review_sha256"]:
            raise ValueError(f"Pilot input requires a new source review: {path}")
        projected[path] = {"role": role, "sha256": sha256(content)}
    return digest(projected), {
        "schema": PROVENANCE_SCHEMA, "unit": unit,
        "project_raw_sha256": {path: sha256(files[path]) for path in roles},
        "metadata_version": version, "policy_document_sha256": sha256(policy),
    }


def provenance_is_complete(value: object, unit: str, *, project_paths=None) -> bool:
    if (not isinstance(value, dict) or set(value) != {"schema", "unit", "project_raw_sha256",
            "metadata_version", "policy_document_sha256"} or value.get("schema") != PROVENANCE_SCHEMA
            or value.get("unit") != unit or not isinstance(unit, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", unit)
            or not isinstance(value.get("project_raw_sha256"), dict)
            or not 6 <= len(value["project_raw_sha256"]) <= 32
            or not (_INITIALIZERS | {"pyproject.toml"}) <= set(value["project_raw_sha256"])
            or (project_paths is not None and set(value["project_raw_sha256"]) != set(project_paths))
            or not isinstance(value.get("metadata_version"), str)
            or not 0 < len(value["metadata_version"]) <= METADATA_PROJECTION["max_version_characters"]
            or any(ord(c) < 32 or ord(c) == 127 for c in value["metadata_version"])):
        return False
    paths = value["project_raw_sha256"]
    if (len([p for p in paths if isinstance(p, str) and re.fullmatch(r"tools/tests/test_[a-z0-9_]+\.py", p)]) != 1
            or any(not isinstance(p, str) or (p != "pyproject.toml" and not re.fullmatch(
                r"tools/(?:core/|tests/)?[a-z0-9_]+\.(?:py|cjs)", p)) for p in paths)):
        return False
    return all(isinstance(v, str) and re.fullmatch("[0-9a-f]{64}", v) for v in [
        *value["project_raw_sha256"].values(), value["policy_document_sha256"]])


def validate_canonical_unit_policy(policy, specs):
    if (not isinstance(policy, dict) or set(policy) != {"schema", "authority", "profile", "enabled", "units"}
            or policy.get("schema") != CANONICAL_POLICY_SCHEMA or policy.get("authority") != TEST_EVIDENCE_AUTHORITY
            or policy.get("profile") != "canonical_all" or type(policy.get("enabled")) is not bool
            or not isinstance(policy.get("units"), dict) or not 1 <= len(policy["units"]) <= 32
            or not isinstance(specs, dict)):
        raise ValueError("canonical_unit_admission_policy_invalid")
    paths = []
    for unit, row in policy["units"].items():
        spec = specs.get(unit)
        if (not isinstance(unit, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", unit)
                or not isinstance(row, dict) or set(row) != {"family"}
                or not isinstance(row["family"], str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", row["family"])
                or not isinstance(spec, dict) or spec.get("input_contract_version") != "v2"
                or spec.get("authority") != INPUT_AUTHORITY
                or spec.get("canonical_profile_reuse_enabled") is not False
                or not isinstance(spec.get("test_path"), str)
                or not re.fullmatch(r"tools/tests/test_[a-z0-9_]+\.py", spec["test_path"])):
            raise ValueError("canonical_unit_admission_input_unreviewed")
        paths.append(spec["test_path"])
    if len(paths) != len(set(paths)):
        raise ValueError("canonical_unit_admission_duplicate_file")
    return policy


def validate_persisted_unit_policy(policy, specs):
    fields = {"schema", "authority", "scope", "enabled", "allowed_units", "cache_directory"}
    if (not isinstance(policy, dict) or set(policy) != fields
            or policy.get("schema") != PERSISTED_POLICY_SCHEMA or policy.get("authority") != PERSISTED_AUTHORITY
            or policy.get("scope") != "development_diagnostic_only"
            or type(policy.get("enabled")) is not bool or policy.get("cache_directory") != UNIT_CACHE_DIRECTORY):
        raise ValueError("persisted_unit_reuse_policy_invalid")
    units = policy["allowed_units"]
    if (not isinstance(units, list) or not 1 <= len(units) <= 32
            or any(not isinstance(unit, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", unit) for unit in units)
            or len(units) != len(set(units)) or not isinstance(specs, dict)
            or any(not isinstance(specs.get(unit), dict)
                   or specs[unit].get("input_contract_version") != "v2"
                   or specs[unit].get("authority") != INPUT_AUTHORITY
                   or specs[unit].get("canonical_profile_reuse_enabled") is not False for unit in units)):
        raise ValueError("persisted_unit_reuse_inputs_unreviewed")
    return policy
