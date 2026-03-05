"""
人工客服模式管理模块（纯内存版）

单进程单实例部署（云服务器），所有状态保存在进程内存中。
操作即时完成，无网络 I/O 延迟。

公共 API（async，接口与原版保持一致）：
    is_human_mode(openid) -> bool
    enter_human_mode(openid) -> None
    exit_human_mode(openid) -> None
    push_message(openid, text, role) -> None
    save_pre_history(openid, ai_history) -> None
    get_all_sessions() -> dict[str, dict]
"""

import logging
import time
from collections import defaultdict

logger = logging.getLogger(__name__)

# ─── 内存状态 ──────────────────────────────────────────────────────────────────
_human_mode: set[str] = set()
_human_queue: dict[str, list] = defaultdict(list)
_pre_history: dict[str, list] = {}   # openid -> [{text, role}, ...]
_MAX_QUEUE = 100


# ─── 公共 API ──────────────────────────────────────────────────────────────────

async def is_human_mode(openid: str) -> bool:
    """判断用户是否处于人工客服模式"""
    return openid in _human_mode


async def enter_human_mode(openid: str) -> None:
    """将用户切换到人工客服模式"""
    _human_mode.add(openid)
    logger.debug(f"[human_service] 进入人工模式 openid={openid[:8]}")


async def exit_human_mode(openid: str) -> None:
    """关闭会话：清除人工模式标志和消息队列"""
    _human_mode.discard(openid)
    _human_queue.pop(openid, None)
    _pre_history.pop(openid, None)
    logger.debug(f"[human_service] 退出人工模式 openid={openid[:8]}")


async def push_message(openid: str, text: str, role: str = "user") -> None:
    """将消息追加到缓冲队列。role 可为 'user' 或 'agent'"""
    queue = _human_queue[openid]
    if len(queue) >= _MAX_QUEUE:
        queue.pop(0)
    queue.append({"text": text, "ts": time.time(), "role": role})


def save_pre_history(openid: str, ai_history: list) -> None:
    """将 AI 对话历史转换为 pre_history 格式保存"""
    _pre_history[openid] = [
        {"text": m["content"], "role": m["role"]}
        for m in ai_history
    ]


async def get_all_sessions() -> dict[str, dict]:
    """
    返回所有人工模式会话。
    格式：{openid: {"messages": [...], "pre_history": [...]}}
    """
    return {
        oid: {
            "messages": list(_human_queue[oid]),
            "pre_history": list(_pre_history.get(oid, [])),
        }
        for oid in _human_mode
    }
