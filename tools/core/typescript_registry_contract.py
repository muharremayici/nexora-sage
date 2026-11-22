"""Finite source-record guards; no engine/storage imports or target evaluation."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from tools.core.json_syntax import loads_json_strict


POLICY_PATH = Path(__file__).resolve().parents[2] / 'config/declarative_registry_review_contract.json'


def registry_source_policy():
    raw = POLICY_PATH.read_bytes()
    root = loads_json_strict(raw.decode('utf-8'))
    if not isinstance(root, dict):
        raise ValueError('typescript_registry_policy_invalid')
    policy = root.get('typescript')
    keys = {'source_bytes', 'nodes', 'tables', 'rows', 'fields', 'stored_bytes'}
    if (not isinstance(policy, dict) or not {'version', 'lane_version', 'limits'}.issubset(policy)
            or set(policy) - {'version', 'lane_version', 'limits', 'member_path'}
            or policy['version'] != 'v1-typescript-literal-key-id'
            or policy['lane_version'] != 'v1-direct-const-string-map-source'
            or not isinstance(policy['limits'], dict) or set(policy['limits']) != keys
            or any(type(v) is not int or v <= 0 for v in policy['limits'].values())
            or not isinstance(root.get('limits'), dict)
            or type(root['limits'].get('name_characters')) is not int or root['limits']['name_characters'] <= 0):
        raise ValueError('typescript_registry_policy_invalid')
    return deepcopy(policy), hashlib.sha256(raw).hexdigest(), root['limits']['name_characters']


def registry_spans(lane, limits):
    """Collect only finite structural spans for the existing file coordinate batch."""
    records = lane.get('records') if isinstance(lane, dict) else None
    if not isinstance(records, list) or len(records) > limits['tables']:
        return
    count = 0
    for table in records:
        if not isinstance(table, dict):
            return
        yield from table.get('spans', {}).values() if isinstance(table.get('spans'), dict) else ()
        rows = table.get('rows')
        if not isinstance(rows, list) or len(rows) > limits['rows']:
            return
        for row in rows:
            count += 1
            if count > limits['rows'] or not isinstance(row, dict):
                return
            yield from row.get('spans', {}).values() if isinstance(row.get('spans'), dict) else ()
            fields = row.get('fields')
            if not isinstance(fields, list) or len(fields) > limits['fields']:
                return
            for field in fields:
                if not isinstance(field, dict):
                    return
                yield from field.get('spans', {}).values() if isinstance(field.get('spans'), dict) else ()


def registry_validation_spans(lane, limits):
    """Include delimiter boundaries requested by complete-property coverage checks.

    Separate from mutable transport spans: synthetic boundaries must never be
    converted twice while normalizing the stored object in place.
    """
    for span in registry_spans(lane, limits):
        yield span
        if (isinstance(span, dict) and type(span.get('start')) is int and type(span.get('end')) is int
                and span['end'] > span['start']):
            yield {**span, 'end':span['start'] + 1, 'end_line':span.get('line')}
            yield {**span, 'start':span['end'] - 1, 'line':span.get('end_line')}


def _registry_source_checks(valid_span, name_limit):
    """Shared literal-property grammar, not a parser or domain classifier."""
    def name(value):
        return isinstance(value, str) and len(value) <= name_limit and value != '__proto__' and not any(ord(c) < 32 for c in value)
    def spans(row, roles):
        value = row.get('spans')
        return (isinstance(value, dict) and set(value) == set(roles)
                and all(valid_span(s) for s in value.values()))
    def contained(outer, inner):
        return outer['start'] <= inner['start'] < inner['end'] <= outer['end']
    def properties(object_span, items):
        start, end = object_span['start'], object_span['end']
        # Delimiter width is one in both native and normalized coordinate units.
        if not valid_span({**object_span, 'end': start + 1, 'end_line': object_span['line']}, '{'):
            return False
        if not valid_span({**object_span, 'start': end - 1, 'line': object_span['end_line']}, '}'):
            return False
        previous = start + 1
        for index, item in enumerate(items):
            prop = item['spans']['property']
            if not contained(object_span, prop) or not valid_span.trivia_between(previous, prop['start'], '' if index == 0 else ','):
                return False
            previous = prop['end']
        return (valid_span.trivia_between(previous, end - 1, '')
                or bool(items) and valid_span.trivia_between(previous, end - 1, ','))
    def property_row(row, value_role):
        s = row['spans']
        return (isinstance(row.get('key_kind'), str) and row['key_kind'] in {'string', 'identifier'} and name(row.get('key'))
                and valid_span(s['key'], row['key'], quoted=row['key_kind'] == 'string')
                and s['property']['start'] == s['key']['start'] and s['property']['end'] == s[value_role]['end']
                and contained(s['property'], s['key']) and contained(s['property'], s[value_role])
                and valid_span.trivia_between(s['key']['end'], s[value_role]['start'], ':'))
    return name, spans, contained, properties, property_row


def registry_lane_problem(lane, valid_span, *, identity, policy, policy_hash, name_limit, ast_version=None):
    """Verify every retained row and complete property coverage before selection."""
    keys = {'version', 'status', 'reason', 'source_hash', 'offset_unit', 'policy_sha256', 'record_count', 'records'}
    if ast_version is not None:
        keys.add('ast_contract_version')
    if (not isinstance(lane, dict) or set(lane) != keys or lane['version'] != policy['lane_version']
            or lane['policy_sha256'] != policy_hash or lane['source_hash'] != identity
            or lane['offset_unit'] != ('utf8_bytes' if ast_version is not None else 'utf16_code_units')
            or (ast_version is not None and lane['ast_contract_version'] != ast_version)
            or lane['status'] != 'observed' or lane['reason'] != ''
            or type(lane['record_count']) is not int or not isinstance(lane['records'], list)
            or lane['record_count'] != len(lane['records']) or len(lane['records']) > policy['limits']['tables']
            or len(json.dumps(lane, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) > policy['limits']['stored_bytes']):
        return 'registry_header_unavailable_or_invalid'
    name, spans, contained, properties, property_row = _registry_source_checks(valid_span, name_limit)
    seen, row_count = set(), 0
    for table in lane['records']:
        if (not isinstance(table, dict) or set(table) != {'name', 'status', 'reason', 'spans', 'rows'}
                or not name(table['name']) or not table['name'] or table['name'] in seen
                or not spans(table, ('declaration', 'binding', 'const_keyword', 'object'))
                or not valid_span(table['spans']['binding'], table['name'])
                or not valid_span(table['spans']['const_keyword'], 'const')
                or not valid_span.trivia_between(table['spans']['const_keyword']['end'], table['spans']['binding']['start'], '')
                or not contained(table['spans']['declaration'], table['spans']['binding'])
                or not contained(table['spans']['declaration'], table['spans']['object'])
                or table['spans']['declaration']['start'] != table['spans']['binding']['start']
                or table['spans']['declaration']['end'] != table['spans']['object']['end']
                or not isinstance(table['status'], str) or not isinstance(table['reason'], str)
                or not isinstance(table['rows'], list)):
            return 'registry_table_invalid'
        seen.add(table['name'])
        if table['status'] == 'unsupported':
            if table['rows'] or table['reason'] not in {'unsupported_or_duplicate_table_property', 'unsupported_or_duplicate_row_property', 'field_budget_exceeded'}:
                return 'registry_unsupported_table_invalid'
            continue
        if table['status'] != 'observed' or table['reason'] != '':
            return 'registry_table_status_invalid'
        row_keys = set()
        for row in table['rows']:
            row_count += 1
            if (row_count > policy['limits']['rows'] or not isinstance(row, dict)
                    or set(row) != {'key', 'key_kind', 'spans', 'fields'}
                    or not spans(row, ('property', 'key', 'object')) or not property_row(row, 'object')
                    or row['key'] in row_keys or not isinstance(row['fields'], list)
                    or len(row['fields']) > policy['limits']['fields']):
                return 'registry_row_invalid'
            row_keys.add(row['key'])
            field_keys = set()
            for field in row['fields']:
                if (not isinstance(field, dict) or not isinstance(field.get('kind'), str) or field['kind'] not in {'opaque', 'string_literal'}
                        or set(field) != ({'key', 'key_kind', 'kind', 'spans', 'value'} if field['kind'] == 'string_literal' else {'key', 'key_kind', 'kind', 'spans'})
                        or not spans(field, ('property', 'key', 'value')) or not property_row(field, 'value')
                        or field['key'] in field_keys):
                    return 'registry_field_invalid'
                field_keys.add(field['key'])
                if field['kind'] == 'string_literal' and (not isinstance(field['value'], str)
                        or len(field['value']) > name_limit or any(ord(c) < 32 for c in field['value'])
                        or not valid_span(field['spans']['value'], field['value'], quoted=True)):
                    return 'registry_literal_invalid'
            if not properties(row['spans']['object'], row['fields']):
                return 'registry_row_coverage_invalid'
        if not properties(table['spans']['object'], table['rows']):
            return 'registry_table_coverage_invalid'
    return None


def registry_member_policy():
    source, digest, width = registry_source_policy()
    policy = source.get('member_path')
    if (not isinstance(policy, dict) or set(policy) != {'version', 'lane_version', 'limits'}
            or policy['version'] != 'v2-typescript-literal-path-key-id'
            or policy['lane_version'] != 'v1-direct-const-literal-member-path-source'
            or not isinstance(policy['limits'], dict)
            or set(policy['limits']) != {'selector_depth', 'objects', 'fields', 'stored_bytes'}
            or any(type(v) is not int or v <= 0 for v in policy['limits'].values())):
        raise ValueError('typescript_registry_member_policy_invalid')
    return deepcopy(policy), digest, width


def registry_member_path_valid(path, policy, width):
    name = _registry_source_checks(None, width)[0]
    return isinstance(path, list) and 1 <= len(path) <= policy['limits']['selector_depth'] and all(name(k) for k in path)


def registry_member_spans(lane, limits, *, validation=False):
    records = lane.get('records') if isinstance(lane, dict) else None
    if not isinstance(records, list) or len(records) > limits['objects']:
        return
    fields_seen = 0
    for record in records:
        if not isinstance(record, dict):
            return
        span = record.get('object_span')
        yield span
        if validation and isinstance(span, dict) and type(span.get('start')) is int and type(span.get('end')) is int:
            yield {**span, 'end':span['start'] + 1, 'end_line':span.get('line')}
            yield {**span, 'start':span['end'] - 1, 'line':span.get('end_line')}
        fields = record.get('fields')
        if not isinstance(fields, list) or len(fields) > limits['fields']:
            return
        for field in fields:
            fields_seen += 1
            if fields_seen > limits['fields'] or not isinstance(field, dict):
                return
            if isinstance(field.get('spans'), dict):
                yield from field['spans'].values()


def registry_member_lane_problem(lane, roots, valid_span, *, identity, policy, root_policy, policy_hash, name_limit, ast_version=None):
    """All retained object facts and parent links, not recursive registry semantics."""
    keys = {'version', 'status', 'reason', 'source_hash', 'offset_unit', 'policy_sha256', 'record_count', 'records'}
    if ast_version is not None:
        keys.add('ast_contract_version')
    if (not isinstance(lane, dict) or set(lane) != keys or lane['version'] != policy['lane_version']
            or lane['status'] != 'observed' or lane['reason'] != '' or lane['source_hash'] != identity
            or lane['policy_sha256'] != policy_hash
            or lane['offset_unit'] != ('utf8_bytes' if ast_version is not None else 'utf16_code_units')
            or (ast_version is not None and lane['ast_contract_version'] != ast_version)
            or type(lane['record_count']) is not int or not isinstance(lane['records'], list)
            or lane['record_count'] != len(lane['records']) or len(lane['records']) > policy['limits']['objects']
            or len(json.dumps(lane,ensure_ascii=False,separators=(',',':')).encode('utf-8')) > policy['limits']['stored_bytes']):
        return 'registry_member_header_unavailable_or_invalid'
    if registry_lane_problem(roots,valid_span,identity=identity,policy=root_policy,policy_hash=policy_hash,name_limit=name_limit,ast_version=ast_version):
        return 'registry_member_root_context_invalid'
    name, spans, contained, properties, property_row = _registry_source_checks(valid_span, name_limit)
    index, total = {}, 0
    for record in lane['records']:
        if (not isinstance(record,dict) or set(record) != {'root_name','member_path','status','reason','object_span','fields'}
                or not name(record['root_name']) or not record['root_name']
                or not isinstance(record['member_path'],list)
                or not 2 <= len(record['member_path']) <= policy['limits']['selector_depth'] + 1
                or not all(name(k) for k in record['member_path'])
                or not isinstance(record['status'],str) or record['status'] not in {'observed','unsupported'}
                or not isinstance(record['reason'],str) or not valid_span(record['object_span'])
                or not isinstance(record['fields'],list) or len(record['fields']) > policy['limits']['fields']):
            return 'registry_member_object_invalid'
        key=(record['root_name'],tuple(record['member_path']))
        if key in index:
            return 'registry_member_duplicate_path'
        index[key]=record
        obj=record['object_span']
        if (not valid_span({**obj,'end':obj['start']+1,'end_line':obj['line']},'{')
                or not valid_span({**obj,'start':obj['end']-1,'line':obj['end_line']},'}')):
            return 'registry_member_object_tokens_invalid'
        if record['status']=='unsupported':
            if record['reason']!='unsupported_or_duplicate_member_property' or record['fields']:
                return 'registry_member_unsupported_invalid'
            continue
        if record['reason']!='':
            return 'registry_member_status_invalid'
        seen=set()
        for field in record['fields']:
            total+=1
            if (total > policy['limits']['fields'] or not isinstance(field,dict)
                    or not isinstance(field.get('kind'),str) or field['kind'] not in {'opaque','string_literal'}
                    or set(field) != ({'key','key_kind','kind','spans','value'} if field['kind']=='string_literal' else {'key','key_kind','kind','spans'})
                    or not spans(field,('property','key','value')) or not property_row(field,'value') or field['key'] in seen):
                return 'registry_member_field_invalid'
            seen.add(field['key'])
            if field['kind']=='string_literal' and (not isinstance(field['value'],str) or len(field['value']) > name_limit
                    or any(ord(c)<32 for c in field['value']) or not valid_span(field['spans']['value'],field['value'],quoted=True)):
                return 'registry_member_literal_invalid'
        if not properties(obj,record['fields']):
            return 'registry_member_coverage_invalid'
    root_index={r['name']:r for r in roots['records'] if r['status']=='observed'}
    for (root,path),record in index.items():
        table=root_index.get(root)
        row=next((r for r in table['rows'] if r['key']==path[0]),None) if table else None
        parent=row if len(path)==2 else index.get((root,path[:-1]))
        if not parent or (len(path)>2 and parent['status']!='observed'):
            return 'registry_member_parent_unavailable'
        field=next((f for f in parent['fields'] if f['key']==path[-1]),None)
        if not field or field['kind']!='opaque' or field['spans']['value']!=record['object_span']:
            return 'registry_member_parent_source_mismatch'
    return None
