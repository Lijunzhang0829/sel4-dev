#!/usr/bin/env python3
"""spec_record_render.py — render a one-page `record.md` for ONE candidate.

The spec analog of lemma-staticize's per-lemma `record.md`: a reviewer should
be able to open ONE file and see the WHOLE story of a modification —

  1. the ORIGINAL lemma's source (untouched — additive discipline),
  2. the NEW lemma's source,
  3. WHY: detector signal + agent rationale + strengthening claim,
  4. the trial-and-repair chain (when the first attempt failed),
  5. the VERIFICATION: trial build, impact verdict, apply status.

It is a pure renderer: everything is read from artifacts the pipeline already
writes (proposal.json / hints.json / trial.log / measurement.json /
p_claim_check.json / repair-N.json / verdict.txt). No build, no LLM.

Called by strengthen.sh at the end of EVERY candidate (success or failure).
Standalone backfill:  spec_record_render.py <candidate_dir> --theory <thy>
                       [--hints <hints.json>] [--baseline-ms N]
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from spec_slot_hints import parse_lemmas  # noqa: E402


def jload(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def fread(p, default=""):
    try:
        return Path(p).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return default


def find_lemma_source(theory_text, name):
    """Return (decl_line, full source text incl. proof) of lemma `name`."""
    if not name:
        return None, None
    for lm in parse_lemmas(theory_text):
        if lm["name"] == name:
            src = "\n".join(
                [theory_text.splitlines()[lm["line"] - 1]]) if False else None
            # reconstruct: header line(s) + statement + proof, straight from
            # the file between decl_line and the end of the proof
            lines = theory_text.splitlines()
            start = lm["line"] - 1
            # proof_lines tells us how far the lemma extends
            n_stmt_and_proof = lm["proof_lines"]
            # find the end: search forward from start for the last proof line
            # (match by content of the final proof line)
            end = start
            if n_stmt_and_proof:
                last = n_stmt_and_proof[-1]
                for i in range(start, min(start + 200, len(lines))):
                    if lines[i] == last:
                        end = i
                        break
            else:
                end = start + 1
            return lm["line"], "\n".join(lines[start:end + 1])
    return None, None


def lemma_at_anchor(theory_text, anchor_line):
    """The lemma whose proof ENDS at/near anchor_line — the F-slot sibling
    reference (proposals without hint_lemma anchor next to their model)."""
    best = None
    for lm in parse_lemmas(theory_text):
        if lm["line"] <= anchor_line:
            best = lm
        else:
            break
    if best is None:
        return None, None
    return find_lemma_source(theory_text, best["name"])


def tail(text, n):
    return "\n".join(text.strip().splitlines()[-n:])


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cand_dir", help="NN-<slot>-<lemma>/ candidate directory")
    ap.add_argument("--theory", required=True, help="theory file (post-run is "
                    "fine — additive never edits the original lemma)")
    ap.add_argument("--hints", default=None, help="run-level hints.json")
    ap.add_argument("--baseline-ms", default=None)
    ap.add_argument("--ledger", default=None,
                    help="candidate ledger (default: <repo>/spec-strengthen/"
                         "candidates/candidate-ledger.jsonl). The LEDGER's "
                         "latest event is the authoritative final verdict — "
                         "trial.log may hold an EARLIER attempt's output "
                         "(e.g. a run killed mid-re-trial).")
    args = ap.parse_args()

    cd = Path(args.cand_dir)
    prop = jload(cd / "proposal.json") or {}
    prop0 = jload(cd / "proposal-0.json")          # pre-repair original
    gate = jload(cd / "delivery_gate.json") or {}
    meas = jload(cd / "measurement.json") or {}
    pclaim = jload(cd / "p_claim_check.json")
    verdict = fread(cd / "verdict.txt").strip() or "?"
    trial_log = fread(cd / "trial.log")
    theory_text = fread(args.theory)
    theory_rel = re.sub(r"^.*verification/l4v/", "", args.theory)

    name = prop.get("lemma_name", cd.name)
    slot = prop.get("slot", "?")
    hint_lemma = prop.get("hint_lemma")
    anchor = int(prop.get("anchor_line") or 0)

    # ---- ledger: the authoritative final state for this candidate ----------
    key = f"{slot}:{Path(theory_rel).stem}:{name}"
    ledger_path = args.ledger or str(
        Path(__file__).resolve().parents[1] / "candidates/candidate-ledger.jsonl")
    last_ev = None
    try:
        for line in open(ledger_path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("key") == key:
                last_ev = d
    except OSError:
        pass

    # which artifacts actually exist (failure dirs lack patch.diff/command.sh)
    has_patch_diff = (cd / "patch.diff").exists()
    has_command_sh = (cd / "command.sh").exists()

    # ---- the ORIGINAL lemma (or a context reference when no hint anchor) ---
    old_line, old_src = (find_lemma_source(theory_text, hint_lemma)
                         if hint_lemma else (None, None))
    old_role = "原 lemma（additive：保持原样，未被修改）"
    if old_src is None and anchor:
        old_line, old_src = lemma_at_anchor(theory_text, anchor)
        if slot == "F":
            old_role = ("参照 lemma（F-slot 无单一\"原 lemma\"——这是锚点处的"
                        "同族 sibling，新 lemma 模仿它的形态）")
        else:
            old_role = (f"锚点处的相邻 lemma（该 {slot}-slot 候选是 agent 自发"
                        f"提议、未锚定 detector hint，没有机械记录的\"原 "
                        f"lemma\"——它强化的对照对象见 §3 的 agent 论证；"
                        f"下面仅是插入位置的上下文）")

    # ---- detector hint for this lemma --------------------------------------
    hint = None
    hints = jload(args.hints) if args.hints else None
    if hints and hint_lemma:
        hint = next((h for h in hints if h.get("lemma") == hint_lemma), None)

    # ---- repair chain -------------------------------------------------------
    repairs = sorted(cd.glob("repair-[0-9]*.json"),
                     key=lambda p: int(re.search(r"repair-(\d+)", p.name).group(1)))
    trial_err = fread(cd / "trial-error.txt")

    # ---- walls --------------------------------------------------------------
    baseline_ms = args.baseline_ms or meas.get("baseline_wall_ms", "?")
    trial_ms = meas.get("trial_wall_ms")
    if trial_ms is None:
        m = re.search(r"OK \((\d+)ms\)", trial_log)
        trial_ms = m.group(1) if m else "?"
    delta = meas.get("delta_pct", "?")

    out = []
    A = out.append
    A(f"# record — `{name}`  ({slot}-slot, {verdict})\n")
    A("| 字段 | 值 |")
    A("|---|---|")
    A(f"| 文件 | `{theory_rel}` |")
    A(f"| Slot / delivery | {slot} / {prop.get('delivery','?')}"
      + (f" ({gate.get('resolved_substate')})" if gate.get('resolved_substate') else "") + " |")
    A(f"| 裁决 | **{verdict}** |")
    A(f"| 墙钟 | baseline={baseline_ms} ms · trial={trial_ms} ms · Δ {delta}% |")
    A(f"| 锚点 | 插入于 L{anchor} 之后 |")
    if hint_lemma:
        A(f"| 锚定的原 lemma | `{hint_lemma}`"
          + (f" (L{old_line})" if old_line else "") + " |")
    A("")

    A(f"## 1. {old_role}\n")
    if old_src:
        A("```isabelle")
        A(old_src)
        A("```")
    else:
        A("（未能定位——见 patch.diff 的上下文行）")
    A("")

    A("## 2. 新增 lemma\n")
    A("```isabelle")
    A(prop.get("new_lemma", "(missing)"))
    A("```")
    if has_patch_diff:
        A("（插入位置与最终 diff 以 `patch.diff` 为准；原 lemma 在 additive "
          "纪律下零改动）\n")
    else:
        A("（该候选未通过 trial，**不存在最终 diff**；上面是当时尝试插入的"
          "内容，原始补丁输入见 `range-patch.patch.txt`）\n")

    A("## 3. 为什么这么改\n")
    if hint:
        A(f"**检测器信号**（机械，`hints.json`）：kind=`{hint.get('kind')}`，"
          f"priority={hint.get('priority')}")
        A(f"> {hint.get('evidence','')}")
        if hint.get("premise"):
            A(f"> 目标前提：`{hint['premise']}`")
        if hint.get("q_strong_text"):
            A(f"> proof 中路过的更强 post：`{hint['q_strong_text']}`")
        A("")
    A(f"**agent 论证**：{prop.get('rationale','(无)')}\n")
    claim = prop.get("strengthening_claim", "(无)")
    if pclaim and pclaim.get("ok"):
        # the mechanically derived claim is authoritative — the agent's
        # free-text version has been seen with the entailment arrow reversed
        A(f"**强化关系**（机械导出 ✓，`p_claim_check.json`）：{pclaim['claim']}\n")
    elif pclaim:
        A(f"**强化关系**（机械验证未通过：{pclaim.get('reason')}；"
          f"以下为 agent 自述）：{claim}\n")
    else:
        A(f"**强化关系**（agent 自述）：{claim}\n")
    A(f"**delivery**：{prop.get('delivery','?')}"
      + (f"，目标：{prop.get('delivery_target')}" if prop.get('delivery_target') else "")
      + (f"；gate：{gate.get('reason','?')}" if gate else "") + "\n")

    A("## 4. 修改过程（试错链）\n")
    if repairs or prop0:
        if prop0:
            A(f"- **attempt 0**（初稿，anchor L{prop0.get('anchor_line')}）→ "
              f"trial 失败：")
            if trial_err:
                A("  ```")
                A("  " + tail(trial_err, 4).replace("\n", "\n  "))
                A("  ```")
        for i, rp in enumerate(repairs, 1):
            r = jload(rp)
            r0 = (r or [{}])[0] if isinstance(r, list) else (r or {})
            A(f"- **repair {i}**：agent 依据 prover 错误修正 → "
              f"anchor L{r0.get('anchor_line','?')}"
              + ("（与初稿不同）" if prop0 and r0.get('anchor_line') != prop0.get('anchor_line') else ""))
        A(f"- 最终结果：**{verdict}**\n")
    else:
        A("一次通过，无修复轮。\n")

    A("## 5. 验证\n")
    last = tail(trial_log, 3) if trial_log else "(无 trial.log)"
    A(f"**最后记录的 prover 输出**（`trial.log`——注意：失败候选若有多轮"
      f"尝试/被中断，这里可能是较早一轮的输出，最终裁决以下一行为准）：")
    A("```")
    A(last)
    A("```")
    # the FINAL verdict, authoritative: verdict.txt + the ledger's reason
    final = f"**最终裁决**：`{verdict}`"
    if last_ev and last_ev.get("reason"):
        final += f" — {last_ev['reason']}"
    elif last_ev:
        final += f"（ledger 终态事件：`{last_ev.get('event')}`）"
    A(final)
    if meas:
        A(f"**impact**：verdict=`{meas.get('impact_verdict','?')}`，"
          f"gate_pass={meas.get('gate_pass')}，"
          f"wall gate {'✓' if meas.get('wall_gate_pass') else '✗'}"
          f"（详见 `measurement.json`）")
    landed = ('已 apply 到源文件' if verdict == 'applied'
              else ('dry-run 通过，未写源文件' if verdict == 'trial_passed'
                    else '未落地（' + verdict + '）'))
    if has_command_sh:
        A(f"**落地**：{landed}；复现：`./command.sh`\n")
    else:
        A(f"**落地**：{landed}；该目录无 `command.sh`（仅成功候选生成）——"
          f"复现方式：用 `range-patch.patch.txt` 重跑 "
          f"`check-theory.sh <theory> <session> --patch <该文件>`\n")

    A("## 6. 跨批次追踪\n")
    # double-quote the grep pattern: lemma names may contain a prime (gts_wf')
    # which breaks single-quoted shell strings
    A(f'```\ngrep "{key}" spec-strengthen/candidates/candidate-ledger.jsonl\n```')
    # the lifecycle note must match the candidate's ACTUAL state — the
    # planned→realized/orphan boilerplate is wrong for realized-on-entry (F+wp)
    # and meaningless for failed candidates.
    dstate = (last_ev or {}).get("delivery_state")
    fev = (last_ev or {}).get("event")
    if fev in ("trial_failed", "impact_failed", "rejected_delivery", "aborted"):
        A(f"（该候选未落地，ledger 终态 `{fev}`——没有 delivery 生命周期需要"
          f"追踪；若要重试，对同一 key 重新跑 strengthen.sh 即可）")
    elif dstate == "realized":
        A("（delivery 已是 `realized`——wp 自动生效或 consumer 已落地，"
          "无需 lifecycle sweep 推进）")
    elif dstate == "pending":
        A("（delivery 为 `pending`（named-planned）：lifecycle sweep 会在 "
          "consumer 落地后推进到 realized，或 grace 超期后标 orphan）")
    elif dstate == "orphan":
        A("（delivery 已 `orphan`——grace 超期无 consumer，待 GC review）")
    else:
        A("（该 key 在 ledger 中暂无 delivery 状态记录）")

    (cd / "record.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"[record] {cd / 'record.md'}")


if __name__ == "__main__":
    main()
