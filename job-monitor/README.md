# 君来暴富 · 肿瘤学方向 AI 招聘监控

面向肿瘤学博士 / 博士后的招聘监控系统：**每日自动抓取 → 过滤去重 → 匹配度打分 → 生成单文件网页**。

- 抓取：可插拔数据源（RSS / HTML 列表页 / JSON 接口三类适配器）
- 打分：按你的画像（肿瘤学 + 博士 + 深圳/福建/长三角）自动算匹配度并分志愿梯度
- 更新：GitHub Actions 每天定时跑，产出网页自动发布，手机电脑打开即最新
- 站点：单文件 HTML，零外链零依赖，断网可用

---

## 最近更新（2026-09-18）

- **地区闸门生效**：`config/profile.json` 新增 `targetProvinces: [广东,福建,浙江,江苏]` 与 `featuredProvinces: [广东,福建]`。
  现仅收录粤闽浙苏四省岗位，其它地区（含「未知」省份无法确认者）一律不入库；「今日推荐」只展示广东/福建岗位。
- **收录范围收敛**：现有数据已从 534 条（24 省）过滤为 **150 条（粤闽浙苏）**——广东 78 / 浙江 45 / 福建 15 / 江苏 12，深圳 29 条。
- **深圳专区前置**：Tab 顺序调整为「今日推荐 → 深圳专区 → 第一志愿 → 第二志愿 → 第三志愿 → 其它 → 全部岗位 → 我的收藏 → 使用说明」。
- **新增收藏功能**：每张卡片右上角 ★ 可收藏，新增「我的收藏」Tab 汇总；收藏仅存本机浏览器（localStorage），不上传、不跨设备。
- 所有岗位仍 100% 带公开原文链接，可逐条回源核验。

---

## 〇、数据真实性红线（不可绕过）

**本系统只收录公开网络上真实抓取到的岗位，不生成、不推测、不补全任何虚构岗位。**

这条不是口号，是代码里四道硬约束：

| 约束 | 实现位置 | 行为 |
|---|---|---|
| 必须带原文链接 | `crawl.py` 过滤段 | 无 http(s) 链接的条目直接丢弃，不入库 |
| 只收岗位详情页 | `crawl.py` `is_noise()` | 仅收 `/announcement/detail/`、`/company/detail/`、`/job/detail/`；原站 `/news/`、`/daily/` 等资讯频道一律剔除 |
| 无示例数据兜底 | 全局（`data/seed.json` 已删除） | **抓不到就是空页**，绝不用编造数据填充 |
| 可回源核验 | `crawler/verify.py` | 随机抽样真实请求原文链接，确认页面可访问且含该岗位标题 |
| 补全只做搬运 | `crawler/enrich.py` | 缺失的单位名从详情页 `<h1>/<title>` 逐字取回；页面抓不到就保持原样，**不填一个可能错的单位名** |
| 单位名只做切分 | `normalize.py` `detect_unit()` | 从原标题里按「前缀+单位后缀」确定性切出单位名（「年薪30万+中国民航大学」→「中国民航大学」），切不出就保留原值，不靠常识推测 |
| 去重指纹只认标题 | `normalize.py` `fingerprint()` | 指纹**只能**由原文固有的标题算出。⚠️ 千万别把 `unit` 或 `link` 加进去——它们是可变的派生字段，一变指纹就变，同一岗位下次抓取会被当成新条目重复入库（曾因此让 860 条里混进 591 条重复） |

另外，源全部故障时**不会清空**已有数据——此前抓到的历史数据同样真实有效，此时保留并标记「未刷新」，
网页顶部会提示「本次抓取未取到新数据（源可能故障），展示的是上一次真实抓取结果」。

一键核验：

```bash
python3 crawler/verify.py --sample 10   # 抽样 10 条回源核验
python3 crawler/verify.py --no-network  # 只做本地链接体检（快）
python3 crawler/verify.py --all         # 全量回源核验（慢）
```

---

## 一、快速开始

```bash
./run.sh              # 抓取 + 生成站点，产物在 dist/index.html
./run.sh --open       # 抓取 + 生成 + 打开浏览器
./run.sh --serve      # 起本地服务，手机连同一 WiFi 可访问
./run.sh --verify     # 抓取后抽样回源，核验每条岗位在公开网络上真实存在
./run.sh --rebuild    # 改了打分规则后，忽略历史数据全量重建
```

单独跑某一步：

```bash
python3 crawler/crawl.py --rebuild        # 只抓数据
python3 crawler/verify.py --sample 10     # 只做真实性核验
python3 site/build.py                     # 只生成页面
```

---

## 二、已接入的源（实测可抓，开箱即用）

当前已配好 7 个源，全部来自**高校人才网**（`www.gaoxiaojob.com`，国内高校/医院/科研院所招聘聚合站），
其 robots.txt 明确允许抓取首页与栏目页、仅禁止 `/member` `/search` 等路径。

| 源名 | 地址 | 说明 |
|---|---|---|
| 医学人才 | `/column/5.html` | 医学口岗位最集中的栏目 |
| 医疗单位博士后招收 | `/column/266.html` | 与博士背景最对口 |
| 基础医学 | `/column/200.html` | 基础医学方向 |
| 临床医学 | `/column/201.html` | 临床医学方向 |
| 生物学 | `/column/146.html` | 生物学方向 |
| 博士人才引进 | `/column/275.html` | 高校教职/引进人才 |
| 博士后频道 | `boshihou.gaoxiaojob.com` | 博士后专项 |
| 最新招聘公告（首页） | 首页 | 更新量最大，覆盖全学科 |

实测结果：首次运行抓到 **640 个岗位**、覆盖 24 个省份，其中方向命中肿瘤学的有 6 个，
过滤掉已下线岗位、招聘会/峰会活动、资讯分析文等噪声 227 条。

### 其他站点实测情况（供你选源时参考）

| 站点 | 结果 | 原因与对策 |
|---|---|---|
| 深圳市人社局 `hrss.sz.gov.cn` | ✗ 失败 | SSL 握手被拒（服务端配置老旧），需在 `fetch.py` 放宽 SSL 策略或用其下属具体栏目页 |
| 中山大学肿瘤防治中心 `sysucc.org.cn` | ✗ 失败 | 302 重定向死循环，需要带 cookie 会话访问，属于反爬 |
| 丁香园人才 `job.dxy.cn` | ✗ 拿不到岗位 | 列表由 JS 动态渲染，静态 HTTP 取不到内容，需浏览器渲染方案 |
| 中国博士后 `chinapostdoctor.org.cn` | ✗ 拿不到岗位 | 首页是 JS 框架空壳（仅 7KB），真实数据在接口里，需找其 API |
| 小木虫 `muchong.com` | ✗ 失败 | 502，可能是网络出口限制 |

> 结论：**静态 HTML 列表页 + 允许抓取的站点**（高校人才网这类）可以直接接；
> **JS 渲染站**和**强反爬站**需要额外方案（浏览器渲染 / 带会话请求 / 找接口），投入产出比低，
> 建议优先找同类站点的静态列表页。

---

## 三、怎么接入你自己的招聘源（下一步）

打开 `config/sources.json`，往 `sources` 数组里加一条，把 `enabled` 设为 `true` 即可。支持三种类型：

### 1）RSS / Atom 源（最省事，推荐优先找）

```json
{
  "id": "my_rss_source",
  "name": "某医院人事处招聘公告",
  "enabled": true,
  "type": "rss",
  "url": "https://example.com/hr/feed.xml"
}
```

### 2）HTML 列表页（大多数招聘公告页属于这类）

```json
{
  "id": "my_html_source",
  "name": "某大学人才招聘网",
  "enabled": true,
  "type": "html",
  "url": "https://example.com/talent/list.htm",
  "itemSelector": "ul.news-list li",
  "fields": {
    "title": "a",
    "link": "a@href",
    "date": "span.date"
  },
  "baseUrl": "https://example.com"
}
```

- `itemSelector`：列表项容器的 CSS 选择器
- `fields`：字段 → 选择器。`sel@attr` 取属性（如 `a@href`），`sel` 取文本
- `baseUrl`：链接是相对路径时用它补全

> 选择器怎么找：浏览器打开列表页 → F12 → 右键列表项 → 复制 → 复制 selector。
> 本机装了 `beautifulsoup4` 时用精确选择器解析，没装也能跑（自动降级为提取页面所有链接，再靠关键词过滤）。

### 3）JSON 接口

```json
{
  "id": "my_api_source",
  "name": "某招聘平台开放接口",
  "enabled": true,
  "type": "json",
  "url": "https://example.com/api/jobs?keyword=肿瘤",
  "itemsPath": "data.list",
  "fields": { "title": "name", "link": "url", "date": "publishTime", "summary": "description" }
}
```

> 抓取失败不会中断整体流程：单个源挂掉只打印一条告警，其它源照常。

---

## 四、推荐策略怎么调

全部集中在 `config/profile.json`：

| 配置项 | 作用 |
|---|---|
| `keywords.strong / medium / weak` | 命中即加分，`strong` 里的「肿瘤学」「博士后」权重最高 |
| `directionRules.core / related / offtopic` | 方向契合加成：肿瘤类 +10、相邻医学 +3~5、无关方向 -5~-10 |
| `unitWeights` | 三甲医院 18 / 高校 16 / 科研院所 14 / 二甲 6 / 医药企业 4 |
| `cityWeights` | 深圳 20，厦门、福州 10，杭州、南京、苏州 6-8 |
| `thresholds.featuredScore` | 超过该分数进「今日推荐」（默认 82） |
| `thresholds.featuredTopN` | 兜底：无论分数，排名前 N 条一定进「今日推荐」（默认 12），防止该栏目空白 |
| `thresholds.starScore` | 超过该分数打星标重点推荐（默认 90） |
| `thresholds.matchTiers` | 匹配度四档阈值（默认 85/75/65），**页面分档颜色、筛选器选项、后端 matchLabel 三处共用这一份** |
| `thresholds.expireDays` | 截止日期超过该天数的岗位判为过期丢弃（默认 90） |

> `matchTiers` 的取值是按真实数据分布校准的：真实抓取只有标题没有正文，且绝大多数岗位与肿瘤学无关，
> 分数天然集中在 50-60。若沿用「极高≥95」这类凭直觉定的阈值，会导致几乎全部岗位被打成「一般匹配」、
> 筛选器筛不出东西。校准后 631 条数据的分档为：极高 3 / 高 13 / 中等 81 / 一般 534，「极高匹配」对应真正的头部岗位。

打分公式：

```
score = 46 + min(42, 关键词命中权重和 × 0.40)
          + 方向契合加成（directionRules，取最高命中不累加）
          + min(9, 单位权重 × 0.5)
          + min(8, 城市权重 × 0.4)
```

`directionRules` 是决定推荐质量的关键：命中「肿瘤/oncology/cancer」等核心词 +10，
命中「医学/临床/生物/药学」等相邻方向 +3~5，命中「材料/机械/海洋/马克思主义」等明显无关方向 -5~-10。
**没有这一层，「肿瘤」岗位会被「脑机接口」这类岗位在单位和城市维度上反超**，推荐就失去意义了。

改完权重后执行 `./run.sh --rebuild` 让历史数据按新规则重算。

---

## 五、部署：每天自动更新 + 在线访问

### 步骤

```bash
cd job-monitor
git init && git add . && git commit -m "init: 招聘监控"
gh repo create junlai-job-monitor --private --source=. --push   # 或手动在 GitHub 建仓后 push
```

然后到仓库 **Settings → Pages → Source 选「GitHub Actions」**，完成。

之后：

- 每天北京时间 **07:00** 自动抓取、生成、发布（也支持在 Actions 页面点 `Run workflow` 手动跑）
- 访问地址：`https://<你的用户名>.github.io/junlai-job-monitor/`
- 手机浏览器打开该地址 → 分享 → **添加到主屏幕**，即可当 APP 用
- 仓库私有也能用 Pages（说明：私有仓库的 Pages 需账号支持，若不可用就设为 public）

### 定时时间调整

改 `.github/workflows/update.yml` 里的 `cron`，注意是 **UTC 时间**：

| 想要北京时间 | cron 写法 |
|---|---|
| 每天 07:00 | `0 23 * * *`（已默认，前一天 UTC 23 点） |
| 每天 08:30 | `30 0 * * *` |
| 每天 12:00 | `0 4 * * *` |

---

## 六、目录结构

```
job-monitor/
├── config/
│   ├── sources.json      ← 抓取源配置（你要改的主要是这里）
│   └── profile.json      ← 用户画像与打分权重
├── crawler/
│   ├── crawl.py          ← 主流程：抓取 → 过滤 → 原文链接校验 → 归一化 → 去重 → 合并历史
│   ├── fetch.py          ← 三类适配器（RSS / HTML / JSON）
│   ├── normalize.py      ← 字段抽取、单位分类、城市识别、匹配度打分
│   ├── enrich.py         ← 回源补全缺失的单位名（取材详情页原文，不做推测）+ --rescore 重算
│   └── verify.py         ← 真实性核验：抽样回源请求原文链接，确认岗位真实存在
├── data/
│   ├── jobs.json         ← 产出数据（抓取结果，会被自动提交）
│   └── meta.json         ← 统计信息
├── site/
│   ├── template.html     ← 页面模板（含数据占位符）
│   └── build.py          ← 把数据注入模板，生成 dist/index.html
├── dist/index.html       ← 最终单文件站点（可部署、可直接双击打开）
├── .github/workflows/update.yml  ← 云端定时任务
└── run.sh                ← 本地一键运行
```

---

## 七、页面说明

8 个 Tab（对应 PROJECT_DOC 的志愿梯度）：

| Tab | 筛选逻辑 |
|---|---|
| 今日推荐 | `featured` 且非博士后岗 |
| 第一志愿 | 三甲医院 / 高校，且要求博士 |
| 第二志愿 | 二甲医院 / 专科院校 / 事业单位 |
| 第三志愿 | 生物医药企业等产业界机会 |
| 深圳专区 | 城市 = 深圳 |
| 其它 | 要求博士或博士后岗 |
| 全部岗位 | 全部收录，按匹配度排序 |
| 使用说明 | 打分规则与志愿梯度说明 |

支持按省份 / 类别 / 匹配度筛选 + 关键词搜索；点卡片展开任职要求、推荐理由、人才政策与原文链接；匹配度 98 分以上打星标并置顶为「重点推荐」。

---

## 八、注意事项

- 抓取频率默认每天 1 次，`sources.json` 里 `delaySeconds` 控制请求间隔（默认 1.5 秒），请勿调得过小
- 仅抓取公开招聘公告，页面版权归原发布单位所有，投递以官方原文为准
- 站点是公开静态页，不包含任何个人隐私数据；订阅邮箱只存在浏览器本地，不会上传
- 岗位按截止日期自动清理：过期或截止日期超过 90 天的会被移除（可在 `profile.json` 调整）

### 改代码时最容易踩的两个坑

**1. 不要往去重指纹里加字段。** `fingerprint()` 只用标题。曾经把 `unit` 算进去，
而 `unit` 会被 `enrich.py` 改写 → 指纹跟着变 → 每跑一次抓取，同一批岗位就重新入库一次，
两轮下来 631 条涨成 860 条。这类 bug 不报错、不崩溃，只会让数据悄悄膨胀，很难第一时间发现。
判断标准很简单：**参与去重的字段必须是数据源固有的，不能是下游加工出来的。**

**2. 改了指纹算法后，历史指纹必须重算。** `crawl.py` 里是**无条件重算** `j["fingerprint"]`，
不去读存储值——因为存储值可能是旧算法的产物，直接信任会让去重静默失效。
如果你自己写清理脚本，也要同样处理。

验证去重是否正常工作，最快的办法是**连跑两次抓取**，第二次应该「新增 0 条」：

```bash
python3 crawler/crawl.py   # 第一次
python3 crawler/crawl.py   # 第二次：应该「新增 0 条」，否则去重坏了
```
