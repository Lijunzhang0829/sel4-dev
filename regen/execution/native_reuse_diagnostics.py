"""Read native, context-scoped explicit-rule observations without changing verdicts."""
from pathlib import Path
import xml.etree.ElementTree as ET


SCHEMA = 'explicit-rule-observation-v1'


def _payload(element):
    return {'proposition': element.findtext('proposition', ''),
            'type_variables': [dict(node.attrib, sorts=[value.text or '' for value in node.findall('class')])
                               for node in element.findall('./type_variables/type_variable')],
            'variables': [dict(node.attrib, type=node.findtext('type', ''))
                          for node in element.findall('./variables/variable')]}


def read_observation(path):
    """Missing/invalid optional diagnostics never reject an existing native result."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        root = ET.fromstring(path.read_text())
        if root.tag != 'reuse_diagnostics' or root.get('schema') != SCHEMA:
            raise ValueError('unexpected reuse-diagnostic schema')
        result = dict(root.attrib)
        if root.find('reason') is not None:
            result['reason'] = root.findtext('reason', '')
        if root.find('reference') is not None:
            result['reference'] = root.findtext('reference', '')
        if root.find('goal') is not None:
            result['goal'] = _payload(root.find('goal'))
        for key in ('facts', 'chained_facts'):
            if root.find(key) is not None:
                result[key] = [_payload(item) for item in root.findall('./' + key + '/fact')]
        match = root.find('matching')
        if match is not None:
            result['matching'] = dict(match.attrib)
            if match.find('remaining_subgoals') is not None:
                result['matching']['remaining_subgoals'] = int(match.findtext('remaining_subgoals'))
        return result
    except (OSError, ValueError, ET.ParseError) as error:
        return {'schema': SCHEMA, 'status': 'unavailable',
                'semantic_scope': 'current_goal_and_context_only',
                'reason': 'Optional native reuse diagnostic could not be read: ' + str(error)}


def attach_observation(state, path):
    observation = read_observation(path)
    if observation is not None:
        # Keep even if the ordinary proof-state file was unavailable. This is
        # descriptive metadata, never a substitute for a proof-state certificate.
        state = dict(state or {'origin': 'before_failed_command'})
        state['reuse_diagnostics'] = observation
    return state
