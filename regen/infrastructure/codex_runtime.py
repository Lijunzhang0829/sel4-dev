"""Content-bound Codex runtimes independent of extension installation lifetime.

The configuration remains the trust root. Relocation changes only a path, never
the expected executable digest; historical launch paths require explicit aliases.
Audits are read-only and never acquire a binary from the network or another version.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


class CodexRuntimeError(RuntimeError):
    """Trusted runtime unavailable/corrupt: infrastructure, not proof feedback."""
    def __init__(self, message, *, path=None, expected_sha256=None, actual_sha256=None):
        self.failure = {key: value for key, value in {
            'message': message, 'path': str(path) if path is not None else None,
            'expected_sha256': expected_sha256, 'actual_sha256': actual_sha256,
        }.items() if value is not None}
        super().__init__(message)


def _digest(path):
    digest = hashlib.sha256()
    try:
        if not path.is_file():
            raise OSError('not a regular file')
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
    except OSError as error:
        raise CodexRuntimeError(f'trusted Codex runtime unavailable: {path}', path=path) from error
    return digest.hexdigest()


def _verify(path, expected):
    actual = _digest(path)
    if actual != expected:
        raise CodexRuntimeError(f'trusted Codex runtime digest mismatch: {path}',
            path=path, expected_sha256=expected, actual_sha256=actual)
    return path


def _expected(settings):
    expected = settings.get('codex_sha256')
    if not isinstance(expected, str) or re.fullmatch('[0-9a-f]{64}', expected) is None:
        raise CodexRuntimeError('Codex runtime requires a frozen SHA-256 digest')
    return expected


def runtime_path(settings):
    expected = _expected(settings)
    source = Path(settings['codex_bin']).resolve()
    root = settings.get('codex_runtime_root')
    if root is None:
        if source.name == 'codex' and source.parent.name == expected:
            root = source.parent.parent
        else:
            from regen.infrastructure.audited_llm import model_config_path
            root = model_config_path().parent / 'codex-runtime'
    return Path(root).resolve() / expected / 'codex'


def _install(source, target, expected):
    """Publish a fully verified private copy; never overwrite an existing object."""
    if target.exists() or target.is_symlink():
        if target.is_symlink():
            raise CodexRuntimeError('pinned Codex runtime must not be a symlink', path=target)
        return _verify(target, expected)
    _verify(source, expected)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.runtime-', delete=False) as output:
            temporary = Path(output.name)
            with source.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        _verify(temporary, expected)
        temporary.chmod(0o555)
        try:
            os.link(temporary, target)
        except FileExistsError:
            pass  # A concurrent planner may have published the identical object.
        _verify(target, expected)
        descriptor = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise CodexRuntimeError(f'cannot pin Codex runtime: {target}', path=target) from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


def pin_codex_runtime(settings):
    """Freeze the configured version, including its optional dynamic-tool host."""
    expected = _expected(settings)
    source = Path(settings['codex_bin']).resolve()
    target = runtime_path(settings)
    _install(source, target, expected)
    # The optional sibling is needed by recent clients even with code_mode off.
    # Bind its first copy in a receipt; all later launches validate that identity.
    receipt = target.parent / 'runtime.json'
    companion = target.with_name('codex-code-mode-host')
    configured_host_hash = settings.get('codex_code_mode_host_sha256')
    if configured_host_hash is not None and (not isinstance(configured_host_hash, str)
            or re.fullmatch('[0-9a-f]{64}', configured_host_hash) is None):
        raise CodexRuntimeError('invalid frozen Codex code-mode host digest')
    if receipt.exists():
        try:
            manifest = json.loads(receipt.read_text())
        except (OSError, ValueError) as error:
            raise CodexRuntimeError('invalid Codex runtime receipt', path=receipt) from error
        if (not isinstance(manifest, dict) or manifest.get('version') != 1
                or manifest.get('codex_sha256') != expected):
            raise CodexRuntimeError('Codex runtime receipt identity mismatch', path=receipt)
        companion_hash = manifest.get('code_mode_host_sha256')
        if companion_hash is not None and (not isinstance(companion_hash, str)
                or re.fullmatch('[0-9a-f]{64}', companion_hash) is None):
            raise CodexRuntimeError('invalid Codex code-mode host receipt digest', path=receipt)
        if configured_host_hash is not None and companion_hash != configured_host_hash:
            raise CodexRuntimeError('Codex code-mode host receipt differs from configuration', path=receipt)
        if companion_hash is not None:
            _verify(companion, companion_hash)
        elif companion.exists():
            raise CodexRuntimeError('unbound Codex code-mode host appeared', path=companion)
    else:
        original_host = source.with_name('codex-code-mode-host')
        companion_hash = _digest(original_host) if original_host.is_file() else None
        if configured_host_hash is not None and companion_hash != configured_host_hash:
            raise CodexRuntimeError('Codex code-mode host differs from configuration',
                path=original_host, expected_sha256=configured_host_hash, actual_sha256=companion_hash)
        if companion_hash is not None:
            _install(original_host, companion, companion_hash)
        elif companion.exists():
            raise CodexRuntimeError('unbound Codex code-mode host appeared', path=companion)
        from regen.execution.proof_commits import durable
        try:
            durable(receipt, {'version': 1, 'codex_sha256': expected,
                'source': str(source), 'code_mode_host_sha256': companion_hash}, immutable=True)
        except (OSError, ValueError) as error:
            raise CodexRuntimeError('cannot bind Codex runtime receipt', path=receipt) from error
    return target


def runtime_verified_settings(settings):
    """Return launch settings while preserving explicitly trusted historical paths."""
    target = pin_codex_runtime(settings)
    aliases = settings.get('codex_historical_paths', [])
    if not isinstance(aliases, list) or any(not isinstance(path, str) for path in aliases):
        raise CodexRuntimeError('codex_historical_paths must be an explicit list of paths')
    return {**settings, 'codex_bin': str(target),
        'codex_runtime_root': str(target.parent.parent),
        'codex_historical_paths': sorted({str(Path(settings['codex_bin']).resolve()),
                                        *(str(Path(path).resolve()) for path in aliases)})}


def verify_codex_launch(settings, launch):
    """Verify old or relocated receipts without modifying history or runtime files."""
    expected = _expected(settings)
    source = Path(settings['codex_bin']).resolve()
    target = runtime_path(settings)
    aliases = settings.get('codex_historical_paths', [])
    if not isinstance(aliases, list) or any(not isinstance(path, str) for path in aliases):
        raise CodexRuntimeError('codex_historical_paths must be an explicit list of paths')
    allowed = {source, target, *(Path(path).resolve() for path in aliases)}
    command = launch.get('command', [])
    if (len(command) < 3 or Path(command[0]).resolve() not in allowed
            or command[1:3] != ['app-server', '--stdio']
            or launch.get('executable_sha256') != expected):
        raise ValueError('workspace launch does not match the trusted Codex executable')
    # A retained byte-identical copy certifies a historical launch after its
    # plugin path disappears. An unrelated path with the same digest is not enough.
    candidate = target if target.exists() or target.is_symlink() else source
    if candidate == target and target.is_symlink():
        raise CodexRuntimeError('pinned Codex runtime must not be a symlink', path=target)
    _verify(candidate, expected)
    host_hash = settings.get('codex_code_mode_host_sha256')
    if host_hash is not None:
        _verify(candidate.with_name('codex-code-mode-host'), host_hash)
    return candidate
