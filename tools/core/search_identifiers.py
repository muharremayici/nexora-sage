"""Pure conservative admission for names already recorded by source adapters."""


def derived_search_identifier_valid(value):
    """Accept the runtime's identifier subset plus dollar, not a JS lexer.

    Dollar substitution is validation-only; never normalize stored/query names.
    Explicit join-control exclusion is independent of runtime Unicode tables.
    Parser, binding, span and coverage evidence remain separate requirements.
    """
    return (isinstance(value, str) and '\u200c' not in value and '\u200d' not in value
            and value.replace('$', '_').isidentifier())
