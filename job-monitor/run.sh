#!/usr/bin/env bash
# 君来暴富 · 招聘监控　本地一键运行脚本
#
#   ./run.sh            抓取 + 生成站点
#   ./run.sh --open     抓取 + 生成 + 打开浏览器
#   ./run.sh --serve    抓取 + 生成 + 起本地服务（手机连同一 WiFi 可访问）
#   ./run.sh --enrich   抓取后回源详情页补全缺失的单位名（取材原文，不做推测）
#   ./run.sh --verify   抓取后抽样回源核验每条岗位在公开网络上真实存在
#   ./run.sh --rebuild  忽略历史数据全量重建（改了打分规则后用）
#
# 真实性红线：本系统只收录公开网络真实抓取到的岗位，不生成任何虚构岗位。
# 抓不到就是空页，绝不用示例数据填充。
set -euo pipefail

cd "$(dirname "$0")"

PY="${PY:-}"
if [ -z "$PY" ]; then
  for cand in \
    "/Users/chris/.workbuddy/binaries/python/versions/3.13.12/bin/python3" \
    "$(command -v python3 || true)"; do
    if [ -n "$cand" ] && [ -x "$cand" ]; then PY="$cand"; break; fi
  done
fi
if [ -z "$PY" ]; then
  echo "未找到 python3，请先安装 Python 3.9+ 或设置 PY 环境变量" >&2
  exit 1
fi

CRAWL_ARGS=()
MODE="build"
VERIFY=0
ENRICH=0
for arg in "$@"; do
  case "$arg" in
    --open)    MODE="open" ;;
    --serve)   MODE="serve" ;;
    --verify)  VERIFY=1 ;;
    --enrich)  ENRICH=1 ;;
    --rebuild) CRAWL_ARGS+=("--rebuild") ;;
    *) echo "未知参数：$arg" >&2; exit 1 ;;
  esac
done

echo "▶ 使用解释器：$PY"
echo "▶ 步骤 1　抓取岗位数据"
"$PY" crawler/crawl.py "${CRAWL_ARGS[@]+"${CRAWL_ARGS[@]}"}"

if [ "$ENRICH" = "1" ]; then
  echo "▶ 步骤 2　回源补全缺失的单位信息"
  "$PY" crawler/enrich.py --max 80 || echo "（补全过程异常，已跳过）"
else
  # 不联网，只按当前字段重算派生信息（改了分档阈值/打分权重后用）
  "$PY" crawler/enrich.py --rescore || echo "（重算派生信息异常，已跳过）"
fi

if [ "$VERIFY" = "1" ]; then
  echo "▶ 步骤 3　真实性核验（抽样回源）"
  "$PY" crawler/verify.py --sample 8 || echo "（核验存在问题，见上方明细）"
fi

echo "▶ 步骤 4　生成单文件站点"
"$PY" site/build.py

OUT="$PWD/dist/index.html"
echo "✅ 完成：$OUT"

case "$MODE" in
  open)
    command -v open >/dev/null 2>&1 && open "$OUT" || echo "请手动打开 $OUT"
    ;;
  serve)
    PORT="${PORT:-8765}"
    echo "▶ 本地预览：http://localhost:$PORT"
    echo "  手机访问：http://$(ipconfig getifaddr en0 2>/dev/null || echo '<本机IP>'):$PORT"
    "$PY" -m http.server "$PORT" -d dist
    ;;
esac
