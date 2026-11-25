"""Pure bounded observations of captured ESLint JSON; never a native runner."""
from __future__ import annotations

import hashlib
import math
import re

from tools.core.atlas_integrity import payload_sha256
from tools.core.json_syntax import loads_json_strict


class _Refusal(ValueError):
    """Content-free format refusal, not a target defect."""


def _document(content, limits):
    text = content.decode('utf-8')
    # Nesting preflight only. The shared strict decoder owns JSON syntax/keys.
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in '[{':
            depth += 1
            if depth > limits['json_depth']:
                raise _Refusal('normalization_budget_exceeded')
        elif char in ']}':
            depth -= 1
    value = loads_json_strict(text)
    pending, count = [value], 0
    while pending:
        node = pending.pop()
        count += 1
        if count > limits['json_values']:
            raise _Refusal('normalization_budget_exceeded')
        if isinstance(node, dict):
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
        elif isinstance(node, float) and not math.isfinite(node):
            raise _Refusal('invalid_json_result')
    return value


def _number(value, minimum=0):
    return type(value) is int and value >= minimum


def _text(value, limit):
    if isinstance(value, str) and len(value) > limit:
        raise _Refusal('normalization_budget_exceeded')
    return isinstance(value, str)


def _edit(value):
    return (isinstance(value, dict) and set(value) == {'range', 'text'}
            and isinstance(value['text'], str) and isinstance(value['range'], list)
            and len(value['range']) == 2 and all(_number(n) for n in value['range'])
            and value['range'][0] <= value['range'][1])


def _message(message, category, selected, limit):
    required = {'ruleId', 'severity', 'message'}
    optional = {'line', 'column', 'endLine', 'endColumn', 'fatal', 'nodeType', 'messageId', 'fix', 'suggestions'}
    if category == 'suppressed':
        required = required | {'suppressions'}
    if not isinstance(message, dict) or not required <= message.keys() or message.keys() - required - optional:
        raise _Refusal('invalid_json_result')
    rule = message['ruleId']
    if rule is not None and (not _text(rule, limit) or not re.fullmatch(selected['rule_id_pattern'], rule)):
        raise _Refusal('unsupported_result_shape')
    if type(message['severity']) is not int or message['severity'] not in (1, 2):
        raise _Refusal('invalid_json_result')
    if not _text(message['message'], limit) or type(message.get('fatal', False)) is not bool:
        raise _Refusal('invalid_json_result')
    fatal = message.get('fatal', False)
    if fatal and (message['severity'] != 2 or rule is not None):
        raise _Refusal('unsupported_result_shape')
    location = {key: message.get(key) for key in ('line', 'column', 'endLine', 'endColumn')}
    if any(key in message and not _number(message[key], 1) for key in location):
        raise _Refusal('invalid_json_result')
    line, column, end_line, end_column = location.values()
    if ((column is not None or end_line is not None) and line is None
            or end_column is not None and end_line is None
            or end_line is not None and end_line < line
            or end_line == line and end_column is not None and column is not None and end_column < column):
        raise _Refusal('invalid_json_result')
    if any(key in message and message[key] is not None and not _text(message[key], limit)
           for key in ('nodeType', 'messageId')):
        raise _Refusal('invalid_json_result')
    if 'fix' in message and not _edit(message['fix']):
        raise _Refusal('invalid_json_result')
    if 'suggestions' in message:
        suggestions = message['suggestions']
        if not isinstance(suggestions, list):
            raise _Refusal('invalid_json_result')
        for row in suggestions:
            if (not isinstance(row, dict) or not {'desc', 'fix'} <= row.keys()
                    or row.keys() - {'desc', 'fix', 'messageId', 'data'}
                    or not _text(row['desc'], limit) or not _edit(row['fix'])):
                raise _Refusal('invalid_json_result')
    if category == 'suppressed':
        rows = message['suppressions']
        if not isinstance(rows, list) or not rows:
            raise _Refusal('invalid_json_result')
        if any(not isinstance(row, dict) or set(row) != {'kind', 'justification'}
               or not all(_text(v, limit) for v in row.values()) for row in rows):
            raise _Refusal('invalid_json_result')
    return {'code': 'eslint_reported_diagnostic', 'category': category,
            'reported_rule_id': rule, 'reported_severity': message['severity'],
            'reported_fatal': fatal, 'reported_location': location,
            'message_sha256': hashlib.sha256(message['message'].encode('utf-8')).hexdigest()}


def _results(document, selected, limits, declared_sources, identifier_key, file_limit):
    if not isinstance(document, list):
        raise _Refusal('unsupported_result_shape')
    if len(document) > file_limit:
        raise _Refusal('normalization_budget_exceeded')
    count_fields = ('errorCount', 'fatalErrorCount', 'warningCount', 'fixableErrorCount', 'fixableWarningCount')
    required = {'filePath', 'messages', 'suppressedMessages', *count_fields}
    optional = {'source', 'output', 'usedDeprecatedRules', 'stats'}
    files, items = [], []
    for result_index, row in enumerate(document):
        if (not isinstance(row, dict) or not required <= row.keys() or row.keys() - required - optional
                or not _text(row['filePath'], limits['line_characters']) or not row['filePath']
                or any(not _number(row[key]) for key in count_fields)
                or any(not isinstance(row[key], list) for key in ('messages', 'suppressedMessages'))
                or any(key in row and not isinstance(row[key], str) for key in ('source', 'output'))
                or 'usedDeprecatedRules' in row and not isinstance(row['usedDeprecatedRules'], list)
                or 'stats' in row and not isinstance(row['stats'], dict)):
            raise _Refusal('invalid_json_result')
        if len(items) + len(row['messages']) + len(row['suppressedMessages']) > limits['occurrences']:
            raise _Refusal('normalization_budget_exceeded')
        active = []
        for category, messages in (('active', row['messages']), ('suppressed', row['suppressedMessages'])):
            for message_index, message in enumerate(messages):
                item = _message(message, category, selected, limits['line_characters'])
                item['result_index'] = result_index
                item['occurrences'] = [{'stream': 'stdout', 'result_index': result_index, 'message_index': message_index}]
                items.append(item)
                if category == 'active':
                    active.append((item, 'fix' in message))
        counts = [sum(item['reported_severity'] == 2 for item, _ in active),
                  sum(item['reported_fatal'] for item, _ in active),
                  sum(item['reported_severity'] == 1 for item, _ in active),
                  sum(item['reported_severity'] == 2 and fix for item, fix in active),
                  sum(item['reported_severity'] == 1 and fix for item, fix in active)]
        if counts != [row[key] for key in count_fields]:
            raise _Refusal('reported_count_mismatch')
        index = declared_sources.get(identifier_key(row['filePath']))
        files.append({'result_index': result_index,
            'identifier_sha256': hashlib.sha256(row['filePath'].encode('utf-8')).hexdigest(),
            'identifier_status': 'DECLARED_SOURCE_INPUT' if index is not None else 'UNRESOLVED_IDENTIFIER',
            'source_input_index': index, 'reported_counts': dict(zip(count_fields, counts)),
            'suppressed_count': len(row['suppressedMessages']),
            'source_present': 'source' in row, 'output_present': 'output' in row})
    return files, items


def observe_eslint_json(receipt, captured, tool, requested, policy, declared_sources, identifier_key, file_limit):
    """Only admitted stdout bytes; locations/config/run are still reported."""
    result = {'format_id': requested, 'status': 'UNAVAILABLE', 'reason_code': 'input_not_ingested',
        'coverage': 'declared_eslint_json_results_only', 'parser_contract_sha256': payload_sha256(policy),
        'run_sha256': receipt['identities']['run_sha256'], 'streams_sha256': receipt['identities']['streams_sha256'],
        'input_observation_sha256': receipt['identities']['input_observation_sha256'],
        'file_results': [], 'items': [], 'stderr_interpretation': 'not_parsed',
        'source_locations': 'reported_not_source_verified', 'cleanliness': 'not_established',
        'payload_retention': 'no_raw_messages_source_output_or_fixes'}
    if receipt['summary']['status'] != 'INGESTED':
        return result
    selected = policy['formats'][requested]
    if tool != selected['tool']:
        result.update(status='UNSUPPORTED_FORMAT', reason_code='format_or_tool_not_supported')
        return result
    try:
        document = _document(captured['stdout'], policy['limits'])
        files, items = _results(document, selected, policy['limits'], declared_sources, identifier_key, file_limit)
    except _Refusal as exc:
        reason = str(exc)
        result.update(status='LIMIT_EXCEEDED' if reason == 'normalization_budget_exceeded' else 'UNREADABLE_FORMAT', reason_code=reason)
        return result
    except (ValueError, UnicodeError, RecursionError):
        result.update(status='UNREADABLE_FORMAT', reason_code='invalid_json_result')
        return result
    result.update(status='OBSERVED' if items else 'NO_SUPPORTED_OBSERVATIONS',
                  reason_code='supported_family_observed' if items else 'supported_family_not_seen',
                  file_results=files, items=items)
    return result
