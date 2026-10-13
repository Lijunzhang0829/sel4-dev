"""Versioned final-theory rendering; missing policy preserves legacy sources.

This policy does not change statement shells, proof batches or hot prefixes.
Changing it requires a new run binding, never an in-place resume override.
"""
ISOLATED_CONTEXT = 'isolated-v1'
ARCH_REUSE_CONTEXT = 'arch-reuse-v1'
COMPLETE_CONTEXT_POLICIES = (ISOLATED_CONTEXT, ARCH_REUSE_CONTEXT)


def validate_complete_context_policy(value):
    if not isinstance(value, str) or value not in COMPLETE_CONTEXT_POLICIES:
        raise ValueError('unsupported complete_context_policy: '+repr(value))
    return value


def resolve_complete_context_policy(run):
    return validate_complete_context_policy(run.get('complete_context_policy', ISOLATED_CONTEXT))


def policy_binding(run):
    policy = resolve_complete_context_policy(run)
    return {} if policy == ISOLATED_CONTEXT else {'complete_context_policy': policy}
