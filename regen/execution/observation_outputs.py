"""Explicit output protocol for trusted observations in a persistent worker.

The logical request and executed source remain separately bound. Only declared
output literals may change; worker reuse must never weaken source validation.
"""
import hashlib
import re


POLICY = 'native-observation-outputs-v1'
SUPPORTED_FILES = frozenset({'resolution.tsv', 'structure.tsv'})


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def supported(files):
    return (isinstance(files, (tuple, list)) and bool(files)
            and all(isinstance(name, str) and name in SUPPORTED_FILES for name in files)
            and len(files) == len(set(files)))


def worker_source(source, files, request_number):
    if not supported(files) or type(request_number) is not int or request_number < 1:
        raise ValueError('unsupported native observation outputs')
    paths = {name: f'/worker/{request_number}.{name}' for name in files}
    executed = _rewrite(source, paths)
    return executed, {'policy': POLICY, 'logical_source_sha256': _sha(source),
                      'executed_source_sha256': _sha(executed), 'output_paths': paths}


def _rewrite(source, paths):
    executed = source
    for name, destination in paths.items():
        literal = '"/symbol-output/' + name + '"'
        if literal not in source:
            raise ValueError('declared observation output absent from source')
        executed = executed.replace(literal, '"' + destination + '"')
    return executed


def validate_source(check, logical_source, files):
    """Accept exact cold source or the narrowly bound hot output-path rewrite."""
    actual, actual_sha = check.get('source_content'), check.get('source_sha256')
    if actual == logical_source and actual_sha == _sha(logical_source):
        return
    binding = check.get('observation_output_binding', {})
    paths = binding.get('output_paths', {})
    if (check.get('check_scope') != 'isolated-imports-observation'
            or binding.get('policy') != POLICY or not supported(files)
            or not isinstance(paths, dict) or set(paths) != set(files)
            or any(not isinstance(path, str) or not re.fullmatch(
                r'/worker/[1-9][0-9]*\.' + re.escape(name), path)
                for name, path in paths.items())
            or binding.get('logical_source_sha256') != _sha(logical_source)):
        raise ValueError('native observation checked a different source')
    expected = _rewrite(logical_source, paths)
    if (actual != expected or actual_sha != _sha(expected)
            or binding.get('executed_source_sha256') != actual_sha):
        raise ValueError('native observation checked a different source')
