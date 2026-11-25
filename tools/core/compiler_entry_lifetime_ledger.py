"""Disabled supplied-metadata ledger. No source acquisition or execution.

The caller supplies every event, dimension and anchor. Conformance never attests
origin, completeness, physical allocation, live ownership or permission.
"""
from __future__ import annotations

from tools.core import compiler_text_selection as metadata

PROFILE = 'private_compiler_entry_lifetime_ledger'


class LifetimeLedgerRefusal(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code
        self.observation = None


def _need(value, code):
    if not value:
        raise LifetimeLedgerRefusal(code)


def _add(a, b):
    _need(type(a) is type(b) is int and a >= 0 and b >= 0 and a <= metadata.MAX_SAFE - b, 'ledger_arithmetic')
    return a + b


def _context(plan, events, expected, descriptor, schema):
    # Bootstrap bound is independent of untrusted descriptor limits.
    try:
        held = metadata._plain({'plan': plan, 'events': events, 'expected': expected,
                                'descriptor': descriptor, 'schema': schema}, 65536, 'ledger')
        metadata._schema_supported(held['schema'], 'ledger')
        for key, definition in [('descriptor', 'policy'), ('plan', 'plan'), ('events', 'events'), ('expected', 'expected')]:
            metadata._checked(held[key], definition, held['schema'], 'ledger_shape', 'ledger')
    except metadata.TextSelectionRefusal as error:
        raise LifetimeLedgerRefusal('ledger_shape') from error
    d, p, e, s = (held[k] for k in ('descriptor', 'plan', 'expected', 'schema'))
    _need(d['version'] == 'v1-private-supplied-entry-lifetime-ledger'
          and d['model'] == 'supplied_named_alias_phase_work_only_v1'
          and d['result_status'] == 'SUPPLIED_LIFETIME_METADATA_REVIEWED_NOT_RUNTIME_PROOF'
          and d['abort_status'] == 'ABORTED_SUPPLIED_LIFETIME_TRACE_NOT_RUNTIME_PROOF', 'ledger_policy')
    _need(all(d[k] is False for k in ('default_enabled', 'native_execution_enabled', 'production_enabled', 'semantic_lineage', 'cache_eligible')), 'ledger_policy')
    _need(all(type(v) is int and 0 < v <= metadata.MAX_SAFE for v in d['limits'].values())
          and d['limits']['metadata_bytes'] <= 65536, 'ledger_policy')
    _need(type(d['basis_multipliers']) is dict
          and all(type(v) is int and v > 0 for v in d['basis_multipliers'].values()), 'ledger_policy')
    _need(d['schema_sha256'] == metadata._hash(s) and e == {
        'transaction_sha256': p['transaction_sha256'], 'plan_sha256': metadata._hash(p),
        'policy_sha256': metadata._hash(d), 'schema_sha256': metadata._hash(s)}, 'ledger_identity')
    _need(len(metadata._canonical(held)) <= d['limits']['metadata_bytes'], 'ledger_metadata_budget')
    return held


def _representations(plan, d):
    rows = {}
    for rep in plan['representations']:
        name, parent, origin = (rep[k] for k in ('id', 'backing', 'origin'))
        _need(name not in rows and not (parent is not None and origin is not None), 'ledger_representation')
        _need(type(rep['offset']) is int and rep['offset'] >= 0, 'ledger_units')
        if rep['basis'] == d['unknown_basis']:
            _need(rep['units'] is None and parent is origin is None and rep['offset'] == 0, 'ledger_units')
            charge = None
        else:
            _need(rep['basis'] in d['basis_multipliers'] and type(rep['units']) is int
                  and 0 <= rep['units'] <= metadata.MAX_SAFE, 'ledger_units')
            factor = d['basis_multipliers'][rep['basis']]
            _need(rep['units'] <= metadata.MAX_SAFE // factor, 'ledger_arithmetic')
            charge = rep['units'] * factor
        if parent is not None:
            _need(parent in rows, 'ledger_alias')  # Strict earlier-row edges exclude cycles/foreign roots.
            source = rows[parent]
            _need(source['basis'] == rep['basis'] and source['space'] == rep['space']
                  and source['charge'] is not None and _add(rep['offset'], rep['units']) <= source['units'], 'ledger_alias')
            if rep['basis'] != d['byte_basis']:
                _need(rep['offset'] == 0 and rep['units'] == source['units'], 'ledger_alias')
            root = source['root']
        else:
            _need(rep['offset'] == 0 and (origin is None or origin in rows), 'ledger_origin')
            root = name
        rows[name] = {**rep, 'root': root, 'charge': charge}
    return rows


def review_supplied_lifetime(plan, events, expected, descriptor, schema):
    """Return a bounded logical summary of supplied rows; never run a producer."""
    try:
        return _review(plan, events, expected, descriptor, schema)
    except LifetimeLedgerRefusal:
        raise
    except (KeyError, IndexError, TypeError, ValueError, OverflowError) as error:
        # A supplied schema cannot make missing structural fields authoritative.
        # No invented prefix/work observation exists before interpretation.
        raise LifetimeLedgerRefusal('ledger_shape') from error


def _review(plan, events, expected, descriptor, schema):
    held = _context(plan, events, expected, descriptor, schema)
    plan, events, d = (held[k] for k in ('plan', 'events', 'descriptor'))
    limits = d['limits']
    _need(len(plan['phases']) == len(set(plan['phases']))
          and len(plan['phases']) == len(plan['required_live'])
          and 0 < len(plan['phases']) <= limits['phases']
          and len(events) <= limits['events'] and len(plan['representations']) <= limits['representations'], 'ledger_inventory')
    definitions = _representations(plan, d)
    for required in plan['required_live']:
        _need(len(required) == len(set(required)) and all(x in definitions for x in required), 'ledger_overlap')
    rows, holders, used = {}, {}, set()
    peaks, seen = [0] * len(plan['phases']), [False] * len(plan['phases'])
    phase = work = processed = 0
    failed = settled = False

    def new_holder(name, rep):
        _need(name is not None and name not in used and len(used) < limits['holders'], 'ledger_holder')
        used.add(name)
        holders[name] = rep

    def live():
        return set(holders.values())

    def sample():
        roots = {definitions[name]['root'] for name in live()}
        total = 0
        for root in roots:
            if definitions[root]['charge'] is not None:
                total = _add(total, definitions[root]['charge'])
        _need(total <= limits['named_bytes'], 'ledger_capacity')
        peaks[phase] = max(peaks[phase], total)
        seen[phase] |= set(plan['required_live'][phase]) <= live()

    try:
        for index, event in enumerate(events):
            target = event['phase']
            _need(type(target) is int and phase <= target <= phase + 1 and target < len(peaks), 'ledger_phase')
            if target != phase:
                _need(failed or seen[phase], 'ledger_overlap')
                phase = target
                sample()  # Retained forms cross the boundary before the first new event.
            work = _add(work, event['work'])
            op, name, holder, peer = (event[k] for k in ('op', 'rep', 'holder', 'peer'))
            _need(not settled, 'ledger_settled')
            _need(not failed or op in ('release', 'dispose', 'abort', 'settle'), 'ledger_aborted')
            if op in ('interrupt', 'settle'):
                _need(name is holder is peer is None, 'ledger_event')
                if op == 'interrupt':
                    failed = True
                else:
                    _need(index == len(events) - 1 and not holders, 'ledger_settlement')
                    _need(failed or (phase == len(peaks) - 1 and all(seen) and set(rows) == set(definitions)), 'ledger_incomplete')
                    settled = True
            elif op in ('acquire', 'alias', 'copy', 'derive'):
                _need(name in definitions and name not in rows, 'ledger_representation')
                rep = definitions[name]
                if op == 'acquire':
                    _need(rep['backing'] is rep['origin'] is peer is None, 'ledger_origin')
                else:
                    source = rep['backing'] if op == 'alias' else rep['origin']
                    _need(source is not None and holders.get(peer) == source
                          and rows[source]['state'] in ('READY', 'BORROWING'), 'ledger_origin')
                    _need((op == 'alias') == (rep['backing'] is not None), 'ledger_alias')
                    if op == 'copy':
                        _need(rep['basis'] == definitions[source]['basis'] and rep['units'] == definitions[source]['units'], 'ledger_copy')
                    if op == 'derive':
                        _need(rep['charge'] is not None, 'ledger_origin')
                new_holder(holder, name)
                rows[name] = {'state': 'READY', 'owner': holder, 'borrower': None, 'borrowed': False}
            else:
                _need(name in rows and holders.get(holder) == name, 'ledger_holder')
                row = rows[name]
                if op == 'hold':
                    _need(row['state'] in ('READY', 'BORROWING'), 'ledger_order')
                    new_holder(peer, name)
                elif op == 'transfer':
                    _need(holder == row['owner'] and row['state'] == 'READY', 'ledger_order')
                    new_holder(peer, name)
                    del holders[holder]
                    row['owner'] = peer
                elif op == 'borrow':
                    _need(holder == row['owner'] and row['state'] == 'READY' and not row['borrowed'], 'ledger_order')
                    new_holder(peer, name)
                    row.update(state='BORROWING', borrowed=True, borrower=peer)
                elif op in ('finish', 'dispose', 'abort'):
                    _need(holder == row['owner'] and row['state'] in ('READY', 'BORROWING'), 'ledger_order')
                    _need((op == 'finish' and row['state'] == 'BORROWING' and peer == row['borrower'])
                          or (op == 'dispose' and row['state'] == 'READY' and peer is None)
                          or (op == 'abort' and peer == row['borrower']), 'ledger_order')
                    if row['borrower'] is not None:
                        del holders[row['borrower']]
                    del holders[holder]
                    row['state'] = 'ABORTED' if op == 'abort' else 'DISPOSED'
                    failed |= op == 'abort'
                elif op == 'release':
                    _need(peer is None and holder not in (row['owner'], row['borrower']), 'ledger_order')
                    del holders[holder]
                else:
                    raise LifetimeLedgerRefusal('ledger_event')
            sample()
            processed += 1
        _need(settled, 'ledger_settlement')
    except LifetimeLedgerRefusal as error:
        error.observation = {'accepted_events': processed, 'attempted_supplied_work_units': work,
                             'phase': phase, 'known_named_peak_bytes': max(peaks), 'settlement_verified': False}
        raise
    result = {
        'version': d['version'], 'status': d['abort_status'] if failed else d['result_status'],
        'transaction_sha256': plan['transaction_sha256'], 'plan_sha256': metadata._hash(plan),
        'events_sha256': metadata._hash(events), 'policy_sha256': metadata._hash(d),
        'known_named_peak_bytes': max(peaks), 'phase_peaks': peaks, 'supplied_work_units': work,
        'accepted_events': processed, 'created_representations': len(rows), 'retained_holders': len(holders),
        'missing_overlap_phases': [i for i, value in enumerate(seen) if not value],
        'unknown_representation_ids': [k for k, r in definitions.items() if r['charge'] is None],
        'supplied_trace_settled': True, 'runtime_settlement_verified': False,
        'source_attested': False, 'reservation_change_eligible': False,
        'named_capacity_basis': 'supplied_logical_dimensions_only', 'physical_fit': None,
        'flags': {k: False for k in d['denied_flags']},
    }
    _need(len(metadata._canonical(result)) <= limits['output_bytes'], 'ledger_output_budget')
    try:
        metadata._checked(result, 'result', held['schema'], 'ledger_output_shape', 'result')
    except metadata.TextSelectionRefusal as error:
        raise LifetimeLedgerRefusal('ledger_output_shape') from error
    return result
