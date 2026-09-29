"""Algorithm identity for shared learners, checkpoints and experiment browsers."""

ALGORITHMS = ('autoserl', 'hilserl')


def algorithm_id(manifest):
    inferred = 'hilserl' if manifest.get('schema', '').startswith('hilserl_') else 'autoserl'
    declared = manifest.get('algorithm_id', inferred)
    if declared not in ALGORITHMS or declared != inferred:
        raise ValueError('Inconsistent experiment algorithm')
    return declared


def require_algorithm(manifest, expected):
    if expected not in ALGORITHMS or algorithm_id(manifest) != expected:
        raise ValueError('Checkpoint algorithm does not match the selected baseline')
