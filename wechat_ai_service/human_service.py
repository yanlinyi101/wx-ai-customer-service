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
_last_activity: dict[str, float] = {}  # openid -> 最后活跃时间戳
_MAX_QUEUE = 100

# ─── 会话归属追踪 ────────────────────────────────────────────────────────────
_session_agent: dict[str, str] = {}    # openid → agent_name（首个回复者）
_enter_human_ts: dict[str, float] = {} # openid → 进入人工模式时间戳
_first_replied: set[str] = set()       # 已被首次回复的 openid 集合
_response_time: dict[str, float] = {}  # openid → 首次应答时长（秒）


# ─── 公共 API ──────────────────────────────────────────────────────────────────

async def is_human_mode(openid: str) -> bool:
    """判断用户是否处于人工客服模式"""
    return openid in _human_mode


async def enter_human_mode(openid: str) -> None:
    """将用户切换到人工客服模式"""
    _human_mode.add(openid)
    _last_activity[openid] = time.time()
    _enter_human_ts[openid] = time.time()
    logger.debug(f"[human_service] 进入人工模式 openid={openid[:8]}")


async def exit_human_mode(openid: str) -> None:
    """关闭会话：清除人工模式标志和消息队列"""
    _human_mode.discard(openid)
    _human_queue.pop(openid, None)
    _pre_history.pop(openid, None)
    _last_activity.pop(openid, None)
    clear_session_tracking(openid)
    logger.debug(f"[human_service] 退出人工模式 openid={openid[:8]}")


async def push_message(
    openid: str,
    text: str = "",
    role: str = "user",
    image_url: str = "",
    msg_type: str = "text",
) -> None:
    """将消息追加到缓冲队列。role 可为 'user' 或 'agent'"""
    queue = _human_queue[openid]
    if len(queue) >= _MAX_QUEUE:
        queue.pop(0)
    entry: dict = {"text": text, "ts": time.time(), "role": role, "msg_type": msg_type}
    if image_url:
        entry["image_url"] = image_url
    queue.append(entry)
    _last_activity[openid] = time.time()  # 更新最后活跃时间


def get_idle_openids(timeout_seconds: float) -> list[str]:
    """返回超过 timeout_seconds 未活跃的 openid 列表"""
    now = time.time()
    return [
        oid for oid in _human_mode
        if now - _last_activity.get(oid, now) >= timeout_seconds
    ]


def get_unattended_openids(timeout_seconds: float) -> list[str]:
    """返回超过 timeout_seconds 无客服接入（未收到任何客服回复）的 openid 列表"""
    now = time.time()
    return [
        oid for oid in _human_mode
        if oid not in _first_replied
        and now - _enter_human_ts.get(oid, now) >= timeout_seconds
    ]


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


# ─── 会话归属追踪 API ─────────────────────────────────────────────────────────

def attribute_session(openid: str, agent_name: str) -> float | None:
    """
    记录会话归属到 agent_name（首个回复者获得归属）。
    返回首次应答时长（秒），若已有归属则返回 None。
    """
    if openid not in _first_replied:
        _first_replied.add(openid)
        _session_agent[openid] = agent_name
        enter_ts = _enter_human_ts.get(openid)
        if enter_ts:
            rt = time.time() - enter_ts
            _response_time[openid] = rt
            return rt
    return None


def get_session_attribution(openid: str) -> dict:
    """获取会话归属信息（agent_name 和已记录的应答时长）"""
    return {
        "agent_name": _session_agent.get(openid),
        "response_time": _response_time.get(openid),
    }


def get_session_queue(openid: str) -> list:
    """获取会话的消息队列（同步）"""
    return list(_human_queue.get(openid, []))


def clear_session_tracking(openid: str) -> None:
    """清理会话归属追踪数据"""
    _session_agent.pop(openid, None)
    _enter_human_ts.pop(openid, None)
    _first_replied.discard(openid)
    _response_time.pop(openid, None)
