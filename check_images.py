#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查 MD 中每个 Q 的图片数量是否与 KB 一致"""
import re, json, sys, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

IMG_PAT = re.compile(r'!\[.*?\]\((https?://[^)]+)\)')
EXCLUDED = ['旧系统', '新系统', '400百问百答']

with open('D:/小程序ai客服webhook/备份文件/百货品百问百答-知识库.md', encoding='utf-8') as f:
    lines = f.readlines()

with open('D:/小程序ai客服webhook/wechat_ai_service/knowledge_base.json', encoding='utf-8') as f:
    kb = json.load(f)

kb_map = {e['question']: e for e in kb}

blocks = []
current_q = None
current_lines = []
in_excluded = False
in_c_block = False

for line in lines:
    raw = line.rstrip('\n')
    stripped = raw.strip()

    if re.match(r'^#\s+\S', raw):
        if current_q:
            blocks.append((current_q, current_lines[:]))
        current_q = None; current_lines = []; in_c_block = False
        title = re.sub(r'^#\s+', '', stripped)
        in_excluded = any(kw in title for kw in EXCLUDED)
        continue

    if in_excluded:
        continue

    if re.match(r'^#{1,4}\s+Q\d+[：:]', stripped, re.IGNORECASE):
        if current_q:
            blocks.append((current_q, current_lines[:]))
        q_text = re.sub(r'^#{1,4}\s+Q\d+[：:]\s*', '', stripped).strip()
        current_q = q_text; current_lines = []; in_c_block = False
        continue

    if re.match(r'^#{1,6}\s+', raw):
        if current_q:
            blocks.append((current_q, current_lines[:]))
        current_q = None; current_lines = []; in_c_block = False
        continue

    if current_q is None:
        continue

    # Stop collecting at C: block
    if re.match(r'^C[：:]', stripped):
        in_c_block = True
        continue

    if in_c_block:
        continue

    current_lines.append(raw)

if current_q:
    blocks.append((current_q, current_lines[:]))

total_with_img = 0
mismatches = []

for q_text, ans_lines in blocks:
    md_imgs = []
    for l in ans_lines:
        md_imgs.extend(IMG_PAT.findall(l))
    if not md_imgs:
        continue
    total_with_img += 1

    kb_entry = kb_map.get(q_text)
    if not kb_entry:
        continue

    kb_urls = kb_entry.get('image_urls') or (
        [kb_entry['image_url']] if kb_entry.get('image_url') else []
    )
    if len(md_imgs) != len(kb_urls):
        mismatches.append((q_text, md_imgs, kb_urls))

print(f'MD 中有图片的 Q 块总数: {total_with_img}')
print(f'图片数量不一致的条目: {len(mismatches)}')
print()

for q, md_imgs, kb_urls in mismatches[:30]:
    print(f'Q: {q[:60]}')
    print(f'  MD {len(md_imgs)} 张 -> KB {len(kb_urls)} 张')
    if len(md_imgs) > len(kb_urls):
        missing = set(md_imgs) - set(kb_urls)
        print(f'  缺失: {list(missing)[:2]}')
    print()
