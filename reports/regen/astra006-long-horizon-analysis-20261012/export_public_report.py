"""Export only approved report text, selected public messages and derived CSV."""
import csv
import hashlib
import html
import io
import json
from pathlib import Path
import re
from markdown_it import MarkdownIt

HERE = Path(__file__).resolve().parent
DEST = HERE / 'public-site/dist'
source = (HERE / 'HELPER-CASE-STUDIES.md').read_text()
refs = dict(re.findall(r'^\[([^\]]+)\]: (.+)$', source, re.M))
quotes = []
for key, target in refs.items():
    name, lineno = target.rsplit(':', 1)
    record = json.loads((HERE / name).read_text().splitlines()[int(lineno)-1])
    item = record['message']['parsed']['params']['item']
    assert item['type'] == 'agentMessage'
    text = item['text']
    assert all(part in source for part in text.splitlines() if part)
    theory = 'Scheduler_Dispatch' if 'Scheduler_Dispatch' in target else 'Untyped_Object_Lifecycle retry-001'
    quotes.append(f'<section id="dialogue-{key}" class="dialogue"><h3>{html.escape(theory)} · 原始行 {lineno}</h3><blockquote>{html.escape(text).replace(chr(10), "<br>")}</blockquote></section>')

# Public text keeps provenance labels, but no nonfunctional local-file links.
public = re.sub(r'^\[[^\]]+\]: .+$', '', source, flags=re.M)
public = re.sub(r'\[([^\]]+)\]\[([^\]]+)\]', lambda m:f'[{m[1]}](#dialogue-{m[2]})', public)
def local_link(m):
    label, target = m.groups()
    if target.startswith('#'):
        return m[0]
    if target == 'helper-costs.csv':
        return f'[{label}](helper-costs.csv)'
    if target.startswith('https://'):
        return m[0]
    return label + '（本地来源索引）'
public = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', local_link, public)
public = public.replace('下方链接保留原始文件与物理行号；Markdown 阅读器不支持行号跳转时，可按标注行号在 IDE 中打开。',
                        '公开版对话链接跳转到本页摘录，保留 theory 与原始行号；完整原始日志不公开。')
md = MarkdownIt('commonmark', {'html': False}).enable('table')
body = md.render(public)
toc=[]
def heading(m):
    anchor='section-'+str(len(toc)+1)
    toc.append((anchor,m[1]))
    return f'<h2 id="{anchor}">{m[1]}</h2>'
body=re.sub(r'<h2>(.*?)</h2>',heading,body)
body=body.replace('<table>','<div class="table-wrap"><table>').replace('</table>','</table></div>')
nav=''.join(f'<a href="#{a}">{title}</a>' for a,title in toc)
page='''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><meta name="description" content="Astra 006 证明实验的 helper 数量、引用情况、时间与 token 成本，以及原始模型对话摘录。">
<title>Astra 006 helper 成本与证明案例</title>
<style>
:root{color-scheme:light;--ink:#202b3a;--muted:#526174;--line:#dce3e9;--accent:#12635c}
*{box-sizing:border-box}body{margin:0;background:#f6f7f9;color:var(--ink);font:16px/1.8 system-ui,-apple-system,"Noto Sans SC",sans-serif}
header{background:#163b38;color:white;padding:28px max(24px,calc((100vw - 1100px)/2));font-size:14px;letter-spacing:.05em}
main{max-width:1100px;margin:0 auto;padding:36px 28px 80px;background:white}h1{font-size:clamp(26px,4vw,38px);line-height:1.35;margin:0 0 24px}h2{font-size:25px;border-top:1px solid var(--line);padding-top:32px;margin-top:46px;scroll-margin-top:16px}h3{font-size:19px;margin-top:28px}
a{color:var(--accent);text-underline-offset:3px;overflow-wrap:anywhere}p,li{overflow-wrap:anywhere}blockquote{margin:20px 0;border-left:3px solid var(--accent);background:#f0f7f5;padding:14px 22px;color:#294b47}blockquote p{margin:0}code{background:#f0f2f5;padding:2px 4px;border-radius:3px;font-size:.9em;overflow-wrap:anywhere}pre{padding:18px;background:#f0f2f5;overflow:auto}pre code{padding:0;white-space:pre}
.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:7px;margin:20px 0}table{border-collapse:collapse;width:100%;font-size:14px;line-height:1.6}th,td{padding:11px 14px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{background:#edf3f2;white-space:nowrap}tr:last-child td{border-bottom:0}td code{white-space:nowrap}nav{display:grid;gap:5px;background:#f5f7fa;padding:18px 22px;border-radius:8px;margin:24px 0}nav a{font-size:14px}.notice{color:var(--muted);font-size:14px}.dialogue{scroll-margin-top:20px}footer{margin-top:48px;border-top:1px solid var(--line);padding-top:20px;font-size:13px;color:var(--muted)}
@media(max-width:600px){main{padding:24px 16px}header{padding:18px 16px}h2{font-size:22px}blockquote{padding:12px 14px}th,td{padding:9px}}
@media print{body{background:white}main{max-width:none}nav{display:none}.table-wrap{overflow:visible}a{color:inherit}}
</style></head><body><header>PROOF GENERATION · ASTRA 006 · 案例与证据</header><main>
<p class="notice">公开分享版 · 无需登录 · 仅发布核对后的报告、衍生明细和选定模型消息。未上传完整会话、凭据或机器配置。未引用不等于已证实无用。</p>
<nav aria-label="报告目录">'''+nav+'</nav>'+body+'<h2 id="dialogues">可分享的原始对话摘录</h2>'+''.join(quotes)+'''
<footer>实验 refine-astra-medium-20261005-006。统计是历史快照分析，不是新实验结果。页面未使用跟踪脚本或第三方资源。noindex 是搜索引擎提示，不是访问控制；任何持链接者均可访问。</footer></main></body></html>'''
assert '/home/' not in page and 'raw_json' not in page
assert 'auth.json' not in page
DEST.mkdir(parents=True,exist_ok=True)
(DEST/'index.html').write_text(page)
# CSV contains only two-theory derived helper metadata, never raw event bodies.
rows=list(csv.DictReader((HERE/'helper-costs.csv').open()))
assert len(rows)==130
assert all(not row['llm_tokens'] for row in rows)
(DEST/'helper-costs.csv').write_text((HERE/'helper-costs.csv').read_text())
ids=set(re.findall(r'\bid="([^"]+)"',page))
for target in re.findall(r'href="#([^"]+)"',page):
    assert target in ids,target
assert len(quotes)==9
print(json.dumps({'html_bytes':len(page.encode()),'quote_count':len(quotes),'helper_rows':len(rows),
    'html_sha256':hashlib.sha256(page.encode()).hexdigest(),'broken_internal_links':0}))
