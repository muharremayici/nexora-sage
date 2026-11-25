"""Disabled finite synthetic sort-budget ledger; metadata events only.

No sort, Buffer, source-value helper, earlier calculator, I/O or runtime admission.
Counters describe the supplied synthetic plan, never actual native operations.
"""

MODEL = 'v1-private-synthetic-sort-budget-ledger'
REQUEST_VERSION = 'v1-private-sort-budget-metadata-request'
CUT = 'six_preparation_comparison_canonical_sorts_only'
JOIN = 'joined_roots_operands_recursion'
DEPTH_UNIT = 'owned_copy_value_edges'
MAX_EVENTS = 256  # Private synthetic trace width, not a runtime budget limit.
_MAX_SAFE = (1 << 53) - 1
_COPY_CEILING = 65536
_HASH_FIELDS = ('copier_source_sha256', 'canonical_source_sha256',
                'owner_source_sha256', 'budget_design_sha256')
_APPLICABILITY = ('returned_owned_tree', 'same_tree_comparison_keys',
                  'ordinary_utf8_builtins', 'fixed_six_root_cut',
                  'joined_no_reset_no_retry', 'prospective_budget_design_applicable')
_EVENTS = ('compare_returned', 'first_buffer_threw', 'second_buffer_threw',
           'compare_threw', 'validation_threw', 'bookkeeping_interrupted', 'seal')
_COUNTERS = ('entered_comparator_requests', 'admitted_comparators',
             'guard_denied_requests', 'reserved_key_payload',
             'buffer_requests', 'buffer_returns', 'buffer_unknown_attempts',
             'compare_requests', 'compare_returns', 'compare_unknown_attempts')


class _Gap(Exception):
    pass


def _mapping(value, fields, code):
    if type(value) is not dict or len(value) != len(fields):
        raise _Gap(code)
    keys = tuple(value)
    if any(type(key) is not str for key in keys) or set(keys) != set(fields):
        raise _Gap(code)
    return {key: value[key] for key in keys}


def _natural(value, low, high, code):
    if type(value) is not int or not low <= value <= high:
        raise _Gap(code)
    return value


def _label(value, expected, code):
    if type(value) is not str or len(value) != len(expected) or value != expected:
        raise _Gap(code)


def project_sort_budget_ledger(request_metadata, events, applicability):
    """Process one bounded synthetic event plan in fresh local state.

    A callback token selects its modeled outcome only if the pair is admitted.
    A terminal outcome permits only a final seal or snapshot-loss marker before
    seal; active retries are metadata errors. Bookkeeping preserves a prefix, not final
    counters. Inputs must remain stable during the call; unexpected native/race/
    output exceptions may propagate without a result. Returned data is detached.
    """
    gap = None
    metadata_valid = False
    declared = counters = prefix = bounds = terminal = refusal = event_count = None
    try:
        view = _mapping(request_metadata, (
            'version', 'source_binding', 'copy_charges', 'depth_contract',
            'limits', 'cut'), 'metadata_request')
        _label(view['version'], REQUEST_VERSION, 'metadata_version')
        binding = _mapping(view['source_binding'], _HASH_FIELDS, 'metadata_source')
        for value in binding.values():
            if (type(value) is not str or len(value) != 64
                    or any(char not in '0123456789abcdef' for char in value)):
                raise _Gap('metadata_source')
        conditions = _mapping(applicability, _APPLICABILITY, 'metadata_applicability')
        if any(conditions[key] is not True for key in _APPLICABILITY):
            raise _Gap('applicability_not_declared')
        copied = _mapping(view['copy_charges'], (
            'outcome', 'node_charges', 'scalar_key_charges', 'ceiling'),
            'metadata_copy')
        _label(copied['outcome'], 'returned', 'copy_not_returned')
        _natural(copied['ceiling'], _COPY_CEILING, _COPY_CEILING, 'metadata_copy')
        nodes = _natural(copied['node_charges'], 1, _COPY_CEILING, 'metadata_copy')
        payload = _natural(copied['scalar_key_charges'], 0, _COPY_CEILING, 'metadata_copy')
        depth = _mapping(view['depth_contract'], (
            'starting_depth', 'depth_ceiling', 'unit'), 'metadata_depth')
        _natural(depth['starting_depth'], 0, 0, 'metadata_depth')
        _natural(depth['depth_ceiling'], 32, 32, 'metadata_depth')
        _label(depth['unit'], DEPTH_UNIT, 'metadata_depth')
        cut = _mapping(view['cut'], ('scope', 'root_sites', 'ledger_policy'), 'metadata_cut')
        _label(cut['scope'], CUT, 'metadata_cut')
        _natural(cut['root_sites'], 6, 6, 'metadata_cut')
        _label(cut['ledger_policy'], JOIN, 'metadata_cut')
        limits = _mapping(view['limits'], (
            'comparator_admissions', 'reserved_key_utf8_payload'), 'metadata_limits')
        fuel = _natural(limits['comparator_admissions'], 0, _MAX_SAFE, 'metadata_limits')
        ceiling = _natural(limits['reserved_key_utf8_payload'], 0, _MAX_SAFE, 'metadata_limits')
        pair = 2 * payload
        if any(value > _MAX_SAFE for value in (pair, fuel + 1, 2 * fuel, pair * fuel)):
            raise _Gap('metadata_arithmetic')
        if type(events) is not list or not 1 <= len(events) <= MAX_EVENTS:
            raise _Gap('metadata_events')
        plan = tuple(events)
        for token in plan:
            if type(token) is not str or len(token) > 23 or token not in _EVENTS:
                raise _Gap('metadata_event')
        if plan[-1] != 'seal' or 'seal' in plan[:-1]:
            raise _Gap('metadata_seal')

        counts = dict.fromkeys(_COUNTERS, 0)
        state = 'open'
        last_active = len(plan) - 2
        for index, token in enumerate(plan[:-1]):
            if token == 'bookkeeping_interrupted':
                if index != last_active:
                    raise _Gap('metadata_after_terminal')
                prefix = {'completed_events': index, 'counters': dict(counts)}
                if state != 'open':
                    prefix['terminal_before_gap'] = state
                state = 'bookkeeping_unknown'
                continue
            if state != 'open':
                raise _Gap('metadata_after_terminal')
            if token == 'validation_threw':
                state = token
                continue
            counts['entered_comparator_requests'] += 1
            next_admitted = counts['admitted_comparators'] + 1
            next_payload = counts['reserved_key_payload'] + pair
            denied_fuel, denied_payload = next_admitted > fuel, next_payload > ceiling
            if denied_fuel or denied_payload:
                counts['guard_denied_requests'] = 1
                refusal = {'fuel': denied_fuel, 'payload': denied_payload}
                state = 'budget_refused'
                continue
            # Both credits commit before any synthetic native-progress counter.
            counts['admitted_comparators'] = next_admitted
            counts['reserved_key_payload'] = next_payload
            counts['buffer_requests'] += 1
            if token == 'first_buffer_threw':
                counts['buffer_unknown_attempts'] += 1
                state = token
                continue
            counts['buffer_returns'] += 1
            counts['buffer_requests'] += 1
            if token == 'second_buffer_threw':
                counts['buffer_unknown_attempts'] += 1
                state = token
                continue
            counts['buffer_returns'] += 1
            counts['compare_requests'] += 1
            if token == 'compare_threw':
                counts['compare_unknown_attempts'] += 1
                state = token
                continue
            counts['compare_returns'] += 1

        metadata_valid = True
        terminal = 'sealed' if state == 'open' else state
        event_count = len(plan)
        declared = {
            'source_binding': dict(binding),
            'copy_charges': {'outcome': 'returned', 'node_charges': nodes,
                             'scalar_key_charges': payload, 'ceiling': _COPY_CEILING},
            'depth_contract': {'starting_depth': 0, 'depth_ceiling': 32, 'unit': DEPTH_UNIT},
            'limits': {'comparator_admissions': fuel, 'reserved_key_utf8_payload': ceiling},
            'cut': {'scope': CUT, 'root_sites': 6, 'ledger_policy': JOIN},
        }
        if state == 'bookkeeping_unknown':
            gap = 'declared_bookkeeping_interruption'
        else:
            counters = dict(counts)
            bounds = {'admitted_comparators': fuel,
                      'entered_comparator_requests': fuel + 1,
                      'buffer_requests': 2 * fuel, 'compare_requests': fuel,
                      'reserved_key_payload': min(ceiling, pair * fuel),
                      'requested_key_payload': min(ceiling, pair * fuel),
                      'returned_buffer_content_payload': min(ceiling, pair * fuel)}
    except _Gap as error:
        gap = str(error)
        metadata_valid = False
        declared = counters = prefix = bounds = terminal = refusal = event_count = None
    finally:
        request_metadata = events = applicability = None

    status = 'INCOMPLETE_SORT_LEDGER_METADATA'
    if metadata_valid:
        status = ('INCOMPLETE_SYNTHETIC_SORT_LEDGER_NOT_RUNTIME_PROOF'
                  if terminal == 'bookkeeping_unknown' else
                  'SYNTHETIC_SORT_LEDGER_REFUSED_NOT_RUNTIME_PROOF'
                  if terminal == 'budget_refused' else
                  'SYNTHETIC_SORT_LEDGER_COMPLETED_NOT_RUNTIME_PROOF'
                  if terminal == 'sealed' else
                  'SYNTHETIC_SORT_LEDGER_FAILED_NOT_RUNTIME_PROOF')
    return {
        'version': MODEL, 'status': status, 'gap': gap,
        'metadata_valid': metadata_valid,
        'applicability': 'declared_not_verified' if metadata_valid else 'not_established',
        'declared_metadata': declared, 'declared_event_count': event_count,
        'synthetic_terminal': terminal, 'synthetic_counters': counters,
        'synthetic_refusal_dimensions': refusal,
        'recorded_synthetic_prefix': prefix,
        'synthetic_bookkeeping_complete': counters is not None,
        'conditional_uppers_if_future_path_enforced': bounds,
        'source_identity_verified': False, 'charge_origin_verified': False,
        'depth_contract_verified': False, 'cut_verified': False,
        'budget_authority_verified': False, 'events_are_actual_runtime_evidence': False,
        'current_source_enforcement': False, 'preflight_admission': False,
        'actual_sort_callback_requests': None, 'actual_Buffer_requests': None,
        'actual_requested_key_payload': None, 'native_work_upper': None,
        'interrupted_work_upper': None, 'full_work_units': None, 'physical_fit': None,
        'full_cost_priced': False, 'default_enabled': False, 'production_enabled': False,
        'semantic_lineage': False, 'cache_eligible': False, 'publication_authority': False,
    }
