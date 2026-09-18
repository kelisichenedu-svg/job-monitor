#!/usr/bin/env python3
"""把抓取到的数据注入模板，生成可直接打开/部署的单文件 dist/index.html。

用法：
  python3 site/build.py
  python3 site/build.py --out dist/index.html
"""
import argparse
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build(out_path):
    tpl_path = os.path.join(ROOT, "site", "template.html")
    with open(tpl_path, "r", encoding="utf-8") as f:
        html = f.read()

    jobs = load(os.path.join(ROOT, "data", "jobs.json"), [])
    meta = load(os.path.join(ROOT, "data", "meta.json"), {})
    profile = load(os.path.join(ROOT, "config", "profile.json"), {})

    jobs_json = json.dumps(jobs, ensure_ascii=False)
    meta_json = json.dumps(meta, ensure_ascii=False)
    th = profile.get("thresholds", {})
    star = th.get("starScore", 90)
    tiers = th.get("matchTiers", {}) or {}
    tiers_json = json.dumps({
        "excellent": int(tiers.get("excellent", 85)),
        "high": int(tiers.get("high", 75)),
        "mid": int(tiers.get("mid", 65)),
    })

    # 用正则匹配占位符（不写死默认值），这样改了 profile 的默认值也不会静默失配
    def fill(pattern, value, label):
        nonlocal html
        html, n = re.subn(pattern, lambda _m: value, html, count=1)
        if n == 0:
            raise SystemExit(f"模板缺少 {label} 占位符")
        return n

    fill(r"/\*__JOBS_DATA__\*/\s*\[\]", jobs_json, "/*__JOBS_DATA__*/")
    fill(r"/\*__META_DATA__\*/\s*\{\}", meta_json, "/*__META_DATA__*/")
    fill(r"/\*__STAR_SCORE__\*/\s*\d+", str(star), "/*__STAR_SCORE__*/")
    fill(r"/\*__TIERS_DATA__\*/\s*\{[^{}]*\}", tiers_json, "/*__TIERS_DATA__*/")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    size_kb = os.path.getsize(out_path) / 1024
    print(f"已生成 {out_path}｜{len(jobs)} 个岗位｜{size_kb:.1f} KB")
    return len(jobs)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "dist", "index.html"))
    args = ap.parse_args()
    build(args.out)
