"""Disabled historical-anchor adapter for a separately authorized trusted caller.

This library checks declarations; it neither authenticates a principal nor grants
permission. A trusted parent must obtain one current scoped decision externally.
Only temporary synthetic fixtures exercise this adapter in its acceptance proof.
"""
from tools.core import compiler_metadata_integration as integration

PROFILE = "private_compiler_historical_metadata_adapter"
DISPATCH = integration._ACTUAL_DISPATCH
OBSERVER = integration._ACTUAL_OBSERVER
CHILD = integration._ACTUAL_CHILD
_target_directory = integration._target_directory
_check_authority = integration._check_authority


def _observe_historical_compiler_metadata(bundle, declaration, external_authority, expected, profile, schema,
                                          *, intent_root, deadline=None):
    """One declared four-role comparison; no default workflow or grant issuer."""
    return integration._integrate_metadata(bundle, declaration, expected, profile, schema, intent_root=intent_root,
                                           deadline=deadline, _actual=True, _authority=external_authority)
