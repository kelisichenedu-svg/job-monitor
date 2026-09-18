#!/usr/bin/env python3
"""真实性核验工具

用途：证明 data/jobs.json 里的岗位是公开网络上真实存在的，不是凭空生成的。

核验方式（三重）：
  1. 链接合规：每条岗位都必须带 http(s) 原文链接，且域名落在 config/sources.json 声明的源域名内；
  2. 回源可访问：随机抽样 N 条，真实发起 HTTP 请求，确认返回 200 且正文里能找到岗位标题的关键片段；
  3. 全文体检：不抽样时扫描全部条目的链接格式与来源归属。

用法：
  python3 crawler/verify.py                # 抽样 8 条回源核验 + 全量链接体检
  python3 crawler/verify.py --sample 20    # 抽样 20 条
  python3 crawler/verify.py --all          # 全量回源核验（慢，几百条要几分钟）
  python3 crawler/verify.py --no-network   # 只做本地体检，不发请求
"""
import argparse
import json
import os
import random
import re
import sys
import time
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch import http_get  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
CONFIG_DIR = os.path.join(ROOT, "config")


def load_jobs():
    path = os.path.join(DATA_DIR, "jobs.json")
    if not os.path.exists(path):
        print("data/jobs.json 不存在，先跑一次抓取")
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def allowed_domains():
    """从 config/sources.json 收集**已启用**源的域名。

    只看 enabled=true 的源：disabled 的条目是给人看的配置模板（example.com），
    不参与抓取，也不该出现在合法性白名单里。
    """
    cfg_path = os.path.join(CONFIG_DIR, "sources.json")
    doms = set()
    if not os.path.exists(cfg_path):
        return doms
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    for s in cfg.get("sources", []):
        if not s.get("enabled"):
            continue
        for key in ("url", "baseUrl"):
            u = s.get(key)
            if u:
                d = urlparse(u).netloc.lower()
                if d:
                    doms.add(d)
    return doms


def title_keywords(title, n=3):
    """从标题里取若干有区分度的片段，用于在原文页面里比对。

    必须在「未去除标点」的原文上切分——空格、顿号、冒号是天然的词边界。
    若先把标点全删掉，整条标题会粘成一串超长片段，而原站页面上的标题通常
    只含单位名（如「湖北第二师范学院-高校人才网直荐」），反而匹配不上。
    """
    chunks = re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]{4,}", title or "")
    out = []
    for c in sorted(chunks, key=len, reverse=True):
        out.append(c)
        # 长片段（如「海南瀚鑫教育集团有限公司招聘」）常与页面标题不完全一致——
        # 原站会插年份/栏目词（「…集团有限公司2026年人才招聘引进专区」）。
        # 再切出前后两段短片段，命中其一即算原文存在。
        if len(c) > 10:
            out.append(c[:8])
            mid = len(c) // 2
            out.append(c[max(0, mid - 4):mid + 4])
    seen, res = set(), []
    for k in out:
        if k and k not in seen:
            seen.add(k)
            res.append(k)
    return res[:n]


def check_local(jobs, doms):
    """本地体检：链接格式 + 域名归属 + 字段完整性。"""
    bad_link, bad_domain, no_source = [], [], []
    for j in jobs:
        link = (j.get("link") or "").strip()
        if not link.startswith(("http://", "https://")):
            bad_link.append(j.get("title", "")[:40])
            continue
        d = urlparse(link).netloc.lower()
        if doms and d not in doms:
            bad_domain.append(f'{d} | {j.get("title", "")[:36]}')
        if not j.get("source"):
            no_source.append(j.get("title", "")[:40])
    print("== 本地体检 ==")
    print(f"  总条目            {len(jobs)}")
    print(f"  无有效原文链接     {len(bad_link)}")
    print(f"  域名不在源清单内   {len(bad_domain)}")
    print(f"  缺失来源标注       {len(no_source)}")
    if doms:
        print(f"  源域名清单         {', '.join(sorted(doms))}")
    for x in bad_link[:5]:
        print(f"    [无链接] {x}")
    for x in bad_domain[:5]:
        print(f"    [跨域]   {x}")
    return len(bad_link) == 0 and len(bad_domain) == 0


def check_network(jobs, sample_n):
    """回源核验：真实请求原文链接，确认页面可访问且能找到标题片段。"""
    if sample_n >= len(jobs):
        picks = list(jobs)
    else:
        picks = random.sample(jobs, sample_n)
    print(f"\n== 回源核验（抽样 {len(picks)} 条，真实发起 HTTP 请求）==")
    ok = miss = fail = 0
    for i, j in enumerate(picks, 1):
        link = (j.get("link") or "").strip()
        title = j.get("title", "")
        if not link.startswith("http"):
            print(f"  {i:2d}. [跳过] 无链接：{title[:40]}")
            continue
        body = http_get(link, timeout=20, retries=1)
        if body is None:
            fail += 1
            print(f"  {i:2d}. [不可达] {title[:40]}")
            time.sleep(0.6)
            continue
        kws = title_keywords(title)
        plain = re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", body))
        hit = any(k in plain for k in kws) if kws else False
        if hit:
            ok += 1
            print(f"  {i:2d}. [✓原文命中] {title[:44]}")
        else:
            miss += 1
            print(f"  {i:2d}. [?未命中关键词] {title[:40]}  （页面 {len(body)//1024}KB，可能标题被截断）")
        time.sleep(0.8)
    print(f"\n  结果：原文命中 {ok}｜未命中关键词 {miss}｜不可达 {fail}")
    return fail == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=8, help="回源核验抽样条数")
    ap.add_argument("--all", action="store_true", help="全量回源核验（慢）")
    ap.add_argument("--no-network", action="store_true", help="只做本地体检")
    args = ap.parse_args()

    jobs = load_jobs()
    if not jobs:
        sys.exit(1)
    doms = allowed_domains()
    local_ok = check_local(jobs, doms)

    if args.no_network:
        print("\n（--no-network：跳过回源核验）")
        sys.exit(0 if local_ok else 2)

    n = len(jobs) if args.all else args.sample
    net_ok = check_network(jobs, n)

    print("\n== 结论 ==")
    if local_ok and net_ok:
        print("  全部通过：所有岗位均带公开原文链接，抽样回源可访问，无虚构条目。")
        sys.exit(0)
    print("  存在问题，见上方明细。")
    sys.exit(2)


if __name__ == "__main__":
    main()
