"""Bounded presentation of the CLI's same-invocation receipt, never a loader."""
from __future__ import annotations

import json
from pathlib import PureWindowsPath

from tools.core.artifact_validator import validate_against_schema, validate_payload
from tools.core.atlas_integrity import payload_sha256
from tools.core.config import CODE_MAPS_DIR
from tools.core.native_run_log_ingestion import _contract
from tools.core.path_identity import strip_current_directory_prefix


def _unavailable(reason):
    return ('ESLint brief unavailable: ' + reason + '.\n'
            'No detail establishes cleanliness, source positions, execution or gate authority.\n')


def _source_label(source):
    # Presentation consistency only; never resolve/stat/open a reported identifier.
    path = source['path'].replace('\\', '/')
    if (not path or path.startswith('/') or PureWindowsPath(path).drive or ':' in path
            or '..' in path.split('/') or any(ord(c) < 32 for c in path)):
        raise ValueError('invalid_declared_display_path')
    return json.dumps(strip_current_directory_prefix(path), ensure_ascii=True)


def _consistent(receipt, observed, normalization):
    identities = receipt['identities']
    if (observed['parser_contract_sha256'] != payload_sha256(normalization)
            or any(observed[k] != identities[k] for k in
                   ('run_sha256', 'streams_sha256', 'input_observation_sha256'))
            or identities['streams_sha256'] != payload_sha256(receipt['streams'])
            or identities['input_observation_sha256'] != payload_sha256(receipt['inputs'])):
        return False
    files, sources = observed['file_results'], receipt['inputs']['source']
    for index, file in enumerate(files):
        if file['result_index'] != index:
            return False
        source = file['source_input_index']
        if source is not None:
            if source >= len(sources):
                return False
            _source_label(sources[source])
    counts = [{} for _ in files]
    for item in observed['items']:
        index, category = item['result_index'], item['category']
        occurrence = item['occurrences'][0]
        if index >= len(files) or occurrence['result_index'] != index:
            return False
        current = counts[index]
        if occurrence['message_index'] != current.get(category, 0):
            return False
        current[category] = current.get(category, 0) + 1
        if category == 'active':
            for key, applies in (('errorCount', item['reported_severity'] == 2),
                                 ('warningCount', item['reported_severity'] == 1),
                                 ('fatalErrorCount', item['reported_fatal'])):
                current[key] = current.get(key, 0) + int(applies)
    return all(file['suppressed_count'] == count.get('suppressed', 0)
               and all(file['reported_counts'][key] == count.get(key, 0)
                       for key in ('errorCount', 'warningCount', 'fatalErrorCount'))
               for file, count in zip(files, counts))


def _diagnostic_line(item):
    location = item['reported_location']
    # Do not synthesize columns/endpoints, including for a line-only report.
    position = ','.join(key + '=' + ('unknown' if value is None else str(value))
                        for key, value in location.items())
    return ('  ' + item['category'] + '[' + str(item['occurrences'][0]['message_index']) + ']'
            + ' rule=' + json.dumps(item['reported_rule_id'], ensure_ascii=True)
            + ' severity=' + str(item['reported_severity'])
            + ' fatal=' + str(item['reported_fatal']).lower()
            + ' reported_position={' + position + '}\n')


def render_eslint_brief(receipt):
    """Same-receipt shape/joins only; not authenticity or source reverification."""
    try:
        contract = _contract()
        policy = contract['brief']
        if validate_against_schema(CODE_MAPS_DIR / contract['brief_schema'], 'native_run_log_brief', policy):
            return _unavailable('invalid_presentation_policy')
    except (KeyError, TypeError, ValueError, OSError):
        return _unavailable('presentation_policy_unavailable')
    observed = receipt.get('diagnostic_observations') if isinstance(receipt, dict) else None
    if not isinstance(observed, dict) or observed.get('format_id') != policy['format_id']:
        return _unavailable('eslint_format_not_selected')
    if validate_payload(contract['artifact_id'], receipt):
        return _unavailable('invalid_receipt')
    if receipt['summary']['status'] != 'INGESTED':
        return _unavailable('input_not_ingested')
    try:
        if not _consistent(receipt, observed, contract['normalization']):
            return _unavailable('inconsistent_receipt')
    except ValueError:
        return _unavailable('invalid_declared_display_path')
    files, items, limits = observed['file_results'], observed['items'], policy['limits']
    header = ('ESLint captured observations: ' + observed['status'] + ' (' + observed['reason_code'] + ').\n'
        + 'Run: ' + receipt['identities']['run_sha256'] + '\n'
        + 'Streams: ' + receipt['identities']['streams_sha256'] + '\n'
        + 'Input observation: ' + receipt['identities']['input_observation_sha256'] + '\n'
        + 'Reported results: files=' + str(len(files)) + '; active=' + str(sum(i['category'] == 'active' for i in items))
        + '; suppressed=' + str(sum(i['category'] == 'suppressed' for i in items))
        + '; fatal=' + str(sum(i['reported_fatal'] for i in items)) + '.\n'
        + 'Current declared input correspondence: ' + receipt['summary']['current_input_correspondence'] + '.\n'
        + 'Historical execution: not_verified; positions: reported_not_source_verified; cleanliness: not_established; gate authority: none.\n'
        + policy['boundary'] + '\n')
    def footer(shown_files, shown_items):
        return (f'Shown result files: {shown_files}/{len(files)}; omitted result files: {len(files) - shown_files}. '
                f'Shown diagnostic rows: {shown_items}/{len(items)}; omitted diagnostic rows: {len(items) - shown_items}.\n')
    # Reserve the longest numeric footer before admitting any whole optional line.
    reserve = (f'Shown result files: {len(files)}/{len(files)}; omitted result files: {len(files)}. '
               f'Shown diagnostic rows: {len(items)}/{len(items)}; omitted diagnostic rows: {len(items)}.\n')
    available = limits['output_characters'] - len(header) - len(reserve)
    if available < 0:
        return _unavailable('mandatory_context_exceeds_budget')
    by_result = [[] for _ in files]
    for item in items:
        by_result[item['result_index']].append(item)
    lines, shown_files, shown_items = [], 0, 0
    for file, diagnostics in zip(files, by_result):
        source_index = file['source_input_index']
        if source_index is None:
            label = 'unresolved_identifier_sha256=' + file['identifier_sha256'] + ' source_now=unknown'
        else:
            source = receipt['inputs']['source'][source_index]
            label = 'declared_path=' + _source_label(source) + ' source_now=' + source['status']
        line = 'result[' + str(file['result_index']) + '] ' + label + '\n'
        if shown_files >= limits['result_files'] or len(line) > available:
            continue
        lines.append(line)
        shown_files += 1
        available -= len(line)
        for item in diagnostics:
            line = _diagnostic_line(item)
            if shown_items >= limits['diagnostic_rows'] or len(line) > available:
                continue
            lines.append(line)
            shown_items += 1
            available -= len(line)
    return header + ''.join(lines) + footer(shown_files, shown_items)
