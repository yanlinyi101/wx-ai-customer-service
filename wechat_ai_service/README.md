# wechat_ai_service

微信小程序 AI 客服核心服务包。基于 FastAPI + 火山方舟 Ark LLM，运行于 VPS（systemd 管理）。

---

## 模块说明

| 文件 | 职责 |
|------|------|
| `main.py` | FastAPI 入口：Webhook 收发、管理 API、WebSocket |
| `config.py` | 全局配置：微信凭证、AI 参数、意图路由阈值、三套提示词 |
| `ai_service.py` | 意图路由（CHAT / VAGUE / CLEAR）+ 火山方舟 API 调用 + 对话历史 |
| `rag_service.py` | 知识库检索：关键词 + 字符重叠评分，返回 `(context, images, top_score)` |
| `human_service.py` | 人工客服模式：状态切换、消息转发 |
| `wechat_api.py` | 微信 API 封装：发文字、发图片、获取 access_token |
| `crypto.py` | 微信消息 AES-256-CBC 加解密 |
| `chat_logger.py` | 全量对话持久化（本地 JSON 文件） |
| `cos_logger.py` | 对话日志同步腾讯云 COS（可选） |
| `kb_tool.py` | 知识库命令行管理工具 |
| `admin.html` | 客服管理后台（单文件，浏览器直接打开） |
| `knowledge_base.json` | Q&A 知识库数据（已加入 .gitignore） |

---

## 意图路由

`ai_service._classify_intent(top_score)` 根据 RAG 检索最高分路由到三种模式：

```
top_score < INTENT_LOW_THRESHOLD (2.0)
  → CHAT   亲和闲聊回复，不注入知识库，不返回图片

INTENT_LOW_THRESHOLD ≤ top_score < INTENT_HIGH_THRESHOLD (4.0)
  → VAGUE  追问用户细节，不注入知识库，不返回图片

top_score ≥ INTENT_HIGH_THRESHOLD (4.0)
  → CLEAR  注入知识库上下文回答，正常返回图片
```

阈值通过 `.env` 中 `INTENT_LOW_THRESHOLD` / `INTENT_HIGH_THRESHOLD` 调整。

---

## 环境变量（.env）

```env
# 微信小程序
WECHAT_TOKEN=
WECHAT_APP_ID=
WECHAT_APP_SECRET=
WECHAT_ENCODING_AES_KEY=

# AI（火山方舟 Ark）
AI_API_KEY=
AI_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
AI_MODEL=doubao-seed-2-0-lite-260215

# 意图路由阈值（可选，有默认值）
INTENT_LOW_THRESHOLD=2.0
INTENT_HIGH_THRESHOLD=4.0

# 管理后台
ADMIN_TOKEN=
ADMIN_OPENID=

# 聊天记录目录
LOG_DIR=/opt/wechat_chat_logs

# 腾讯云 COS（可选）
COS_ENABLED=false
COS_SECRET_ID=
COS_SECRET_KEY=
COS_BUCKET=
COS_REGION=ap-guangzhou
```

---

## 本地开发

```bash
pip install -r requirements.txt
cp .env.example .env   # 填写配置
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

访问 `http://localhost:8000/health` 返回 `{"status":"ok"}` 即正常。

---

## 部署

```bash
# 在项目根目录执行
python deploy.py
```

`deploy.py` 将此目录下源码打包为 tar，通过单次 scp 上传至 VPS `/opt/wechat-ai/`，并自动重启 `wechat-ai` systemd 服务。

---

## 管理后台用户昵称

管理后台（`admin.html`）**等待中**和**历史记录** Tab 均会尝试显示微信昵称。

### 当前限制

微信 `cgi-bin/user/info` 接口（errcode 48001）仅对**已认证服务号**开放，订阅号和未认证服务号无权调用，因此系统无法自动从微信获取用户真实昵称。未能获取昵称时，列表降级显示 `openid[:8]...`。

### 若需真实昵称，可选方案

| 方案 | 说明 |
|------|------|
| 升级为认证服务号 | 开通后直接调用 `user/info` 接口即可，代码已预留该逻辑 |
| 小程序侧主动上报 | 用户在小程序授权后，将昵称 POST 到后端 `/user/profile` 等接口，保存至 `{openid}.json` 的 `nickname` 字段 |

### 昵称缓存说明

昵称存储在 `LOG_DIR/{openid}.json` 的 `nickname` 字段。系统只缓存真实昵称，不会将 openid 前缀作为昵称缓存，避免污染数据。已获取到昵称的用户无需重复请求微信 API。

---

## 知识库管理

```bash
python kb_tool.py list            # 查看所有条目
python kb_tool.py add             # 交互式添加条目
python kb_tool.py delete <序号>   # 删除指定条目
python kb_tool.py export          # 导出为 Excel
python kb_tool.py import          # 从 Excel 批量导入
```

条目格式：

```json
{
  "question": "如何申请退款？",
  "answer": "支持7天无理由退款，请在小程序「我的订单」中申请。",
  "keywords": ["退款", "退货", "不想要", "七天"],
  "image_url": ""
}
```
