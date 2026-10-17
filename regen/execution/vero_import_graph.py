"""Recover VERO's validated additive generated imports from theory snapshots.

The accepted plan fixes the base edges. VERO's imports slots may add edges
within that fixed theory inventory, so sealing must use the effective graph.
"""
import re
from collections import Counter


def _without_comments(source):
    result=[];depth=0;position=0
    while position<len(source):
        pair=source[position:position+2]
        if pair=='(*':
            depth+=1;position+=2
        elif pair=='*)':
            if depth==0:raise ValueError('unmatched VERO imports comment close')
            depth-=1;position+=2
        else:
            if depth==0:result.append(source[position])
            position+=1
    if depth:raise ValueError('unclosed VERO imports comment')
    return ''.join(result)


def effective_generated_imports(snapshot, frozen_imports, *, require_rendered=False):
    """Return base plus additive edges; never accept a new theory or base-edge removal.

    ``require_rendered`` is for the checker-side schema-v2 source, which must
    contain VERO's generated slot and matching theory header. The original
    agent workspace is only inspected to revoke missing sources before VERO
    extracts/renders it, so its missing markers remain extraction feedback.
    """
    graph={name:tuple(providers) for name,providers in frozen_imports.items()}
    for theory,base in graph.copy().items():
        raw=snapshot.get(theory+'.thy')
        if raw is None:continue
        source=raw.decode('utf-8')
        start=f'(* VERO START {theory} imports *)'
        end=f'(* VERO END {theory} imports *)'
        starts=source.count(start);ends=source.count(end)
        if starts!=ends or starts>1 or (require_rendered and starts!=1):
            raise ValueError('invalid VERO imports slot: '+theory)
        added=[]
        if starts:
            first=source.index(start)+len(start)
            last=source.index(end)
            if last<first:raise ValueError('reversed VERO imports slot: '+theory)
            added=_without_comments(source[first:last]).split()
            if (len(added)!=len(set(added)) or any(
                    not re.fullmatch(r'Vero_[A-Za-z0-9_]+',name)
                    or name not in graph or name==theory for name in added)):
                raise ValueError('invalid additive VERO imports: '+theory)
        effective=tuple(dict.fromkeys([*base,*added]))
        graph[theory]=effective
        if require_rendered:
            header_end=source.find('\nbegin\n',source.index(end)+len(end))
            if header_end<0:raise ValueError('missing VERO theory begin: '+theory)
            header=_without_comments(source[:header_end])
            match=re.fullmatch(r'\s*theory\s+'+re.escape(theory)+
                r'\s+imports\s+(.*?)\s*',header,re.S)
            # The VERO renderer writes the slot verbatim, while context()
            # deduplicates base-plus-added edges for scheduling. Compare the
            # literal header, including an extra already present in the base.
            if (not match or Counter(match.group(1).split())
                    != Counter(['Vero_Support',*base,*added])):
                raise ValueError('VERO rendered imports disagree with effective DAG: '+theory)
    # The normal VERO service calls slots.context() before the adapter, but
    # direct checker clients must not be able to retain old authority after
    # submitting a syntactically valid cyclic additive import graph.
    visited=set();active=set()
    def visit(theory):
        if theory in active:
            raise ValueError('generated imports contain a cycle')
        if theory in visited:
            return
        active.add(theory)
        for imported in graph[theory]:
            if imported not in graph:
                raise ValueError('generated imports reference unknown theory')
            visit(imported)
        active.remove(theory)
        visited.add(theory)
    for theory in graph:
        visit(theory)
    return graph


def conservative_missing_imports(frozen_imports):
    """If an agent workspace slot is malformed, missing-source revocation errs wide."""
    names=tuple(frozen_imports)
    return {name:tuple(other for other in names if other!=name) for name in names}


def attributable_import_failure(snapshot, frozen_imports, *, require_rendered=False):
    """Scope a malformed source only when every other raw import slot parses.

    A multi-source cycle or an error not attributable to individual sources
    remains unknown and must use the caller's global fail-closed policy.
    """
    invalid=[]
    for theory in sorted(frozen_imports):
        name=theory+'.thy'
        if name not in snapshot:continue
        try:effective_generated_imports({name:snapshot[name]},frozen_imports,
            require_rendered=require_rendered)
        except (ValueError,UnicodeError):invalid.append(theory)
    if not invalid:return [],None
    valid={name:data for name,data in snapshot.items()
        if name not in {theory+'.thy' for theory in invalid}}
    try:graph=effective_generated_imports(valid,frozen_imports)
    except (ValueError,UnicodeError):return [],None
    return invalid,graph
