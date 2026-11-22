"""Private declared-metadata checks; never approval, a grant or runtime admission."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from tools.core.artifact_validator import ArtifactValidationError, _validate
from tools.core.compiler_text_selection import MAX_SAFE, TextSelectionRefusal, _plain
from tools.core.json_io import load_json_object_strict
from tools.core.json_syntax import loads_json_strict

ROOT = Path(__file__).resolve().parents[2]
PROFILE = "private_compiler_preparation_comparison_authority"
POLICY_PATH = ROOT / "config/analysis_snapshot_lineage_contract.json"
SCHEMA_PATH = ROOT / "config/schemas/compiler_preparation_comparison_authority.schema.json"


class PreparationComparisonAuthorityRefusal(ValueError):
    def __init__(self, code: str, stage: str):
        super().__init__(code)
        self.code, self.stage = code, stage


def _need(ok, code, stage):
    if not ok:
        raise PreparationComparisonAuthorityRefusal(code, stage)


def _supported(node, root, refs=()):
    """Only vocabulary actually implemented by the shared validator is accepted."""
    allowed = {"$schema", "$id", "$defs", "$ref", "title", "description", "type", "properties", "required",
               "additionalProperties", "const", "enum", "pattern", "minimum", "allOf", "if", "then", "else",
               "items", "maxItems", "uniqueItems", "x-metadata-copy-charge-ceiling"}
    _need(type(node) is dict and set(node) <= allowed, "comparison_authority_schema", "vocabulary")
    if "$ref" in node:
        ref = node["$ref"]
        _need(type(ref) is str and ref.startswith("#/$defs/") and set(node) == {"$ref"}, "comparison_authority_schema", "reference")
        name = ref[len("#/$defs/"):]
        _need(name in root.get("$defs", {}) and name not in refs, "comparison_authority_schema", "reference")
        _supported(root["$defs"][name], root, (*refs, name))
        return
    if "pattern" in node:
        _need(type(node["pattern"]) is str, "comparison_authority_schema", "pattern")
        re.compile(node["pattern"])
    for key in ("$defs", "properties"):
        if key in node:
            _need(type(node[key]) is dict, "comparison_authority_schema", "vocabulary")
            for child in node[key].values():
                _supported(child, root, refs)
    if "allOf" in node:
        _need(type(node["allOf"]) is list, "comparison_authority_schema", "vocabulary")
        for child in node["allOf"]:
            _supported(child, root, refs)
    for key in ("if", "then", "else", "items", "additionalProperties"):
        if type(node.get(key)) is dict:
            _supported(node[key], root, refs)


def _schema():
    try:
        raw = SCHEMA_PATH.read_bytes()
        schema = loads_json_strict(raw.decode("utf-8"))
        _supported(schema, schema)
        ceiling = schema["x-metadata-copy-charge-ceiling"]
        _need(type(ceiling) is int and 0 < ceiling <= MAX_SAFE, "comparison_authority_schema", "ceiling")
        return schema, hashlib.sha256(raw).hexdigest()
    except PreparationComparisonAuthorityRefusal:
        raise
    except (OSError, ValueError, TypeError, KeyError, RecursionError, re.error) as error:
        raise PreparationComparisonAuthorityRefusal("comparison_authority_schema", "load") from error


def _snapshot(value, schema, stage):
    try:
        return _plain(value, schema["x-metadata-copy-charge-ceiling"], stage)
    except TextSelectionRefusal as error:
        raise PreparationComparisonAuthorityRefusal("comparison_authority_metadata", stage) from error


def _checked(value, definition, schema, stage):
    errors = []
    try:
        _validate(value, schema["$defs"][definition], schema, ["$"], errors)
    except (KeyError, TypeError, RecursionError, ArtifactValidationError) as error:
        raise PreparationComparisonAuthorityRefusal("comparison_authority_schema", stage) from error
    _need(not errors, "comparison_authority_metadata", stage)


def _policy_snapshot(value, schema, schema_sha256):
    policy = _snapshot(value, schema, "policy")
    _checked(policy, "policy", schema, "policy")
    # These are file-consistency observations, never approval or live-code attestation.
    _need(policy["schema_sha256"] == schema_sha256
          and policy["source_sha256"] == hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
          "comparison_authority_contract_identity", "policy")
    return policy


def _contract():
    schema, schema_sha256 = _schema()
    try:
        value = load_json_object_strict(POLICY_PATH, label="preparation comparison authority contract")["artifacts"]["ts_diagnostics"][PROFILE]
        return _policy_snapshot(value, schema, schema_sha256), schema
    except PreparationComparisonAuthorityRefusal:
        raise
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise PreparationComparisonAuthorityRefusal("comparison_authority_contract_unavailable", "policy") from error


def validate_policy_profile(profile):
    """Check a supplied policy-shaped object against the fixed repository schema."""
    schema, schema_sha256 = _schema()
    _policy_snapshot(profile, schema, schema_sha256)
    return {"status": "VALID_UNAVAILABLE_POLICY_METADATA", "runtime_admission": False}


def _evaluate(metadata, policy, schema):
    data = _snapshot(metadata, schema, "declaration")
    _checked(data, "metadata", schema, "declaration")
    caps = data.get("caps")
    if caps is not None:
        F = caps["fuel"]
        _need(F + 1 <= MAX_SAFE and 2 * F <= MAX_SAFE,
              "comparison_authority_arithmetic", "declaration")
    approval, acquisition = data.get("approval"), data.get("acquisition")
    matched = None
    if approval is not None and acquisition is not None:
        _need(approval["policy_snapshot_sha256"] == acquisition["policy_snapshot_sha256"],
              "comparison_authority_binding", "declaration")
        matched = True
    missing = [name for name in ("approval", "caps", "acquisition", "binding") if data.get(name) is None]
    out = {"status": "METADATA_VALID_RUNTIME_UNAVAILABLE", "metadata_state": "INCOMPLETE_DECLARATIONS" if missing else "COMPLETE_DECLARATIONS_NOT_AUTHORITY",
           "runtime_status": "UNAVAILABLE", "missing_fields": missing,
           "declared_caps": None if caps is None else dict(caps), "matching_declared_policy_identity": matched,
           "flags": {name: False for name in policy["denied_flags"]}, "full_work_units": None, "physical_fit": None}
    _checked(out, "result", schema, "result")
    return out


def validate_preparation_comparison_authority_metadata(metadata):
    """Even complete declarations yield no admission, issuer token or approval."""
    policy, schema = _contract()
    return _evaluate(metadata, policy, schema)


def validate_registered_contract():
    policy, schema = _contract()
    empty = {key: policy[key] for key in ("version", "cut", "issuance_scope")}
    return _evaluate(empty, policy, schema)
