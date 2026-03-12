"""
灰度测试服务模块

负责：
- 按用户 sticky 分组（ai / human）
- 灰度配置持久化（gray_config.json）
- 提供管理员 API 所需的 get_config / update_config
"""

import json
import logging
import random
from pathlib import Path

from config import GRAY_ENABLED, GRAY_AI_RATIO

logger = logging.getLogger(__name__)
_CONFIG_PATH = Path(__file__).parent / "gray_config.json"

# ── 运行时状态 ──────────────────────────────────────────────────────────────
_assignments: dict[str, str] = {}   # openid → "ai" | "human"
_enabled: bool = GRAY_ENABLED
_ai_ratio: float = GRAY_AI_RATIO    # 初始值来自 config.py / env


def _load_config() -> None:
    """如存在持久化文件，优先读取（覆盖 env 值）"""
    global _enabled, _ai_ratio
    if _CONFIG_PATH.exists():
        try:
            data = json.loads(_CONFIG_PATH.read_text("utf-8"))
            _enabled  = data.get("enabled", _enabled)
            _ai_ratio = data.get("ai_ratio", _ai_ratio)
        except Exception:
            logger.warning("[GRAY] 读取 gray_config.json 失败，使用默认值")


_load_config()


# ── 公开 API ────────────────────────────────────────────────────────────────

def get_or_assign(openid: str) -> str:
    """返回用户的分组（ai/human）。灰度未启用时始终返回 ai。"""
    if not _enabled:
        return "ai"
    if openid not in _assignments:
        _assignments[openid] = "ai" if random.random() < _ai_ratio else "human"
        logger.debug(f"[GRAY] {openid[:8]} 新分组 → {_assignments[openid]}")
    return _assignments[openid]


def clear(openid: str) -> None:
    """清除用户的分组记录（会话关闭后调用，下次重新随机分配）"""
    _assignments.pop(openid, None)


def update_config(enabled: bool, ai_ratio: float) -> None:
    """管理员从后台修改后调用，实时生效并持久化"""
    global _enabled, _ai_ratio
    _enabled  = enabled
    _ai_ratio = max(0.0, min(1.0, ai_ratio))
    _CONFIG_PATH.write_text(
        json.dumps({"enabled": _enabled, "ai_ratio": _ai_ratio}, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info(f"[GRAY] 配置已更新 enabled={_enabled} ai_ratio={_ai_ratio}")


def get_config() -> dict:
    """返回当前灰度配置"""
    return {"enabled": _enabled, "ai_ratio": _ai_ratio}
