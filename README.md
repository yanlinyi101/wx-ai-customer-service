# wx-ai-customer-service

微信小程序 AI 客服 Webhook 服务。用户在小程序客服对话中发消息，系统自动检索知识库、调用 AI 生成回复；用户输入关键词可一键转接人工，客服通过 Web 管理后台实时接待。

---

## 功能特性

- **AI 自动回复** — 接入 DeepSeek / OpenAI / Claude 等兼容 OpenAI 格式的模型，开箱即用
- **RAG 知识库检索** — 关键词 + 汉字重叠双重评分，优先从知识库提取答案注入提示词，支持图文回复
- **多轮对话记忆** — 每个用户独立维护最近 5 轮对话历史
- **一键转人工** — 检测关键词自动切换模式，通知管理员微信
- **Web 管理后台** — 实时 WebSocket 推送 + HTTP 轮询双保险，展示完整 AI + 人工聊天记录
- **聊天记录持久化** — 全量对话写入服务器 JSON 文件，服务重启不丢失
- **消息安全加密** — 全程微信安全模式（AES-256-CBC），防消息伪造

---

## 架构

```
微信用户
  │  发消息
  ▼
微信服务器
  │  POST /webhook（AES 加密 XML）
  ▼
云服务器 FastAPI（uvicorn + nginx）
  ├─ crypto.py       解密 & 验签
  ├─ rag_service.py  检索知识库，提取相关 Q&A
  ├─ ai_service.py   拼装提示词，调用 AI API
  ├─ human_service.py 人工模式状态管理（内存）
  ├─ chat_logger.py  全量对话写入本地 JSON 文件
  └─ wechat_api.py   调用微信客服消息 API 发送回复

管理员
  │  打开 admin.html
  ▼
WebSocket /ws/admin ──── 实时推送新消息
REST API /admin/*   ──── 回复、结束会话、查历史
```

---

## 项目结构

```
wx-ai-customer-service/
├── wechat_ai_service/
│   ├── main.py              FastAPI 入口，Webhook 路由 + 管理 API + WebSocket
│   ├── ai_service.py        AI 调用、对话历史管理
│   ├── human_service.py     人工客服模式状态管理（内存）
│   ├── chat_logger.py       聊天记录持久化（本地 JSON 文件）
│   ├── rag_service.py       知识库加载与检索
│   ├── wechat_api.py        微信 API（发消息、上传素材、access_token）
│   ├── crypto.py            微信消息加解密（安全模式）
│   ├── config.py            所有配置项（从 .env 环境变量读取）
│   ├── kb_tool.py           知识库管理命令行工具
│   ├── admin.html           客服管理后台（单文件，直接浏览器打开）
│   └── requirements.txt     Python 依赖
├── deploy/
│   ├── nginx.conf           Nginx 反向代理配置（含 WebSocket 支持）
│   ├── wechat-ai.service    systemd 服务配置
│   └── setup.sh             服务器一键初始化脚本
├── DEPLOY.md                服务器信息 & 快速部署命令
└── MAINTENANCE.md           完整运维手册
```

---

## 快速部署

### 前提条件

- 一台公网 Linux 服务器（Ubuntu 20.04+），已备案域名或直接用 IP
- 微信小程序已开通客服消息功能
- DeepSeek / OpenAI 等 AI 服务 API Key

### 1. 克隆仓库

```bash
git clone https://github.com/yanlinyi101/wx-ai-customer-service.git
cd wx-ai-customer-service
```

### 2. 服务器初始化（首次部署）

```bash
# 编辑脚本，填入你的域名和邮箱
nano deploy/setup.sh

# 上传代码到服务器
scp -r wechat_ai_service/* root@YOUR_SERVER_IP:/opt/wechat-ai/

# 在服务器上执行初始化
ssh root@YOUR_SERVER_IP "bash /opt/wechat-ai/deploy/setup.sh"
```

### 3. 配置环境变量

在服务器 `/opt/wechat-ai/.env` 中填写：

```env
# 微信小程序（mp.weixin.qq.com → 开发设置）
WECHAT_TOKEN=自定义字符串
WECHAT_APP_ID=小程序AppID
WECHAT_APP_SECRET=小程序AppSecret
WECHAT_ENCODING_AES_KEY=43位随机字符串

# AI 服务
AI_API_KEY=your_deepseek_api_key
AI_BASE_URL=https://api.deepseek.com
AI_MODEL=deepseek-chat

# 管理后台
ADMIN_TOKEN=自定义访问令牌（建议32位随机字符串）
ADMIN_OPENID=管理员微信openid（可选，用于转人工时推送通知）
```

### 4. 启动服务

```bash
systemctl restart wechat-ai
curl http://127.0.0.1:8000/health  # 返回 {"status":"ok"} 即成功
```

### 5. 微信后台配置

在 [mp.weixin.qq.com](https://mp.weixin.qq.com) → 开发 → 开发设置 → 消息推送：

- URL：`https://你的域名/webhook`
- Token：与 `WECHAT_TOKEN` 一致
- 加密方式：安全模式
- 消息加密密钥：与 `WECHAT_ENCODING_AES_KEY` 一致

### 6. 打开管理后台

用浏览器直接打开本地文件 `wechat_ai_service/admin.html`，填入服务器地址和 `ADMIN_TOKEN` 登录即可。

---

## 管理后台使用

| 操作 | 说明 |
|------|------|
| 等待列表 | 左侧显示所有请求转人工的用户，有新请求时弹出提示 |
| 查看历史 | 点击用户后加载完整 AI + 人工对话记录 |
| 发送回复 | 输入框输入，点击发送或 `Ctrl+Enter` |
| 结束会话 | 点击"结束会话"，用户恢复 AI 自动回复模式 |

---

## 知识库编辑

### 文件说明

| 文件 | 说明 |
|------|------|
| `knowledge_base_example.json` | **示例文件**，已提交至 Git，供参考格式使用 |
| `knowledge_base.json` | **业务数据文件**，含真实业务内容，已加入 `.gitignore` 不上传 |

### 首次初始化

```bash
cd wechat_ai_service

# 以示例文件为模板，创建自己的知识库
cp knowledge_base_example.json knowledge_base.json
```

### 条目格式

```json
{
  "question": "如何申请退款？",
  "answer": "支持收到商品后7天无理由退款，请在小程序「我的订单」中申请。",
  "keywords": ["退款", "退货", "不想要", "七天", "7天", "无理由"],
  "image_url": ""
}
```

| 字段 | 说明 |
|------|------|
| `question` | 问题描述，参与字符重叠评分 |
| `answer` | 标准答案，命中时原文注入 AI 提示词 |
| `keywords` | 触发词列表，每命中一词 +2 分；**宁多勿少，多写口语词和同义词** |
| `image_url` | 命中时附带发送的图片链接，无图留空 `""` |

### 命令行工具

```bash
cd wechat_ai_service
python kb_tool.py list            # 查看所有条目
python kb_tool.py add             # 交互式添加
python kb_tool.py delete <序号>   # 删除
python kb_tool.py export          # 导出为 Excel
python kb_tool.py import          # 从 Excel 批量导入
```

### 更新知识库后部署

```bash
# 上传到服务器
scp -i "zm_pc1.pem" wechat_ai_service/knowledge_base.json \
  root@SERVER_IP:/opt/wechat-ai/

# 重启服务（知识库在启动时加载）
ssh -i "zm_pc1.pem" root@SERVER_IP "systemctl restart wechat-ai"
```

---

## 技术栈

| 组件 | 选型 |
|------|------|
| Web 框架 | FastAPI + uvicorn |
| 实时通信 | WebSocket（FastAPI 原生） |
| HTTP 客户端 | httpx（异步） |
| 消息加解密 | pycryptodome（AES-256-CBC） |
| 部署 | 云服务器 + systemd + Nginx |
| AI 接口 | OpenAI 兼容格式（默认 DeepSeek） |
| 聊天记录 | 本地 JSON 文件（`/opt/wechat_chat_logs/`） |

---

## 切换 AI 服务商

修改服务器 `.env`，重启服务即可：

| 服务商 | `AI_BASE_URL` | `AI_MODEL` |
|--------|--------------|------------|
| DeepSeek（推荐） | `https://api.deepseek.com` | `deepseek-chat` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| Claude | `https://api.anthropic.com/v1` | `claude-haiku-4-5-20251001` |

---

## 文档

- [DEPLOY.md](./DEPLOY.md) — 服务器信息 & 快速部署命令参考
- [MAINTENANCE.md](./MAINTENANCE.md) — 完整运维手册（日志查看、知识库维护、故障排查等）

---

## License

MIT
