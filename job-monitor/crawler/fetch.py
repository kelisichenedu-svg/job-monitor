"""抓取适配层：HTTP 请求 + RSS / HTML / JSON 三类解析器。

设计原则：
- 零硬依赖。bs4 可用时用它精确解析 CSS 选择器，不可用则自动降级到正则宽松解析。
- 所有异常在适配器内部吞掉并返回空列表，保证单个源挂掉不影响整体抓取。
"""
import gzip
import json
import re
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from io import BytesIO

try:  # 可选依赖，用于精确 CSS 选择器解析
    from bs4 import BeautifulSoup  # type: ignore
    HAS_BS4 = True
except Exception:  # pragma: no cover
    BeautifulSoup = None  # type: ignore
    HAS_BS4 = False


def http_get(url, timeout=20, user_agent=None, retries=2, encoding=None):
    """GET 一个 URL，返回文本。失败返回 None。

    encoding：源级显式编码（如 gb2312/gbk）。命中时直接用它解码，跳过自动探测，
    避免 GBK 站点因响应头未声明 charset 而被误当成 UTF-8 导致乱码。
    """
    headers = {
        "User-Agent": user_agent or "Mozilla/5.0 (compatible; JobMonitor/1.0)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip",
    }
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.GzipFile(fileobj=BytesIO(raw)).read()
                charset = None
                ctype = resp.headers.get("Content-Type", "")
                m = re.search(r"charset=([\w\-]+)", ctype, re.I)
                if m:
                    charset = m.group(1)
                if encoding:
                    # 源级编码优先（如 boshihoujob.com 的 GBK）
                    body = raw.decode(encoding, errors="ignore")
                else:
                    body = raw.decode(charset or "utf-8", errors="ignore")
                    # 兜底：meta charset
                    if not charset:
                        m2 = re.search(rb'charset=["\']?([\w\-]+)', raw[:2048], re.I)
                        if m2:
                            try:
                                body = raw.decode(m2.group(1).decode("ascii", "ignore"), errors="ignore")
                            except Exception:
                                pass
                return body
        except Exception as e:  # noqa: BLE001
            last_err = e
    print(f"    [warn] 请求失败 {url}: {last_err}")
    return None


def _clean(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(text))
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------
# RSS / Atom
# --------------------------------------------------------------------------
def parse_rss(xml_text, limit=100):
    items = []
    if not xml_text:
        return items
    try:
        # 去掉可能干扰解析的命名空间前缀声明
        root = ET.fromstring(xml_text.strip())
    except Exception as e:  # noqa: BLE001
        print(f"    [warn] RSS 解析失败: {e}")
        return items

    def tag(el):
        return el.tag.split("}")[-1].lower()

    nodes = []
    for el in root.iter():
        if tag(el) in ("item", "entry"):
            nodes.append(el)
            if len(nodes) >= limit:
                break

    for node in nodes:
        rec = {"title": "", "link": "", "date": "", "summary": ""}
        for child in list(node):
            t = tag(child)
            if t == "title":
                rec["title"] = _clean(child.text)
            elif t == "link":
                href = child.attrib.get("href") or child.text
                if href:
                    rec["link"] = str(href).strip()
            elif t in ("pubdate", "published", "updated", "date"):
                rec["date"] = _clean(child.text)
            elif t in ("description", "summary", "content"):
                rec["summary"] = _clean(child.text or "")
        items.append(rec)
    return items


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------
_TAG_RE = re.compile(r"<a\s[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)


def parse_html(html_text, cfg, limit=100):
    """按 itemSelector + fields 解析列表页。bs4 不可用时降级为全页 <a> 抽取。"""
    if not html_text:
        return []
    item_sel = cfg.get("itemSelector", "")
    fields = cfg.get("fields", {}) or {}
    base = cfg.get("baseUrl", "")
    out = []

    if HAS_BS4 and item_sel:
        try:
            soup = BeautifulSoup(html_text, "html.parser")
            nodes = soup.select(item_sel)[:limit]
            for node in nodes:
                rec = {"title": "", "link": "", "date": "", "summary": ""}
                for key, sel in fields.items():
                    if key not in rec:
                        continue
                    if "@" in sel:
                        css, attr = sel.split("@", 1)
                        sub = node.select_one(css)
                        val = sub.get(attr) if sub else None
                    else:
                        sub = node.select_one(sel)
                        val = sub.get_text(" ", strip=True) if sub else None
                    if val:
                        rec[key] = str(val).strip()
                if not rec["title"]:
                    rec["title"] = node.get_text(" ", strip=True)[:120]
                if rec["link"] and base and rec["link"].startswith("/"):
                    rec["link"] = base.rstrip("/") + rec["link"]
                rec["summary"] = node.get_text(" ", strip=True)[:400]
                out.append(rec)
            return out
        except Exception as e:  # noqa: BLE001
            print(f"    [warn] HTML 选择器解析失败，降级为链接抽取: {e}")

    # 降级：抽所有 <a>。这里不能只取前 limit 个——列表页前几百个 <a> 通常全是导航，
    # 必须先全量抽取，交给 linkFilter 过滤后再截断（见 fetch_source）。
    for href, text in _TAG_RE.findall(html_text)[:limit * 30]:
        title = _clean(text)
        if len(title) < 6:
            continue
        link = href.strip()
        if base and link.startswith("/"):
            link = base.rstrip("/") + link
        out.append({"title": title, "link": link, "date": "", "summary": title})
    return out


# --------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------
def _dig(obj, path):
    cur = obj
    for part in [p for p in path.split(".") if p]:
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except Exception:  # noqa: BLE001
                return None
        else:
            return None
    return cur


def parse_json(text, cfg, limit=100):
    if not text:
        return []
    try:
        data = json.loads(text)
    except Exception as e:  # noqa: BLE001
        print(f"    [warn] JSON 解析失败: {e}")
        return []
    items_path = cfg.get("itemsPath", "")
    rows = _dig(data, items_path) if items_path else data
    if not isinstance(rows, list):
        return []
    mapping = cfg.get("fields", {}) or {}
    out = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        rec = {"title": "", "link": "", "date": "", "summary": ""}
        for key, path in mapping.items():
            if key not in rec:
                continue
            val = _dig(row, path)
            if val:
                rec[key] = _clean(val)
        out.append(rec)
    return out


def _source_urls(source):
    """一个源可能有多页（列表分页）：urls 数组优先，其次单个 url。

    分页支持：
    - urls：显式多页数组；
    - pagination：{pattern, pages, start} 自动按列栏目 ID 展开分页 URL，
      首页 url 作为第 1 页，pattern 生成第 start..pages 页（如 boshihoujob 的
      /gxbsh/guangdong/list_249_{n}.html）。start 默认 2，避免与首页重复。
    多页共用同一个 linkFilter，无需为每页单独配一条源。
    """
    urls = source.get("urls")
    if isinstance(urls, list) and urls:
        return [u for u in urls if u]
    u = source.get("url", "")
    if not u:
        return []
    pg = source.get("pagination")
    if isinstance(pg, dict) and pg.get("pattern"):
        pat = pg["pattern"]
        pages = int(pg.get("pages", 1))
        start = int(pg.get("start", 2))
        gen = [u]
        for n in range(start, pages + 1):
            gen.append(pat.replace("{n}", str(n)))
        return gen
    return [u]


def fetch_source(source, req_cfg):
    """根据 source['type'] 抓取并返回原始记录列表（支持多页 urls）。"""
    urls = _source_urls(source)
    if not urls:
        return []
    stype = (source.get("type") or "html").lower()
    limit = req_cfg.get("maxItemsPerSource", 120)
    recs = []
    for url in urls:
        body = http_get(
            url,
            timeout=req_cfg.get("timeout", 20),
            user_agent=req_cfg.get("userAgent"),
            encoding=source.get("encoding"),
        )
        if body is None:
            continue
        if stype == "rss":
            recs.extend(parse_rss(body, limit))
        elif stype == "json":
            recs.extend(parse_json(body, source, limit))
        else:
            recs.extend(parse_html(body, source, limit))
    for r in recs:
        r["_source"] = source.get("id", "")
        r["_sourceName"] = source.get("name", "")
        # 源自带的地区分区（如「博士后人才网 · 广东」栏目）：站点已按省份归档，
        # 这属于搬运站点既有分类，不是推测；供 normalize 在标题无线索时兜底。
        if source.get("provinceHint"):
            r["_provinceHint"] = source["provinceHint"]
        if r.get("link") and base_url(source) and r["link"].startswith("/"):
            r["link"] = base_url(source).rstrip("/") + r["link"]

    # linkFilter：只保留详情页链接，过滤掉导航/栏目/广告
    lf = source.get("linkFilter")
    if lf:
        rx = re.compile(lf)
        recs = [r for r in recs if r.get("link") and rx.search(r["link"])]

    # 同 URL 去重后再截断
    dedup, seen = [], set()
    for r in recs:
        k = r.get("link") or r.get("title")
        if k in seen:
            continue
        seen.add(k)
        dedup.append(r)
    return dedup[:limit]


def base_url(source):
    url = source.get("baseUrl") or source.get("url") or ""
    m = re.match(r"(https?://[^/]+)", url)
    return m.group(1) if m else ""
