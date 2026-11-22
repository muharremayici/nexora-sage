"""Disabled recursive canonical bounds over declared metadata only.

Consumes a closed metadata view, not source values or the earlier calculator.
No source/helper invocation, I/O, hashing, admission or measured-cost claim.
"""

MODEL = 'v1-private-recursive-canonical-metadata-bound'
OPERAND_MODEL = 'v1-private-completed-operand-root-text-bound'
ENVELOPE_KIND = 'source_prefix_plus_rejecting_operand_envelope'
DEPTH_UNIT = 'owned_copy_value_edges'
_COPY_CEILING = 65536
_DEPTH_CEILING = 32
_ROOTS = 6
_MAX_SLOTS = 65536
_MAX_ENVELOPE = 1 << 40
_HASH_FIELDS = ('copier_source_sha256', 'canonical_source_sha256',
                'owner_source_sha256', 'root_model_sha256')
_APPLICABILITY = ('returned_owned_tree', 'ordinary_builtins',
                  'fixed_schema_and_fixture', 'no_same_value_refs',
                  'no_repeated_comparison_paths', 'operand_envelope_applicable',
                  'source_literals_serializable', 'copy_depth_contract_applicable',
                  'failed_root_completed_descendants_covered')


class _Gap(Exception):
    pass


def _mapping(value, fields, code):
    if type(value) is not dict or len(value) != len(fields):
        raise _Gap(code)
    keys = tuple(value)
    if len(keys) != len(fields) or any(type(key) is not str for key in keys):
        raise _Gap(code)
    if set(keys) != set(fields):
        raise _Gap(code)
    # Snapshot bounded metadata; never traverse a source value or opaque field.
    return {key: value[key] for key in keys}


def _natural(value, low, high, code):
    if type(value) is not int or not low <= value <= high:
        raise _Gap(code)
    return value


def _label(value, expected, code):
    if type(value) is not str or len(value) != len(expected) or value != expected:
        raise _Gap(code)


def project_recursive_canonical_bound(operand_metadata, depth_contract,
                                     applicability):
    """Project logical uppers from a declared operand-envelope metadata view.

    The envelope must cover the normal prefix plus the next worst-case rejecting
    operand, including completed children of a failed outer canonical call.
    This is NOT measured completed-root bytes. Source truth remains unverified.
    Metadata must remain stable during the call; unexpected native/race/output
    exceptions may propagate without a result. No mutable request state escapes.
    """
    gap = None
    binding = charges = progress = slots = depth = envelope = None
    levels = possible = root_calls = all_calls = inner_calls = returned_text = None
    try:
        view = _mapping(operand_metadata, (
            'version', 'envelope_kind', 'declared_source_binding',
            'declared_copy_charges', 'declared_progress', 'root_slots',
            'declared_cut_completed_operand_text_upper'), 'metadata_operand_view')
        _label(view['version'], OPERAND_MODEL, 'metadata_operand_version')
        _label(view['envelope_kind'], ENVELOPE_KIND, 'metadata_envelope_kind')
        declared = _mapping(view['declared_source_binding'], _HASH_FIELDS,
                            'metadata_source_binding')
        candidate = {}
        for key in _HASH_FIELDS:
            value = declared[key]
            if (type(value) is not str or len(value) != 64
                    or any(char not in '0123456789abcdef' for char in value)):
                raise _Gap('metadata_source_binding')
            candidate[key] = value
        binding = candidate

        conditions = _mapping(applicability, _APPLICABILITY,
                              'metadata_applicability')
        if any(conditions[key] is not True for key in _APPLICABILITY):
            raise _Gap('applicability_not_declared')
        declared_depth = _mapping(depth_contract, ('starting_depth', 'depth_ceiling',
                                                  'unit'), 'metadata_depth')
        _natural(declared_depth['starting_depth'], 0, 0, 'metadata_depth')
        _natural(declared_depth['depth_ceiling'], _DEPTH_CEILING,
                 _DEPTH_CEILING, 'metadata_depth')
        _label(declared_depth['unit'], DEPTH_UNIT, 'metadata_depth')

        copied = _mapping(view['declared_copy_charges'],
                          ('outcome', 'node_charges', 'scalar_key_charges',
                           'ceiling'), 'metadata_copy_charges')
        _label(copied['outcome'], 'returned', 'copy_not_returned')
        _natural(copied['ceiling'], _COPY_CEILING, _COPY_CEILING,
                 'metadata_copy_ceiling')
        nodes = _natural(copied['node_charges'], 1, _COPY_CEILING,
                         'metadata_copy_charges')
        payload = _natural(copied['scalar_key_charges'], 0, _COPY_CEILING,
                           'metadata_copy_charges')

        roots = view['root_slots']
        if type(roots) is not list or len(roots) != _ROOTS:
            raise _Gap('metadata_roots')
        counts = []
        for index, root in enumerate(tuple(roots)):
            row = _mapping(root, ('root_index', 'possible_comparison_slots'),
                           'metadata_root')
            _natural(row['root_index'], index, index, 'metadata_root_order')
            counts.append(_natural(row['possible_comparison_slots'], 0,
                                   _MAX_SLOTS, 'metadata_root_slots'))
        declared_progress = _mapping(view['declared_progress'],
                                     ('attempted_roots', 'normal_returned_roots',
                                      'threw_roots'), 'metadata_progress')
        attempted = _natural(declared_progress['attempted_roots'], 0, _ROOTS,
                             'metadata_progress')
        normal = _natural(declared_progress['normal_returned_roots'], 0, _ROOTS,
                          'metadata_progress')
        threw = _natural(declared_progress['threw_roots'], 0, 1,
                         'metadata_progress')
        if attempted != normal + threw:
            raise _Gap('metadata_progress')
        operand_upper = _natural(view['declared_cut_completed_operand_text_upper'],
                                 0, _MAX_ENVELOPE, 'metadata_envelope')
        q = sum(counts[:attempted])
        if ((q == 0 and operand_upper != 0)
                or (threw and counts[normal] > 0 and operand_upper == 0)):
            raise _Gap('metadata_envelope_coherence')

        # Only the new recursive units are derived. The earlier H/F/prefix
        # arithmetic and the truth of supplied facts are not reconstructed here.
        levels = _DEPTH_CEILING + 1
        possible = q
        root_calls = 2 * q
        all_calls = root_calls * nodes
        inner_calls = root_calls * (nodes - 1)
        returned_text = levels * operand_upper
        envelope = operand_upper
        charges = {'outcome': 'returned', 'node_charges': nodes,
                   'scalar_key_charges': payload, 'ceiling': _COPY_CEILING}
        progress = {'attempted_roots': attempted, 'normal_returned_roots': normal,
                    'threw_roots': threw}
        slots = [{'root_index': index, 'possible_comparison_slots': count}
                 for index, count in enumerate(counts)]
        depth = {'starting_depth': 0, 'depth_ceiling': _DEPTH_CEILING,
                 'unit': DEPTH_UNIT}
    except _Gap as error:
        gap = str(error)
    finally:
        operand_metadata = depth_contract = applicability = None

    return {
        'version': MODEL,
        'status': ('CONDITIONAL_RECURSIVE_CANONICAL_BOUND_NOT_MEASUREMENT'
                   if gap is None else 'INCOMPLETE_RECURSIVE_CANONICAL_BOUND'),
        'gap': gap, 'metadata_complete': gap is None,
        'applicability': 'declared_not_verified' if gap is None else 'not_established',
        'declared_source_binding': binding,
        'declared_copy_charges': charges, 'declared_progress': progress,
        'declared_root_slots': slots, 'declared_depth_contract': depth,
        'declared_operand_root_text_envelope': envelope,
        'maximum_ancestor_levels': levels,
        'possible_comparison_slots_in_cut': possible,
        'operand_root_call_requests_upper': root_calls,
        'canonical_call_requests_including_roots_upper': all_calls,
        'canonical_inner_call_requests_upper': inner_calls,
        'completed_subtree_return_text_upper': returned_text,
        'source_identity_verified': False, 'charge_origin_verified': False,
        'progress_verified': False, 'model_semantics_verified': False,
        'depth_contract_verified': False,
        'actual_canonical_invocation_count': None,
        'actual_completed_subtree_return_bytes': None,
        'interrupted_canonical_work_upper': None, 'native_work_upper': None,
        'full_work_units': None, 'physical_fit': None,
        'preflight_admission': False, 'full_cost_priced': False,
        'default_enabled': False, 'production_enabled': False,
        'semantic_lineage': False, 'cache_eligible': False,
        'publication_authority': False,
    }
