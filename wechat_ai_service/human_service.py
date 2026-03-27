"""
人工客服模式管理模块（纯内存版）

单进程单实例部署（云服务器），所有状态保存在进程内存中。
操作即时完成，无网络 I/O 延迟。

所有内存字典 key 为 f"{app_id}:{openid}"（简称 uid），
支持多小程序命名空间隔离：同一用户在不同小程序有不同 uid。

公共 API（async，接口与原版保持一致，增加 app_id 参数）：
    is_human_mode(app_id, openid) -> bool
    enter_human_mode(app_id, openid) -> None
    exit_human_mode(app_id, openid) -> None
    push_message(app_id, openid, text, role) -> None
    save_pre_history(app_id, openid, ai_history) -> None
    get_all_sessions() -> dict[str, dict]  # key 为 uid
"""

import logging
import time
from collections import defaultdict

logger = logging.getLogger(__name__)

# ─── 内存状态（key 均为 uid = f"{app_id}:{openid}"） ─────────────────────────
_human_mode: set[str] = set()
_human_queue: dict[str, list] = defaultdict(list)
_pre_history: dict[str, list] = {}
_last_activity: dict[str, float] = {}
_MAX_QUEUE = 100

# ─── 会话归属追踪 ────────────────────────────────────────────────────────────
_session_agent: dict[str, str] = {}    # uid → agent_name（首个回复者）
_enter_human_ts: dict[str, float] = {} # uid → 进入人工模式时间戳
_first_replied: set[str] = set()       # 已被首次回复的 uid 集合
_response_time: dict[str, float] = {}  # uid → 首次应答时长（秒）

# ─── 会话认领追踪 ────────────────────────────────────────────────────────────
_claimed_by: dict[str, str] = {}   # uid → agent_name（已认领的客服）


# ─── 公共 API ──────────────────────────────────────────────────────────────────

async def is_human_mode(app_id: str, openid: str) -> bool:
    """判断用户是否处于人工客服模式"""
    return f"{app_id}:{openid}" in _human_mode


async def enter_human_mode(app_id: str, openid: str) -> None:
    """将用户切换到人工客服模式"""
    uid = f"{app_id}:{openid}"
    _human_mode.add(uid)
    _last_activity[uid] = time.time()
    _enter_human_ts[uid] = time.time()
    logger.debug(f"[human_service] 进入人工模式 openid={openid[:8]}")


async def exit_human_mode(app_id: str, openid: str) -> None:
    """关闭会话：清除人工模式标志和消息队列"""
    uid = f"{app_id}:{openid}"
    _human_mode.discard(uid)
    _human_queue.pop(uid, None)
    _pre_history.pop(uid, None)
    _last_activity.pop(uid, None)
    clear_claim(app_id, openid)
    clear_session_tracking(app_id, openid)
    from gray_service import clear as gray_clear
    gray_clear(app_id, openid)
    logger.debug(f"[human_service] 退出人工模式 openid={openid[:8]}")


async def push_message(
    app_id: str,
    openid: str,
    text: str = "",
    role: str = "user",
    image_url: str = "",
    msg_type: str = "text",
) -> None:
    """将消息追加到缓冲队列。role 可为 'user' 或 'agent'"""
    uid = f"{app_id}:{openid}"
    queue = _human_queue[uid]
    if len(queue) >= _MAX_QUEUE:
        queue.pop(0)
    entry: dict = {"text": text, "ts": time.time(), "role": role, "msg_type": msg_type}
    if image_url:
        entry["image_url"] = image_url
    queue.append(entry)
    _last_activity[uid] = time.time()


def get_idle_openids(timeout_seconds: float) -> list[str]:
    """返回超过 timeout_seconds 未活跃的 uid 列表"""
    now = time.time()
    return [
        uid for uid in _human_mode
        if now - _last_activity.get(uid, now) >= timeout_seconds
    ]


def get_unattended_openids(timeout_seconds: float) -> list[str]:
    """返回超过 timeout_seconds 无客服接入（未收到任何客服回复）的 uid 列表"""
    now = time.time()
    return [
        uid for uid in _human_mode
        if uid not in _first_replied
        and now - _enter_human_ts.get(uid, now) >= timeout_seconds
    ]


def save_pre_history(app_id: str, openid: str, ai_history: list) -> None:
    """将 AI 对话历史转换为 pre_history 格式保存"""
    uid = f"{app_id}:{openid}"
    _pre_history[uid] = [
        {"text": m["content"], "role": m["role"]}
        for m in ai_history
    ]


async def get_all_sessions() -> dict[str, dict]:
    """
    返回所有人工模式会话。
    格式：{uid: {"app_id": ..., "openid": ..., "messages": [...], "pre_history": [...], "claimed_by": ...}}
    uid = f"{app_id}:{openid}"
    """
    result = {}
    for uid in _human_mode:
        app_id, openid = uid.split(":", 1)
        result[uid] = {
            "app_id": app_id,
            "openid": openid,
            "messages": list(_human_queue[uid]),
            "pre_history": list(_pre_history.get(uid, [])),
            "claimed_by": _claimed_by.get(uid, ""),
        }
    return result


# ─── 会话归属追踪 API ─────────────────────────────────────────────────────────

def attribute_session(app_id: str, openid: str, agent_name: str) -> float | None:
    """
    记录会话归属到 agent_name（首个回复者获得归属）。
    返回首次应答时长（秒），若已有归属则返回 None。
    """
    uid = f"{app_id}:{openid}"
    if uid not in _first_replied:
        _first_replied.add(uid)
        _session_agent[uid] = agent_name
        enter_ts = _enter_human_ts.get(uid)
        if enter_ts:
            rt = time.time() - enter_ts
            _response_time[uid] = rt
            return rt
    return None


def get_session_attribution(app_id: str, openid: str) -> dict:
    """获取会话归属信息（agent_name 和已记录的应答时长）"""
    uid = f"{app_id}:{openid}"
    return {
        "agent_name": _session_agent.get(uid),
        "response_time": _response_time.get(uid),
    }


def get_session_queue(app_id: str, openid: str) -> list:
    """获取会话的消息队列（同步）"""
    uid = f"{app_id}:{openid}"
    return list(_human_queue.get(uid, []))


def clear_session_tracking(app_id: str, openid: str) -> None:
    """清理会话归属追踪数据"""
    uid = f"{app_id}:{openid}"
    _session_agent.pop(uid, None)
    _enter_human_ts.pop(uid, None)
    _first_replied.discard(uid)
    _response_time.pop(uid, None)


# ─── 会话认领 API ──────────────────────────────────────────────────────────────

def claim_session(app_id: str, openid: str, agent_name: str) -> bool:
    """尝试认领会话；已被他人认领则返回 False"""
    uid = f"{app_id}:{openid}"
    if uid in _claimed_by and _claimed_by[uid] != agent_name:
        return False
    _claimed_by[uid] = agent_name
    return True


def get_claimer(app_id: str, openid: str) -> str:
    """获取当前认领该会话的客服名，未认领返回空串"""
    uid = f"{app_id}:{openid}"
    return _claimed_by.get(uid, "")


def clear_claim(app_id: str, openid: str) -> None:
    """清除会话认领"""
    uid = f"{app_id}:{openid}"
    _claimed_by.pop(uid, None)
