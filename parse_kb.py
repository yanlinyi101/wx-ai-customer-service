#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
解析 百货品百问百答-知识库.md 生成 knowledge_base.json
"""
import re
import json
import sys

INPUT_FILE = "D:/小程序ai客服webhook/备份文件/百货品百问百答-知识库.md"
OUTPUT_FILE = "D:/小程序ai客服webhook/wechat_ai_service/knowledge_base.json"

# ---- 辅助函数 ----

def extract_image_url(text):
    """提取第一张图片的 URL（若有）"""
    m = re.search(r'!\[.*?\]\((https?://[^)]+)\)', text)
    return m.group(1) if m else ""

def clean_answer(text):
    """清洗 answer 文本：去除图片、链接、内部备注等"""
    lines = text.split('\n')
    cleaned = []
    skip_internal_block = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            # 空行不重置 skip_internal_block，C: 之后的所有内容都跳过
            if skip_internal_block:
                continue
            continue
        # 去掉内部备注行（C: 开头块，之后全部跳过直到下一个问题）
        if stripped.startswith('C：') or stripped.startswith('C:'):
            skip_internal_block = True
            continue
        if skip_internal_block:
            continue
        if stripped.startswith('⚠️'):
            continue
        if re.match(r'^#{1,6}\s', stripped):
            continue
        # 去掉占位符，如 （第二句）（第三句）等
        if re.match(r'^（第[一二三四五六七八九十\d]+句）$', stripped):
            continue
        # 去掉纯钉钉/内部链接行（整行是 [请至钉钉...] 类型）
        if re.match(r'^\[请至钉钉文档', stripped):
            continue
        # 去掉图片 markdown
        line = re.sub(r'!\[.*?\]\([^)]*\)', '', line)
        # 去掉钉钉/外链 → 整个链接行删除（不保留链接文字，避免无意义引用）
        line = re.sub(r'\[请至钉钉[^\]]*\]\([^)]*\)', '', line)
        # 其他 [text](url) → 保留 text
        line = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', line)
        # 去掉 $\color{...}{...}$ 格式的颜色标记
        line = re.sub(r'\$\\color\{[^}]*\}\{[^}]*\}\$', '', line)
        # 去掉 @人名 格式
        line = re.sub(r'@\S+', '', line)
        # 去掉 **...** 但保留内容
        line = re.sub(r'\*\*(.+?)\*\*', r'\1', line)
        # 去掉前缀 A：/A:
        line = re.sub(r'^[Aa][：:]\s*', '', line.strip())
        line = line.strip()
        if line:
            cleaned.append(line)
    return '\n'.join(cleaned).strip()

def extract_keywords(question, section):
    """从问题和分类中提取关键词"""
    keywords = set()
    # 加入分类（去掉前导数字和emoji）
    sec_clean = re.sub(r'^[0-9️⃣1️⃣2️⃣3️⃣4️⃣5️⃣6️⃣7️⃣8️⃣9️⃣🔟\s]+', '', section).strip()
    if sec_clean:
        keywords.add(sec_clean)

    # 提取问题中的名词短语（简单策略：2-8字的中文子串）
    q_clean = re.sub(r'[？?！!，,。.、：:]', ' ', question)
    words = q_clean.split()
    # 加入整个问题（去标点后的简化版）
    full_q = re.sub(r'\s+', '', q_clean).strip()
    if full_q:
        keywords.add(full_q)

    # 提取连续中文字符段
    cn_chunks = re.findall(r'[\u4e00-\u9fff]{2,8}', question)
    for c in cn_chunks:
        keywords.add(c)

    return sorted(keywords)

def parse_question_from_heading(heading_text):
    """从标题中提取问题文字，去掉 Q1: 等前缀"""
    # 去掉 Q1: / Q1： / Q1. 等前缀
    text = re.sub(r'^Q\d+[:.：.]\s*', '', heading_text.strip(), flags=re.IGNORECASE)
    # 去掉 ✅xxx 等标注
    text = re.sub(r'✅\S+', '', text).strip()
    return text.strip()


# ---- 主解析逻辑 ----

def parse_markdown(filepath):
    with open(filepath, encoding='utf-8') as f:
        content = f.read()

    lines = content.split('\n')

    entries = []

    # 状态
    current_h1 = ""       # # 一级标题（分类）
    current_h2 = ""       # ## 二级标题（可能是分类或问题）
    current_section = ""  # 用于 keywords 的分类

    current_question = None
    current_answer_lines = []
    internal_block_started = False  # 遇到 C: 后停止收集答案

    def flush_entry():
        """将当前积累的 Q&A 存为一个 entry"""
        if current_question is None:
            return
        raw_answer = '\n'.join(current_answer_lines)
        image_url = extract_image_url(raw_answer)
        answer = clean_answer(raw_answer)
        if not answer:
            return  # 没有实质答案的跳过
        keywords = extract_keywords(current_question, current_section)
        entries.append({
            "question": current_question,
            "answer": answer,
            "keywords": keywords,
            "image_url": image_url
        })

    i = 0
    while i < len(lines):
        line = lines[i]

        # 一级标题 → 更新分类，结束当前 Q&A
        if re.match(r'^# [^#]', line):
            flush_entry()
            current_question = None
            current_answer_lines = []
            internal_block_started = False
            current_h1 = line[2:].strip()
            current_section = current_h1
            i += 1
            continue

        # 二级标题
        if re.match(r'^## ', line):
            flush_entry()
            current_question = None
            current_answer_lines = []
            internal_block_started = False
            heading = line[3:].strip()

            # 判断是否是 Q&A 形式
            if re.match(r'^Q\d+', heading, re.IGNORECASE):
                current_question = parse_question_from_heading(heading)
                # 如果标题本身很短或看起来只是分类标题，需要判断
                # 简单判断：含 "?" "？" 或 "怎" "如何" "什么" "是否" 等
                if not current_question or len(current_question) < 2:
                    current_question = None
                    current_h2 = heading
                    current_section = current_h1
            else:
                # 非 Q 形式的二级标题 → 作为子分类
                current_h2 = heading
                current_section = heading if heading else current_h1
            i += 1
            continue

        # 三级标题
        if re.match(r'^### ', line):
            flush_entry()
            current_question = None
            current_answer_lines = []
            internal_block_started = False
            heading = line[4:].strip()

            if re.match(r'^Q\d+', heading, re.IGNORECASE):
                current_question = parse_question_from_heading(heading)
                if not current_question or len(current_question) < 2:
                    current_question = None
            else:
                current_section = heading if heading else current_h2
            i += 1
            continue

        # 四级及以下标题：当问题处理
        if re.match(r'^#{4,6} ', line):
            flush_entry()
            current_question = None
            current_answer_lines = []
            internal_block_started = False
            heading = re.sub(r'^#{4,6} ', '', line).strip()
            if re.match(r'^Q\d+', heading, re.IGNORECASE):
                current_question = parse_question_from_heading(heading)
            else:
                current_section = heading
            i += 1
            continue

        # 跳过内部备注
        stripped = line.strip()
        if stripped.startswith('⚠️'):
            i += 1
            continue
        if stripped.startswith('C：') or stripped.startswith('C:'):
            # C: 标记后的所有内容都是内部信息，停止收集答案
            internal_block_started = True
            i += 1
            continue

        # 如果当前有问题，且未进入内部块，收集答案行
        if current_question is not None and not internal_block_started:
            current_answer_lines.append(line)

        i += 1

    # 最后一个条目
    flush_entry()

    return entries


def main():
    print(f"解析: {INPUT_FILE}")
    entries = parse_markdown(INPUT_FILE)
    print(f"共解析 {len(entries)} 条 Q&A")

    # 过滤掉问题为空或过短的
    entries = [e for e in entries if e['question'] and len(e['question']) >= 3]
    print(f"过滤后 {len(entries)} 条")

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

    print(f"已写入: {OUTPUT_FILE}")

    # 打印前3条预览
    for e in entries[:3]:
        print("---")
        print("Q:", e['question'])
        print("A:", e['answer'][:80])
        print("KW:", e['keywords'][:5])
        print("IMG:", e['image_url'][:60] if e['image_url'] else "(none)")

if __name__ == '__main__':
    main()
