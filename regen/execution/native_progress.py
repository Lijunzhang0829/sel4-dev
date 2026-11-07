"""Small request-bound native progress, diagnostic only; never proof authority."""
import hashlib
import math
import xml.etree.ElementTree as ET

POLICY = 'native-stage-progress-v1'


def read_progress(root, stem, worker, token):
    path = root / (stem + '.progress.xml')
    if not path.exists():
        return None
    try:
        raw = path.read_bytes()
        if len(raw) > 65536:
            raise ValueError('native progress exceeds diagnostic size limit')
        node = ET.fromstring(raw)
        if (node.tag != 'native_progress' or node.get('worker') != worker
                or node.get('request') != stem or node.get('token') != token):
            raise ValueError('native progress request binding differs')
        timings = {}
        for item in node.findall('./timings/phase'):
            seconds = float(item.get('seconds').replace('~', '-'))
            if not math.isfinite(seconds) or seconds < 0:
                raise ValueError('invalid native phase duration')
            timings[item.get('name')] = seconds
        return dict(policy=POLICY, diagnostic_only=True, worker=worker,
            request=stem, token=token, stage=node.get('stage'),
            command=node.get('command'), line=node.get('line'),
            last_completed_command=node.get('last_completed_command'),
            last_completed_line=node.get('last_completed_line'), phase_seconds=timings,
            source=str(path), sha256=hashlib.sha256(raw).hexdigest())
    except (OSError, ValueError, TypeError, ET.ParseError) as error:
        return dict(policy=POLICY, diagnostic_only=True, available=False,
                    error=str(error), source=str(path))
