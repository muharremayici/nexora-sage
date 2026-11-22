"""Pure finite captured-import coordinates. No parser, resolver, storage or live IO."""
from copy import deepcopy
import hashlib
import json

from tools.core.atlas_ast_contract import AST_CONTRACT_VERSION
from tools.core.source_coordinates import (
    _native_utf16_span_validator, _utf16_offsets_to_utf8_byte_offsets, _utf8_span_validator,
)

VERSION = 'v1-static-es-import-bindings'
SPAN_ROLES = ('statement', 'module', 'binding', 'name', 'keyword')


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def occurrence_policy(root):
    """Use the installed shared record vocabulary and one occurrence budget owner."""
    policy = {**root['occurrences'], 'record_kinds': root['record_kinds'], 'record_scope': root['record_scope']}
    if (set(policy) != {'kind', 'version', 'max_file_rows', 'max_projection_rows', 'max_source_bytes',
                       'authority_rule', 'record_kinds', 'record_scope'}
            or policy['kind'] != 'nexora.audit_import_occurrences' or policy['version'] != VERSION
            or policy['record_scope'] != 'top_level' or not isinstance(policy['record_kinds'], list)
            or not policy['record_kinds'] or not all(isinstance(k, str) and k for k in policy['record_kinds'])
            or not isinstance(policy['authority_rule'], str) or not policy['authority_rule']
            or any(type(policy[k]) is not int or policy[k] < 1
                   for k in ('max_file_rows', 'max_projection_rows', 'max_source_bytes'))):
        raise ValueError('invalid_import_occurrence_policy')
    return policy


def unavailable_occurrences(reason):
    return {'version': VERSION, 'status': 'unavailable', 'reason': reason,
            'offset_unit': 'utf8_bytes', 'ast_contract_version': AST_CONTRACT_VERSION,
            'rows': [], 'omitted': 0}


def _valid_rows(rows, content, policy, valid_span, *, native):
    """Verify exact tokens/containment once against a shared coordinate context."""
    encoded = content.encode('utf-16-le' if native else 'utf-8')
    unit = 2 if native else 1
    seen, previous, statement_tokens = set(), None, {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'record', *SPAN_ROLES}:
            return False
        record = row['record']
        if (not isinstance(record, dict) or set(record) != {'source', 'name', 'kind', 'scope'}
                or any(not isinstance(v, str) or not v or v != v.strip() for v in record.values())
                or record['kind'] not in policy['record_kinds'] or record['scope'] != policy['record_scope']
                or not all(valid_span(row[k]) for k in SPAN_ROLES)
                or not valid_span(row['module'], record['source'], quoted=True)):
            return False
        statement, module, binding, name, keyword = (row[k] for k in SPAN_ROLES)
        if (not statement['start'] <= module['start'] < module['end'] <= statement['end']
                or not statement['start'] <= binding['start'] < binding['end'] <= statement['end']
                or not binding['start'] <= name['start'] < name['end'] <= binding['end']
                or keyword['start'] != statement['start'] or not valid_span(keyword, 'import')):
            return False
        statement_key = (statement['start'], statement['end'])
        if statement_key not in statement_tokens:
            statement_tokens[statement_key] = encoded[statement['start'] * unit:statement['end'] * unit].decode('utf-16-le' if native else 'utf-8')
        token = statement_tokens[statement_key]
        if (not token.startswith('import') or len(token) <= len('import')
                or token[len('import')] not in ' \t\r\n\u2028\u2029\ufeff{*\'"/'
                or not (valid_span.trivia_between(module['end'], statement['end'], '')
                        or valid_span.trivia_between(module['end'], statement['end'], ';'))):
            return False
        if record['kind'] == 'side_effect':
            if (record['name'] != '*' or binding != module or name != module
                    or not valid_span.trivia_between(keyword['end'], module['start'], '')):
                return False
        elif binding['end'] > module['start'] or binding['start'] < statement['start'] + len('import'):
            return False
        if record['kind'] == 'default':
            if (record['name'] != 'default' or name != binding
                    or not valid_span.trivia_between(keyword['end'], binding['start'], '')):
                return False
        if record['kind'] in ('named', 'namespace') and not valid_span(name, record['name']):
            return False
        if record['kind'] == 'named' and name['start'] != binding['start']:
            return False
        if record['kind'] == 'namespace' and (name['end'] != binding['end']
                or not valid_span.trivia_between(binding['start'], name['start'], '*as')):
            return False
        key = (statement['start'], binding['start'], binding['end'])
        if key in seen or (previous is not None and key <= previous):
            return False
        seen.add(key)
        previous = key
    return True


def normalize_import_occurrences(entries, content, *, root_policy, atlas_source_hash, parser_status):
    """Normalize the existing native AST sidecar; refuse atomically, not row rescue."""
    invalid = unavailable_occurrences('native_source_policy_or_shape_invalid')
    try:
        policy = occurrence_policy(root_policy)
        meta = next((r for r in entries if isinstance(r, dict) and r.get('name') == '__file_meta__'), {})
        raw = meta.get('moduleImportOccurrences')
        if parser_status != 'observed' or not isinstance(raw, dict) or raw.get('status') == 'unavailable':
            return unavailable_occurrences('native_occurrences_or_parser_unavailable')
        if (set(raw) != {'version', 'status', 'offset_unit', 'source_hash', 'policy_sha256',
                         'parser_kind', 'parser_version', 'rows', 'omitted'}
                or raw['version'] != VERSION or raw['offset_unit'] != 'utf16_code_units'
                or raw['status'] not in ('observed', 'partial')
                or raw['source_hash'] != hashlib.sha256(content.encode()).hexdigest()
                or raw['policy_sha256'] != _digest(policy) or raw['parser_kind'] != 'typescript_compiler_api'
                or not isinstance(raw['parser_version'], str) or not raw['parser_version']
                or len(content.encode()) > policy['max_source_bytes']
                or not isinstance(raw['rows'], list) or len(raw['rows']) > policy['max_file_rows']
                or type(raw['omitted']) is not int or raw['omitted'] < 0
                or (raw['status'] == 'partial') != (raw['omitted'] > 0)):
            return invalid
        if not _valid_rows(raw['rows'], content, policy, _native_utf16_span_validator(content), native=True):
            return invalid
        offsets = [span[k] for row in raw['rows'] for role in SPAN_ROLES
                   for span in [row[role]] for k in ('start', 'end')]
        mapping = _utf16_offsets_to_utf8_byte_offsets(content, offsets)
        if any(k not in mapping for k in offsets):
            return invalid
        normalized = deepcopy(raw)
        for row in normalized['rows']:
            for role in SPAN_ROLES:
                row[role].update(start=mapping[row[role]['start']], end=mapping[row[role]['end']])
        normalized.update(offset_unit='utf8_bytes', ast_contract_version=AST_CONTRACT_VERSION,
                          atlas_source_hash=atlas_source_hash)
        return normalized
    except (KeyError, TypeError, ValueError, AttributeError, StopIteration):
        return invalid


def validate_stored_occurrences(stored, content, *, root_policy, file_data):
    """Recheck captured source and selected-primary membership, without live reads."""
    policy = occurrence_policy(root_policy)
    if (not isinstance(stored, dict) or set(stored) != {'version', 'status', 'offset_unit', 'source_hash',
            'policy_sha256', 'parser_kind', 'parser_version', 'rows', 'omitted', 'ast_contract_version', 'atlas_source_hash'}
            or stored['version'] != VERSION or stored['status'] not in ('observed', 'partial')
            or stored['offset_unit'] != 'utf8_bytes' or stored['ast_contract_version'] != AST_CONTRACT_VERSION
            or file_data.get('ast_contract_version') != AST_CONTRACT_VERSION
            or file_data.get('parser_evidence', {}).get('status') != 'observed'
            or not isinstance(content, str) or len(content.encode()) > policy['max_source_bytes']
            or stored['source_hash'] != hashlib.sha256(content.encode()).hexdigest()
            or stored['atlas_source_hash'] != file_data.get('hash') or stored['policy_sha256'] != _digest(policy)
            or stored['parser_kind'] != 'typescript_compiler_api'
            or not isinstance(stored['parser_version'], str) or not stored['parser_version']
            or not isinstance(stored['rows'], list) or len(stored['rows']) > policy['max_file_rows']
            or type(stored['omitted']) is not int or stored['omitted'] < 0
            or (stored['status'] == 'partial') != (stored['omitted'] > 0)):
        raise ValueError('stored_import_occurrence_identity_invalid')
    spans = [r[k] for r in stored['rows'] if isinstance(r, dict) for k in SPAN_ROLES if k in r]
    if not _valid_rows(stored['rows'], content, policy, _utf8_span_validator(content, spans), native=False):
        raise ValueError('stored_import_occurrence_spans_invalid')
    primary = [{k: r.get('raw_source' if k == 'source' else k) for k in ('source', 'name', 'kind', 'scope')}
               for r in file_data.get('import_records', []) if isinstance(r, dict)]
    if any(r['record'] not in primary for r in stored['rows']):
        raise ValueError('occurrence_not_in_primary_record_set')
    return stored


def project_import_occurrences(base, stored, policy):
    """Bound rows to verified retained subject IDs, never alter canonical findings."""
    by_record = {tuple(s['record'][k] for k in ('raw_source', 'name', 'kind', 'scope')): s['id'] for s in base['subjects']}
    if len(by_record) != len(base['subjects']):
        raise ValueError('ambiguous_subject_record_identity')
    matches = []
    for row in stored['rows']:
        key = tuple(row['record'][k] for k in ('source', 'name', 'kind', 'scope'))
        if key in by_record:
            matches.append({'subject_id': by_record[key], **{k: deepcopy(row[k]) for k in SPAN_ROLES}})
    retained = matches[:policy['max_projection_rows']]
    missing = sorted(set(by_record.values()) - {r['subject_id'] for r in matches})
    result = {'kind': policy['kind'], 'version': VERSION, 'status': 'bound_occurrences',
        'finding_binding_id': base['binding_id'],
        **{k: base[k] for k in ('project_key', 'atlas_rel_path', 'atlas_snapshot_id', 'source_hash')},
        'parsed_source_sha256': stored['source_hash'], 'offset_unit': 'utf8_bytes',
        'ast_contract_version': stored['ast_contract_version'], 'policy_sha256': stored['policy_sha256'],
        'rows': retained, 'producer_omitted': stored['omitted'],
        'display_omitted': len(matches) - len(retained), 'subject_omitted': base['omitted_subject_count'],
        'subjects_without_locations': missing, 'live_source_currentness': 'not_evaluated',
        'causal_relation': 'not_evaluated', 'target_native_effective_policy': 'not_evaluated',
        'runtime_proof': 'not_performed', 'mutation_authority': 'none'}
    if stored['omitted'] or result['display_omitted'] or result['subject_omitted'] or missing:
        result['status'] = 'partial'
    if not retained:
        result.update(status='unavailable', reason='no_supported_retained_occurrences')
    return result
