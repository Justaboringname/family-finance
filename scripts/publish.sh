#!/bin/bash
# 把代码发布到 GitHub 上的远端仓库（脱敏版）。本地仓库的提交历史不推：以前的提交说明和旧版本文件里有个人信息。
#
#   scripts/publish.sh "这次发布的说明"          发布当前 HEAD
#   scripts/publish.sh --check                  只做导出和检查，不提交、不推送
#
# 做法：
# 1. 用 git archive 导出 HEAD 的入库文件（没提交的改动不会发出去），去掉 PRIVATE 里列的文件；
# 2. 用 data/private_terms.txt（不入库；一行一个正则，# 开头是注释）检查导出的内容，有一处命中就停；
#    再用 scripts/publish_scan.py 拿 data/finance.db 里的真实记录反查一遍（日期、批次、金额、备注），对得上也停；
# 3. 在 .publish/（不入库；远端仓库的一个 clone）里换成这份内容，提交一次，推送。远端的历史就是一次次发布。
# 远端地址存在本仓库的 git 配置里：git config publish.url <地址>。不要把它加成 remote，免得顺手把本地历史推上去。
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
PRIVATE=(CLAUDE.md)          # 本机的交接笔记：有个人的税务口径和家里的事，不发布
TERMS="data/private_terms.txt"

check_only=""
[ "${1:-}" = "--check" ] && { check_only=1; shift; }
msg="${1:-同步 $(date +%F)}"

[ -f "$TERMS" ] || { echo "缺 $TERMS：没有这份清单就没法检查个人信息，不发布。" >&2; exit 1; }
url="$(git config --get publish.url || true)"
[ -n "$url" ] || [ -n "$check_only" ] || { echo "还没设远端：git config publish.url <地址>" >&2; exit 1; }
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "提示：有没提交的改动，发布的是已提交的 HEAD，不含这些改动。" >&2
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
git archive HEAD | tar -x -C "$tmp"
for f in "${PRIVATE[@]}"; do rm -rf "${tmp:?}/$f"; done

# 检查：清单里的词（姓名、账户尾号、真实金额、邮箱等）一处都不能出现，文件名也查
pat="$tmp/.terms"
grep -v -E '^\s*(#|$)' "$TERMS" >"$pat"
hits="$( (cd "$tmp" && { grep -rInE -f .terms --exclude=.terms . || true; find . -type f | grep -E -f .terms || true; }) | cut -c1-200)"
rm -f "$pat"
if [ -n "$hits" ]; then
  echo "导出的内容里有个人信息，没有发布：" >&2
  echo "$hits" >&2
  exit 2
fi
# 第二道：拿本机真实数据库反查。清单靠人想到才写得进去，照搬进测试的真实批次、出入境日期它查不出来
python3 -I scripts/publish_scan.py "$tmp" || { echo "没有发布。" >&2; exit 2; }
n="$(find "$tmp" -type f | wc -l | tr -d ' ')"
if [ -n "$check_only" ]; then
  echo "检查通过：$n 个文件，没有命中 $TERMS，也没有和真实数据库对得上的内容。"
  exit 0
fi

pub="$ROOT/.publish"
if [ ! -d "$pub/.git" ]; then
  rm -rf "$pub"
  git clone -q "$url" "$pub" 2>/dev/null || { mkdir -p "$pub"; git -C "$pub" init -q -b main; git -C "$pub" remote add origin "$url"; }
fi
git -C "$pub" remote set-url origin "$url"
git -C "$pub" config user.name "$(git config user.name)"
git -C "$pub" config user.email "$(git config user.email)"
git -C "$pub" fetch -q origin 2>/dev/null && git -C "$pub" rev-parse -q --verify origin/main >/dev/null && git -C "$pub" reset -q --hard origin/main || true
git -C "$pub" checkout -q -B main

rsync -a --delete --exclude .git "$tmp"/ "$pub"/
git -C "$pub" add -A
if git -C "$pub" diff --cached --quiet; then
  echo "远端已经是最新的，没有要发布的改动。"
  exit 0
fi
git -C "$pub" commit -q -m "$msg"
git -C "$pub" push -q -u origin main
echo "已发布 $n 个文件到 $url（$(git -C "$pub" log -1 --format=%h)）。"
