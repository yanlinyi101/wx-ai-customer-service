"""
微信 API 调用模块
- 获取并缓存 access_token（按 app_id 隔离）
- 发送客服文本消息
- 上传图片素材并缓存 media_id（按 app_id 隔离）
- 发送客服图片消息
"""

import logging
import time

import httpx

from config import MINIAPPS

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────
# Access Token 内存缓存（有效期7200秒，按 app_id 隔离）
# ──────────────────────────────────────────
_token_cache: dict[str, dict] = {}  # app_id -> {"value": str, "expire_at": float}


async def get_access_token(app_id: str) -> str:
    """获取微信 access_token，按 app_id 自动缓存复用"""
    now = time.time()
    cache = _token_cache.get(app_id)
    if cache and cache.get("value") and now < cache.get("expire_at", 0):
        return cache["value"]

    # 查找该 app_id 对应的凭证
    app_config = None
    for cfg in MINIAPPS.values():
        if cfg["app_id"] == app_id:
            app_config = cfg
            break
    if not app_config:
        logger.error(f"[微信API] 未找到 app_id={app_id} 的配置，请检查 MINIAPPS 设置")
        return ""

    url = "https://api.weixin.qq.com/cgi-bin/stable_token"
    body = {
        "grant_type": "client_credential",
        "appid": app_id,
        "secret": app_config["app_secret"],
        "force_refresh": False,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=body, timeout=10)
        resp.raise_for_status()
        data = resp.json()

    token = data.get("access_token", "")
    expires_in = data.get("expires_in", 7200)

    _token_cache[app_id] = {
        "value": token,
        "expire_at": now + expires_in - 60,  # 提前60秒刷新
    }
    return token


# ──────────────────────────────────────────
# 发送客服文本消息
# ──────────────────────────────────────────

async def send_text_message(openid: str, content: str, app_id: str) -> bool:
    """
    向用户发送客服文本消息
    注意：必须在用户发消息后 48 小时内调用，且最多回复5条
    """
    token = await get_access_token(app_id)
    if not token:
        return False
    url = f"https://api.weixin.qq.com/cgi-bin/message/custom/send?access_token={token}"

    payload = {
        "touser": openid,
        "msgtype": "text",
        "text": {"content": content},
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, timeout=10)
        data = resp.json()

    if data.get("errcode", 0) != 0:
        logger.error(f"[微信API] 发送失败: {data}")
        return False
    return True


# ──────────────────────────────────────────
# 图片素材上传与缓存
# ──────────────────────────────────────────
# 微信临时素材有效期 3 天，缓存 2 天后强制重新上传
_MEDIA_TTL = 2 * 24 * 3600
# key: f"{app_id}:{image_url}" → (media_id, upload_timestamp)
# media_id 是 per-app 的，不同小程序不可复用
_media_cache: dict[str, tuple[str, float]] = {}

# 支持的图片格式及对应 Content-Type
_EXT_MAP = {
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
}


async def _upload_image(image_url: str, app_id: str) -> str:
    """下载图片并上传到微信临时素材库，返回 media_id"""
    async with httpx.AsyncClient(timeout=15) as client:
        img_resp = await client.get(image_url)
        img_resp.raise_for_status()
        img_data = img_resp.content
        content_type = img_resp.headers.get("content-type", "image/jpeg")

    # 根据 URL 后缀或 content-type 推断扩展名
    ext = "jpg"
    for e, ct in _EXT_MAP.items():
        if e in image_url.lower() or e in content_type:
            ext = e
            content_type = ct
            break

    token = await get_access_token(app_id)
    if not token:
        return ""
    upload_url = (
        f"https://api.weixin.qq.com/cgi-bin/media/upload"
        f"?access_token={token}&type=image"
    )
    files = {"media": (f"image.{ext}", img_data, content_type)}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(upload_url, files=files)
        data = resp.json()

    media_id = data.get("media_id", "")
    if not media_id:
        logger.error(f"[图片上传] 失败: {data}")
    else:
        logger.info(f"[图片上传] 成功 media_id={media_id[:12]}...")
    return media_id


async def get_or_upload_media(image_url: str, app_id: str) -> str:
    """
    获取图片的 media_id，优先使用缓存。
    缓存超过 2 天则重新上传。
    注意：media_id 是 per-app 的，不同小程序需分别上传。
    """
    cache_key = f"{app_id}:{image_url}"
    now = time.time()
    if cache_key in _media_cache:
        media_id, ts = _media_cache[cache_key]
        if now - ts < _MEDIA_TTL:
            return media_id

    media_id = await _upload_image(image_url, app_id)
    if media_id:
        _media_cache[cache_key] = (media_id, now)
    return media_id


# ──────────────────────────────────────────
# 发送客服图片消息
# ──────────────────────────────────────────

async def send_image_message(openid: str, media_id: str, app_id: str) -> bool:
    """发送客服图片消息（使用已上传的 media_id）"""
    token = await get_access_token(app_id)
    if not token:
        return False
    url = f"https://api.weixin.qq.com/cgi-bin/message/custom/send?access_token={token}"

    payload = {
        "touser": openid,
        "msgtype": "image",
        "image": {"media_id": media_id},
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, timeout=10)
        data = resp.json()

    if data.get("errcode", 0) != 0:
        logger.error(f"[图片发送] 失败: {data}")
        return False
    return True


async def download_user_image(pic_url: str, save_path: str) -> bool:
    """
    下载用户发送的图片（PicUrl 是公开临时 URL，无需 token）并保存到本地。
    返回 True 表示成功，False 表示失败。
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(pic_url)
            resp.raise_for_status()
            with open(save_path, "wb") as f:
                f.write(resp.content)
        logger.info(f"[图片下载] 成功 save_path={save_path}")
        return True
    except Exception as e:
        logger.error(f"[图片下载] 失败 url={pic_url} err={e}")
        return False


async def send_typing_indicator(openid: str, app_id: str) -> None:
    """
    显示"客服正在输入"状态，提升用户体验
    在调用 AI 前发送，让用户知道消息已收到
    """
    token = await get_access_token(app_id)
    if not token:
        return
    url = f"https://api.weixin.qq.com/cgi-bin/message/custom/typing?access_token={token}"

    payload = {
        "touser": openid,
        "command": "Typing",
    }
    async with httpx.AsyncClient() as client:
        await client.post(url, json=payload, timeout=5)


# ──────────────────────────────────────────
# 获取用户昵称
# ──────────────────────────────────────────

async def get_user_nickname(openid: str, app_id: str) -> str:
    """获取微信用户昵称，失败则返回 openid 前8位"""
    try:
        token = await get_access_token(app_id)
        if not token:
            return openid[:8]
        url = (
            f"https://api.weixin.qq.com/cgi-bin/user/info"
            f"?access_token={token}&openid={openid}&lang=zh_CN"
        )
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=10)
            data = resp.json()
        nickname = data.get("nickname", "")
        return nickname if nickname else openid[:8]
    except Exception as e:
        logger.error(f"[get_user_nickname] 失败 openid={openid[:8]}: {e}")
        return openid[:8]


# ──────────────────────────────────────────
# 转接人工客服
# ──────────────────────────────────────────

async def send_transfer_to_human(openid: str, app_id: str, kf_account: str = "") -> bool:
    """
    将对话转接给人工客服。
    调用后该用户后续消息路由给微信客服平台，不再触发 Webhook。

    kf_account: 指定客服账号（格式 xxx@公众号ID），留空则系统自动分配
    """
    token = await get_access_token(app_id)
    if not token:
        return False
    url = f"https://api.weixin.qq.com/cgi-bin/message/custom/send?access_token={token}"

    payload: dict = {
        "touser": openid,
        "msgtype": "transfer_customer_service",
    }
    if kf_account:
        payload["transfer_customer_service"] = {"kf_account": kf_account}

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, timeout=10)
        data = resp.json()

    errcode = data.get("errcode", 0)
    errmsg  = data.get("errmsg", "")

    if errcode != 0:
        logger.error(
            f"[转人工] 转接失败 openid={openid[:8]}... "
            f"kf_account={kf_account or '(auto)'} "
            f"errcode={errcode} errmsg={errmsg}"
        )
        return False

    logger.info(
        f"[转人工] 转接成功 openid={openid[:8]}... "
        f"kf_account={kf_account or '(auto)'} errmsg={errmsg}"
    )
    return True
