#!/usr/bin/env python3
"""
日志文件迁移脚本（一次性执行）

旧路径：{LOG_DIR}/{openid}.json
新路径：{LOG_DIR}/default/{openid}.json

将 LOG_DIR 根目录下的所有 .json 文件移入 default/ 子目录，
同时在 JSON 内容中补充 "app_id": "default" 字段。

用法：
    cd wechat_ai_service
    python migrate_logs.py [--dry-run]

--dry-run：只打印操作，不实际移动文件。
"""

import argparse
import json
import sys
from pathlib import Path

# 从 config 读取 LOG_DIR，也可直接在命令行传入
try:
    from config import LOG_DIR
except ImportError:
    LOG_DIR = "chat_logs"

_log_dir = Path(LOG_DIR)


def migrate(dry_run: bool) -> None:
    if not _log_dir.exists():
        print(f"[migrate] LOG_DIR 不存在：{_log_dir}，无需迁移。")
        return

    # 只处理根目录（非子目录）下的 .json 文件
    json_files = [p for p in _log_dir.iterdir() if p.is_file() and p.suffix == ".json"]

    if not json_files:
        print("[migrate] 根目录下没有 .json 文件，无需迁移。")
        return

    target_dir = _log_dir / "default"
    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)

    moved = 0
    skipped = 0

    for src in json_files:
        dst = target_dir / src.name

        if dst.exists():
            print(f"[skip]   {src.name} → 目标已存在，跳过")
            skipped += 1
            continue

        try:
            data = json.loads(src.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"[error]  {src.name} 读取失败：{e}，跳过")
            skipped += 1
            continue

        # 补充 app_id 字段（若尚未存在）
        if "app_id" not in data:
            data["app_id"] = "default"

        print(f"[move]   {src} → {dst}")
        if not dry_run:
            tmp = dst.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(dst)
            src.unlink()

        moved += 1

    print(f"\n迁移完成：移动 {moved} 个文件，跳过 {skipped} 个文件。")
    if dry_run:
        print("（dry-run 模式，未实际修改任何文件）")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="迁移日志文件到 default/ 子目录")
    parser.add_argument("--dry-run", action="store_true", help="仅打印操作，不实际移动文件")
    args = parser.parse_args()

    migrate(dry_run=args.dry_run)
    sys.exit(0)
