"""Disabled synthetic lifecycle model; never canonicalizes or validates source.

Declarations and object/array shapes are authored metadata, not authenticated
copy, source, policy or actual CJS execution. No runtime consumer imports this
module. Format widths below are model safety bounds, not selected runtime caps.
"""

VERSION = 'v1-private-synthetic-preparation-context'
CUT = 'six_preparation_comparison_roots'
ROOTS = ('d', 't', 'i', 'a', 'i.expected_context',
         'i.expected_context.entry_expected')
_CODES = ('context_policy', 'context_policy', 'context_intent',
          'context_anchors', 'context_expected', 'context_expected')
MAX_MODEL_WORK = 256
MAX_MODEL_DEPTH = 32
_SAFE = 2**53 - 1
_OUTCOMES = ('returned', 'first_buffer_threw', 'second_buffer_threw', 'compare_threw')


class SyntheticContextRefusal(Exception):
    def __init__(self, code, reason):
        super().__init__(code)
        self.code, self.reason = code, reason


class _Handle:
    __slots__ = ()

    def __reduce_ex__(self, protocol):
        raise TypeError('synthetic_context_not_serializable')


def _natural(value):
    return type(value) is int and 0 <= value <= _SAFE


def _digest(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _keys(value, expected):
    return (type(value) is dict and len(value) == len(expected)
            and all(type(key) is str for key in value) and value.keys() == expected)


def _shape(value, depth=0, work=None):
    """Closed shape grammar only; no source keys, values or canonical bytes."""
    if work is None:
        work = [0]
    work[0] += 1
    if depth > MAX_MODEL_DEPTH or work[0] > MAX_MODEL_WORK or type(value) is not tuple:
        raise SyntheticContextRefusal('context_model', 'shape_width_or_type')
    if len(value) == 1 and type(value[0]) is str and value[0] == 'scalar':
        return value
    if len(value) not in (2, 3) or type(value[0]) is not str:
        raise SyntheticContextRefusal('context_model', 'shape_grammar')
    if value[0] == 'array' and len(value) == 2:
        children = value[1]
    elif value[0] == 'object' and len(value) == 3 and _natural(value[1]) and value[1] <= MAX_MODEL_WORK:
        children = value[2]
    else:
        raise SyntheticContextRefusal('context_model', 'shape_grammar')
    if type(children) is not tuple or len(children) > MAX_MODEL_WORK:
        raise SyntheticContextRefusal('context_model', 'shape_children')
    for child in children:
        _shape(child, depth + 1, work)
    return value


def create_synthetic_preparation_context(cap_declaration, copy_declaration, binding):
    """Create a local model, including an explicit UNAVAILABLE bootstrap result."""
    return _Owner(cap_declaration, copy_declaration, binding)


class _Owner:
    def __init__(self, caps, witness, binding):
        self._state, self._reason, self._primary = 'UNAVAILABLE', 'bootstrap_metadata', None
        self._handle = self._counters = self._binding = self._prefix = None
        self._root, self._next, self._returned, self._threw = None, 0, 0, 0
        self._order, self._work, self._lost, self._identity_retained = [], 0, False, False
        binding_keys = {'invocation_id', 'tree_sha256', 'source_sha256', 'cut'}
        if not _keys(binding, binding_keys):
            return
        if not all(_digest(binding[k]) for k in binding_keys - {'cut'}) or type(binding['cut']) is not str or binding['cut'] != CUT:
            return
        cap_keys = {'version', 'authority_kind', 'source_sha256', 'invocation_id', 'cut', 'fuel', 'payload'}
        if not _keys(caps, cap_keys):
            self._reason = 'independent_cap_declaration_unavailable'
            return
        if any(type(caps[k]) is not str for k in cap_keys - {'fuel', 'payload'}):
            return
        if (caps['version'] != VERSION or caps['authority_kind'] != 'independent_declaration'
                or any(caps[k] != binding[k] for k in ('source_sha256', 'invocation_id', 'cut'))
                or not _natural(caps['fuel']) or not _natural(caps['payload'])):
            self._reason = 'cap_binding_or_units'
            return
        copy_keys = {'version', 'outcome', 'snapshot', 'invocation_id', 'tree_sha256', 'source_sha256',
                     'nodes', 'bytes', 'ceiling', 'starting_depth', 'depth_ceiling'}
        if not _keys(witness, copy_keys):
            self._reason = 'copy_witness_unavailable'
            return
        if any(type(witness[k]) is not str for k in copy_keys - {'nodes', 'bytes', 'ceiling', 'starting_depth', 'depth_ceiling'}):
            return
        if (witness['version'] != VERSION or witness['outcome'] != 'returned' or witness['snapshot'] != 'available'
                or any(witness[k] != binding[k] for k in ('invocation_id', 'tree_sha256', 'source_sha256'))
                or not all(_natural(witness[k]) for k in ('nodes', 'bytes', 'ceiling', 'starting_depth', 'depth_ceiling'))
                or not 1 <= witness['nodes'] <= 65536 or witness['bytes'] > 65536
                or (witness['ceiling'], witness['starting_depth'], witness['depth_ceiling']) != (65536, 0, 32)):
            self._reason = 'copy_not_returned_available_or_bound'
            return
        B, F, P = witness['bytes'], caps['fuel'], caps['payload']
        if any(x > _SAFE for x in (2 * B, F + 1, 2 * F, 2 * B * F)):
            self._reason = 'derived_arithmetic'
            return
        self._binding = tuple(binding[k] for k in ('invocation_id', 'tree_sha256', 'source_sha256', 'cut'))
        self._B, self._F, self._P = B, F, P
        self._counters = dict.fromkeys(('entered', 'admitted', 'denied', 'reserved_payload',
                                        'buffer_requests', 'buffer_returns', 'buffer_unknown',
                                        'compare_requests', 'compare_returns', 'compare_unknown'), 0)
        self._state, self._reason, self._handle = 'OPEN', None, _Handle()
        self._checkpoint()

    @property
    def handle(self):
        return self._handle

    def _checkpoint(self):
        self._prefix = {'counters': dict(self._counters), 'roots_entered': self._next,
                        'roots_returned': self._returned, 'roots_threw': self._threw,
                        'operand_order': list(self._order)}
        if self._state == 'TERMINAL':
            self._prefix['terminal_before_gap'] = self._reason

    def _terminate(self, reason, primary=None):
        if self._state == 'OPEN':
            self._state, self._reason = 'TERMINAL', reason
            self._primary = primary or (_CODES[self._next - 1] if self._root is not None else 'context_policy')
            if self._root is not None:
                self._threw += 1
            self._checkpoint()

    def _reject(self, reason):
        self._terminate(reason)
        raise SyntheticContextRefusal(self._primary or 'context_model', reason)

    def _access(self, handle):
        if self._state != 'OPEN':
            raise SyntheticContextRefusal(self._primary or 'context_model', 'context_not_open')
        if handle is not self._handle:
            self._reject('context_identity')

    def _tick(self):
        if self._work >= MAX_MODEL_WORK:
            self._reject('model_work_width')
        self._work += 1

    def enter_root(self, handle, root, binding):
        self._access(handle)
        self._tick()
        if (not _keys(binding, {'invocation_id', 'tree_sha256', 'source_sha256', 'cut'})
                or any(type(binding[k]) is not str for k in binding)
                or tuple(binding[k] for k in ('invocation_id', 'tree_sha256', 'source_sha256', 'cut')) != self._binding):
            self._reject('root_binding')
        if self._root is not None or self._next >= 6 or type(root) is not str or root != ROOTS[self._next]:
            self._reject('root_sequence')
        self._root = root
        self._next += 1
        self._checkpoint()

    def leave_root(self, handle):
        self._access(handle)
        self._tick()
        if self._root is None:
            self._reject('no_active_root')
        self._returned += 1
        self._root = None
        self._checkpoint()

    def _compare(self, outcome):
        self._tick()
        c = self._counters
        c['entered'] += 1
        if c['admitted'] + 1 > self._F or c['reserved_payload'] + 2 * self._B > self._P:
            c['denied'] += 1
            self._reject('sort_budget')
        c['admitted'] += 1
        c['reserved_payload'] += 2 * self._B
        for part in ('first_buffer_threw', 'second_buffer_threw'):
            c['buffer_requests'] += 1
            if outcome == part:
                c['buffer_unknown'] += 1
                self._reject(part)
            c['buffer_returns'] += 1
        c['compare_requests'] += 1
        if outcome == 'compare_threw':
            c['compare_unknown'] += 1
            self._reject(outcome)
        c['compare_returns'] += 1

    def _walk(self, shape, handle, outcome):
        # One shape walker for both modes. Explicit child calls do not receive
        # an array index as authority. This is not a canonical implementation.
        private = handle is not None
        if private:
            self._access(handle)
            self._tick()
        kind = shape[0]
        if kind == 'scalar':
            return 1
        visited = 1
        if kind == 'object' and private:
            for _ in range(shape[1]):
                self._compare(outcome)
        for child in shape[-1]:
            visited += self._walk(child, handle, outcome)
        return visited

    def equality(self, handle, kind, value_shape, literal_shape, *, matched=True, outcome='returned'):
        self._access(handle)
        self._tick()
        if self._root is None:
            self._reject('no_active_root')
        if type(kind) is not str or kind not in ('const', 'enum') or type(matched) is not bool or type(outcome) is not str or outcome not in _OUTCOMES:
            self._reject('equality_metadata')
        try:
            _shape(value_shape)
            _shape(literal_shape)
        except SyntheticContextRefusal:
            self._reject('shape_metadata')
        operands = (('value', value_shape), ('literal', literal_shape))
        if kind == 'enum':
            operands = tuple(reversed(operands))
        for label, shape in operands:
            self._order.append(label)
            self._walk(shape, handle, outcome)
        if kind == 'const' and not matched:
            self._reject('const_mismatch')
        self._checkpoint()
        return matched

    def ordinary(self, shape, *ignored_extra_arguments):
        # Extra arguments, including an index or live handle, never select the
        # private route. No raw values are evaluated or returned by this model.
        _shape(shape)
        return {'mode': 'ordinary_no_context', 'shape_nodes': self._walk(shape, None, 'returned'),
                'canonical_bytes_produced': False}

    def validation_failure(self, handle, primary):
        self._access(handle)
        if not isinstance(primary, BaseException):
            self._reject('primary_not_exception')
        self._identity_retained = True
        self._terminate('validation_threw')
        raise primary

    def _lose_snapshot(self):
        if self._counters is not None and not self._lost:
            # Only the already obtained completed prefix is retained. No
            # reconstruction from live/incomplete counters or event guesses.
            self._lost = True
            self._counters = None
            if self._state == 'OPEN':
                self._state, self._reason = 'TERMINAL', 'bookkeeping_interrupted'

    def seal(self, *, snapshot_available=True):
        if type(snapshot_available) is not bool:
            raise SyntheticContextRefusal('context_model', 'snapshot_metadata')
        if self._state == 'SEALED':
            return self.snapshot()
        if self._state == 'OPEN':
            if self._returned == 6 and self._root is None:
                self._state, self._reason = 'TERMINAL', 'completed'
                self._checkpoint()
            elif snapshot_available:
                self._terminate('root_sequence_incomplete')
        if not snapshot_available:
            self._lose_snapshot()
        self._state, self._handle, self._binding, self._root = 'SEALED', None, None, None
        return self.snapshot()

    def snapshot(self):
        prefix = None
        if self._lost and self._prefix is not None:
            prefix = {**self._prefix, 'counters': dict(self._prefix['counters']),
                      'operand_order': list(self._prefix['operand_order'])}
        return {'version': VERSION, 'status': 'SYNTHETIC_CONTEXT_NOT_RUNTIME_PROOF',
                'state': self._state, 'terminal_reason': self._reason, 'primary_code': self._primary,
                'original_primary_identity_retained': self._identity_retained,
                'synthetic_counters': dict(self._counters) if self._counters is not None else None,
                'roots': {'entered': self._next, 'returned': self._returned, 'threw': self._threw} if self._counters is not None else None,
                'operand_order': list(self._order) if not self._lost else None,
                'recorded_synthetic_prefix': prefix, 'bookkeeping_complete': self._counters is not None and self._state == 'SEALED',
                'source_identity_verified': False, 'charge_origin_verified': False,
                'budget_authority_verified': False, 'current_source_enforcement': False,
                'runtime_admission': False, 'production_enabled': False, 'semantic_lineage': False,
                'cache_eligible': False, 'publication_authority': False,
                'actual_sort_Buffer_work': None, 'physical_fit': None, 'full_work_units': None}
