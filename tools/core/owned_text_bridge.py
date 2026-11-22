"""Private immutable one-read input; no artifact, configuration or runtime imports."""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
from typing import Any


class OwnedTextBridgeError(ValueError):
    """Bounded public error code, never a source/decoder/OS exception message."""


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def policy_identity(policy: dict) -> str:
    return hashlib.sha256(canonical(policy).encode("utf-8")).hexdigest()


def config_input_policy_identity(config, image, bridge, auxiliary):
    """One shared identity spelling for the existing config input contracts."""
    return policy_identity({'config': config, 'image': image, 'bridge': bridge, 'auxiliary': auxiliary})


_OWNED_WIRE_BASE_TOKEN = object()


class _OwnedWireBase:
    """Private construction observer; stores no full frame references.

    Hash state may buffer input internally. Disposal is not secure erasure,
    caller authentication or a guarantee about Python/OS memory copies.
    """
    __slots__ = ('_context', '_settings', '_parts', '_hash', '_state', '_code', '_base', '_binding', '_parent', '_lock', '_owner')

    def __init__(self, profiles, context):
        profile = profiles['private_owned_wire_base']
        source = checked_policy(profiles[profile['source_profile']])
        config = profiles[profile['config_profile']]
        extends = profiles[profile['extends_profile']]
        image = profiles[config['image_profile']]
        ceiling = image['limits']['metadata_bytes']
        text = canonical({'profile': profile, 'context': context})
        if (type(ceiling) is not int or ceiling < 1 or len(text.encode('utf-8')) > ceiling
                or profile['version'] != 'v1-private-owned-wire-base'
                or profile['source_key'] != 'source_request' or profile['config_key'] != 'tsconfig_request'
                or any(profile[k] is not False for k in ('default_enabled', 'native_execution_enabled',
                    'raw_transport_enabled', 'production_enabled', 'semantic_lineage', 'cache_eligible'))):
            raise OwnedTextBridgeError('owned_wire_base_policy_refused')
        context = json.loads(text)['context']
        if set(context) != {'source', 'config'}:
            raise OwnedTextBridgeError('owned_wire_base_context_refused')
        src, cfg = context['source'], context['config']
        digest = lambda value: isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) is not None
        count = lambda value, limit: type(value) is int and 0 <= value <= limit
        identifier = lambda value: isinstance(value, str) and re.fullmatch('[A-Za-z0-9_-]+', value) and len(value) <= source['max_request_id_chars']
        if (set(src) != {'request_id', 'source_sha256', 'source_bytes', 'source_policy_sha256'}
                or not identifier(src['request_id']) or not digest(src['source_sha256'])
                or src['source_policy_sha256'] != policy_identity(source)
                or not count(src['source_bytes'], source['max_source_bytes'])
                or set(cfg) != {'invocation_id', 'context_sha256', 'compiler_version', 'compiler_source_sha256', 'files'}
                or not identifier(cfg['invocation_id']) or not digest(cfg['context_sha256'])
                or not digest(cfg['compiler_source_sha256']) or cfg['compiler_version'] != extends['compiler_version']
                or set(cfg['files']) != set(extends['roster'])):
            raise OwnedTextBridgeError('owned_wire_base_context_refused')
        for role, owner in extends['roster'].items():
            row = cfg['files'][role]
            if (set(row) != {'atlas_path', 'virtual_path', 'bytes', 'raw_sha256', 'host_sha256'}
                    or any(row[k] != owner[k] for k in ('atlas_path', 'virtual_path'))
                    or not count(row['bytes'], config['max_config_bytes'])
                    or not digest(row['raw_sha256']) or not digest(row['host_sha256'])):
                raise OwnedTextBridgeError('owned_wire_base_context_refused')
        cfg.update(policy_sha256=policy_identity(extends),
            input_policy_sha256=config_input_policy_identity(config, image, source, profiles['atlas_auxiliary_inputs']),
            auxiliary_policy_sha256=policy_identity(profiles['atlas_auxiliary_inputs']))
        if any(type(n) is not int or n < 1 for n in (source['max_request_bytes'], config['max_request_bytes'])):
            raise OwnedTextBridgeError('owned_wire_base_policy_refused')
        self._settings = {'source_cap': source['max_request_bytes'], 'config_cap': config['max_request_bytes'], 'metadata_cap': ceiling}
        self._context = context
        self._parts = {'source': None, 'config': None}
        self._hash = hashlib.sha256()
        self._state, self._code = 'source', 'wire_base_incomplete'
        self._base = self._binding = self._parent = None
        self._lock = threading.RLock()
        self._owner = _OWNED_WIRE_BASE_TOKEN

    def __repr__(self):
        return '<_OwnedWireBase>'

    def __reduce_ex__(self, protocol):
        raise TypeError('owned_wire_base_not_serializable')

    def _fail(self, code):
        with self._lock:
            self._state, self._code = 'unavailable', code
            self._hash = self._base = self._binding = self._parent = None

    def _frame(self, wire, role):
        if (type(wire) is not bytes or len(wire) < 2 or len(wire) > self._settings[role + '_cap']
                or not wire.endswith(b'\n') or wire.count(b'\n') != 1
                or wire[:1] != b'{' or wire[-2:-1] != b'}'):
            raise OwnedTextBridgeError('owned_wire_base_frame_refused')
        return memoryview(wire)[:-1]

    def observe_source(self, wire, reference):
        with self._lock:
            try:
                if self._state != 'source' or canonical(reference) != canonical(self._context['source']):
                    raise OwnedTextBridgeError('owned_wire_base_source_refused')
                body = self._frame(wire, 'source')
                self._hash.update(b'{"source_request":')
                self._hash.update(body)
                self._parts['source'] = {'jsonl_bytes': len(wire), 'sha256': hashlib.sha256(wire).hexdigest()}
                self._state = 'config'
                return True
            except (KeyError, TypeError, ValueError):
                self._fail('wire_base_source_refused')
                return False

    def observe_config(self, wire, binding, parent):
        with self._lock:
            try:
                if self._state != 'config':
                    raise OwnedTextBridgeError('owned_wire_base_config_refused')
                text = canonical({'binding': binding, 'parent': parent})
                if len(text.encode('utf-8')) > self._settings['metadata_cap']:
                    raise OwnedTextBridgeError('owned_wire_base_context_refused')
                copied = json.loads(text)
                binding, parent = copied['binding'], copied['parent']
                cfg = self._context['config']
                if (set(binding) != {'atlas_snapshot_id', 'atlas_sha256', 'invocation_id', 'compiler_version',
                        'compiler_source_sha256', 'policy_sha256', 'input_policy_sha256', 'auxiliary_policy_sha256', 'context_sha256', 'files'}
                        or any(binding[k] != value for k, value in cfg.items())
                        or set(parent) != {'snapshot_id', 'atlas_sha256', 'validation_sha256', 'source_sha256'}
                        or any(not isinstance(v, str) or re.fullmatch('[a-f0-9]{64}', v) is None for v in parent.values())
                        or parent['snapshot_id'] != binding['atlas_snapshot_id'] or parent['atlas_sha256'] != binding['atlas_sha256']
                        or parent['source_sha256'] != self._context['source']['source_sha256']):
                    raise OwnedTextBridgeError('owned_wire_base_context_refused')
                body = self._frame(wire, 'config')
                self._hash.update(b',"tsconfig_request":')
                self._hash.update(body)
                self._hash.update(b'}\n')
                self._parts['config'] = {'jsonl_bytes': len(wire), 'sha256': hashlib.sha256(wire).hexdigest()}
                overhead = len(b'{"source_request":') + len(b',"tsconfig_request":') + len(b'}\n') - 2
                self._base = {'jsonl_bytes': sum(p['jsonl_bytes'] for p in self._parts.values()) + overhead,
                    'sha256': self._hash.hexdigest(), 'source_bytes': self._context['source']['source_bytes'],
                    'config_bytes': {k: v['bytes'] for k, v in cfg['files'].items()}}
                self._binding, self._parent = policy_identity(binding), policy_identity(parent)
                self._hash = None
                self._state, self._code = 'complete', ''
                return True
            except (KeyError, TypeError, ValueError):
                self._fail('wire_base_config_refused')
                return False

    def snapshot(self):
        with self._lock:
            return json.loads(canonical({'version': 'v1-private-owned-wire-base',
                'status': 'OWNED_WIRE_BASE_DERIVED_NOT_ADMITTED' if self._state == 'complete' else 'OWNED_WIRE_BASE_UNAVAILABLE',
                'code': self._code, 'components': self._parts, 'base': self._base,
                'binding_sha256': self._binding, 'parent_ref_sha256': self._parent,
                'compound_frame_emitted': False, 'send_verified': False, 'native_execution_attested': False,
                'base_content_verified': False, 'production_enabled': False, 'semantic_lineage': False, 'cache_eligible': False}))

    def dispose(self):
        with self._lock:
            self._context = self._settings = self._hash = self._base = self._binding = self._parent = None
            self._parts = {'source': None, 'config': None}
            self._state, self._code = 'disposed', 'wire_base_disposed'


def create_owned_wire_base(profiles, context):
    """Caller owns metadata schema validation and actual frame/parent origin."""
    try:
        return _OwnedWireBase(profiles, context)
    except (KeyError, TypeError, ValueError):
        return None


def _owns_wire_base(receiver):
    return type(receiver) is _OwnedWireBase and getattr(receiver, '_owner', None) is _OWNED_WIRE_BASE_TOKEN


def observe_owned_wire_source(receiver, wire, reference):
    if not _owns_wire_base(receiver):
        return False
    try:
        return receiver.observe_source(wire, reference)
    except (KeyError, TypeError, ValueError, RuntimeError, AttributeError):
        receiver._fail('wire_base_observer_unavailable')
        return False


def observe_owned_wire_config(receiver, wire, binding, parent):
    if not _owns_wire_base(receiver):
        return False
    try:
        return receiver.observe_config(wire, binding, parent)
    except (KeyError, TypeError, ValueError, RuntimeError, AttributeError):
        receiver._fail('wire_base_observer_unavailable')
        return False


def owned_wire_base_snapshot(receiver):
    return receiver.snapshot() if _owns_wire_base(receiver) else None


def owned_wire_base_plan_context(receiver):
    """Copy completed owner metadata for a planner; no raw frames or admission.

    The caller still supplies independent expected identities. The receiver's
    construction observations authenticate neither hostile callers nor native
    execution. Incomplete/disposed owners cannot seed a plan.
    """
    if not _owns_wire_base(receiver):
        return None
    with receiver._lock:
        if receiver._state != 'complete':
            return None
        source, config = receiver._context['source'], receiver._context['config']
        return {'receipt': receiver.snapshot(), 'source_request_id': source['request_id'],
            'config_invocation_id': config['invocation_id'], 'compiler_version': config['compiler_version'],
            'compiler_source_sha256': config['compiler_source_sha256'],
            'source_policy_sha256': source['source_policy_sha256'], 'config_policy_sha256': config['policy_sha256'],
            'input_policy_sha256': config['input_policy_sha256'],
            'auxiliary_policy_sha256': config['auxiliary_policy_sha256']}


def dispose_owned_wire_base(receiver):
    if _owns_wire_base(receiver):
        receiver.dispose()


def checked_policy(policy: dict) -> dict:
    tsx = isinstance(policy, dict) and policy.get('version') == 'v3-owned-utf8-single-tsx'
    fields = {"version", "schema", "default_enabled", "production_enabled", "semantic_lineage",
              "max_files", "max_source_bytes", "max_request_bytes", "max_response_bytes", "max_request_id_chars",
              "virtual_file", "claim_boundary"}
    if tsx:
        fields.add('script_kind')
    if (not isinstance(policy, dict) or set(policy) != fields
            or policy["version"] != ('v3-owned-utf8-single-tsx' if tsx else 'v2-owned-utf8-single-ts')
            or policy["schema"] != ('config/schemas/owned_tsx_text_bridge.schema.json' if tsx else 'config/schemas/owned_text_bridge.schema.json')
            or any(policy[k] is not False for k in ("default_enabled", "production_enabled", "semantic_lineage"))
            or type(policy["max_files"]) is not int or policy["max_files"] != 1
            or any(type(policy[k]) is not int or policy[k] <= 0 for k in (
                "max_source_bytes", "max_request_bytes", "max_response_bytes", "max_request_id_chars"))
            or policy["virtual_file"] != ('/__sage_owned_text__/entry.tsx' if tsx else '/__sage_owned_text__/entry.ts')
            or tsx and policy['script_kind'] != 'TSX'
            or not isinstance(policy["claim_boundary"], str) or not policy["claim_boundary"]):
        raise OwnedTextBridgeError("owned_policy_invalid")
    # The invoking owner validates the full fixed-limit schema. This pure
    # transport layer never imports the artifact/config service dependency graph.
    return json.loads(canonical(policy))


@dataclass(frozen=True)
class OwnedSourceInput:
    raw: bytes = field(repr=False)
    text: str = field(repr=False)
    source_sha256: str
    policy_json: str = field(repr=False)

    def request(self, request_id: str) -> tuple[str, dict]:
        policy = checked_policy(json.loads(self.policy_json))
        if (not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", request_id)
                or len(request_id) > policy["max_request_id_chars"]):
            raise OwnedTextBridgeError("owned_request_id_invalid")
        if (type(self.raw) is not bytes or not isinstance(self.text, str)
                or len(self.raw) > policy["max_source_bytes"]
                or self.text.encode("utf-8") != self.raw
                or hashlib.sha256(self.raw).hexdigest() != self.source_sha256):
            raise OwnedTextBridgeError("owned_input_changed")
        body = {"protocolVersion": 3 if 'script_kind' in policy else 2, "requestId": request_id,
                "policySha256": policy_identity(policy),
                "source": {"path": policy["virtual_file"], "bytes": len(self.raw),
                           "sha256": self.source_sha256,
                           "utf8Base64": base64.b64encode(self.raw).decode("ascii")}}
        if 'script_kind' in policy:
            body['source']['scriptKind'] = policy['script_kind']
        frame = canonical(body)
        if len(frame.encode("utf-8")) + 1 > policy["max_request_bytes"]:
            raise OwnedTextBridgeError("owned_request_over_budget")
        return frame, body


def capture_owned_source(path: str | Path, *, root: Path, policy: dict) -> OwnedSourceInput:
    """One bounded content read in an explicit local fixture root; no live fallback.

    Metadata probes reject unsupported aliases. They do not prove atomic disk
    snapshotting, hostile-race confinement or a caller's asserted data origin.
    """
    policy = checked_policy(policy)
    try:
        root = Path(root).absolute()
        source = Path(path).absolute()
        if (str(root).startswith(("\\\\", "//")) or root.resolve(strict=True) != root
                or source.resolve(strict=True) != source or not source.is_relative_to(root)
                or source.suffix != ('.tsx' if policy.get('script_kind') == 'TSX' else '.ts')
                or source.name.endswith(".d.ts")):
            raise OwnedTextBridgeError("owned_path_refused")
        before = source.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise OwnedTextBridgeError("owned_path_refused")
        with source.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise OwnedTextBridgeError("owned_file_changed")
            raw = stream.read(policy["max_source_bytes"] + 1)
        if len(raw) > policy["max_source_bytes"]:
            raise OwnedTextBridgeError("owned_source_over_budget")
        text = raw.decode("utf-8", errors="strict")
    except OwnedTextBridgeError:
        raise
    except UnicodeError:
        raise OwnedTextBridgeError("owned_encoding_refused") from None
    except (OSError, ValueError):
        raise OwnedTextBridgeError("owned_source_unavailable") from None
    return OwnedSourceInput(raw, text, hashlib.sha256(raw).hexdigest(), canonical(policy))


def validate_owned_response(line: str, expected: dict) -> None:
    """Caller owns actual execution; a matching response is not producer attestation."""
    try:
        response = json.loads(line)
        if not isinstance(response, dict):
            raise OwnedTextBridgeError("owned_response_invalid")
        meta = response["batchMeta"]
        if response.get("error"):
            raise OwnedTextBridgeError("owned_child_refused")
        source = expected["source"]
        if (set(response) != {"batchMeta", "results"} or not isinstance(meta, dict)
                or not isinstance(response["results"], dict)
                or meta.get("protocolVersion") != expected['protocolVersion'] or meta.get("requestId") != expected["requestId"]
                or expected['protocolVersion'] == 3 and (source.get('scriptKind') != 'TSX' or meta.get('sourceScriptKind') != 'TSX')
                or meta.get("sourceSha256") != source["sha256"]
                or meta.get("policySha256") != expected["policySha256"]
                or type(meta.get("filesRequested")) is not int or meta["filesRequested"] != 1
                or type(meta.get("filesReported")) is not int or meta["filesReported"] != 1
                or meta.get("semanticLineage") is not False
                or meta.get("productionEnabled") is not False
                or set(response["results"]) != {source["path"]}):
            raise OwnedTextBridgeError("owned_response_mismatch")
        rows = response["results"][source["path"]]
        if not isinstance(rows, list):
            raise OwnedTextBridgeError("owned_response_invalid")
        metas = [r for r in rows if isinstance(r, dict) and r.get("name") == "__file_meta__"]
        if expected['protocolVersion'] == 3 and (len(metas) != 1 or metas[0].get('parserScriptKind') != 'TSX'):
            raise OwnedTextBridgeError('owned_response_mismatch')
        hashes = [f for f in metas[0].get("features", []) if isinstance(f, str) and f.startswith("Hash:")] if len(metas) == 1 else []
        if hashes != ["Hash:" + source["sha256"]]:
            raise OwnedTextBridgeError("owned_parser_text_mismatch")
    except OwnedTextBridgeError:
        raise
    except (KeyError, TypeError, ValueError, IndexError):
        raise OwnedTextBridgeError("owned_response_invalid") from None
