"""Finite manifest-selected leaf observations, not build or package proof."""
from copy import deepcopy
import hashlib
from pathlib import Path, PurePosixPath

from tools.core.atlas_integrity import payload_sha256
from tools.core.atlas_typescript_inputs import POLICY_PATH, auxiliary_context_identity, auxiliary_input_policy
from tools.core.json_io import load_json_object_strict_cached
from tools.core.language_registry import skip_dirs
from tools.core.manifest_declaration_links import _candidate, _encoded, _literal_shape, _manifest_evidence, _sha, _text
from tools.core.package_contracts import _type_entry_policy
from tools.core.patch_input import PatchInputError, _read_confined, _relative_file
from tools.core.repository_topology import project_owned_path_identity


def declared_artifact_policy():
    settings = load_json_object_strict_cached(POLICY_PATH, label='Analysis snapshot lineage')['artifacts']['ts_diagnostics']['atlas_declared_artifact_observations']
    aux = auxiliary_input_policy()
    policy = deepcopy(settings)
    if (policy.get('version') != 'v1-literal-declared-artifact-bytes'
            or policy.get('scope') != 'top_level_types_typings_output_leaf_observation'
            or policy.get('literal_fields') != ['types', 'typings']):
        raise ValueError('Invalid declared artifact policy')
    for key in ('output_roots', 'reason_codes'):
        values = policy.get(key)
        if (not isinstance(values, list) or not values or len(set(values)) != len(values)
                or any(not isinstance(v, str) or not v for v in values)):
            raise ValueError('Invalid declared artifact vocabulary: ' + key)
    if any('/' in v or v not in skip_dirs() for v in policy['output_roots']):
        raise ValueError('Declared artifact roots must remain excluded from source')
    refs = policy.get('budget_refs')
    required = {'max_packages', 'max_candidates', 'max_records', 'max_text_bytes', 'max_record_bytes',
        'max_total_bytes', 'max_files', 'max_file_bytes', 'max_read_bytes'}
    if not isinstance(refs, dict) or set(refs) != required:
        raise ValueError('Invalid declared artifact budget references')
    for name, reference in refs.items():
        value = aux
        if not isinstance(reference, str):
            raise ValueError('Invalid declared artifact budget path')
        for part in reference.split('.'):
            value = value[part]
        if type(value) is not int or value < 1:
            raise ValueError('Invalid declared artifact limit: ' + name)
        policy[name] = value
    policy['supported_suffixes'] = list(aux['declaration_contract_evidence']['supported_suffixes'])
    policy['manifest_policy_sha256'] = _type_entry_policy()[1]
    if not policy['max_text_bytes'] <= policy['max_record_bytes'] <= policy['max_total_bytes']:
        raise ValueError('Inconsistent declared artifact metadata budgets')
    return policy, payload_sha256(policy)


def _path(value):
    try:
        return _relative_file(value) == value
    except PatchInputError:
        return False


def _eligible(manifest, literal, policy):
    candidate, reason = _candidate(manifest, literal, policy)
    if reason:
        return candidate, 'unsupported_target'
    if not _path(candidate):
        return candidate, 'unsupported_target'
    package = PurePosixPath(manifest).parent
    relative = PurePosixPath(candidate).relative_to(package)
    if (len(relative.parts) < 2 or relative.parts[0] not in policy['output_roots']
            or any(part.lower() in skip_dirs() for part in relative.parts[1:-1])
            or not candidate.lower().endswith(tuple(policy['supported_suffixes']))):
        return candidate, 'outside_output_subset'
    return candidate, ''


def _envelope(project, root, policy, digest):
    return {'version': policy['version'], 'scope': policy['scope'], 'policy_sha256': digest,
        'producer_contract': 'atlas-declared-artifact-v1', 'project_key': project, 'project_root': root,
        'manifest_context_sha256': None, 'ownership_sha256': None, 'status': 'unavailable',
        'entries': [], 'files': {}, 'visited_candidates': 0, 'producer_omitted': None,
        'io_budget_charged_bytes': 0, 'reasons': ['capture_unavailable'],
        'build_attribution': 'not_established', 'package_inclusion': 'not_established',
        'runtime_execution': 'not_performed', 'live_freshness': 'not_checked',
        'content_semantics': 'not_inspected', 'mutation_authority': 'not_granted'}


def collect_declared_artifacts(project, root, capture, public_contracts, *, excluded_roots=(), surgical=False, max_metadata_bytes=None):
    """The Atlas owner supplies current inputs; no walk, parse or target execution."""
    policy, digest = declared_artifact_policy()
    root = Path(root).resolve()
    group = _envelope(project, root.as_posix(), policy, digest)
    if surgical:
        group['reasons'] = ['surgical_scope_unavailable']
        return group
    if max_metadata_bytes is not None and (type(max_metadata_bytes) is not int or max_metadata_bytes < 1):
        raise ValueError('Invalid declared artifact selected-plane budget')
    metadata_limit = min(policy['max_total_bytes'], max_metadata_bytes or policy['max_total_bytes'])
    if len(_encoded(group)) > metadata_limit:
        return None
    context = auxiliary_context_identity(capture)
    packages = public_contracts.get('packages') if isinstance(public_contracts, dict) else None
    if (not _text(project, policy['max_text_bytes']) or not project or '::' in project
            or not _sha(context) or capture.get('project_root') != root.as_posix()
            or not isinstance(capture.get('files'), dict)
            or len(capture['files']) > auxiliary_input_policy()['max_files'] or not isinstance(packages, list)):
        return group
    exclusions = tuple(Path(p).resolve() for p in excluded_roots)
    group.update(manifest_context_sha256=context, ownership_sha256=payload_sha256(
        {'project_root': root.as_posix(), 'excluded_roots': sorted(p.as_posix() for p in exclusions)}),
        producer_omitted=0, reasons=[])
    reasons, files, tested, used, previous = set(), group['files'], {}, {}, None
    manifest_policy, manifest_digest = _type_entry_policy()

    def unavailable(reason, *, unknown=False):
        reasons.add(reason)
        if unknown:
            group['producer_omitted'] = None

    def read(relative):
        remaining = policy['max_read_bytes'] - group['io_budget_charged_bytes']
        if remaining <= 1:
            return None, 'input_budget_exceeded'
        limit = min(policy['max_file_bytes'], remaining - 1)
        try:
            owned, canonical = project_owned_path_identity(root, relative, excluded_roots=exclusions)
            if not owned or not canonical:
                return None, 'path_or_file_refused'
            data = _read_confined(root, relative, limit)
        except PatchInputError as exc:
            # Failed reads may have consumed the entire bounded sentinel read.
            # Charge the ceiling, not a fabricated actual-byte measurement.
            group['io_budget_charged_bytes'] += limit + 1
            return None, {'patch_input_file_unavailable': 'file_unavailable',
                'patch_input_over_budget': 'file_over_budget',
                'patch_input_unstable_file': 'file_unstable'}.get(exc.reason, 'path_or_file_refused')
        except (OSError, RuntimeError, ValueError):
            group['io_budget_charged_bytes'] += limit + 1
            return None, 'file_unavailable'
        group['io_budget_charged_bytes'] += len(data) + 1
        return data, ''

    stopped = False
    for index, package in enumerate(packages):
        if index >= policy['max_packages']:
            unavailable('candidate_budget_exceeded', unknown=True)
            break
        evidence = _manifest_evidence(package, capture, manifest_digest, policy, manifest_policy)
        manifest = package.get('manifest') if isinstance(package, dict) else None
        if evidence is None or not _path(manifest) or previous is not None and manifest <= previous:
            unavailable('manifest_evidence_unavailable', unknown=True)
            continue
        previous = manifest
        if evidence['status'] != 'observed':
            unavailable('manifest_evidence_unavailable', unknown=True)
            continue
        for literal in evidence['records']:
            if group['visited_candidates'] >= policy['max_candidates'] or len(group['entries']) >= policy['max_records']:
                unavailable('candidate_budget_exceeded', unknown=True)
                stopped = True
                break
            group['visited_candidates'] += 1
            if not _literal_shape(literal, policy, manifest_policy['max_depth']):
                unavailable('manifest_evidence_unavailable', unknown=True)
                continue
            candidate, reason = _eligible(manifest, literal, policy)
            if not reason:
                if candidate not in tested:
                    if len(tested) >= policy['max_files']:
                        tested[candidate] = (None, 'file_budget_exceeded')
                    else:
                        data, reason = read(candidate)
                        tested[candidate] = (None if data is None else
                            {'raw_sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data)}, reason)
                observation, reason = tested[candidate]
                if observation is not None:
                    files[candidate] = observation
                    used[manifest] = evidence['source_hash']
            row = {'source_manifest': manifest, 'source_manifest_sha256': evidence['source_hash'],
                **deepcopy(literal), 'candidate': candidate, 'file_ref': None if reason else candidate,
                'status': 'unavailable' if reason else 'observed', 'reason': reason}
            if len(_encoded(row)) > policy['max_record_bytes']:
                unavailable('metadata_budget_exceeded', unknown=True)
                stopped = True
                break
            group['entries'].append(row)
            if len(_encoded(group)) + len(_encoded(policy['reason_codes'])) > metadata_limit:
                group['entries'].pop()
                unavailable('metadata_budget_exceeded', unknown=True)
                stopped = True
                break
            if reason:
                unavailable(reason)
        if stopped:
            break
    for manifest, expected in used.items():
        data, reason = read(manifest)
        if reason or hashlib.sha256(data).hexdigest() != expected:
            failure = 'manifest_recheck_unavailable' if reason else 'manifest_changed'
            unavailable(failure)
            for row in group['entries']:
                if row['source_manifest'] == manifest and row['status'] == 'observed':
                    row.update(status='unavailable', reason=failure, file_ref=None)
    referenced = {row['file_ref'] for row in group['entries'] if row['status'] == 'observed'}
    group['files'] = {path: row for path, row in files.items() if path in referenced}
    if not group['files'] and not reasons:
        reasons.add('no_supported_target')
    group.update(status='partial' if reasons and group['entries'] else 'unavailable' if reasons else 'observed', reasons=sorted(reasons))
    if len(_encoded(group)) > metadata_limit:
        group = _envelope(project, root.as_posix(), policy, digest)
        group['reasons'] = ['metadata_budget_exceeded']
    return group


def declared_artifact_group_valid(group, project):
    """Stored finite metadata admission, not live-path or producer attestation."""
    policy, digest = declared_artifact_policy()
    try:
        base = _envelope(project, group.get('project_root'), policy, digest)
        mutable = {'status', 'manifest_context_sha256', 'ownership_sha256', 'entries', 'files',
            'visited_candidates', 'producer_omitted', 'io_budget_charged_bytes', 'reasons'}
        if (not isinstance(group, dict) or set(group) != set(base)
                or any(group[k] != value for k, value in base.items() if k not in mutable)
                or not _text(project, policy['max_text_bytes']) or not project or '::' in project
                or not _text(group['project_root'], policy['max_text_bytes']) or not Path(group['project_root']).is_absolute()
                or group['status'] not in {'observed', 'partial', 'unavailable'}
                or not isinstance(group['entries'], list) or len(group['entries']) > policy['max_records']
                or not isinstance(group['files'], dict) or len(group['files']) > policy['max_files']
                or type(group['visited_candidates']) is not int or not len(group['entries']) <= group['visited_candidates'] <= policy['max_candidates']
                or type(group['io_budget_charged_bytes']) is not int or not 0 <= group['io_budget_charged_bytes'] <= policy['max_read_bytes']
                or not (group['producer_omitted'] is None or type(group['producer_omitted']) is int and group['producer_omitted'] >= 0)
                or not isinstance(group['reasons'], list) or group['reasons'] != sorted(set(group['reasons']))
                or any(r not in policy['reason_codes'] for r in group['reasons'])
                or len(_encoded(group)) > policy['max_total_bytes']):
            return False
        if group['status'] == 'unavailable':
            return (not group['entries'] and not group['files'] and bool(group['reasons'])
                and all(group[k] is None or _sha(group[k]) for k in ('manifest_context_sha256', 'ownership_sha256')))
        if not _sha(group['manifest_context_sha256']) or not _sha(group['ownership_sha256']):
            return False
        manifest_policy, _ = _type_entry_policy()
        refs, seen, manifests, previous = set(), set(), {}, None
        for row in group['entries']:
            extra = {'source_manifest', 'source_manifest_sha256', 'candidate', 'file_ref', 'status', 'reason'}
            if not isinstance(row, dict) or len(row) != 13:
                return False
            literal = {k: v for k, v in row.items() if k not in extra}
            manifest = row['source_manifest']
            if (not _literal_shape(literal, policy, manifest_policy['max_depth']) or not _path(manifest)
                    or not _text(manifest, policy['max_text_bytes']) or PurePosixPath(manifest).name != 'package.json'
                    or not _sha(row['source_manifest_sha256']) or len(_encoded(row)) > policy['max_record_bytes']):
                return False
            candidate, reason = _eligible(manifest, literal, policy)
            if manifest in manifests and manifests[manifest] != row['source_manifest_sha256']:
                return False
            manifests[manifest] = row['source_manifest_sha256']
            key, order = (manifest, tuple(row['field_path'])), (manifest, tuple(row['sibling_order']))
            if key in seen or previous is not None and order <= previous or row['candidate'] != candidate:
                return False
            seen.add(key)
            previous = order
            if row['status'] == 'observed':
                if reason or row['reason'] != '' or row['file_ref'] != candidate or candidate not in group['files']:
                    return False
                refs.add(candidate)
            elif (row['status'] != 'unavailable' or row['file_ref'] is not None
                    or row['reason'] not in policy['reason_codes'] or reason and row['reason'] != reason
                    or row['reason'] not in group['reasons']):
                return False
        for path, row in group['files'].items():
            if (path not in refs or not isinstance(row, dict) or set(row) != {'raw_sha256', 'size_bytes'}
                    or not _sha(row['raw_sha256']) or type(row['size_bytes']) is not int
                    or not 0 <= row['size_bytes'] <= policy['max_file_bytes']):
                return False
        return (set(group['files']) == refs and (group['status'] == 'observed') ==
            (bool(refs) and not group['reasons'] and group['producer_omitted'] == 0))
    except (KeyError, TypeError, ValueError, AttributeError, UnicodeError, PatchInputError):
        return False
