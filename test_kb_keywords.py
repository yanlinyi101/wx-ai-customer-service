"""
知识库关键词命中率测试
随机抽取100条产品问题，验证：
  1. retrieve() top-1 命中是否为正确条目（自检）
  2. 得分是否 >= INTENT_HIGH_THRESHOLD（4.0），即能正确路由到 CLEAR
"""

import sys
import json
import random
import os
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SERVICE_DIR = Path(__file__).parent / "wechat_ai_service"
sys.path.insert(0, str(SERVICE_DIR))
os.chdir(SERVICE_DIR)

from rag_service import retrieve, _score, get_kb
from config import INTENT_HIGH_THRESHOLD, INTENT_LOW_THRESHOLD

# ── 颜色 ──
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

SAMPLE_SIZE = 100
SEED = 42


def run():
    kb = get_kb()

    # 过滤产品相关（非"通用"类）
    product_entries = [e for e in kb if "通用" not in e.get("keywords", [])]
    print(f"\n{BOLD}{'='*70}{RESET}")
    print(f"{BOLD}  知识库关键词覆盖测试{RESET}")
    print(f"{BOLD}{'='*70}{RESET}")
    print(f"  知识库总条目：{len(kb)}  产品类条目：{len(product_entries)}")
    print(f"  随机抽样：{SAMPLE_SIZE} 条  |  CLEAR 阈值：≥ {INTENT_HIGH_THRESHOLD}")
    print(f"{'='*70}\n")

    random.seed(SEED)
    samples = random.sample(product_entries, min(SAMPLE_SIZE, len(product_entries)))

    results = []
    fail_wrong_top1 = []   # top-1 命中条目不正确
    fail_low_score  = []   # 得分不足（VAGUE 或 CHAT）
    pass_count = 0

    for i, entry in enumerate(samples, 1):
        question = entry["question"]
        expected_q = question  # 原问题应该是 top-1

        ctx, imgs, top_score = retrieve(question)

        # 找出 top-1 命中的条目
        scored = [(e, _score(question, e)) for e in kb]
        scored.sort(key=lambda x: x[1], reverse=True)
        top1_entry = scored[0][0] if scored else None
        top1_score = scored[0][1] if scored else 0.0
        top1_q = top1_entry["question"] if top1_entry else ""

        is_top1_correct = (top1_q == expected_q)
        is_score_ok     = top_score >= INTENT_HIGH_THRESHOLD

        passed = is_top1_correct and is_score_ok

        if passed:
            pass_count += 1
            status = f"{GREEN}PASS{RESET}"
        else:
            status = f"{RED}FAIL{RESET}"

        score_color = GREEN if is_score_ok else RED
        top1_color  = GREEN if is_top1_correct else RED

        print(f"{BOLD}#{i:03d}{RESET} [{status}] {CYAN}{question}{RESET}")
        print(f"       得分: {score_color}{top_score:.2f}{RESET}  "
              f"top-1正确: {top1_color}{is_top1_correct}{RESET}", end="")

        if not is_top1_correct and top1_entry:
            print(f"\n       {RED}top-1实际命中: {top1_q[:50]}{RESET}", end="")
        if not is_score_ok:
            if top_score < INTENT_LOW_THRESHOLD:
                route = f"{RED}→ CHAT（被当作闲聊！）{RESET}"
            else:
                route = f"{YELLOW}→ VAGUE（追问用户）{RESET}"
            print(f"\n       路由: {route}", end="")
        print()

        result = {
            "id": i,
            "question": question,
            "keywords": entry.get("keywords", []),
            "top_score": top_score,
            "is_top1_correct": is_top1_correct,
            "is_score_ok": is_score_ok,
            "passed": passed,
            "top1_question": top1_q,
        }
        results.append(result)

        if not is_top1_correct:
            fail_wrong_top1.append(result)
        if not is_score_ok:
            fail_low_score.append(result)

    # ── 汇总 ──
    fail_count = len(samples) - pass_count
    print(f"\n{'='*70}")
    print(f"{BOLD}  结果汇总{RESET}")
    print(f"{'='*70}")
    print(f"  通过: {GREEN}{BOLD}{pass_count}/{len(samples)}{RESET}  失败: {RED}{BOLD}{fail_count}{RESET}")
    print(f"  top-1不正确: {RED}{len(fail_wrong_top1)}{RESET} 条  |  得分不足: {RED}{len(fail_low_score)}{RESET} 条")

    if fail_low_score:
        print(f"\n  {RED}{BOLD}得分不足（无法路由到 CLEAR）：{RESET}")
        for r in fail_low_score:
            route = "CHAT" if r["top_score"] < INTENT_LOW_THRESHOLD else "VAGUE"
            print(f"    #{r['id']:03d} score={r['top_score']:.2f} →{route}  Q: {r['question'][:60]}")
            print(f"         keywords: {r['keywords'][:5]}")

    if fail_wrong_top1:
        print(f"\n  {YELLOW}{BOLD}top-1 命中错误（关键词混淆）：{RESET}")
        for r in fail_wrong_top1:
            print(f"    #{r['id']:03d} Q: {r['question'][:50]}")
            print(f"         命中了: {r['top1_question'][:50]}")

    # 得分分布
    scores = [r["top_score"] for r in results]
    score_buckets = {
        f"≥{INTENT_HIGH_THRESHOLD}(CLEAR)": sum(1 for s in scores if s >= INTENT_HIGH_THRESHOLD),
        f"[{INTENT_LOW_THRESHOLD},{INTENT_HIGH_THRESHOLD})(VAGUE)": sum(1 for s in scores if INTENT_LOW_THRESHOLD <= s < INTENT_HIGH_THRESHOLD),
        f"<{INTENT_LOW_THRESHOLD}(CHAT)": sum(1 for s in scores if s < INTENT_LOW_THRESHOLD),
    }
    print(f"\n  {BOLD}得分分布：{RESET}")
    for label, count in score_buckets.items():
        pct = count / len(results) * 100
        bar = "█" * count + "░" * (len(results) - count)
        print(f"    {label}: {count:3d}条 ({pct:.0f}%)")

    avg_score = sum(scores) / len(scores)
    min_score = min(scores)
    max_score = max(scores)
    print(f"\n  平均得分: {YELLOW}{avg_score:.2f}{RESET}  最低: {RED}{min_score:.2f}{RESET}  最高: {GREEN}{max_score:.2f}{RESET}")
    print(f"{'='*70}\n")

    # 保存详细结果
    out_file = Path(__file__).parent / "test_kb_keywords_result.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"  详细结果已保存：{out_file}\n")


if __name__ == "__main__":
    run()
