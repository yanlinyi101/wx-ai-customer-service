#!/bin/bash
# 微信 AI 客服 — 服务器一键部署脚本
# 使用前请修改以下两个变量
DOMAIN="YOUR_DOMAIN"         # 例如：ai.example.com
EMAIL="YOUR_EMAIL"           # Let's Encrypt 邮箱

set -e

echo "==> [1/7] 安装系统依赖..."
apt-get update -qq
apt-get install -y -qq python3.11 python3.11-venv nginx certbot python3-certbot-nginx

echo "==> [2/7] 创建应用目录..."
mkdir -p /opt/wechat-ai

echo "==> [3/7] 复制代码文件..."
# 请先在本机执行（将 wechat_ai_service/ 目录内容上传到服务器）：
#   scp -r wechat_ai_service/* ubuntu@SERVER_IP:/opt/wechat-ai/
echo "    !! 请确保已通过 scp 将代码复制到 /opt/wechat-ai/"
echo "    !! 如果尚未完成，请 Ctrl+C 中断，上传后重新运行"
read -r -p "    继续？(y/N) " confirm
[[ "$confirm" =~ ^[Yy]$ ]] || exit 0

echo "==> [4/7] 创建虚拟环境 + 安装依赖..."
cd /opt/wechat-ai
python3.11 -m venv venv
venv/bin/pip install --quiet -r requirements.txt

echo "==> [5/7] 配置 nginx..."
# 替换域名占位符
sed "s/YOUR_DOMAIN/${DOMAIN}/g" /opt/wechat-ai/deploy/nginx.conf \
    > /etc/nginx/sites-available/wechat-ai
ln -sf /etc/nginx/sites-available/wechat-ai /etc/nginx/sites-enabled/wechat-ai
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo "==> [6/7] 申请 SSL 证书（Let's Encrypt）..."
certbot --nginx -d "${DOMAIN}" --non-interactive --agree-tos -m "${EMAIL}"

echo "==> [7/7] 配置并启动 systemd 服务..."
cp /opt/wechat-ai/deploy/wechat-ai.service /etc/systemd/system/wechat-ai.service
systemctl daemon-reload
systemctl enable wechat-ai
systemctl start wechat-ai

echo ""
echo "==> 部署完成！"
echo "    健康检查：curl https://${DOMAIN}/health"
echo "    查看日志：journalctl -u wechat-ai -f"
echo "    重启服务：systemctl restart wechat-ai"
echo ""
echo "    微信后台 Webhook URL 改为：https://${DOMAIN}/webhook"
