"""归一化层：把原始抓取记录整理成站点可用的 Job 结构。

职责：抽取城市/省份/薪资/截止日期/方向 → 推断单位分类 → 计算匹配度 → 生成推荐理由。
所有抽取都是规则式的，抓不到就给保守默认值，绝不抛异常。
"""
import hashlib
import re
from datetime import datetime, timedelta

CITY_PROVINCE = {
    # 广东（含全部 21 个地级市）
    "广州": "广东", "深圳": "广东", "珠海": "广东", "佛山": "广东", "东莞": "广东",
    "汕头": "广东", "中山": "广东", "惠州": "广东", "江门": "广东", "湛江": "广东",
    "茂名": "广东", "肇庆": "广东", "揭阳": "广东", "清远": "广东", "韶关": "广东",
    "梅州": "广东", "汕尾": "广东", "阳江": "广东", "河源": "广东", "潮州": "广东",
    "云浮": "广东",
    # 福建（含全部 9 个设区市）
    "福州": "福建", "厦门": "福建", "泉州": "福建", "漳州": "福建", "莆田": "福建",
    "龙岩": "福建", "三明": "福建", "南平": "福建", "宁德": "福建",
    # 浙江（含全部 11 个设区市）
    "杭州": "浙江", "宁波": "浙江", "温州": "浙江", "嘉兴": "浙江", "湖州": "浙江",
    "绍兴": "浙江", "金华": "浙江", "衢州": "浙江", "舟山": "浙江", "台州": "浙江",
    "丽水": "浙江",
    # 江苏（含全部 13 个设区市）
    "南京": "江苏", "苏州": "江苏", "无锡": "江苏", "常州": "江苏", "南通": "江苏",
    "徐州": "江苏", "扬州": "江苏", "镇江": "江苏", "泰州": "江苏", "盐城": "江苏",
    "淮安": "江苏", "连云港": "江苏", "宿迁": "江苏",
    # 其它省市（用于识别并排除，不属于目标地区）
    "上海": "上海", "北京": "北京", "天津": "天津", "重庆": "重庆",
    "武汉": "湖北", "长沙": "湖南", "成都": "四川", "西安": "陕西",
    "济南": "山东", "青岛": "山东", "合肥": "安徽", "郑州": "河南",
    "南昌": "江西", "昆明": "云南", "贵阳": "贵州", "沈阳": "辽宁",
    "大连": "辽宁", "哈尔滨": "黑龙江", "长春": "吉林", "石家庄": "河北",
    "太原": "山西", "南宁": "广西", "海口": "海南", "兰州": "甘肃",
    "乌鲁木齐": "新疆", "呼和浩特": "内蒙古", "拉萨": "西藏", "银川": "宁夏",
    "西宁": "青海", "香港": "中国香港", "澳门": "中国澳门", "台北": "中国台湾",
}

# 省级名称：标题/单位名里直接写省名时用（「广东省人民医院」「福建省肿瘤医院」）
PROVINCE_NAMES = [
    "广东", "福建", "浙江", "江苏", "上海", "北京", "天津", "重庆", "河北", "山西",
    "辽宁", "吉林", "黑龙江", "安徽", "江西", "山东", "河南", "湖北", "湖南",
    "四川", "贵州", "云南", "陕西", "甘肃", "青海", "内蒙古", "广西", "西藏",
    "宁夏", "新疆", "海南", "台湾",
]

# 省份简称写法：「粤」「闽」「浙」「苏」不作为判据（单字误伤风险高），
# 但「广东省」「广东」这类完整写法一律识别。
CITY_ALIASES = {
    "深圳市": "深圳", "广州市": "广州", "厦门市": "厦门", "福州市": "福州",
    "杭州市": "杭州", "南京市": "南京", "苏州市": "苏州", "宁波市": "宁波",
    "东莞市": "东莞", "佛山市": "佛山", "珠海市": "珠海", "中山市": "中山",
    "惠州市": "惠州", "温州市": "温州", "泉州市": "泉州", "无锡市": "无锡",
}

# 顺序即优先级：附属医院优先判为医院，避免"XX大学附属医院"被吃成"高校"
UNIT_RULES = [
    ("科研院所", ["科学院", "研究院", "研究所", "实验室", "academy of sciences"]),
    ("三甲医院", ["三甲", "三级甲等", "人民医院", "中心医院", "肿瘤医院", "附属", "防治中心"]),
    ("二甲医院", ["二甲", "二级医院", "区医院", "市立医院", "中医院"]),
    ("高校", ["大学", "university", "医学院", "医科大学"]),
    ("专科院校", ["职业学院", "职业技术学院", "专科学校", "高等专科"]),
    ("事业单位", ["疾控", "卫健委", "事业单位", "公共服务", "妇幼保健院"]),
    ("医药企业", ["制药", "生物科技", "医药", "药业", "biotech", "pharma"]),
]

CATEGORY_RULES = [
    ("博士后", ["博士后", "postdoc", "博新计划", "博士后流动站"]),
    ("医院", ["医院", "hospital", "临床", "医师", "科室"]),
    ("高校科研院所", ["大学", "学院", "研究院", "研究所", "university", "教职", "讲师", "教授"]),
    ("生物医药", ["生物", "制药", "医药", "药企", "biotech", "pharma", "CRO"]),
    ("事业单位", ["事业单位", "事业编", "疾控", "卫健", "公务员"]),
]

SALARY_PATTERNS = [
    r"年薪\s*[\d\-—~至]{2,20}\s*万",
    r"[\d]{1,3}\s*[-—~至]\s*[\d]{1,3}\s*万/年",
    r"[\d]{1,3}\s*万\s*[-—~至]\s*[\d]{1,3}\s*万",
    r"月薪\s*[\d\-—~至]{2,20}\s*[kK元]",
    r"[\d]{1,2}K?\s*[-—~至]\s*[\d]{1,2}K",
    r"安家费\s*[\d\-—~至]{2,20}\s*万",
]

DATE_PATTERNS = [
    (r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})", "ymd"),
    (r"(\d{1,2})[-/月](\d{1,2})", "md"),
]

DIRECTION_KWS = [
    "肿瘤", "癌症", "oncology", "cancer", "免疫", "immuno", "靶向", "targeted",
    "分子生物", "分子机制", "细胞", "信号通路", "基因组", "genomics", "蛋白",
    "药物研发", "新药", "临床试验", "clinical trial", "放疗", "化疗", "病理",
    "血液", "乳腺", "肺", "肝", "胃肠", "car-t", "CAR-T", "单细胞", "生信",
]


def _text(rec):
    return " ".join(str(rec.get(k) or "") for k in ("title", "summary", "unit", "detail"))


# 校名/单位名 → 实际所在城市。必须最先匹配，否则"中山大学"会被误判成广东中山市
INST_CITY = {
    "南方科技大学": "深圳", "深圳大学": "深圳", "香港中文大学（深圳）": "深圳",
    "中山大学": "广州", "南方医科大学": "广州", "暨南大学": "广州", "广州医科大学": "广州",
    "厦门大学": "厦门", "福建医科大学": "福州", "福建中医药大学": "福州",
    "浙江大学": "杭州", "温州医科大学": "温州", "宁波大学": "宁波",
    "南京医科大学": "南京", "南京大学": "南京", "东南大学": "南京", "苏州大学": "苏州",
    "复旦大学": "上海", "上海交通大学": "上海", "同济大学": "上海",
    "北京大学": "北京", "清华大学": "北京", "协和": "北京", "首都医科大学": "北京",
    "四川大学": "成都", "华中科技大学": "武汉", "中南大学": "长沙", "湘雅": "长沙",
    "山东大学": "济南", "中国科学技术大学": "合肥", "郑州大学": "郑州", "南昌大学": "南昌",
}


def detect_city(text, unit_hint=""):
    for inst, city in INST_CITY.items():
        if inst in text:
            return city
    for alias, std in CITY_ALIASES.items():
        if alias in text:
            return std
    # 优先匹配明确城市名：按长度倒序，避免长城市名被更短的词先吃掉
    for city in sorted(CITY_PROVINCE, key=len, reverse=True):
        if city in text:
            return city
    if unit_hint:
        for inst, city in INST_CITY.items():
            if inst in unit_hint:
                return city
    return "未知"


def detect_province(city, text=""):
    """省份判定：城市反查 → 文本里的省名 → 未知。

    text 里直接写省名的岗位很多（「广东省人民医院」「福建省肿瘤医院」），
    早期只扫 10 个省份导致这类岗位被判为「未知」，扩到全部省级名称。
    """
    if city in CITY_PROVINCE:
        return CITY_PROVINCE[city]
    for prov in PROVINCE_NAMES:
        if prov in text:
            return prov
    return "未知"


def detect_salary(text):
    for pat in SALARY_PATTERNS:
        m = re.search(pat, text)
        if m:
            return m.group(0).strip()
    return "面议"


def detect_deadline(text):
    if not text:
        return "详见正文"
    low = text
    for kw in ["长期有效", "长期招聘", "长期", "常年", "招满即止", "额满为止"]:
        if kw in low:
            return "长期有效" if kw == "长期" else kw
    m = re.search(DATE_PATTERNS[0][0], low)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            return "详见正文"
    m2 = re.search(r"(?:截至|截止|报名至|到)\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", low)
    if m2:
        now = datetime.now()
        try:
            dt = datetime(now.year, int(m2.group(1)), int(m2.group(2)))
            if dt < now:
                dt = datetime(now.year + 1, int(m2.group(1)), int(m2.group(2)))
            return dt.strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            return "详见正文"
    return "详见正文"


def detect_direction(text):
    hits = [k for k in DIRECTION_KWS if k in text]
    if not hits:
        return "未明确方向"
    seen, out = set(), []
    for h in hits:
        key = h.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
        if len(out) >= 4:
            break
    return " / ".join(out)


# 单位名后缀：用于从标题里抽取招聘主体。纯文本切分，不做任何推测补全。
# 顺序按「先具体后宽泛」排列，正则用非贪婪匹配取最短可行前缀。
UNIT_SUFFIX = (
    "妇幼保健院|人民医院|中心医院|肿瘤医院|口腔医院|儿童医院|中医院|附属医院|专科医院|医院",
    "卫生健康委员会|卫健委|卫生健康系统|卫健系统|疾控中心|疾病预防控制中心",
    "科学院|研究院|研究所|重点实验室|实验室|课题组",
    "大学|学院|学校|中学|小学|党校|干部学院",
    "出版社|报社|集团|有限公司|股份公司|科技公司|生物技术|制药|药业",
    "人力资源和社会保障局|人力资源|组织部|政治工作部|工作部|科技局|教育局|财政局|国资委|管委会|管理局|人民政府",
    "事业单位|公共服务|人才中心|人才市场",
    "中心|基地|园区|站点",
)
# 展平成词表，便于按单个词过滤（UNIT_SUFFIX 是分组元组，不能直接整体比较）
_ALL_SUFFIX_WORDS = [w for group in UNIT_SUFFIX for w in group.split("|")]
_SUFFIX_ALT = "|".join(_ALL_SUFFIX_WORDS)
# 机构级后缀：不含课题组/实验室。
# 贪婪匹配时若带上这些，会把「四川大学华西第二医院…肿瘤微环境实验室」整个吃进去，
# 反而丢掉真正的单位主体，所以主匹配只用机构级后缀，这些仅作兜底。
_ORG_ALT = "|".join(w for w in _ALL_SUFFIX_WORDS if w not in ("课题组", "实验室"))
# 贪婪匹配：取最长可行单位名，避免「中山大学附属第一医院」被截成「中山大学」
# 「/」也当分隔符：「中山大学附属第八医院/临床心理科/徐勇教授课题组」应停在医院
_UNIT_HEAD_RE = re.compile(r"^\[?([^\]｜|：:，,。、（(/]{2,40}(?:" + _ORG_ALT + r"))\]?")
# 尾部拼接型：「岗位名 面议 博士研究生 学科 N人 单位名 城市」
_UNIT_TAIL_RE = re.compile(r"([^\s，,。、（(/]{2,40}(?:" + _ORG_ALT + r"))(?:\s+[^\s]+)?\s*$")
# 任意位置搜索（兜底）：单位名夹在标题中间时用，此时才允许课题组/实验室这类软后缀
_UNIT_ANY_RE = re.compile(r"([^\s，,。、｜|：:（(/]{2,40}(?:" + _SUFFIX_ALT + r"))")
# 抽出来的不是单位名：含动词/营销符号的一律丢掉。
# 「万」只按「数字+万」匹配，否则真名「浙江万里学院」会被误伤。
_NOT_UNIT_RE = re.compile(
    r"[！!丨｜+＋%★☆]|\d\s*[万W]"
    r"|招聘|引进|公告|公开|面向|关于|岗位|人才|诚信|待遇|年薪|编制|报名|截止|面议"
    r"|头条|热招|急招|全部有编|高技能"
)
# 口号/栏目前缀：「智汇江淮 创领未来 江淮实验室」→ 按分隔符切开后只留单位名那节
_SPLIT_RE = re.compile(r"[\s\-—–_/！!，,、；;：:丨｜]+")
_PART_RE = re.compile(r"([^\s，,。、｜|：:（）(/]{2,40}(?:" + _SUFFIX_ALT + r"))")


def _clean_unit_name(name):
    """剥掉口号/栏目前缀，只留真正的单位名。

    按分隔符切开逐节找单位后缀，取最长的一节。
    「年薪45-55万！中山大学」→「中山大学」；「智汇江淮 创领未来 江淮实验室」→「江淮实验室」。
    """
    raw = (name or "").strip(" []（）()、,。")
    if not raw:
        return ""
    best = ""
    for part in _SPLIT_RE.split(raw):
        part = part.strip()
        if not part:
            continue
        m = _PART_RE.search(part)
        if not m:
            continue
        cand = m.group(1).strip()
        if _NOT_UNIT_RE.search(cand) or len(cand) < 3:
            continue
        if len(cand) > len(best):
            best = cand
    return best.strip()


def is_noisy_unit(name):
    """判断单位名是否夹带了营销语/栏目名（如「长期招聘！海南医科大学」「年薪30万+中国民航大学」）。

    只用于决定「要不要按标题重切一次」，不会凭空改掉干净的单位名。
    复用 _NOT_UNIT_RE 再加顿号/分号，避免两套判据漂移。
    """
    return bool(_NOT_UNIT_RE.search(name or "") or re.search(r"[，,；;、]", name or ""))


def detect_unit(title, summary=""):
    """从标题里抽招聘单位名。

    三级确定性切分：标题前缀 → 标题尾部 → 任意位置。全部失败返回「详见公告」，
    绝不用常识补全或推测单位（那是编造）。
    """
    t = (title or "").strip()
    for rx in (_UNIT_HEAD_RE, _UNIT_TAIL_RE, _UNIT_ANY_RE):
        m = rx.search(t)
        if m:
            name = _clean_unit_name(m.group(1))
            if name:
                return name
    m2 = re.search(r"([^\s，,。、]{2,20}(?:大学|学院|医院|研究院|研究所))", summary or "")
    if m2:
        return _clean_unit_name(m2.group(1)) or "详见公告"
    return "详见公告"


def classify(text, title=""):
    """标题优先判定：避免正文里一句『有博士后经历优先』把整个教职岗吃成博士后岗。"""
    if title:
        tl = title.lower()
        for cat, kws in CATEGORY_RULES:
            for kw in kws:
                if kw.lower() in tl:
                    return cat
    low = text.lower()
    for cat, kws in CATEGORY_RULES:
        for kw in kws:
            if kw.lower() in low:
                return cat
    return "中等岗位"


def detect_unit_tag(text):
    for tag, kws in UNIT_RULES:
        for kw in kws:
            if kw.lower() in text.lower():
                return tag
    return "未知"


def _kw_items(group):
    """兼容两种权重写法：[{"kw":"肿瘤","w":20}] 或 [["肿瘤",20]]。"""
    items = []
    for it in group or []:
        if isinstance(it, dict):
            k, w = it.get("kw"), it.get("w", 0)
        elif isinstance(it, (list, tuple)) and len(it) >= 2:
            k, w = it[0], it[1]
        else:
            continue
        try:
            w = float(w)
        except (TypeError, ValueError):
            continue
        if k:
            items.append((str(k), w))
    return items


def direction_bonus(text, profile):
    """方向契合度加成（取最高命中，不累加）。

    方向是否对口是决定性信号，不能只当普通加权词——否则"肿瘤"岗会被"脑机接口""海洋科学"
    这类岗位在单位/城市维度上反超，推荐结果就失去意义。
    """
    rules = profile.get("directionRules", {})
    low = text.lower()
    for k, v in (rules.get("core") or {}).items():
        if k.lower() in low:
            return float(v)
    for k, v in (rules.get("offtopic") or {}).items():
        if k.lower() in low:
            return float(v)
    for k, v in (rules.get("related") or {}).items():
        if k.lower() in low:
            return float(v)
    return 0.0


def compute_score(text, city, unit_tag, profile):
    """匹配度 = 基础分 + 关键词(归一化) + 方向契合 + 单位平台 + 城市偏好。

    score = 44 + min(42, 命中权重和 × 0.34) + 方向加成 + min(9, 单位 × 0.5) + min(8, 城市 × 0.4)
    """
    low = text.lower()
    raw = 0.0
    kws = profile.get("keywords", {})
    for group in ("strong", "medium", "weak"):
        for k, w in _kw_items(kws.get(group)):
            if k.lower() in low:
                raw += w
    kw_part = min(42.0, raw * 0.40)
    dir_part = direction_bonus(text, profile)
    unit_part = min(9.0, float(profile.get("unitWeights", {}).get(unit_tag, 0)) * 0.5)
    city_part = min(8.0, float(profile.get("cityWeights", {}).get(city, 0)) * 0.4)
    return int(max(30, min(99, round(46 + kw_part + dir_part + unit_part + city_part))))


# 分档按真实抓取数据校准过：批量抓取时标题信息量有限，绝对分数普遍低于带正文的公告，
# 沿用 95/85/75 会让「极高匹配」长期为 0。改配置时记得同步 site/template.html 的 scoreClass。
# 匹配度四档默认阈值；实际以 config/profile.json 的 thresholds.matchTiers 为准，
# 保持「配置文件 = 唯一数据源」，避免前后端各写一套导致筛选口径对不上。
DEFAULT_TIERS = {"excellent": 85, "high": 75, "mid": 65}


def get_tiers(profile=None):
    t = (profile or {}).get("thresholds", {}).get("matchTiers") or {}
    return {
        "excellent": int(t.get("excellent", DEFAULT_TIERS["excellent"])),
        "high": int(t.get("high", DEFAULT_TIERS["high"])),
        "mid": int(t.get("mid", DEFAULT_TIERS["mid"])),
    }


def match_label(score, tiers=None):
    t = tiers or DEFAULT_TIERS
    if score >= t["excellent"]:
        return "极高匹配"
    if score >= t["high"]:
        return "高匹配"
    if score >= t["mid"]:
        return "中等匹配"
    return "一般匹配"


def target_provinces(profile):
    """收录范围（地区闸门）：只收录这些省份的岗位，其余一律不入库。"""
    p = (profile or {}).get("targetProvinces")
    return set(p) if isinstance(p, list) and p else set()


def featured_provinces(profile):
    """「今日推荐」的省份范围，未单独配置时退回收录范围。"""
    p = (profile or {}).get("featuredProvinces")
    if isinstance(p, list) and p:
        return set(p)
    return target_provinces(profile)


def is_featured(job, profile):
    """单条判断：分数达标 且 省份在推荐范围内（不含排名兜底，兜底见 apply_featured）。"""
    fp = featured_provinces(profile)
    if fp and job.get("province") not in fp:
        return False
    return int(job.get("matchScore") or 0) >= int(
        (profile or {}).get("thresholds", {}).get("featuredScore", 82)
    )


def apply_featured(jobs, profile):
    """重算全部岗位的「今日推荐」标记——唯一出口，crawl 与 enrich 都走这里。

    规则：候选只取推荐范围内的省份（默认广东、福建），先按分数达标，
    再按排名前 N 兜底（真实岗位标题信息量少，纯阈值容易整批打不上导致栏目空白）。
    ⚠️ jobs 必须已按 matchScore 倒序。
    """
    fp = featured_provinces(profile)
    feat_score = int((profile or {}).get("thresholds", {}).get("featuredScore", 82))
    top_n = int((profile or {}).get("thresholds", {}).get("featuredTopN", 12))
    ranked = 0
    for j in jobs:
        if fp and j.get("province") not in fp:
            j["featured"] = False
            continue
        ranked += 1
        j["featured"] = bool(int(j.get("matchScore") or 0) >= feat_score or ranked <= top_n)
    return len([j for j in jobs if j.get("featured")])


def build_reason(text, city, unit_tag, score):
    bits = []
    if "肿瘤" in text or "oncology" in text.lower() or "cancer" in text.lower():
        bits.append("研究方向与肿瘤学高度契合")
    if "博士后" in text or "postdoc" in text.lower():
        bits.append("博士后通道，与博士背景直接对口")
    if "博士" in text or "PhD" in text:
        bits.append("明确要求博士学历，符合你的学历层次")
    if unit_tag in ("三甲医院", "高校", "科研院所"):
        bits.append(f"{unit_tag}平台，科研与临床资源稳定")
    if city in ("深圳", "厦门", "福州", "杭州", "南京", "苏州"):
        bits.append(f"位于{city}，命中你的重点城市")
    if "编制" in text or "事业编" in text:
        bits.append("含编制，稳定性较强")
    if "安家费" in text or "年薪" in text:
        bits.append("待遇明确（含安家费/年薪）")
    if not bits:
        bits.append("岗位方向与你背景部分相关，可作为机会储备")
    return "；".join(bits[:4]) + f"（综合匹配度 {score}）"


_FP_STRIP_RE = re.compile(r"[\s　|｜·・,，。、；;:：!！?？\-—_/\\()（）\[\]【】\"'“”‘’]+")


def fingerprint(title, unit=None, link=None):
    """去重指纹：只依赖原文固有的标题（unit/link 参数保留仅为兼容旧调用点）。

    ⚠️ **绝不能把 unit 算进指纹**。unit 是派生字段，enrich 补全/清洗后会变，
    指纹一变，同一岗位在下一轮抓取时就被当成新条目重新入库。
    实测后果：860 条里 591 条是这么涨出来的，而且每跑一次 enrich 就翻一倍。

    link 同样不能进指纹：同一份公告在原站的不同栏目下 URL 不同
    （`/company/detail/92355.html` vs `/announcement/detail/204128.html`），
    用 link 去重会漏掉这类跨栏目重复。
    """
    norm = _FP_STRIP_RE.sub("", title or "").lower()
    return hashlib.md5(norm.encode("utf-8")).hexdigest()[:16]


def is_expired(deadline, expire_days=90):
    if deadline in ("长期有效", "长期", "招满即止", "详见正文", "详见公告", "岗位招满前有效", ""):
        return False
    try:
        dl = datetime.strptime(deadline, "%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return False
    now = datetime.now()
    return dl < now or dl > now + timedelta(days=expire_days)


TITLE_NOISE = [
    "查看详情", "立即查看", "医院 | 公立（国有）", "双一流院校 | 公立（国有）",
    "公立（国有）", "| 公立", "查看更多",
]


# 列表页会在单位名后另起一个「单位类型」字段，如
# 「北京市生态环境保护科学研究院 自然与应用科研机构（事业单位类型） 2026年博士后招聘公告」。
# 关键：只删「以空白分隔的独立字段」，否则会误伤真名里的「事业单位」
# （「黑龙江省东宁市事业单位2026年度招聘…」里的「事业单位」是单位名的一部分，必须保留）。
_UNIT_TYPE_RE = re.compile(
    r"(?<=[\s　])"
    r"[^，,。\s]{0,12}?"
    r"(?:科研机构|研究机构|研发机构|事业单位|机关单位|非盈利组织及其他|政府国有企业|"
    r"医疗卫生单位|双一流院校|普通本科院校|高职高专|高等院校|"
    r"中国科学院系统|中国社会科学院系统|中央部委直属)"
    r"(?:（[^）]{0,12}?类型）)?"
    r"[\s　]*"
)


def clean_title(t):
    """列表页常把标题、单位标签、一句话简介拼在一起，去掉标签段只留可读标题。"""
    for s in TITLE_NOISE:
        t = t.replace(s, " ")
    t = re.sub(r"\s*\|\s*", " ", t)
    # 单位类型字段（必须在空白之后，避免动到真名里的「事业单位」）
    t = _UNIT_TYPE_RE.sub(" ", t)
    # 列表项的「详情 / 查看详情」是按钮文字，被 <a> 抽取时粘到了标题尾部
    t = re.sub(r"[\s　]*(查看|了解|点击)?详情[\s　]*$", "", t)
    t = re.sub(r"^[\s　]*(查看|了解|点击)详情[\s　]*", "", t)
    return re.sub(r"\s{2,}", " ", t).strip()


def normalize(rec, profile, idx=0):
    """把一条原始记录转成 Job dict。"""
    text = _text(rec)
    title = clean_title((rec.get("title") or "").strip()) or "未命名岗位"
    unit = (rec.get("unit") or "").strip() or detect_unit(title, text)
    city = detect_city(text, unit)
    province = detect_province(city, text)
    # 源自带的地区分区兜底：站点已把该公告归入「广东/福建/浙江/江苏」栏目，
    # 这是搬运站点既有的分类事实（不是我们推测的）。仅当标题里确实无线索时才采用。
    hint = str(rec.get("_provinceHint") or "").strip()
    if province == "未知" and hint in PROVINCE_NAMES:
        province = hint
    # 具体城市未在公告中给出时退到省份展示（不虚构城市名）
    if city == "未知" and province in set(CITY_PROVINCE.values()):
        city = province
    unit_tag = detect_unit_tag(text)
    category = classify(text, title)
    if category == "中等岗位" and unit_tag in ("三甲医院", "二甲医院"):
        category = "医院"
    if category == "中等岗位" and unit_tag in ("高校", "专科院校", "科研院所"):
        category = "高校科研院所"
    score = compute_score(text, city, unit_tag, profile)
    # 「今日推荐」只在指定省份（默认广东、福建）内产生；排名兜底由 apply_featured 统一重算。
    _feat_score = int(profile.get("thresholds", {}).get("featuredScore", 82))
    _feat_provs = featured_provinces(profile)

    job = {
        "id": f"auto-{idx:04d}",
        "unit": unit,
        "title": title,
        "city": city,
        "salary": detect_salary(text),
        "direction": detect_direction(text),
        "deadline": detect_deadline(text),
        "matchScore": score,
        "province": province,
        "category": category,
        "unitTag": unit_tag,
        "description": (rec.get("summary") or title)[:300],
        "requirements": extract_requirements(text),
        "reason": build_reason(text, city, unit_tag, score),
        "talentPlan": extract_talent_plan(text),
        "link": rec.get("link") or "",
        "featured": bool(score >= _feat_score and (not _feat_provs or province in _feat_provs)),
        "coreDirection": direction_bonus(text, profile) >= 6,
        "matchLabel": match_label(score, get_tiers(profile)),
        "source": rec.get("_sourceName", ""),
        "crawledAt": datetime.now().strftime("%Y-%m-%d"),
        "isSeed": bool(rec.get("_seed")),
    }
    job["fingerprint"] = fingerprint(job["title"], job["unit"], job["link"])
    return job


def extract_requirements(text):
    hits = []
    for kw in ["博士", "博士后", "硕士", "发表", "SCI", "第一作者", "国家自然科学基金", "海外经历", "年龄", "35周岁", "编制"]:
        if kw in text:
            hits.append(kw)
    if not hits:
        return "详见公告原文"
    return "要求含：" + "、".join(hits[:6])


def extract_talent_plan(text):
    hits = []
    for kw in ["安家费", "住房补贴", "科研启动", "人才引进", "编制", "落户", "子女入学", "年薪"]:
        if kw in text:
            hits.append(kw)
    if not hits:
        return "公告未明确，建议电话咨询人事处"
    return "含 " + "、".join(hits[:6])
