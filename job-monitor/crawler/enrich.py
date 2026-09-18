#!/usr/bin/env python3
"""岗位信息补全（回源取材，不做任何推测）

背景：高校人才网首页列表项把「单位名」和「公告标题」分成两个元素，抽取链接文本时
只能拿到"2026年博士后招聘公告 详情"这种残缺标题，单位缺失（显示"详见公告"）。
这类条目虽然链接真实，但用户不知道是哪家单位在招，可用性不合格。

本模块的做法：回源请求该岗位的详情页，从页面上**真实存在的** <title> / <h1> 里
取出完整标题与单位名补回去。

⚠️ 真实性约束：这里只做「搬运」和「切分」，不做「生成」——
   - 标题、单位名均逐字取自详情页 HTML 或原标题，不拼接、不改写、不补全用户没写的部分；
   - 单位名靠「前缀 + 单位后缀」确定性切分得到（如「年薪30万+中国民航大学」→「中国民航大学」），
     切不出来就保持原样（宁可留着残缺信息，也不填一个可能错的单位名）。

单独运行：
  python3 crawler/enrich.py                 # 回源补全单位缺失的条目
  python3 crawler/enrich.py --dry-run       # 只报告会改哪些，不写文件
  python3 crawler/enrich.py --rescore       # 不联网：重切单位名 + 重算分类/匹配度
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch import http_get  # noqa: E402
from normalize import (  # noqa: E402
    classify,
    clean_title,
    compute_score,
    detect_city,
    detect_direction,
    detect_province,
    detect_unit,
    detect_unit_tag,
    direction_bonus,
    get_tiers,
    is_noisy_unit,
    match_label,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")

# 详情页 <title> 尾部的站点名，剥掉才是干净标题
SITE_SUFFIX = re.compile(r"[-_—|]\s*(高校人才网\s*\|?\s*高才网|高才博士后|高校人才网|高才网)\s*$")

# 无信息量的单位占位值
EMPTY_UNIT = {"详见公告", "详见正文", "未命名", "", "无"}

# 详情页域名 → 编码。部分站点（如 boshihoujob.com）详情页为 GBK 且响应头不声明 charset，
# 默认 UTF-8 解码会乱码；按域名显式指定编码，保证 --enrich 回源补全不乱码。
DETAIL_ENCODING = {
    "boshihoujob.com": "gbk",
}


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def page_title(html):
    """取 <title> 并剥掉站点后缀。"""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if not m:
        return ""
    t = re.sub(r"&[a-zA-Z]+;", " ", m.group(1))
    t = re.sub(r"&#\d+;", " ", t)
    t = SITE_SUFFIX.sub("", t.strip())
    return re.sub(r"\s{2,}", " ", t).strip()


def page_h1(html):
    """取第一个 <h1> 的纯文本（原站 h1 恰好是完整标题或单位名）。"""
    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
    if not m:
        return ""
    t = re.sub(r"<[^>]+>", "", m.group(1))
    t = re.sub(r"&[a-zA-Z]+;", " ", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t


def looks_like_unit_detail(link):
    """单位招聘专区页（company/detail）：h1 就是单位名。"""
    return "/company/detail/" in (link or "")


def needs_enrich(job):
    """判断该条是否需要回源补全：单位缺失，或标题短得没有信息量。"""
    unit = (job.get("unit") or "").strip()
    title = (job.get("title") or "").strip()
    if unit in EMPTY_UNIT:
        return True
    # 标题里既没有单位名也没有具体岗位词，只有年份+泛称 → 也是残缺
    if len(title) < 14 and re.match(r"^20\d{2}年.*(招聘|公告|启事|简章)", title):
        return True
    return False


def enrich_one(job, profile=None):
    """回源补全一条。返回 (是否有改动, 变更说明)。"""
    link = job.get("link", "")
    enc = None
    for dom, e in DETAIL_ENCODING.items():
        if dom in link:
            enc = e
            break
    html = http_get(link, timeout=20, retries=1, encoding=enc)
    if not html:
        return False, "页面不可达，保持原样"

    title_page = page_title(html)
    h1 = page_h1(html)
    changed = []

    # 单位名：单位专区页的 h1 就是单位名（逐字取自页面）
    cur_unit = (job.get("unit") or "").strip()
    if looks_like_unit_detail(job.get("link", "")) and h1 and len(h1) <= 40:
        if cur_unit in EMPTY_UNIT:
            job["unit"] = h1
            changed.append(f"单位←{h1[:24]}")

    # 标题：优先用 <h1>（页面主标题），其次 <title>；只在更完整时替换
    cur_title = (job.get("title") or "").strip()
    for cand in (h1, title_page):
        if cand and len(cand) > len(cur_title) and cand != cur_title:
            job["title"] = cand
            changed.append(f"标题←{cand[:26]}…")
            break

    if not changed:
        return False, "页面无更完整信息，保持原样"

    apply_derived_fields(job, profile)
    return True, "；".join(changed)


def apply_derived_fields(job, profile):
    """按当前的 title / unit / description 重算所有派生字段。

    只读已有文本、只写派生结果，不新增任何原文没有的信息。
    补全单位后（例如补出「大学」→ 单位类型从「其他」变「高校」），
    分类与匹配度必须同步重算，否则会出现「显示为高校但分数没算高校加成」这类自相矛盾的展示。
    """
    # 标题里可能混着列表页的「单位类型」字段（「某研究院 自然与应用科研机构（事业单位类型） 2026…」）。
    # clean_title 只删以空白分隔的独立字段，不会动到真名里的「事业单位」，可重复执行。
    job["title"] = clean_title(job.get("title") or "")

    # 单位名缺失、或夹带营销语（「长期招聘！海南医科大学」）时，从标题里重切一次。
    # 只做「前缀+单位后缀」的确定性切分：标题里确实没有就保持原值——
    # 绝不用常识推测单位名称，也不把干净的单位名改坏。
    cur_unit = (job.get("unit") or "").strip()
    if cur_unit in ("", "详见公告") or is_noisy_unit(cur_unit):
        better = detect_unit(job.get("title", ""), job.get("description", ""))
        if better and better != "详见公告":
            job["unit"] = better
        elif cur_unit in ("", "详见公告"):
            job["unit"] = better

    text = " ".join(str(job.get(k) or "") for k in ("title", "unit", "description"))
    job["city"] = detect_city(text, job.get("unit", ""))
    job["province"] = detect_province(job["city"], text)
    job["unitTag"] = detect_unit_tag(text)
    job["direction"] = detect_direction(text)

    if not profile:
        return
    cat = classify(text, job.get("title", ""))
    if cat == "中等岗位" and job["unitTag"] in ("三甲医院", "二甲医院"):
        cat = "医院"
    if cat == "中等岗位" and job["unitTag"] in ("高校", "专科院校", "科研院所"):
        cat = "高校科研院所"
    job["category"] = cat
    job["matchScore"] = compute_score(text, job["city"], job["unitTag"], profile)
    job["matchLabel"] = match_label(job["matchScore"], get_tiers(profile))
    job["featured"] = job["matchScore"] >= profile.get("thresholds", {}).get("featuredScore", 82)
    job["coreDirection"] = direction_bonus(text, profile) >= 6


def run(jobs, dry_run=False, max_items=60, delay=0.8, profile=None):
    all_targets = [j for j in jobs if needs_enrich(j)]
    pending = len(all_targets)
    print(f"  待补全 {pending} 条（单位缺失/标题残缺）")
    if not all_targets:
        return 0, jobs

    targets = all_targets[:max_items]
    if len(targets) < pending:
        print(f"  本次最多处理 {max_items} 条，其余下次继续")

    fixed = 0
    for i, j in enumerate(targets, 1):
        try:
            ok, msg = enrich_one(j, profile)
        except Exception as e:  # noqa: BLE001
            ok, msg = False, f"异常：{e}"
        if ok:
            fixed += 1
            print(f"   {i:2d}. ✓ {j.get('title','')[:44]} ｜ {msg}")
        else:
            print(f"   {i:2d}. · {msg}：{(j.get('title') or '')[:36]}")
        time.sleep(delay)

    print(f"  补全完成：{fixed} 条已更新")
    return fixed, jobs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只报告不写文件")
    ap.add_argument("--max", type=int, default=60, help="本次最多处理条数")
    ap.add_argument("--rescore", action="store_true",
                    help="不联网，只按当前字段重算全部派生字段（地域/分类/匹配度）")
    args = ap.parse_args()

    path = os.path.join(DATA_DIR, "jobs.json")
    meta_path = os.path.join(DATA_DIR, "meta.json")
    jobs = load_json(path, [])
    if not jobs:
        print("data/jobs.json 为空，先跑 crawler/crawl.py")
        sys.exit(1)
    profile = load_json(os.path.join(ROOT, "config", "profile.json"), {})

    if args.rescore:
        print("▶ 按当前字段重算派生信息（不联网、不改标题与单位）")
        before = [(j.get("id"), j.get("matchScore"), j.get("category")) for j in jobs]
        # 顺带用最新的清洗规则过一遍历史标题（只去掉「详情」这类按钮文字等噪声，不改语义）
        cleaned = 0
        for j in jobs:
            t = clean_title(j.get("title") or "")
            if t and t != j.get("title"):
                j["title"] = t
                cleaned += 1
        if cleaned:
            print(f"  标题清洗：{cleaned} 条去掉了残留噪声文字")
        for j in jobs:
            apply_derived_fields(j, profile)
        after = [(j.get("id"), j.get("matchScore"), j.get("category")) for j in jobs]
        changed_n = sum(1 for a, b in zip(before, after) if a != b)
        print(f"  {len(jobs)} 条中 {changed_n} 条的匹配度/分类发生变化")
        if not args.dry_run:
            jobs.sort(key=lambda x: x.get("matchScore", 0), reverse=True)
            top_n = int(profile.get("thresholds", {}).get("featuredTopN", 12))
            for j in jobs[:top_n]:
                j["featured"] = True
            save_json(path, jobs)

            meta = load_json(meta_path, {})
            if meta:
                meta["total"] = len(jobs)
                meta["coreDirection"] = len([j for j in jobs if j.get("coreDirection")])
                meta["highMatch"] = len([j for j in jobs if j.get("matchScore", 0) >= 88])
                save_json(meta_path, meta)
            print(f"  已写回 {path}")
        return

    print("▶ 回源补全岗位信息（取材自详情页原文，不做推测）")
    fixed, jobs = run(jobs, dry_run=args.dry_run, max_items=args.max, profile=profile)

    if not args.dry_run and fixed:
        # 补全导致分数变化后，重新应用「排名前 N 必进今日推荐」兜底，
        # 否则该栏目可能因阈值线移动而变空。
        jobs.sort(key=lambda x: x.get("matchScore", 0), reverse=True)
        top_n = int(profile.get("thresholds", {}).get("featuredTopN", 12))
        for j in jobs[:top_n]:
            j["featured"] = True
        save_json(path, jobs)

        meta = load_json(meta_path, {})
        if meta:
            meta["total"] = len(jobs)
            meta["coreDirection"] = len([j for j in jobs if j.get("coreDirection")]) or meta.get("coreDirection", 0)
            save_json(meta_path, meta)
        print(f"  已写回 {path}")


if __name__ == "__main__":
    main()
