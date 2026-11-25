"""Private synthetic graph metadata, unwired from value validation and acquisition.

Models the branch/ref/equality structure of compiler_stream_stager.validate.
It does not implement its assertion vocabulary or validate a data value.
Source hashes are caller declarations; external audit owns their verification.
"""

MODEL = "v1-synthetic-stream-validator-graph"
_LIMITS = (('schema_entries', 128), ('structural_links', 256), ('effective_edges', 256),
           ('input_keys', 1024), ('name_characters', 16384), ('analysis_steps', 1024),
           ('output_cells', 160))


class _Gap(Exception):
    pass


class _Budget:
    def __init__(self):
        self.limits = dict(_LIMITS)
        self.counts = dict.fromkeys(self.limits, 0)

    def charge(self, key, amount=1):
        self.counts[key] += amount
        if self.counts[key] > self.limits[key]:
            raise _Gap('budget_' + key)

    def name(self, value):
        if type(value) is not str or len(value) > 256:
            raise _Gap('metadata_name')
        self.charge('name_characters', len(value))
        return value

    def mapping(self, value):
        if type(value) is not dict or len(value) > 64:
            raise _Gap('metadata_mapping')
        # Check exact builtin keys before ANY keyed lookup: hostile key equality
        # must never run. Opaque const/enum payloads are deliberately not walked.
        for key in value:
            self.charge('input_keys')
            self.name(key)
        return value


def _index(schema, budget):
    definitions = budget.mapping(schema.get('$defs', {}))
    nodes, identities, pending = [], {}, [(schema, 0)]
    while pending:
        node, depth = pending.pop()
        budget.charge('schema_entries')
        if depth > 24:
            raise _Gap('metadata_depth')
        if id(node) in identities:
            raise _Gap('metadata_structural_alias_or_cycle')
        budget.mapping(node)
        identities[id(node)] = len(nodes)
        record = {'ref': None, 'children': [], 'slots': 0, 'union_skip': False}
        nodes.append(record)
        if '$ref' in node:
            ref = budget.name(node['$ref'])
            if len(node) != 1 or not ref.startswith('#/$defs/') or ref[8:] not in definitions:
                raise _Gap('metadata_reference')
            record['ref'] = id(definitions[ref[8:]])
        else:
            kind = node.get('type')
            if kind is not None and type(kind) is not str and type(kind) is not list:
                raise _Gap('metadata_type_shape')
            if type(kind) is str:
                budget.name(kind)
            if type(kind) is list:
                if len(kind) > 8:
                    raise _Gap('metadata_type_shape')
                for item in kind:
                    budget.name(item)
                record['union_skip'] = 'properties' in node or 'items' in node or type(node.get('additionalProperties')) is dict
            record['slots'] = int('const' in node)
            if 'enum' in node:
                enum = node['enum']
                if type(enum) is not list or len(enum) > 64:
                    raise _Gap('metadata_enum_shape')
                record['slots'] += len(enum)
        for key in ('$defs', 'properties'):
            if key not in node:
                continue
            children = budget.mapping(node[key])
            for child in children.values():
                budget.charge('structural_links')
                pending.append((child, depth + 1))
                if key == 'properties' and type(node.get('type')) is str and node['type'] == 'object':
                    record['children'].append(id(child))
        for key in ('items', 'additionalProperties'):
            child = node.get(key)
            if type(child) is dict:
                budget.charge('structural_links')
                pending.append((child, depth + 1))
                if node.get('type') == ('array' if key == 'items' else 'object'):
                    record['children'].append(id(child))
            elif child is not None and child is not False and not (key == 'additionalProperties' and child is True):
                raise _Gap('metadata_child_shape')
    for record in nodes:
        edges = []
        if record['ref'] is not None:
            if record['ref'] not in identities:
                raise _Gap('metadata_reference')
            edges.append((identities[record['ref']], False))
        else:
            edges.extend((identities[child], True) for child in record['children'])
        for _ in edges:
            budget.charge('effective_edges')
        record['edges'] = edges
        record.pop('ref')
        record.pop('children')
    return nodes, identities, definitions


def _closure(start, nodes, budget, hops):
    # Iterative DFS classifies back edges by whether their cycle descends a value.
    colors, active, frames, kinds = {start: 1}, {start: 0}, [[start, 0]], []
    descent_cycle = False
    while frames:
        budget.charge('analysis_steps')
        node, offset = frames[-1]
        if offset == len(nodes[node]['edges']):
            colors[node] = 2
            active.pop(node)
            frames.pop()
            if frames:
                kinds.pop()
            continue
        target, descends = nodes[node]['edges'][offset]
        frames[-1][1] += 1
        if target in active:
            descent_cycle |= descends or any(kinds[active[target]:])
        elif target not in colors:
            colors[target] = 1
            active[target] = len(frames)
            frames.append([target, 0])
            kinds.append(descends)
    # Distinct functional-ref analysis; no data validation or canonicalization.
    for start in colors:
        budget.charge('analysis_steps')
        chain, positions, node = [], set(), start
        while node not in hops:
            budget.charge('analysis_steps')
            edges = nodes[node]['edges']
            if not edges or edges[0][1]:
                hops[node] = 0
                break
            if node in positions:
                hops[node] = None
                break
            positions.add(node)
            chain.append(node)
            node = edges[0][0]
        count = hops[node]
        for parent in reversed(chain):
            budget.charge('analysis_steps')
            count = None if count is None else count + 1
            hops[parent] = count
    cycle = any(hops[node] is None for node in colors)
    maximum = None if cycle else max(hops[node] for node in colors)
    return {'reachable_schema_objects': len(colors), 'same_value_ref_cycle': cycle,
            'value_descent_cycle': bool(descent_cycle), 'max_same_value_ref_hops': maximum,
            'conditional_validate_entry_factor': None if maximum is None else maximum + 1,
            'max_comparison_slots_per_entry': max(nodes[node]['slots'] for node in colors),
            'union_child_walks_skipped': sum(nodes[node]['union_skip'] for node in colors),
            'root_metadata_complete': True}


def analyze_synthetic_schema_closure(schema, root_names, source_binding):
    """Inspect only an already-owned synthetic schema tree and named definitions.

    Exact builtin structural metadata only; const/enum values stay opaque.
    Limits cover examined graph/name metadata and output cells, NOT opaque payload
    size, physical memory, native work or time. No value argument is accepted.
    Failure metadata contains fixed gap codes and spent counters, never inputs.
    """
    budget, rows, declared = _Budget(), [], None
    gap, requested = None, None
    nodes = identities = definitions = hops = None
    try:
        budget.mapping(source_binding)
        if (set(source_binding) != {'model', 'validator_source_sha256', 'owner_source_sha256'}
                or type(source_binding['model']) is not str or source_binding['model'] != MODEL):
            raise _Gap('metadata_source_model')
        for key in ('validator_source_sha256', 'owner_source_sha256'):
            value = source_binding[key]
            if type(value) is not str or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                raise _Gap('metadata_source_model')
        declared = {key: source_binding[key] for key in source_binding}
        if type(root_names) is not list or not 1 <= len(root_names) <= 8:
            raise _Gap('metadata_roots')
        requested = len(root_names)
        unique = set()
        for name in root_names:
            budget.name(name)
            if name in unique:
                raise _Gap('metadata_roots')
            unique.add(name)
        budget.mapping(schema)
        nodes, identities, definitions = _index(schema, budget)
        hops = {}
        budget.charge('output_cells', 32)
        for index, name in enumerate(root_names):
            if name not in definitions:
                raise _Gap('metadata_root_missing')
            row = _closure(identities[id(definitions[name])], nodes, budget, hops)
            budget.charge('output_cells', 20)
            rows.append({'root_index': index, **row})
    except _Gap as error:
        gap = str(error)
    finally:
        # No schema, opaque value, frame, exception or mutable input escapes.
        schema = root_names = source_binding = nodes = identities = definitions = hops = None
    return {'version': MODEL, 'status': 'SYNTHETIC_SCHEMA_GRAPH_MODELED_NOT_VALIDATION' if gap is None else 'INCOMPLETE_SYNTHETIC_SCHEMA_GRAPH_NOT_VALIDATION',
            'gap': gap, 'graph_metadata_complete': gap is None, 'declared_source_binding': declared,
            'roots_requested': requested, 'roots': rows, 'spent': dict(budget.counts),
            'schema_assertion_admission': 'not_evaluated', 'source_identity_verified': False,
            'value_validation_result': False, 'actual_validation_entries': None,
            'actual_equality_attempts': None, 'canonical_operand_bytes': None,
            'full_cost_priced': False, 'physical_fit_verified': False,
            'default_enabled': False, 'production_enabled': False, 'semantic_lineage': False,
            'cache_eligible': False, 'publication_authority': False}
