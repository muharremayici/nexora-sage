"""Explicit stored artifact-file metadata; no source/build/edit authority."""
from copy import deepcopy
import json
from pathlib import Path

from tools.core.agent_packet_budget import COMPACT_AGENT_PACKET_TOKENS, context_budget_profile
from tools.core.atlas_io import load_atlas_declared_artifact_metadata
from tools.core.declared_artifact_inputs import declared_artifact_group_valid, declared_artifact_policy, _path
from tools.core.manifest_declaration_links import _text


INTRO = 'Stored declared-target byte observation only; no live, build, package, type or edit proof.\n\n```json\n'


def render_declared_artifact_inspection(payload):
    return INTRO + json.dumps(payload, ensure_ascii=True, allow_nan=False) + '\n```'


def _fits(payload):
    return context_budget_profile(render_declared_artifact_inspection(payload),
        budget_tokens=COMPACT_AGENT_PACKET_TOKENS)['status'] == 'pass'


def declared_artifact_inspection(raw_dir: Path, query: str, *, record_limit: int, line_requested=False):
    policy, _ = declared_artifact_policy()
    payload = {'inspection_plane': 'declared_artifact', 'declared_artifact_ref': '',
        'source_binding': 'unavailable', 'live_source_binding': 'not_checked',
        'source_grounded_for_inspection': False, 'one_shot_edit_ready': False,
        'mutation_proposed': False, 'target_files': [], 'stored_atlas_identity': None,
        'build_attribution': 'not_established', 'package_inclusion': 'not_established',
        'runtime_execution': 'not_performed', 'type_safety': 'not_established',
        'declared_artifact': {'status': 'unavailable', 'reason': 'exact_project_qualified_target_required',
            'entries': [], 'shown': 0, 'display_omitted': None, 'producer_omitted': None}}
    if not isinstance(query, str):
        return payload
    project, sep, relative = query.partition('::')
    if (not sep or not project or '::' in relative or not _path(relative)
            or not _text(project, policy['max_text_bytes']) or not _text(relative, policy['max_text_bytes'])
            or not relative.lower().endswith(tuple(policy['supported_suffixes']))):
        return payload
    payload['declared_artifact_ref'] = query
    section = payload['declared_artifact']
    if not _fits(payload):
        payload['declared_artifact_ref'] = ''
        section['reason'] = 'query_over_budget'
        return payload
    if line_requested:
        section['reason'] = 'line_range_unavailable_for_byte_metadata'
        return payload
    read = load_atlas_declared_artifact_metadata(raw_dir, project)
    if not read.primary_verified:
        section['reason'] = 'selected_primary_unavailable'
        return payload
    data = read.data.get(project)
    group = data.get('declared_artifact_observations') if isinstance(data, dict) else None
    if not declared_artifact_group_valid(group, project):
        section['reason'] = 'stored_observation_missing_or_invalid'
        return payload
    rows = [row for row in group['entries'] if row['candidate'] == relative]
    payload['stored_atlas_identity'] = read.identity
    payload['source_binding'] = 'stored_declared_artifact_metadata_only'
    section.update(reason='stored_observation_not_live_freshness', producer_status=group['status'],
        producer_omitted=group['producer_omitted'], display_omitted=len(rows), matching_stored_entries=len(rows),
        policy_sha256=group['policy_sha256'], observation=deepcopy(group['files'].get(relative)))
    if not rows:
        section.update(reason='target_not_observed_in_finite_scope', observation=None)
    else:
        section['status'] = 'observed' if relative in group['files'] else 'unavailable'
    if not _fits(payload):
        section.clear()
        section.update(status='unavailable', reason='observation_header_over_budget', entries=[], shown=0,
            display_omitted=None, producer_omitted=None)
        return payload
    for row in rows:
        candidate = deepcopy(payload)
        target = candidate['declared_artifact']
        target['entries'].append(deepcopy(row))
        target['shown'] += 1
        target['display_omitted'] -= 1
        if target['shown'] > record_limit or not _fits(candidate):
            break
        payload = candidate
    return payload
