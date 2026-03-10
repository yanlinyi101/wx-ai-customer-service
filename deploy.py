"""
deploy.py — 一键部署到 VPS

用法：
    python deploy.py

功能：
    将 wechat_ai_service/ 下源码打包为 tar，通过单次 SSH 连接传输并解压，然后重启服务。
"""

import os
import subprocess
import sys
import tarfile
import tempfile

# ── 配置 ──────────────────────────────────────────────────────
SSH_KEY    = os.getenv("SSH_KEY",    r"D:\小程序ai客服webhook\zm_pc1.pem")
SSH_HOST   = os.getenv("SSH_HOST",   "root@YOUR_SERVER_IP")
REMOTE_DIR = os.getenv("REMOTE_DIR", "/opt/wechat-ai")
LOCAL_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wechat_ai_service")
SERVICE    = os.getenv("SERVICE",    "wechat-ai")

DEPLOY_FILES = [
    "main.py", "config.py", "ai_service.py", "rag_service.py",
    "human_service.py", "wechat_api.py", "crypto.py",
    "chat_logger.py", "cos_logger.py", "kb_tool.py",
    "admin.html", "requirements.txt",
]
# ──────────────────────────────────────────────────────────────

SSH_OPTS = ["-i", SSH_KEY, "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes"]


def run(cmd: str, desc: str) -> str:
    print(f"  {desc} ...")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        print(f"  [失败]\n{result.stderr.strip()}")
        sys.exit(1)
    return result.stdout.strip()


def build_tar() -> str:
    tmp = tempfile.mktemp(suffix=".tar.gz")
    with tarfile.open(tmp, "w:gz") as tar:
        for fname in DEPLOY_FILES:
            fpath = os.path.join(LOCAL_DIR, fname)
            if os.path.exists(fpath):
                tar.add(fpath, arcname=fname)
                print(f"    + {fname}")
            else:
                print(f"    - 跳过 {fname}（不存在）")
    size_kb = os.path.getsize(tmp) / 1024
    print(f"  打包完成：{tmp}（{size_kb:.1f} KB）")
    return tmp


def main():
    print("[1/3] 打包文件")
    tar_path = build_tar()

    # 用 scp 发送 tar
    print("\n[2/3] 上传到服务器")
    scp_cmd = f'scp -i "{SSH_KEY}" -o StrictHostKeyChecking=no -o BatchMode=yes "{tar_path}" {SSH_HOST}:/tmp/deploy.tar.gz'
    run(scp_cmd, "scp 上传")

    # SSH 解压 + 重启
    print("\n[3/3] 解压并重启服务")
    remote_cmd = (
        f"tar -xzf /tmp/deploy.tar.gz -C {REMOTE_DIR} && "
        f"rm /tmp/deploy.tar.gz && "
        f"systemctl restart {SERVICE} && "
        f"sleep 2 && curl -s http://127.0.0.1:8000/health"
    )
    ssh_cmd = f'ssh -i "{SSH_KEY}" -o StrictHostKeyChecking=no -o BatchMode=yes {SSH_HOST} "{remote_cmd}"'
    output = run(ssh_cmd, "解压 & 重启")
    print(f"  服务器响应：{output}")
    print("\n部署完成！")

    os.unlink(tar_path)


if __name__ == "__main__":
    main()
