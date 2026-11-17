#!/usr/bin/env python3
"""Extract exact draft targets and bind them to their semantic source closure.

This is a preparation catalog, not a proof runner and not an approval mechanism.
The native target theory remains authoritative for elaboration and context.
"""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "targets-session/Binary_Heap_Targets.thy"
DESCRIPTIONS = {
    "bh_count_exact": ("binary_heap_num_entries", "计数等于抽象多重集大小，状态不变"),
    "bh_insert_correct": ("binary_heap_insert", "成功增加一次给定指针；失败保持状态；保持堆序与所有权"),
    "bh_insert_without_growth": ("binary_heap_insert", "有空余容量时必定成功，不改变分配状态、数组地址或容量"),
    "bh_pop_correct": ("binary_heap_pop", "空堆返回空指针；非空堆移除一个最小键元素，保持堆序和分配状态"),
    "bh_free_owned_storage": ("binary_heap_free", "只释放堆对象和数组，保留调用者数据"),
    "bh_new_empty_or_failure": ("binary_heap_new", "成功创建容量16的空堆；失败回收临时块；保留已有块和调用者数据"),
}

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    text = TARGET.read_text()
    matches = re.findall(r'(?ms)^lemma (\w+):\s*"(.*?)"\s*oops', text)
    if [name for name, _ in matches] != list(DESCRIPTIONS):
        raise RuntimeError("Target inventory differs from reviewed preparation schema")
    paths = [ROOT / "ROOT", ROOT / "source-lock.json", ROOT / "environment-lock.json", ROOT / "c/binary_heap_input.c",
             *sorted((ROOT / "theories").glob("*.thy")),
             *sorted((ROOT / "targets-session").glob("*.thy"))]
    closure = {str(p.relative_to(ROOT)): digest(p) for p in paths}
    binding = hashlib.sha256(json.dumps(closure, sort_keys=True).encode()).hexdigest()
    targets = []
    for name, proposition in matches:
        function, description = DESCRIPTIONS[name]
        targets.append({"id": name, "source_function": function,
                        "source_file": "vendor/src/binary-heap.c",
                        "intent": description, "proposition": proposition,
                        "isabelle_context": "binary_heap_input_global_addresses",
                        "theory": "Calg_Binary_Targets.Binary_Heap_Targets",
                        "status": "unproved", "semantic_binding": binding})
    approval_path = ROOT / "review-approval.json"
    comparison_path = ROOT / "evidence/readback-comparison.json"
    approved = (approval_path.is_file() and
                json.loads(approval_path.read_text()).get("approved_semantic_binding") == binding)
    reviewed = False
    if comparison_path.is_file():
        review = json.loads(comparison_path.read_text())
        reviewed = (review.get("semantic_binding") == binding and review.get("status") == "consistent_with_approved_scope"
                    and digest(ROOT / review["readback_path"]) == review["readback_sha256"])
    adapter_checked = False
    adapter_path = ROOT / "evidence/adapter-verification.json"
    if adapter_path.is_file():
        adapter = json.loads(adapter_path.read_text())
        adapter_checked = (adapter.get("semantic_binding") == binding and adapter.get("status") == "pass"
                           and bool(adapter.get("evidence_files"))
                           and all((ROOT / p).is_file() and digest(ROOT / p) == sha
                                   for p, sha in adapter["evidence_files"].items()))
    result = {"schema_version": 1,
              "status": "semantics_approved" if approved and reviewed else "draft_pending_semantic_review",
              "scope": "binary heap only", "generation_allowed": approved and reviewed,
              "independent_readback_completed": reviewed,
              "native_generation_adapter_checked": adapter_checked,
              "adapter_evidence": "evidence/adapter-verification.json" if adapter_checked else None,
              "target_count": len(targets), "semantic_binding": binding,
              "source_closure": closure, "targets": targets}
    (ROOT / "targets.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"targets": len(targets), "status": result["status"], "binding": binding}))

if __name__ == "__main__":
    main()
