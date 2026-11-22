"""Disabled fixed five-role historical-anchor use for a trusted parent.

Matching external-decision metadata is linkage, not authentication or a grant
issuer. Acceptance proof uses authored fixtures only. No evaluator or owner.
"""
from tools.core import compiler_entry_same_read_use_transport as parent

PROFILE = "private_compiler_entry_actual_use_transport"
DISPATCH = "private_compiler_entry_actual_use_dispatch"
IDENTITY_PROFILE = "private_compiler_entry_actual_identity"
USE_PROFILE = "private_compiler_entry_actual_use"
CHILD = parent._ACTUAL_CHILD
CONTRACT = parent._ACTUAL
_check_external_authority = parent._check_external_authority
_target_actual = parent._target_actual


def _pins(d, schema, context):
    return parent._pins(d, schema, context, _contract=CONTRACT)


def _transport_pins(d, schema, prepared):
    return parent._transport_pins(d, schema, prepared, _contract=CONTRACT)


def _use_historical_compiler_entry(request, decision, expected, use_expected, profiles, schemas,
                                   dispatch_profile, transport_profile, transport_schema, transport_expected, *,
                                   external_authority, intent_root, deadline=None):
    """One source-fixed guarded use; caller supplies no mode/child/backend."""
    return parent._run_fixed_use(request, decision, expected, use_expected, profiles, schemas,
                                 dispatch_profile, transport_profile, transport_schema, transport_expected,
                                 fixture_root=intent_root, deadline=deadline, _contract=CONTRACT,
                                 external_authority=external_authority)
