"""
测试集运行脚本 — 本地直接测试 RAG 评分 + 意图路由 + AI 回复

运行方式：
    python run_test.py             # 只测 RAG 评分和意图路由（快速，不调用 AI）
    python run_test.py --ai        # 同时调用 AI 生成完整回复（需要 API Key）
"""

import sys
import json
import asyncio
import os
from pathlib import Path

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── 设置工作目录，确保能加载 config.py 和 knowledge_base.json ──
SERVICE_DIR = Path(__file__).parent / "wechat_ai_service"
sys.path.insert(0, str(SERVICE_DIR))
os.chdir(SERVICE_DIR)

# ── 注入 AI Key 和 URL（用于 --ai 模式）──
os.environ.setdefault("AI_API_KEY",  "488e5679-7ea3-4257-adfb-de988cee2970")
os.environ.setdefault("AI_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
os.environ.setdefault("AI_MODEL",    "doubao-seed-2-0-pro-260215")

from rag_service import retrieve
from config import INTENT_LOW_THRESHOLD, INTENT_HIGH_THRESHOLD, HUMAN_TAKEOVER_KEYWORDS

TEST_FILE = Path(__file__).parent / "test_set.json"

# ANSI 颜色
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


def classify_intent(question: str) -> tuple[str, float]:
    """返回 (intent, top_score)"""
    # 先检查转人工关键词
    if any(kw in question for kw in HUMAN_TAKEOVER_KEYWORDS):
        return "HUMAN", 99.0
    _, _, top_score = retrieve(question)
    if top_score < INTENT_LOW_THRESHOLD:
        intent = "CHAT"
    elif top_score < INTENT_HIGH_THRESHOLD:
        intent = "VAGUE"
    else:
        intent = "CLEAR"
    return intent, top_score


async def get_ai_reply_for_test(question: str, intent: str, top_score: float) -> str:
    from ai_service import get_ai_reply
    reply, _ = await get_ai_reply("test_user_001", question)
    return reply


def run_tests(with_ai: bool = False):
    with open(TEST_FILE, encoding="utf-8") as f:
        tests = json.load(f)

    print(f"\n{BOLD}{'='*65}{RESET}")
    print(f"{BOLD}  微信 AI 客服系统 — 测试集运行报告{RESET}")
    print(f"{BOLD}{'='*65}{RESET}")
    print(f"  阈值：CHAT < {INTENT_LOW_THRESHOLD}  |  VAGUE < {INTENT_HIGH_THRESHOLD}  |  CLEAR ≥ {INTENT_HIGH_THRESHOLD}")
    print(f"  测试数量：{len(tests)} 条  |  AI 回复：{'开启' if with_ai else '关闭（仅测路由）'}")
    print(f"{'='*65}\n")

    results = []
    pass_count = 0

    for t in tests:
        tid      = t["id"]
        question = t["question"]
        expected = t["intent"]
        target   = t["target"]

        actual_intent, top_score = classify_intent(question)
        passed = actual_intent == expected

        if passed:
            pass_count += 1
            status = f"{GREEN}PASS{RESET}"
        else:
            status = f"{RED}FAIL{RESET}"

        score_str = f"{top_score:.1f}" if top_score < 90 else "触发关键词"
        intent_color = GREEN if passed else RED

        print(f"{BOLD}#{tid:02d}{RESET} [{status}] {CYAN}{question}{RESET}")
        print(f"     期望: {BOLD}{expected}{RESET}  实际: {intent_color}{BOLD}{actual_intent}{RESET}  得分: {YELLOW}{score_str}{RESET}")

        if with_ai:
            try:
                reply = asyncio.run(get_ai_reply_for_test(question, actual_intent, top_score))
                print(f"     AI回复: {reply[:120]}{'...' if len(reply) > 120 else ''}")
            except Exception as e:
                print(f"     {RED}AI回复失败: {e}{RESET}")

        if not passed:
            print(f"     {RED}目标行为: {target[:80]}{RESET}")

        print()
        results.append({"id": tid, "question": question, "expected": expected,
                         "actual": actual_intent, "score": top_score, "pass": passed})

    # ── 汇总 ──
    fail_count = len(tests) - pass_count
    print(f"{'='*65}")
    print(f"{BOLD}  结果汇总{RESET}")
    print(f"{'='*65}")
    print(f"  通过: {GREEN}{BOLD}{pass_count}/{len(tests)}{RESET}  失败: {RED}{BOLD}{fail_count}{RESET}")

    if fail_count > 0:
        print(f"\n  {RED}失败列表:{RESET}")
        for r in results:
            if not r["pass"]:
                score_str = f"{r['score']:.1f}" if r['score'] < 90 else "触发关键词"
                print(f"    #{r['id']:02d} 期望={r['expected']} 实际={r['actual']} 得分={score_str}  Q: {r['question']}")

    # 按意图分类统计
    print(f"\n  {BOLD}按意图分类:{RESET}")
    for intent in ["CHAT", "VAGUE", "CLEAR", "HUMAN"]:
        intent_tests = [r for r in results if r["expected"] == intent]
        intent_pass  = [r for r in intent_tests if r["pass"]]
        if intent_tests:
            pct = len(intent_pass) / len(intent_tests) * 100
            bar = "█" * len(intent_pass) + "░" * (len(intent_tests) - len(intent_pass))
            print(f"    {intent:6s}: {len(intent_pass)}/{len(intent_tests)} ({pct:.0f}%)  [{bar}]")

    print(f"{'='*65}\n")
    return results


if __name__ == "__main__":
    with_ai = "--ai" in sys.argv
    run_tests(with_ai=with_ai)
