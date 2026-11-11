"""Explicit native layout for the AutoCorres2 pilot; legacy layouts stay intact.

The descriptor lives in the frozen semantic workspace. It can select only this
known profile, not arbitrary host mounts or executable commands.
"""
import json
import re
from pathlib import Path

CALG_BASE = 'Calg_Project_Base'
AFP_CACHE = '/screen/afp-2026/mirror-afp-2026-fca9585c6341cbf0f91816850b375c02e2c17ba6/thys'
ISABELLE_CACHE = '/screen/Isabelle2026'
PROFILE = {'schema_version': 1, 'profile': 'autocorres2-isabelle2026-x64-v1'}


def is_calg(checker):
    return getattr(checker, 'base_session', '') == CALG_BASE


def validate(checker):
    if is_calg(checker):
        descriptor = checker.semantic_workspace / 'native-runtime.json'
        if json.loads(descriptor.read_text()) != PROFILE:
            raise ValueError('unsupported Calg native runtime profile')
        if checker.architecture != 'X64' or not (checker.l4v_dir / 'afp/ROOTS').is_file():
            raise ValueError('Calg requires X64 and the frozen AFP catalog')


def uses_sel4(checker):
    return getattr(checker, 'base_session', 'Semantic_Base') not in {
        'Intervaltree_Project_Base', CALG_BASE}


def process_tool(checker):
    return 'process'


def theory_argument(checker, theory):
    return ['-T', theory]


def worker_source(checker, text):
    if 'Regen_Reuse_Diagnostics.snapshot' in text:
        text = Path(__file__).with_name('native_reuse_diagnostics.ML').read_text() + '\n' + text
    if not is_calg(checker):
        return text
    # The removed boolean argument used to control interactive proof commands.
    # Preserve that meaning for the trusted, disposable helper admission only;
    # ordinary commands and the sketch consumer keep strict noninteractive mode.
    text = text.replace('Toplevel.command_exception allow_admission tr st',
                        'Interactive.setmp allow_admission (Toplevel.command_exception tr) st')
    text = text.replace('Toplevel.command_exception true', 'Toplevel.command_exception')
    text = text.replace('OS.Process.sleep', 'Time.sleep')
    text = text.replace('YXML.content_of', '(XML.content_of o YXML.parse_body)')
    text = text.replace('Outer_Syntax.parse_text theory (fn () => theory)',
                        'Outer_Syntax.parse_text theory (fn () => {skip_theory = false, theory = theory})')
    return re.sub(r'Toplevel.make_state \(SOME (\w+)\)',
                  r'Toplevel.make_state (SOME (Context.Theory \1))', text)


def catalogs(checker):
    if is_calg(checker):
        return [AFP_CACHE]
    from regen.preparation.semantic_base_builder import SESSION_CATALOGS
    return ['/sel4-project/verification/l4v/' + p for p in SESSION_CATALOGS]


def configure_command(checker, command):
    if not is_calg(checker):
        return command
    # Parent heaps record these source names. Expose only the two immutable
    # dependency trees, never the original screening project or its proofs.
    at = command.index('-e')
    command[at:at] = [
        '--mount', f'type=bind,src={checker.isabelle_dir},dst={ISABELLE_CACHE},readonly',
        '--mount', f'type=bind,src={checker.l4v_dir / "afp"},dst={AFP_CACHE},readonly']
    executable = '/workspace/verification/isabelle/bin/isabelle'
    at = command.index(executable) + 1
    # Isabelle2026 changed the default user heap directory. Set after settings,
    # just as the checker already does for ML/Java resource bounds.
    runner = ['python3', '/semantic-base/native_process.py'] if 'process' in command else [executable]
    command[at:at] = ['env', 'ISABELLE_HEAPS=/root/.isabelle/heaps', *runner]
    return command
