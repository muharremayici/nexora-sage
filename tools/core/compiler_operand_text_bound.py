"""Private, disabled metadata projection; no acquisition or value validation.

All hashes, charges, model coefficients, applicability and progress are caller
declarations. This calculator verifies their bounded shape, not their truth.
It is deliberately unwired from the handoff, admission and shared helpers.
"""

MODEL = 'v1-private-completed-operand-root-text-bound'
_COPY_CEILING = 65536
_ROOTS = 6
_MODEL_INTEGER_CEILING = 65536
_HASH_FIELDS = ('copier_source_sha256', 'canonical_source_sha256',
                'owner_source_sha256', 'root_model_sha256')
_APPLICABILITY = ('returned_owned_tree', 'ordinary_builtins',
                  'fixed_schema_and_fixture', 'no_same_value_refs',
                  'no_repeated_comparison_paths',
                  'successful_prefix_model_applicable')


class _Gap(Exception):
    pass


def _mapping(value, fields, code):
    if type(value) is not dict or len(value) != len(fields):
        raise _Gap(code)
    # Inspect exact builtin key types before lookup, hashing or equality.
    if any(type(key) is not str for key in value):
        raise _Gap(code)
    if set(value) != set(fields):
        raise _Gap(code)
    return value


def _natural(value, low, high, code):
    if type(value) is not int or not low <= value <= high:
        raise _Gap(code)
    return value


def project_completed_operand_text_bound(copy_charges, root_model, progress,
                                        source_binding, applicability):
    """Return a conditional metadata envelope, never execution permission.

    A returned root canonical operand text is bounded by H=6*(N+B).
    A root with q possible comparison slots and literal weight L has envelope
    q*H+L, even on rejection. A declared successful prefix uses its separate
    literal postcondition bounds. Interrupted/recursive/native/full work stays
    unknown. No value or schema argument is accepted, and no hash is computed.
    Unexpected native/output exceptions may propagate without an observation.
    """
    gap = None
    binding = charges = declared_progress = rows = None
    single = all_roots = normal_prefix = rejecting = combined = None
    rejecting_index = None
    try:
        _mapping(source_binding, _HASH_FIELDS, 'metadata_source_binding')
        for key in _HASH_FIELDS:
            value = source_binding[key]
            if (type(value) is not str or len(value) != 64
                    or any(char not in '0123456789abcdef' for char in value)):
                raise _Gap('metadata_source_binding')
        binding = {key: source_binding[key] for key in _HASH_FIELDS}
        _mapping(applicability, _APPLICABILITY, 'metadata_applicability')
        if any(applicability[key] is not True for key in _APPLICABILITY):
            raise _Gap('applicability_not_declared')

        _mapping(copy_charges, ('outcome', 'node_charges', 'scalar_key_charges',
                               'ceiling'), 'metadata_copy_charges')
        if type(copy_charges['outcome']) is not str:
            raise _Gap('metadata_copy_charges')
        if copy_charges['outcome'] != 'returned':
            raise _Gap('copy_not_returned')
        _natural(copy_charges['ceiling'], _COPY_CEILING, _COPY_CEILING,
                 'metadata_copy_ceiling')
        nodes = _natural(copy_charges['node_charges'], 1, _COPY_CEILING,
                         'metadata_copy_charges')
        payload = _natural(copy_charges['scalar_key_charges'], 0,
                           _COPY_CEILING, 'metadata_copy_charges')

        _mapping(root_model, ('version', 'roots'), 'metadata_root_model')
        if type(root_model['version']) is not str or root_model['version'] != MODEL:
            raise _Gap('metadata_root_model')
        roots = root_model['roots']
        if type(roots) is not list or len(roots) != _ROOTS:
            raise _Gap('metadata_roots')
        coefficients = []
        for index, root in enumerate(roots):
            _mapping(root, ('root_index', 'comparison_slots', 'literal_text_upper',
                            'normal_returned_text_upper'), 'metadata_root')
            _natural(root['root_index'], index, index, 'metadata_root_order')
            q = _natural(root['comparison_slots'], 0, _MODEL_INTEGER_CEILING,
                         'metadata_root_coefficient')
            literal = _natural(root['literal_text_upper'], 0,
                               _MODEL_INTEGER_CEILING, 'metadata_root_coefficient')
            normal = _natural(root['normal_returned_text_upper'], 0,
                              _MODEL_INTEGER_CEILING, 'metadata_root_coefficient')
            if (q == 0 and (literal != 0 or normal != 0)) or (q > 0 and literal == 0):
                raise _Gap('metadata_root_coherence')
            coefficients.append((q, literal, normal))

        _mapping(progress, ('attempted_roots', 'normal_returned_roots',
                            'threw_roots'), 'metadata_progress')
        attempted = _natural(progress['attempted_roots'], 0, _ROOTS,
                             'metadata_progress')
        returned = _natural(progress['normal_returned_roots'], 0, _ROOTS,
                            'metadata_progress')
        threw = _natural(progress['threw_roots'], 0, 1, 'metadata_progress')
        if attempted != returned + threw:
            raise _Gap('metadata_progress')

        single = 6 * (nodes + payload)
        rows = [{'root_index': index, 'possible_comparison_slots': q,
                 'declared_literal_text_upper': literal,
                 'declared_normal_returned_text_upper': normal,
                 'completed_operand_root_text_upper': q * single + literal}
                for index, (q, literal, normal) in enumerate(coefficients)]
        all_roots = sum(row['completed_operand_root_text_upper'] for row in rows)
        normal_prefix = sum(normal for _, _, normal in coefficients[:returned])
        if threw:
            rejecting_index = returned
            rejecting = rows[rejecting_index]['completed_operand_root_text_upper']
        combined = normal_prefix + (rejecting if threw else 0)
        charges = {'node_charges': nodes, 'scalar_key_charges': payload,
                   'ceiling': _COPY_CEILING, 'outcome': 'returned'}
        declared_progress = {'attempted_roots': attempted,
                             'normal_returned_roots': returned,
                             'threw_roots': threw}
    except _Gap as error:
        gap = str(error)
    finally:
        # No caller-owned mapping/list is returned or retained in module state.
        copy_charges = root_model = progress = source_binding = applicability = None

    return {
        'version': MODEL,
        'status': ('CONDITIONAL_COMPLETED_OPERAND_ROOT_TEXT_BOUND_NOT_MEASUREMENT'
                   if gap is None else 'INCOMPLETE_OPERAND_ROOT_TEXT_BOUND'),
        'gap': gap, 'metadata_complete': gap is None,
        'applicability': 'declared_not_verified' if gap is None else 'not_established',
        'declared_source_binding': binding,
        'declared_copy_charges': charges, 'declared_progress': declared_progress,
        'root_envelopes': rows,
        'single_completed_operand_text_upper': single,
        'all_roots_completed_operand_text_upper': all_roots,
        'normal_returned_prefix_text_upper': normal_prefix,
        'rejecting_root_index': rejecting_index,
        'rejecting_root_text_upper': rejecting,
        'declared_cut_completed_operand_text_upper': combined,
        'source_identity_verified': False, 'charge_origin_verified': False,
        'progress_verified': False, 'model_semantics_verified': False,
        'actual_equality_attempts': None, 'actual_canonical_operand_bytes': None,
        'recursive_canonical_call_count': None,
        'interrupted_canonical_work_upper': None,
        'full_work_units': None, 'physical_fit': None,
        'preflight_admission': False, 'full_cost_priced': False,
        'default_enabled': False, 'production_enabled': False,
        'semantic_lineage': False, 'cache_eligible': False,
        'publication_authority': False,
    }
