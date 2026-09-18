#!/usr/bin/env python3
"""君来暴富 · 招聘监控抓取主程序

流程：
  加载配置 → 遍历启用的源抓取 → 相关性过滤 → 原文链接校验 → 归一化(分类/打分) →
  指纹去重 → 合并历史未过期数据 → 写 data/jobs.json

真实性红线（不可绕过）：
  1. 系统只收录公开网络上真实抓取到的岗位，不生成、不推测、不补全任何虚构岗位；
  2. 每条入库岗位必须携带 http(s) 原文链接，无链接者一律丢弃（见 --rebuild 后的日志）；
  3. 抓不到就是空页，绝不用示例数据填充。

用法：
  python3 crawler/crawl.py                # 正常抓取
  python3 crawler/crawl.py --dry-run      # 只打印统计，不写文件
  python3 crawler/crawl.py --rebuild      # 忽略历史，按最新规则全量重建
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fetch as fetcher  # noqa: E402
from normalize import (  # noqa: E402
    normalize,
    is_expired,
    fingerprint,
    apply_featured,
    target_provinces,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(ROOT, "config")
DATA_DIR = os.path.join(ROOT, "data")

# 相关性门槛：至少要命中其中一个，否则判定为噪声丢弃
RELEVANT_ANY = [
    "肿瘤", "癌症", "oncolog", "cancer", "博士", "博士后", "postdoc", "PhD",
    "医院", "hospital", "大学", "university", "学院", "研究院", "研究所",
    "科研", "教职", "讲师", "教授", "研究员", "生物", "医药", "制药",
    "编制", "人才引进", "招聘",
]


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 读取失败 {path}: {e}")
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


# 硬性噪声：不管标题里有没有「博士后」这类词，命中即丢（已下线岗位 / 线下活动）
HARD_BLOCK = ["已下线", "已结束", "已过期", "现场对接", "可现场", "对接会", "峰会", "双选会", "招聘会"]

# 软噪声：招聘会、双选会、倒计时、展会……不是具体岗位
BLOCK_KWS = ["专场", "倒计时", "引才活动", "展会", "直播", "巡展", "参会", "报名"]
# 命中这些说明确实是岗位公告，优先级高于上面的噪声词
KEEP_OVERRIDE = ["招聘公告", "招聘启事", "公开招聘", "招收", "博士后", "招聘简章", "诚聘", "引进公告"]


def is_relevant(text):
    low = text.lower()
    return any(k.lower() in low for k in RELEVANT_ANY)


# 资讯/分析类文章不是岗位：疑问句、对比体、盘点体
NEWS_RE = re.compile(
    r"[？?]|VS|vs|双城记|告诉你|谁在|盘点|观察|解读|榜单|一文看懂|深度|条真实岗位|注意|提醒|政策解读"
)

# URL 路径判据：原站的 /news/ 是资讯频道，页面真实存在但不是招聘岗位。
# 只收录 announcement（公告）/ company（单位招聘专区）/ job（职位）三类详情页。
NON_JOB_PATH = re.compile(r"/news/|/daily/|/article/|/zt/|/special/")

# 合法岗位详情页路径（命中其一才算岗位，避免侧边栏推荐链接混入）
JOB_PATH = re.compile(r"/(announcement|company|job)/detail/")


def is_noise(title, link=""):
    t = title or ""
    if any(k in t for k in HARD_BLOCK):   # 已下线岗位、线下活动：无条件丢弃
        return True
    if NEWS_RE.search(t):                 # 资讯分析文
        return True
    if link:
        if NON_JOB_PATH.search(link):     # 资讯频道链接：不是岗位
            return True
        if not JOB_PATH.search(link):     # 既不是公告/单位/职位详情页，视为侧边栏噪声
            return True
    if any(k in t for k in KEEP_OVERRIDE):
        return False
    return any(k in t for k in BLOCK_KWS)


def _richness(job):
    """衡量一条记录的信息完整度：同指纹归并时用，挑「信息最全」的那条留下来。

    优先级：单位名完整（不是「详见公告」）> 单位名更长 > 有描述 > 匹配度更高。
    这样多源抓到的同一岗位不会随机留一条残缺的。
    """
    unit = (job.get("unit") or "").strip()
    return (
        0 if unit in ("", "详见公告", "详见正文", "未命名") else 1,
        len(unit),
        1 if (job.get("description") or "").strip() else 0,
        int(job.get("matchScore") or 0),
        len(job.get("title") or ""),   # 同分时选标题更长的（信息量更大）
    )


def next_id(jobs):
    max_id = 0
    for j in jobs:
        try:
            max_id = max(max_id, int(str(j.get("id", "0")).replace("auto-", "")))
        except Exception:  # noqa: BLE001
            pass
    return max_id + 1


def run(dry_run=False, rebuild=False):
    profile = load_json(os.path.join(CONFIG_DIR, "profile.json"), {})
    sources_cfg = load_json(os.path.join(CONFIG_DIR, "sources.json"), {"sources": [], "request": {}})
    history = [] if rebuild else load_json(os.path.join(DATA_DIR, "jobs.json"), [])
    if rebuild:
        print("  [--rebuild] 忽略历史数据，按最新规则全量重建")

    req_cfg = sources_cfg.get("request", {})
    sources = [s for s in sources_cfg.get("sources", []) if s.get("enabled")]

    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 启动抓取｜启用源 {len(sources)} 个｜历史 {len(history)} 条")

    raw_records = []
    if not sources:
        print("  [警告] config/sources.json 里没有启用任何源，本次不会有数据")
    for src in sources:
        print(f"  → 抓取 {src.get('name')} ({src.get('type')}) ...")
        recs = fetcher.fetch_source(src, req_cfg)
        print(f"    得到 {len(recs)} 条原始记录")
        raw_records.extend(recs)
        time.sleep(req_cfg.get("delaySeconds", 1.5))

    # 相关性过滤
    kept = []
    dropped = 0
    for rec in raw_records:
        text = " ".join(str(rec.get(k) or "") for k in ("title", "summary"))
        if is_relevant(text) and not is_noise(rec.get("title", ""), rec.get("link", "")):
            kept.append(rec)
        else:
            dropped += 1
    # 真实性校验：没有可点击原文链接的一律丢弃（保证每条岗位都能点回公开来源核验）
    before_link = len(kept)
    kept = [r for r in kept if str(r.get("link", "")).startswith(("http://", "https://"))]
    no_link = before_link - len(kept)
    if no_link:
        print(f"  无原文链接丢弃 {no_link} 条（不可核验的条目不入库）")
    print(f"  相关性过滤：保留 {len(kept)} 条，丢弃 {dropped} 条噪声")

    # 本系统不生成任何虚构岗位：抓不到就是空，宁可空页也不造假。
    # 但「源全部失败」不等于「世上没有岗位」——此前真实抓到的历史数据依然有效，
    # 此时保留历史并标记 stale，避免一次网络抖动把真实数据清空。
    stale = False
    if not kept and history:
        stale = True
        print("  [警告] 本次未抓到任何可核验的真实岗位（可能是源故障/网络抖动）")
        print(f"         保留上次真实抓到的 {len(history)} 条，标记为「未刷新」，不写入新数据")

        # 更新 meta 的 stale 标记后直接返回，jobs.json 保持原样
        meta_path = os.path.join(DATA_DIR, "meta.json")
        prev_meta = load_json(meta_path, {})
        prev_meta["total"] = len(history)
        prev_meta["stale"] = True
        prev_meta["staleAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not dry_run:
            save_json(meta_path, prev_meta)
        return prev_meta

    # 已有指纹（历史 + 本次）。**一律重算**，不用存储值——
    # 指纹算法修正过（早期版本错误地把可变的 unit 算了进去），存值可能是旧算法的，
    # 直接信任会让去重静默失效。
    for j in history:
        j["fingerprint"] = fingerprint(j.get("title", ""))
    seen_fp = {j["fingerprint"] for j in history}

    new_jobs = []
    counter = next_id(history) if history else 1
    # 地区闸门：只收录目标省份（config/profile.json → targetProvinces，默认广东/福建/浙江/江苏）。
    # 省份推断不出来的也一律不收——宁可少而准，也不把外省岗位混进来。
    target_provs = target_provinces(profile)
    off_region = 0
    for rec in kept:
        job = normalize(rec, profile, counter)
        if target_provs and job.get("province") not in target_provs:
            off_region += 1
            continue
        if job["fingerprint"] in seen_fp:
            continue
        seen_fp.add(job["fingerprint"])
        new_jobs.append(job)
        counter += 1
    if off_region:
        print(f"  地区过滤：丢弃 {off_region} 条非目标省份（{ '/'.join(sorted(target_provs)) }）的岗位")

    # 合并：历史中去重、去过期、过地区闸门（重建范围时一并清除历史里的外省数据）
    expire_days = profile.get("thresholds", {}).get("expireDays", 90)
    keep_days = profile.get("thresholds", {}).get("keepDays", 180)
    merged = []
    hist_fp = set()
    expired_count = 0
    hist_off_region = 0
    for j in history:
        fp = j["fingerprint"]
        if fp in hist_fp:
            continue
        hist_fp.add(fp)
        if is_expired(j.get("deadline", ""), expire_days):
            expired_count += 1
            continue
        if target_provs and j.get("province") not in target_provs:
            hist_off_region += 1
            continue
        merged.append(j)
    if hist_off_region:
        print(f"  地区过滤：历史数据中移除 {hist_off_region} 条非目标省份岗位")

    # 新数据里剔除与历史重复的
    new_jobs = [j for j in new_jobs if j["fingerprint"] not in hist_fp]

    # 两层去重，缺一不可：
    #   ① 按指纹（归一化标题）——抓跨栏目重复：同一公告在不同栏目下 URL 不同，但标题一致；
    #   ② 按 link——抓同页面重复：同一页面被不同来源用不同链接文本抽出来，
    #      标题会各不相同（「XX中心 2026年全球人才招聘」「XX中心2026年人才招聘引进专区」「XX中心」），
    #      指纹拦不住，只能靠 link 归并。
    # 每层都保留「信息最全」的那条。
    def _dedup(items, keyfn):
        best = {}
        for j in items:
            k = keyfn(j)
            if not k:
                continue
            cur = best.get(k)
            if cur is None or _richness(j) > _richness(cur):
                best[k] = j
        return list(best.values())

    all_jobs = _dedup(merged + new_jobs, lambda j: j["fingerprint"])
    all_jobs = _dedup(all_jobs, lambda j: j.get("link") or "")
    all_jobs.sort(key=lambda x: x.get("matchScore", 0), reverse=True)

    # 「今日推荐」唯一出口：只在该栏目范围内（默认广东、福建）按分数 + 排名前 N 兜底重算。
    # 真实岗位标题信息量远少于带正文的公告，绝对分数阈值常整批打不上，
    # 因此除分数阈值外，再保证范围排名前 N 的一定进今日推荐，避免该栏目空白。
    featured_n = apply_featured(all_jobs, profile)
    print(f"  今日推荐：{featured_n} 条（仅广东/福建）")

    # 「新增」要按**归并之后**的结果统计：new_jobs 里的一部分会被 link 去重合并掉
    # （同一页面被不同来源用不同标题抽出来），用 len(new_jobs) 会虚高，
    # 让人误以为去重坏了。判据用 id 是否在历史里出现过。
    hist_ids = {j.get("id") for j in history}
    hist_links = {j.get("link") for j in history if j.get("link")}
    real_new = len([
        j for j in all_jobs
        if j.get("id") not in hist_ids and j.get("link") not in hist_links
    ])

    stats = {
        "total": len(all_jobs),
        "new": real_new,
        "expiredRemoved": expired_count,
        "noiseRemoved": dropped,
        "provinces": len({j.get("province") for j in all_jobs if j.get("province") != "未知"}),
        "updatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sourcesActive": len(sources),
        "stale": False,
        "highMatch": len([j for j in all_jobs if j.get("matchScore", 0) >= 88]),
        "coreDirection": len([j for j in all_jobs if j.get("coreDirection")]),
    }

    print(f"  汇总：总计 {stats['total']}｜新增 {stats['new']}｜清理过期 {expired_count}")
    print(f"  覆盖省份 {stats['provinces']} 个｜极高匹配 {stats['highMatch']} 条")

    if not dry_run:
        save_json(os.path.join(DATA_DIR, "jobs.json"), all_jobs)
        save_json(os.path.join(DATA_DIR, "meta.json"), stats)
        print(f"  已写入 data/jobs.json")
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只跑流程不写文件")
    ap.add_argument("--rebuild", action="store_true", help="忽略历史数据全量重建（改了打分规则后用）")
    args = ap.parse_args()
    run(dry_run=args.dry_run, rebuild=args.rebuild)
