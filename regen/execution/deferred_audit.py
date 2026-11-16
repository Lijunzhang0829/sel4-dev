"""Declaration identity capture with deferred dependency audit.

Absent policy preserves historical bytes. Never silently upgrade frozen evidence.
"""
import json
LEGACY = 'declaration-export-audit-v1'
DEFERRED = 'declaration-export-deferred-v2'
def policy(binding):
    value=binding.get('audit_policy',LEGACY)
    if value not in (LEGACY,DEFERRED):raise ValueError('unsupported audit_policy: '+repr(value))
    return value

def defer_block(block,node_id):
    prefix='ML \\<open>\nlocal\n';suffix='\nend\n\\<close>'
    if not block.startswith(prefix) or not block.endswith(suffix):raise ValueError('unknown audit block')
    resolution,rest=block[len(prefix):-len(suffix)].split('  val hex_digits = ',1)
    declarations,body=rest.split('\nin\n',1)
    body=body.replace('  val _ =','').replace('TextIO.closeOut stream;','TextIO.closeOut stream')
    label=json.dumps('Deferred audit '+node_id+': ')
    return (prefix+resolution+'\nin\n  val _ = regen_deferred_audits := (fn () => ((let\n  val hex_digits = '+declarations+'\nin\n'+body+'\nend) handle ERROR message => error ('+label+' ^ message))) :: !regen_deferred_audits;'+suffix)

def wrap_queue(source):
    # Initialize at theory scope, outside any reused locale group.
    at=source.index("\nbegin\n")+len("\nbegin\n")
    source=source[:at]+'ML \\<open>val regen_deferred_audits = Unsynchronized.ref ([] : (unit -> unit) list);\\<close>\n'+source[at:]
    at=source.rfind('\nend\n')
    if at<0:raise ValueError('missing theory end')
    return source[:at]+'\nML \\<open>List.app (fn f => f ()) (rev (!regen_deferred_audits)); regen_deferred_audits := []; writeln "DEFERRED_AUDIT_COMPLETE";\\<close>\n'+source[at:]
