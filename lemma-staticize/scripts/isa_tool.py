#!/usr/bin/env python3
"""Thin CLI an agent (claude -p) calls to rewrite a lemma's automation line via the
long-lived poc_repl_server.py (file bridge). No py4j here — just writes a request and
reads the response, so it's safe to call repeatedly from claude -p's Bash tool.

  python3 isa_tool.py state          # the goal A, target B, and available facts
  python3 isa_tool.py try  '<tac>'   # run <tac> on a CLONE of A; reports reached_B / audit_static / resulting goal
  python3 isa_tool.py commit '<tac>' # like try, but ADVANCES the base state (build a multi-step path)
  python3 isa_tool.py stop           # shut the server down

A successful rewrite = a tactic (or commit-sequence) with reached_B=true. For a STATIC
rewrite also require audit_static=true (no auto/blast/fastforce/force/clarsimp...).
"""
import os, sys, json, time

BRIDGE=os.environ.get("BRIDGE","/workspace/tools/seL4-proof-search/Isa-Repl/poc-bridge")

def main():
    if len(sys.argv)<2:
        print("usage: isa_tool.py {state|try <tac>|commit <tac>|stop}"); sys.exit(2)
    cmd=sys.argv[1]; tac=sys.argv[2] if len(sys.argv)>2 else ""
    req={"cmd":cmd,"tactic":tac}
    rp=os.path.join(BRIDGE,"poc-resp.json"); qp=os.path.join(BRIDGE,"poc-req.json")
    if os.path.exists(rp):
        try: os.remove(rp)
        except OSError: pass
    json.dump(req, open(qp+".t","w")); os.replace(qp+".t",qp)
    t0=time.monotonic()
    while time.monotonic()-t0 < 60:
        if os.path.exists(rp):
            try: d=json.load(open(rp))
            except Exception: time.sleep(0.1); continue
            print(json.dumps(d, ensure_ascii=False, indent=1)); return
        time.sleep(0.1)
    print(json.dumps({"ok":False,"error":"server timeout (is poc_repl_server.py running?)"})); sys.exit(1)

if __name__=="__main__": main()
