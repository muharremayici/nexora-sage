"""Shared recorded Class member admission; no source reading or evaluation."""
from tools.core.search_identifiers import derived_search_identifier_valid


def valid_recorded_class_member(parent, start, name, line, end_line, is_static, class_end=None):
    return (derived_search_identifier_valid(parent) and derived_search_identifier_valid(name)
            and all(type(n) is int and n > 0 for n in (start, line, end_line))
            and start <= line <= end_line and type(is_static) is bool
            and (class_end is None or type(class_end) is int and end_line <= class_end))


def recorded_class_members(file_info, kinds):
    """Return admitted same-file records and independent partial-coverage state."""
    symbols = file_info.get('symbols')
    partial = not isinstance(symbols, list)
    rows = []
    for symbol in symbols if isinstance(symbols, list) else []:
        if not isinstance(symbol, dict):
            partial = True
            continue
        if symbol.get('type') != 'Class':
            continue
        parent, start, end = symbol.get('name'), symbol.get('line'), symbol.get('end_line')
        members = symbol.get('member_details')
        if (not derived_search_identifier_valid(parent) or type(start) is not int or start <= 0
                or type(end) is not int or end < start or not isinstance(members, list)):
            partial = True
            continue
        for member in members:
            if not isinstance(member, dict):
                partial = True
                continue
            kind = member.get('kind')
            if not isinstance(kind, str) or kind not in kinds:
                continue
            name, line, end_line = member.get('name'), member.get('line'), member.get('end_line')
            is_static = member.get('static')
            if not valid_recorded_class_member(parent, start, name, line, end_line, is_static, end):
                partial = True
                continue
            rows.append((parent, start, f'{parent}.{name}', name, int(is_static), line, end_line, kind))
    return rows, partial
