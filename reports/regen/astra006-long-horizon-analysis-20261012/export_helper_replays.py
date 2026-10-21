"""Publish selected public proof interactions, never reasoning/internal items."""
import difflib
import hashlib
import html
import json
from pathlib import Path
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from regen.infrastructure.incremental_log import read_artifact

RUN = ROOT / 'reports/regen/refine-astra-medium-20261005-006/proof'
SESSIONS = RUN / 'parallel-sessions/60ea84ad16ab439ba102b3a49fc88a8a'
DIST = HERE / 'public-site/dist'
SITE = 'https://astra006-helper-case-studies.blush-duck-9562.chatgpt.site'
CASES = [
    dict(slug='tcb-slots', title='TCB slot helper 从宽泛展开到被父 helper 使用',
         theory='Scheduler_Dispatch', target='schedule_corres',
         spans=[('Scheduler_Dispatch/session-transcript.jsonl', 5721, 6923)],
         names=['dispatch_sched_tcb_slots','dispatch_sched_tcb_valid','dispatch_sched_ctes',
                'dispatch_tcb_layout_update','dispatch_sched_load_cte','dispatch_cte_wp_load','dispatch_lookup_replace'],
         requests=list(range(77,86)),
         explanation='模型先直接展开 valid_tcb 和 slot 分类；请求 77 剩余 31 个目标后，提取 slot 选择器保持性。随后反复修复 next 关键字、位置参数与类型问题，最后使用 fastforce 和 prod.splits 处理同一个 slot 上的事实组合。',
         outcome='slot helper 被 TCB-validity 和 capability-map 的候选证明显式使用；请求 82 前两者中的 TCB-validity 已通过命令，请求 85 对六个 helper 正式 GREEN。schedule_corres 本体仍未闭合。'),
    dict(slug='device-reset', title='Device reset helpers 从分支设计到三次检查',
         theory='Untyped_Object_Lifecycle', target='resetUntypedCap_corres',
         spans=[('Untyped_Object_Lifecycle/retry-001/session-transcript.jsonl',1049,1722)],
         names=['ul_set_freeindex_corres','ul_reset_device_A','ul_reset_device_C'],requests=[74,75,76],
         explanation='模型读取 reset 两侧定义及 updateFreeIndex_corres，先专门化 free-index 更新，再分别把抽象和具体 device 分支改写成删除区域后清零索引，意图降低后续组合证明的复杂度。',
         outcome='请求 74 的单子目标是 monad/lift 归一化，75 剩余两个集合成员 witness 目标，76 三个 helper 正式 GREEN。日志没有目标 contract 的后续闭合，也不能将模型计划组合等同于实际已经使用。'),
    dict(slug='machine-operation', title='Machine operation helper 从通用设计到显式 witness',
         theory='Untyped_Object_Lifecycle', target='resetUntypedCap_corres',
         spans=[('Untyped_Object_Lifecycle/session-transcript.jsonl',5242,1000000),
                ('Untyped_Object_Lifecycle/retry-001/session-transcript.jsonl',1,1049)],
         names=['ul_machine_op_corres','ul_clear_memory_corres','ul_untyped_reads','ul_reset_zero'],
         requests=list(range(67,74)),
         explanation='模型为 reset 的不同分支抽出机器操作、清零、cap 读取和零索引事实。通用 machine-operation helper 用 no_fail 约束任意机器操作；自动 witness 搜索超时后改成显式选取相同结果，再修复变量作用域与同批 helper 语法。',
         outcome='请求 73 四个 helper 正式 GREEN。ul_clear_memory_corres 的实际 proof body 引用了 ul_machine_op_corres；ul_reset_zero 引用了 ul_untyped_reads。这里有 helper 间的真实使用，但不是 reset contract 完成。'),
    dict(slug='reset-arithmetic', title='循环算术 helpers 的设计与未完成检查',
         theory='Untyped_Object_Lifecycle', target='resetUntypedCap_corres',
         spans=[('Untyped_Object_Lifecycle/retry-001/session-transcript.jsonl',1847,1940)],
         names=['ul_filter_upt_less','ul_reset_indices','ul_step_enum_nat'],requests=[77],
         explanation='模型检索枚举和地址转换定义后，先把过滤后的自然数索引改成区间，再把机器字地址枚举改成同一索引区间的 map。三个完整 proof body 和一次 power_add 修改都可见。',
         outcome='随后 check_theory 提交 source_sha256="INVALID"，被拒绝；这里没有原生检查反馈或接受结果。不能补写成已经验证或已经被 contract 使用。')]

def blocks(source):
    found={}
    for m in re.finditer(r'\(\* REGEN-BEGIN\s+(\{.*?\})\s*\*\)(.*?)\(\* REGEN-END\s*\*\)', source, re.S):
        meta=json.loads(m[1]); found[meta['id']]=m[0]
    return found

def clean(value):
    if isinstance(value,str):
        return re.sub(r'/home/[^\s"\)]+', '[local-path]', value)
    if isinstance(value,list):return [clean(x) for x in value]
    if isinstance(value,dict):
        return {k:clean(v) for k,v in value.items() if k not in {
            'raw_json','reasoning','encrypted_content','auth','authorization','token','access_token',
            'audit_content','log_content','source_content','transcript_prefix','command',
            'old_binding','new_binding','binding','isolation','environment'}}
    return value

def response_view(obj, focus):
    if isinstance(obj,list):return [response_view(x,focus) for x in obj]
    if not isinstance(obj,dict):return clean(obj)
    result={}
    for k,v in clean(obj).items():
        if k in {'source','document_source'} and isinstance(v,str) and 'REGEN-BEGIN' in v:
            result[k]={i:b for i,b in blocks(v).items() if i in focus}
        elif k=='proofs' and isinstance(v,dict):result[k]={i:p for i,p in v.items() if i in focus}
        else:result[k]=response_view(v,focus)
    return result

cache={}; pools={}; checks={}; targets={}
for theory in {c['theory'] for c in CASES}:
    pool={}; batches={}
    for p in sorted((RUN/'theories'/theory/'batch-requests').glob('*/proposal.json')):
        n=int(p.parent.name); d=read_artifact(p); src=d.get('document_source','')
        if src:pool[hashlib.sha256(src.encode()).hexdigest()]=src
        cp=p.with_name('check.json')
        if cp.exists():batches[n]=(d,read_artifact(cp))
    pools[theory]=pool; checks[theory]=batches
    ps=sorted((RUN/'theories'/theory/'acceptance/commits').glob('*.json'))
    final=read_artifact(ps[-1])['state']
    targets[theory]={n['name']:n['statement'] for n in final['graph']['nodes'] if n['role']=='contract'}

style=re.search(r'<style>(.*?)</style>',(DIST/'index.html').read_text(),re.S)[1]
style+='\n.event{padding:20px 0;border-top:1px solid #dce3e9;scroll-margin-top:12px}.meta{color:#526174;font-size:13px}details{margin:12px 0;padding:10px 14px;background:#f5f7fa;border-radius:6px}summary{cursor:pointer;font-weight:600}pre{max-height:65vh;white-space:pre-wrap;overflow-wrap:anywhere}.kind{color:#12635c;font-weight:700}.badge{font-size:13px;background:#edf3f2;padding:4px 8px}.toolbar{display:flex;gap:12px;flex-wrap:wrap}button{font:inherit;padding:7px 12px;border:1px solid #ccd8d6;border-radius:6px;background:white;cursor:pointer}.diff{background:#eef6f0}li{margin:6px 0}'
index=[]
for case in CASES:
    focus={'helper:'+x for x in case['names']}
    events=[]; latest={}; receipts={}; origins=[]; missing_bases=[]
    for rel,lo,hi in case['spans']:
        path=SESSIONS/rel
        if rel not in cache:
            cache[rel]=[json.loads(l) for l in path.open()]
        raw=cache[rel]; origins.append({'source':rel,'first_line':lo,'last_line':min(hi,len(raw)),
            'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        for ln,e in enumerate(raw,1):
            m=e['message'];m=m.get('parsed',m);method=m.get('method');it=m.get('params',{}).get('item',{})
            within=lo<=ln<=hi
            stamp=datetime.fromtimestamp(e['time_unix'],ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')
            base={'source':rel,'line':ln,'time':stamp,'anchor':('retry-' if 'retry-' in rel else 'main-')+str(ln)}
            if method=='item/completed' and it.get('type')=='agentMessage' and within:
                events.append({**base,'kind':'模型公开说明','text':it.get('text','')})
            if it.get('type')!='dynamicToolCall':continue
            args=it.get('arguments',{});args=json.loads(args) if isinstance(args,str) else args
            prop=args.get('proposal',{}) if isinstance(args,dict) else {};action=prop.get('action',it.get('tool',''))
            if method=='item/started':
                if action=='edit_theory':
                    src=prop.get('source'); pool=pools[case['theory']]
                    if src is None:
                        src=pool.get(prop.get('base_sha256'))
                        if src is not None:
                            for patch in prop.get('edits',[]):
                                old=patch.get('old','');new=patch.get('new','')
                                if not old or src.count(old)!=1:
                                    src=None;break
                                src=src.replace(old,new,1)
                    changed=[]
                    if src is not None:
                        pool[hashlib.sha256(src.encode()).hexdigest()]=src
                        for identity,body in blocks(src).items():
                            if identity in focus and body!=latest.get(identity):
                                previous=latest.get(identity,'')
                                changed.append({'id':identity,'source':body,
                                    'diff':''.join(difflib.unified_diff(previous.splitlines(True),body.splitlines(True),fromfile='previous',tofile='submitted'))})
                                latest[identity]=body
                    elif within:
                        missing_bases.append(base['anchor'])
                    if within:
                        events.append({**base,'kind':'模型提交编辑','tool':it.get('tool'),'action':action,
                            'changed_helpers':changed,'patches':clean(prop.get('edits',[])),
                            'note':'只摘出本案例相关条目的完整版本与差异；未重复整份 theory 的无关前缀。',
                            'reconstruction': 'exact unique replacements' if src is not None else 'raw patches only'})
                elif within:
                    events.append({**base,'kind':'模型工具请求','tool':it.get('tool'),'request':clean(args)})
                if within:receipts[it['id']]=base['anchor']
            elif method=='item/completed' and within:
                content=[]
                for x in it.get('contentItems') or []:
                    if 'text' not in x:continue
                    try: obj=json.loads(x['text'])
                    except ValueError: obj=x['text']
                    content.append(response_view(obj,focus))
                events.append({**base,'kind':'工具返回给模型','tool':it.get('tool'),
                    'request_anchor':receipts.get(it['id']),'success':it.get('success'),
                    'content':content,'note':'保留公开工具反馈与检索结果；移除环境绑定、整份原生审计/日志副本和本地绝对路径。'})
    audited=[]
    for n in case['requests']:
        if n not in checks[case['theory']]:continue
        proposal,check=checks[case['theory']][n];c=check.get('check',{})
        bs=blocks(proposal.get('document_source',''))
        audited.append({'request':n,'reason':proposal.get('reason'),
            'helpers':{i:b for i,b in bs.items() if i in focus},
            'verdict':c.get('verdict'),'diagnostic':clean(c.get('diagnostic','')),
            'node_results':check.get('node_results',[]),
            'candidate_wall_ms':c.get('wall_ms'), 'prefix_prepare_ms':c.get('prefix_prepare_ms')})
    payload={'title':case['title'],'scope':origins,'target':targets[case['theory']][case['target']],
        'explanation':case['explanation'],'outcome':case['outcome'],
        'events':events,'batch_evidence':audited,'unreconstructed_edit_anchors':missing_bases,
        'limits':'公开事件回放，不含内部推理、系统提示或其他无关 theory 正文。编辑提交不等于已保存，命令完成不等于正式接受；以工具返回和批次证据交叉核对。'}
    assert not missing_bases,missing_bases
    js=json.dumps(payload,ensure_ascii=False,indent=2)
    assert '/home/' not in js
    for forbidden in ['"encrypted_content"','"type": "reasoning"','"raw_json"','auth.json']:
        assert forbidden not in js,forbidden
    (DIST/(case['slug']+'.json')).write_text(js+'\n')
    def pre(text,cls=''):
        return '<pre class="'+cls+'"><code>'+html.escape(text)+'</code></pre>'
    def details(title,body,opened=False):
        return '<details'+(' open' if opened else '')+'><summary>'+html.escape(title)+'</summary>'+body+'</details>'
    parts=[]
    for ev in events:
        anchor=ev['anchor'];content=''
        if ev['kind']=='模型公开说明':
            content='<blockquote>'+html.escape(ev['text']).replace('\n','<br>')+'</blockquote>'
            waiting=bool(re.search(r'still|pending|waiting|no new|no result|not returned|remains in progress',ev['text'],re.I))
            if waiting:content=details('等待消息 原文保留',content)
        elif ev['kind']=='模型提交编辑':
            for row in ev['changed_helpers']:
                content+=details(row['id']+' 完整陈述与 proof body',pre(row['source']),True)
                content+=details('相对上一版的逐行修改',pre(row['diff'],'diff'))
            if ev['patches']:content+=details('原始 old/new 编辑请求',pre(json.dumps(ev['patches'],ensure_ascii=False,indent=2)))
            if not ev['changed_helpers']:content+='<p>本次未改变所选 helper 的完整条目；相关补丁或其他同批次条目变更见原始编辑请求。</p>'
        elif ev['kind']=='模型工具请求':content=details('完整请求 '+str(ev['tool']),pre(json.dumps(ev['request'],ensure_ascii=False,indent=2)))
        else:
            if ev['request_anchor']:content+='<p><a href="#'+ev['request_anchor']+'">对应工具请求</a></p>'
            for returned in ev['content']:
                if isinstance(returned,dict):
                    feedback=returned.get('feedback',{})
                    check=feedback.get('check',{}) if isinstance(feedback,dict) else {}
                    if isinstance(check,dict) and check.get('diagnostic'):
                        diagnostic=check['diagnostic']
                        if '\n' not in diagnostic:diagnostic=diagnostic.replace('\\n','\n')
                        content+=details('实际反馈中的诊断 可读视图',pre(diagnostic))
            content+=details('模型实际收到的返回内容',pre(json.dumps(ev['content'],ensure_ascii=False,indent=2)))
        parts.append('<article class="event" id="'+anchor+'"><div class="kind">'+ev['kind']+' <a href="#'+anchor+'">#</a></div><p class="meta">'+html.escape(ev['time']+' · '+ev['source']+' · 行 '+str(ev['line']))+'</p>'+content+'</article>')
    batches=[]
    for b in audited:
        content='<p>'+html.escape(str(b['reason']))+'</p>'+pre(json.dumps(b['node_results'],ensure_ascii=False,indent=2))
        content+=details('完整原生诊断 不截断',pre(b['diagnostic']))
        for identity,body in b['helpers'].items():content+=details(identity+' 本批次提交版本',pre(body))
        batches.append('<section id="batch-'+str(b['request'])+'"><h3>请求 '+str(b['request'])+' · '+html.escape(str(b['verdict']))+'</h3>'+content+'</section>')
    doc='<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>'+html.escape(case['title'])+'</title><style>'+style+'</style></head><body><header>ASTRA 006 · HELPER 对话与证明回放</header><main><a href="/">返回案例报告</a><h1>'+html.escape(case['title'])+'</h1><p>'+html.escape(payload['limits'])+'</p><p class="notice">时区 Asia/Shanghai。以下导读是依据记录的分析，不冒充模型原话。每条记录可通过 # 分享；等待、工具返回、旧新版本和完整诊断均可展开。</p><h2>读这段过程要看什么</h2><p>'+html.escape(case['explanation'])+'</p><p><strong>实际结果：</strong>'+html.escape(case['outcome'])+'</p>'+details('原 contract 目标陈述',pre(payload['target']))+'<div class="toolbar"><a href="#timeline">连续对话</a><a href="#batch-evidence">逐批次 proof 与诊断</a><a href="'+case['slug']+'.json">下载公开回放 JSON</a><button onclick="document.querySelectorAll(\'details\').forEach(x=>x.open=true)">展开全部</button><button onclick="document.querySelectorAll(\'details\').forEach(x=>x.open=false)">折叠全部</button></div><h2 id="timeline">连续公开事件</h2>'+''.join(parts)+'<h2 id="batch-evidence">逐批次完整 proof 与原生诊断</h2><p>这是检查产物的补充视图，不代表每个字段都以相同形式被模型收到；模型实际收到什么，以前面的工具返回事件为准。</p>'+''.join(batches)+'</main></body></html>'
    ids=set(re.findall(r'\bid="([^"]+)"',doc))
    assert len(ids)==len(re.findall(r'\bid="([^"]+)"',doc))
    for a in re.findall(r'href="#([^"]+)"',doc):assert a in ids,a
    (DIST/(case['slug']+'.html')).write_text(doc)
    index.append({'slug':case['slug'],'url':SITE+'/'+case['slug']+'.html','title':case['title'],
        'events':len(events),'edit_events':sum(e['kind']=='模型提交编辑' for e in events),
        'helper_versions':sum(len(e.get('changed_helpers',[])) for e in events),
        'batches':[b['request'] for b in audited],'origins':origins,
        'html_sha256':hashlib.sha256(doc.encode()).hexdigest()})
    print(json.dumps(index[-1],ensure_ascii=False),flush=True)
(HERE/'helper-replays.json').write_text(json.dumps(index,ensure_ascii=False,indent=2)+'\n')
