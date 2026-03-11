"""
知识库短关键词补全脚本

问题：用户省略品牌名询问（如"鲜花饼多少钱"）时，
      知识库条目只有完整品名关键词（"潘祥记鲜花饼"），无法命中。

策略：
  对每个条目，找出「长品名关键词」（≥5字，全汉字），
  提取其有意义的 2~4 字后缀（即去掉品牌/产地前缀的产品名），
  若该后缀：
    1. 出现在本条目 question 中
    2. 不在本条目现有 keywords 中
    3. 不是通用泛化词（如：多久、价格、什么、怎么...）
    4. 在整个 KB 中仅覆盖当前同类条目（不超过 150 条）
  则自动补充为关键词。

运行：
  python fix_short_keywords.py          # dry-run，只打印不修改
  python fix_short_keywords.py --apply  # 实际修改并保存
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

KB_PATH = Path(__file__).parent / "wechat_ai_service" / "knowledge_base.json"

# ── 通用/无意义词黑名单（不应单独成为关键词）──
GENERIC_WORDS = {
    # 疑问/描述词
    '什么', '怎么', '如何', '哪里', '哪些', '多久', '多少', '多大', '是什么',
    '有什么', '是否', '可以', '能不', '为什么', '怎样', '几个', '几种',
    # 动词/通用
    '价格', '规格', '保存', '储存', '食用', '使用', '功效', '作用', '成分',
    '配料', '发货', '退款', '退货', '发票', '保质', '有效', '质量', '售后',
    # 字符/无意义后缀
    '多久', '期多', '几个', '有几', '里面', '里的', '一样', '不一', '怎了',
    '吗？', '呢？', '哦', '的吗', '吗', '呢', '吧', '呀',
    # 太短/太通用
    '产品', '商品', '食品', '药品', '礼盒', '组合', '套装', '件套',
    '报告', '检测', '品牌', '正品', '真假', '成分', '配方',
}

MAX_COVERAGE = 150   # 短词覆盖条数上限（超过则认为太通用）
MIN_COVERAGE = 2     # 短词覆盖条数下限
MIN_SUFFIX_LEN = 2   # 最短后缀长度
MAX_SUFFIX_LEN = 5   # 最长后缀长度（太长=还是品名全称）
MIN_KW_LEN = 5       # 触发提取的关键词最短长度

# 不能出现在关键词首尾的字符（动词/助词/连词，非名词）
BOUNDARY_CHARS = set('怎是有为了吗到前后与及在等外内上下中也还且而过再时地得被将把从让使向对否不料工加么一')
# 整个短词里不能含这些字（一出现就一定不是名词性产品名）
CONTENT_BLACKLIST = set('什否吗啊呢哦嗯吧呀哈噢')

# 整词黑名单（全词精确匹配，无论长短）
FULL_WORD_BLACKLIST = {
    '营业执照', '业执照', '执照', '生产日期', '产日期', '日期',
    '保质期多久', '质期多久', '期多久', '期多', '质期',
    '制作工艺', '作工艺', '工艺', '生产工艺',
    '食用方', '用方', '食用', '用法',
    '产地是哪里', '哪里', '哪里的', '哪些', '来源',
    '人群', '么人群', '合人群', '适合人群',
    '区别', '的区别', '皮的区别', '有区别',
    '口感', '感怎么样', '口感怎么样',
    '包装', '包装是',
    '卖点', '售卖',
    '不一样', '怎了',
    '尺码', '颜色', '么颜色',
}


def is_all_cjk(text: str) -> bool:
    return all('\u4e00' <= c <= '\u9fff' for c in text) and len(text) > 0


def is_valid_suffix(s: str) -> bool:
    """过滤掉首尾含动词/助词的片段，保留真正的名词性产品名"""
    if not s:
        return False
    if s[0] in BOUNDARY_CHARS or s[-1] in BOUNDARY_CHARS:
        return False
    # 整词含黑名单字符
    if any(c in CONTENT_BLACKLIST for c in s):
        return False
    # 整词精确黑名单
    if s in FULL_WORD_BLACKLIST:
        return False
    return True


def extract_suffixes(kw: str) -> list[str]:
    """从长关键词中提取有效后缀"""
    suffixes = []
    for length in range(MIN_SUFFIX_LEN, min(len(kw), MAX_SUFFIX_LEN + 1)):
        suffix = kw[-length:]
        if is_all_cjk(suffix) and suffix not in GENERIC_WORDS and suffix != kw and is_valid_suffix(suffix):
            suffixes.append(suffix)
    return suffixes


def main():
    apply = '--apply' in sys.argv

    with open(KB_PATH, 'r', encoding='utf-8') as f:
        kb = json.load(f)

    print(f"知识库加载：{len(kb)} 条\n")

    def cjk_only(text: str) -> str:
        """剔除 emoji / 数字等非汉字前缀，仅保留汉字"""
        return ''.join(c for c in text if '\u4e00' <= c <= '\u9fff')

    # ── Step 1：统计每个候选短词覆盖哪些条目 ──
    short_word_coverage: dict[str, set] = defaultdict(set)
    for idx, item in enumerate(kb):
        q = item['question']
        for kw in item.get('keywords', []):
            kw_cjk = cjk_only(kw)
            if len(kw_cjk) >= MIN_KW_LEN:
                for suffix in extract_suffixes(kw_cjk):
                    if suffix in q:
                        short_word_coverage[suffix].add(idx)

    # ── Step 2：对每个条目，判断哪些短词需要补充 ──
    total_added = 0
    entries_updated = 0

    for idx, item in enumerate(kb):
        q = item['question']
        existing_kws = set(item.get('keywords', []))
        to_add = set()

        for kw in list(existing_kws):
            kw_cjk = cjk_only(kw)
            if len(kw_cjk) >= MIN_KW_LEN:
                for suffix in extract_suffixes(kw_cjk):
                    if (suffix not in existing_kws          # 尚未存在
                            and suffix in q                  # 出现在问题中
                            and suffix not in GENERIC_WORDS  # 非通用词
                            and MIN_COVERAGE
                                <= len(short_word_coverage.get(suffix, set()))
                                <= MAX_COVERAGE):            # 覆盖范围合理
                        to_add.add(suffix)

        if to_add:
            entries_updated += 1
            total_added += len(to_add)
            # 短词排在最前，方便人工审查
            new_kws = sorted(to_add, key=len) + [k for k in item['keywords'] if k not in to_add]
            if apply:
                item['keywords'] = new_kws
            else:
                print(f"[DRY-RUN] #{idx+1} {q[:45]}")
                print(f"  +补充: {sorted(to_add)}")
                print(f"  原KW: {item['keywords'][:4]}")
                print()

    if apply:
        with open(KB_PATH, 'w', encoding='utf-8') as f:
            json.dump(kb, f, ensure_ascii=False, indent=2)
        print(f"已保存：更新 {entries_updated} 条，共补充 {total_added} 个短关键词")
    else:
        print(f"\n[DRY-RUN 汇总] 将更新 {entries_updated} 条，共补充 {total_added} 个短关键词")
        print("  运行 python fix_short_keywords.py --apply 正式修改")


if __name__ == '__main__':
    main()
