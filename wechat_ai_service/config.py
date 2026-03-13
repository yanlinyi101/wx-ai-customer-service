"""
配置模块

所有配置项均可通过 .env 文件或环境变量覆盖。

意图路由阈值（INTENT_*_THRESHOLD）：
    RAG top_score 的分界值，决定消息被路由到哪种回复模式。
    可在 .env 中设置 INTENT_LOW_THRESHOLD / INTENT_HIGH_THRESHOLD 动态调整，无需改代码。

三套系统提示词：
    CHAT_SYSTEM_PROMPT  — 闲聊模式，亲和友好
    VAGUE_SYSTEM_PROMPT — 模糊问题模式，引导追问
    CLEAR_SYSTEM_PROMPT — 明确问题模式，基于知识库回答（含 {context} 占位符）
"""

import json
import os
import pathlib

from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
# 微信小程序配置（在 mp.weixin.qq.com 后台获取）
# ─────────────────────────────────────────────
WECHAT_TOKEN            = os.getenv("WECHAT_TOKEN", "")
WECHAT_APP_ID           = os.getenv("WECHAT_APP_ID", "")
WECHAT_APP_SECRET       = os.getenv("WECHAT_APP_SECRET", "")
WECHAT_ENCODING_AES_KEY = os.getenv("WECHAT_ENCODING_AES_KEY", "")

# ─────────────────────────────────────────────
# AI 配置（默认使用 DeepSeek，可切换）
# ─────────────────────────────────────────────
AI_API_KEY   = os.getenv("AI_API_KEY", "")
AI_BASE_URL  = os.getenv("AI_BASE_URL", "https://api.deepseek.com")
AI_MODEL     = os.getenv("AI_MODEL", "deepseek-chat")

# ─────────────────────────────────────────────
# 客服 AI 系统提示词（根据你的业务修改）
# ─────────────────────────────────────────────
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT", """
你是一名专业、亲切的在线客服助手。请遵守以下规则：
1. 用简洁、友好的语气回复，每次回复不超过200字
2. 如果用户询问退款、投诉等敏感问题，告知会转接人工客服
3. 不要编造产品信息，不确定时说"我帮您确认后回复"
4. 始终保持礼貌，称呼用户为"您"
""".strip())

# ─────────────────────────────────────────────
# 触发人工接管的关键词
# ─────────────────────────────────────────────
HUMAN_TAKEOVER_KEYWORDS = ["转人工", "人工客服", "人工", "转接", "真人"]

# 转接的指定客服账号（留空则自动分配）格式：xxx@公众号ID
KF_ACCOUNT = os.getenv("KF_ACCOUNT", "")

# 每个用户保留的对话历史轮数（太多会增加 AI 费用）
MAX_HISTORY_TURNS = 5

# ─────────────────────────────────────────────
# RAG 知识库配置
# ─────────────────────────────────────────────
RAG_ENABLED = os.getenv("RAG_ENABLED", "true").lower() == "true"
RAG_TOP_K   = int(os.getenv("RAG_TOP_K", "3"))   # 每次检索返回最相关的条目数
RAG_MIN_SCORE = float(os.getenv("RAG_MIN_SCORE", "1.0"))  # 最低相关性分数，低于此分数不注入

# ─────────────────────────────────────────────
# 意图路由阈值（基于 RAG top_score）
# ─────────────────────────────────────────────
INTENT_LOW_THRESHOLD  = float(os.getenv("INTENT_LOW_THRESHOLD",  "2.0"))  # < 2.0 → 闲聊
INTENT_HIGH_THRESHOLD = float(os.getenv("INTENT_HIGH_THRESHOLD", "4.0"))  # ≥ 4.0 → 明确问题

# ─────────────────────────────────────────────
# 三套意图系统提示词
# ─────────────────────────────────────────────
CHAT_SYSTEM_PROMPT = """你是一名专业、亲和的电商客服助手。
当前客户在和你闲聊，请用温暖、友好的语气回应，保持简洁自然。
不要主动推销产品，但如果客户问到产品相关问题，引导他们说出具体需求。
每次回复不超过100字，开头用"亲"或"亲您好"称呼用户，不要用"您，"单独作为句首称谓。"""

VAGUE_SYSTEM_PROMPT = """你是一名专业电商客服。
客户的问题还不够具体，请礼貌地追问1-2个关键细节，帮助明确需求。
例如：询问是哪款产品、具体遇到什么问题、是否已购买等。
不要猜测或随意回答，专注于引导客户说清楚问题。
每次回复不超过100字，开头用"亲"或"亲您好"称呼用户，不要用"您，"单独作为句首称谓。"""

CLEAR_SYSTEM_PROMPT = """你是一名专业电商客服。
请根据以下知识库内容回答客户问题，回答要准确、简洁。
如果知识库没有相关信息，诚实告知并建议联系人工客服。
每次回复不超过200字，开头用"亲"或"亲您好"称呼用户，不要用"您，"单独作为句首称谓。

【知识库参考信息】
{context}"""

# ── 转人工升级提示配置 ────────────────────────────────────────────────
LOGISTICS_KEYWORDS = [
    "快递", "物流", "运单", "配送", "发货", "查件",
    "收货", "包裹", "寄件", "追踪", "到货", "派送",
    "运费", "签收", "揽件", "已发货", "什么时候到"
]

FRUSTRATION_KEYWORDS = [
    "烦", "算了", "差评", "投诉", "没用", "服务差",
    "不满", "骗人", "假货", "差劲", "无语", "生气",
    "催", "怎么还没", "等了很久", "一直没有", "太慢了"
]

LOW_CONF_TURNS_THRESHOLD = int(os.getenv("LOW_CONF_TURNS_THRESHOLD", "3"))
MAX_TURNS_BEFORE_ESCALATION = int(os.getenv("MAX_TURNS_BEFORE_ESCALATION", "20"))

# ─────────────────────────────────────────────
# 腾讯云 COS 聊天日志配置
# ─────────────────────────────────────────────
COS_ENABLED    = os.getenv("COS_ENABLED", "false").lower() == "true"
COS_SECRET_ID  = os.getenv("COS_SECRET_ID", "")
COS_SECRET_KEY = os.getenv("COS_SECRET_KEY", "")
COS_REGION     = os.getenv("COS_REGION", "ap-guangzhou")
COS_BUCKET     = os.getenv("COS_BUCKET", "")    # 格式：bucketname-appid

# ─────────────────────────────────────────────
# 人工客服管理配置
# ─────────────────────────────────────────────
ADMIN_TOKEN  = os.getenv("ADMIN_TOKEN", "")   # 管理页访问令牌（URL ?token=xxx）
ADMIN_OPENID = os.getenv("ADMIN_OPENID", "")  # 客服人员微信 openid，转人工时推送通知（可空）

# ─────────────────────────────────────────────
# 聊天日志本地存储目录
# ─────────────────────────────────────────────
LOG_DIR = os.getenv("LOG_DIR", "/opt/wechat_chat_logs")

# ─────────────────────────────────────────────
# 图片存储配置
# ─────────────────────────────────────────────
IMAGE_DIR      = os.getenv("IMAGE_DIR", "/opt/wechat_images")
IMAGE_BASE_URL = os.getenv("IMAGE_BASE_URL", "")  # e.g. https://your-domain.com/images

# ─────────────────────────────────────────────
# 客服账号管理
# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
# 灰度测试配置
# ─────────────────────────────────────────────
GRAY_ENABLED  = os.getenv("GRAY_ENABLED",  "false").lower() == "true"
GRAY_AI_RATIO = float(os.getenv("GRAY_AI_RATIO", "0.2"))

AGENTS_FILE = pathlib.Path(__file__).parent / "agents.json"


import threading as _threading
_agents_lock = _threading.Lock()


def load_agents() -> list[dict]:
    """加载客服账号列表"""
    with _agents_lock:
        if AGENTS_FILE.exists():
            try:
                with open(AGENTS_FILE, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return []


def save_agents(agents: list[dict]) -> None:
    """保存客服账号列表（原子写入）"""
    with _agents_lock:
        AGENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = AGENTS_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(agents, f, ensure_ascii=False, indent=2)
        tmp.replace(AGENTS_FILE)
