# 部署手册 — 微信AI客服 Webhook

## 服务器信息

| 项目 | 值 |
|------|-----|
| IP | `YOUR_SERVER_IP` |
| 用户 | `root` |
| SSH 密钥 | `D:/小程序ai客服webhook/zm_pc1.pem` |
| 代码目录 | `/opt/wechat-ai/` |
| 服务名 | `wechat-ai` |
| 本地代码 | `D:/小程序ai客服webhook/wechat_ai_service/` |

---

## 快速部署（代码有改动时）

### 第一步：上传文件

```bash
scp -i "D:/小程序ai客服webhook/zm_pc1.pem" -o StrictHostKeyChecking=no \
  "D:/小程序ai客服webhook/wechat_ai_service/main.py" \
  "D:/小程序ai客服webhook/wechat_ai_service/human_service.py" \
  "D:/小程序ai客服webhook/wechat_ai_service/chat_logger.py" \
  "D:/小程序ai客服webhook/wechat_ai_service/ai_service.py" \
  "D:/小程序ai客服webhook/wechat_ai_service/config.py" \
  "D:/小程序ai客服webhook/wechat_ai_service/wechat_api.py" \
  "D:/小程序ai客服webhook/wechat_ai_service/rag_service.py" \
  "D:/小程序ai客服webhook/wechat_ai_service/knowledge_base.json" \
  "D:/小程序ai客服webhook/wechat_ai_service/admin.html" \
  root@YOUR_SERVER_IP:/opt/wechat-ai/
```

### 第二步：重启服务

```bash
ssh -i "D:/小程序ai客服webhook/zm_pc1.pem" -o StrictHostKeyChecking=no \
  root@YOUR_SERVER_IP "systemctl restart wechat-ai"
```

### 第三步：验证

```bash
ssh -i "D:/小程序ai客服webhook/zm_pc1.pem" -o StrictHostKeyChecking=no \
  root@YOUR_SERVER_IP "curl -s http://127.0.0.1:8000/health"
```

返回 `{"status":"ok"}` 即成功。

---

## 常用运维命令

以下命令均通过 SSH 在服务器上执行：

```bash
# SSH 连接
ssh -i "D:/小程序ai客服webhook/zm_pc1.pem" root@YOUR_SERVER_IP

# 查看服务状态
systemctl status wechat-ai

# 查看实时日志
journalctl -u wechat-ai -f

# 查看最近 100 行日志
journalctl -u wechat-ai -n 100

# 重启服务
systemctl restart wechat-ai

# 停止 / 启动
systemctl stop wechat-ai
systemctl start wechat-ai
```

---

## 安装新依赖（requirements.txt 有改动时）

```bash
ssh -i "D:/小程序ai客服webhook/zm_pc1.pem" -o StrictHostKeyChecking=no \
  root@YOUR_SERVER_IP \
  "cd /opt/wechat-ai && venv/bin/pip install -r requirements.txt && systemctl restart wechat-ai"
```

---

## 目录结构（服务器 /opt/wechat-ai/）

```
/opt/wechat-ai/
├── main.py            # FastAPI 主入口
├── human_service.py   # 人工客服状态管理（纯内存）
├── chat_logger.py     # 对话日志（COS）
├── ai_service.py      # AI 回复逻辑
├── config.py          # 环境变量读取
├── wechat_api.py      # 微信 API 调用
├── rag_service.py     # 知识库检索
├── knowledge_base.json
├── admin.html         # 客服管理后台
├── .env               # 环境变量（不上传，服务器本地维护）
└── venv/              # Python 虚拟环境
```

---

## 环境变量（服务器 /opt/wechat-ai/.env）

> 此文件只存在于服务器，不在本地代码库，修改后需重启服务。

```bash
# 在服务器上编辑
ssh -i "D:/小程序ai客服webhook/zm_pc1.pem" root@YOUR_SERVER_IP "nano /opt/wechat-ai/.env"
```

关键变量参考 `wechat_ai_service/腾讯云部署说明.md` 第三章。

---

## 注意事项

- `zm_pc1.pem` 权限如报错，执行：`chmod 600 "D:/小程序ai客服webhook/zm_pc1.pem"`（Linux/Mac）或在 Windows 上确保只有当前用户有读权限
- 服务使用 **单 worker**（`--workers 1`），人工客服状态存于内存，多 worker 会导致状态不共享
- Nginx 反向代理已配置，外部通过域名/IP 访问，内部转发到 `127.0.0.1:8000`
