"""Private fixed config/declaration roster at the existing auxiliary read owner."""
from dataclasses import dataclass, field
import base64
import hashlib
import json
from pathlib import Path
import stat
from types import MappingProxyType
from typing import Mapping

from tools.core.artifact_validator import ensure_against_schema
from tools.core.atlas_typescript_inputs import capture_auxiliary_inputs, auxiliary_context_identity
from tools.core.json_io import load_json_object_strict
from tools.core.json_syntax import loads_json_strict
from tools.core.owned_text_bridge import canonical

ROOT = Path(__file__).resolve().parents[2]


class OwnedConfigError(ValueError):
    """Safe private refusal code, without raw config or physical path."""


@dataclass(frozen=True)
class OwnedConfigInputs:
    raw_by_name: Mapping[str, bytes] = field(repr=False)
    inventory_json: str = field(repr=False)
    context_sha256: str
    policy_sha256: str

    def wire_sources(self):
        inventory = json.loads(self.inventory_json)
        result = {}
        for name, raw in self.raw_by_name.items():
            row = inventory['files'][name]
            if hashlib.sha256(raw).hexdigest() != row['raw_sha256']:
                raise OwnedConfigError('config_input_changed')
            result[name] = {'bytes': len(raw), 'raw_sha256': row['raw_sha256'],
                'host_text_sha256': row['host_text_sha256'], 'utf8Base64': base64.b64encode(raw).decode('ascii')}
        return result


def _capture_config_roster(root, limits, expected_count):
    root = Path(root).absolute()
    if str(root).startswith(('\\\\', '//')) or root.resolve(strict=True) != root:
        raise OwnedConfigError('config_path_refused')
    roster = []
    for name in limits:
        path = root / name
        observed = path.lstat()
        if path.resolve(strict=True) != path or not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
            raise OwnedConfigError('config_path_refused')
        roster.append((str(path), name, name))
    retained = {}
    def observed_read(relative, raw, row):
        if relative not in limits or relative in retained or row.get('canonical_file_path') is not True:
            raise OwnedConfigError('config_capture_refused')
        retained[relative] = raw
    inventory = capture_auxiliary_inputs(root, roster, _private_owned_read=observed_read,
        _private_file_byte_limits=limits)
    identity = auxiliary_context_identity(inventory)
    if (not identity or set(retained) != set(limits) or set(inventory['files']) != set(limits)
            or inventory['selected_files'] != expected_count or inventory['omitted_files']):
        raise OwnedConfigError('config_capture_refused')
    return retained, inventory, identity


def capture_owned_config_inputs(*, root: Path, profile: str = 'private_owned_config_handoff') -> OwnedConfigInputs:
    if profile not in ('private_owned_config_handoff','private_owned_tsx_config_handoff','private_owned_react_capture','private_owned_react_dom_capture','private_owned_react_two_source_capture','private_owned_react_barrel_capture'):
        raise OwnedConfigError('config_profile_refused')
    contracts = load_json_object_strict(ROOT / 'config/analysis_snapshot_lineage_contract.json')['artifacts']['ts_diagnostics']
    settings = contracts[profile]
    tsx = profile == 'private_owned_tsx_config_handoff'
    ensure_against_schema(ROOT / settings['schema'], 'owned_config_handoff', settings)
    limits = {settings['config_atlas_path']: settings['max_config_bytes'],
        settings['auxiliary_atlas_path']: settings['max_auxiliary_bytes']}
    expected_files = [settings['atlas_relative_path'],settings['auxiliary_atlas_path']]
    if tsx:
        limits[settings['jsx_atlas_path']] = settings['max_jsx_bytes']
        expected_files.append(settings['jsx_atlas_path'])
    try:
        retained, inventory, identity = _capture_config_roster(root, limits, settings['max_auxiliary_files'])
        config = loads_json_strict(retained[settings['config_atlas_path']].decode('utf-8-sig', errors='strict'))
        if (not isinstance(config, dict) or set(config) != {'files', 'compilerOptions'}
                or config['files'] != expected_files
                or canonical(config['compilerOptions']) != canonical(contracts[settings['image_profile']]['compiler_options'])):
            raise OwnedConfigError('config_shape_refused')
    except OwnedConfigError:
        raise
    except (OSError, ValueError, UnicodeError, KeyError, RecursionError):
        raise OwnedConfigError('config_capture_unavailable') from None
    return OwnedConfigInputs(MappingProxyType(dict(retained)), canonical(inventory), identity,
        hashlib.sha256(canonical(settings).encode('utf-8')).hexdigest())


def capture_owned_extends_inputs(*, root: Path) -> OwnedConfigInputs:
    """Private exact root/base retention; JSONC grammar belongs to the pinned TS owner."""
    contracts = load_json_object_strict(ROOT / 'config/analysis_snapshot_lineage_contract.json')['artifacts']['ts_diagnostics']
    settings = contracts['private_owned_config_extends']
    ensure_against_schema(ROOT / settings['schema'], 'owned_config_extends', settings)
    cap = contracts[settings['config_limit_profile']]['max_config_bytes']
    limits = {row['atlas_path']: cap for row in settings['roster'].values()}
    try:
        retained, inventory, identity = _capture_config_roster(root, limits, len(settings['roster']))
    except OwnedConfigError:
        raise
    except (OSError, ValueError, UnicodeError, KeyError):
        raise OwnedConfigError('config_extends_capture_unavailable') from None
    return OwnedConfigInputs(MappingProxyType(dict(retained)), canonical(inventory), identity,
        hashlib.sha256(canonical(settings).encode('utf-8')).hexdigest())
