#!/usr/bin/env bash
# 解开邮件附件里的一个 .zip，把白名单格式的文件改成生成名放进 _inbox/。
#
# 用法：bash unpack-zip.sh <zip 绝对路径> <临时目录> <_inbox 目录> <文件名前缀>
#   临时目录：<工作目录>/发票/.index/tmp/<zip 的 SHA-256 前 12 位>，脚本开始时清空、结束时删除
#   文件名前缀：<邮件 id>_<附件下标>，与这个 zip 自己在 _inbox/ 里的前缀相同
#
# 输出（逐行，制表符分隔）：
#   OK    <生成名>    <zip 里的原条目路径>     放进 _inbox/ 的文件
#   SKIP  <zip 里的原条目路径>                不在白名单里，丢弃
#   DONE  <放进 _inbox/ 的文件数>             成功，退出码 0
#   FAIL  <原因>                              失败，退出码 1；_inbox/ 不会多出任何文件
#
# 为什么是脚本而不是让 Agent 自己敲命令：zip 的条目名由发件方决定，可以写成
# `inv$(命令).pdf` 这样的形状。条目名只经过这个脚本里的变量传递、只被打印，
# 不会出现在任何一条被 shell 重新解析的命令里。
#
# 挡住的情况：条目超过 50 个；解出的文件含符号链接等非普通文件；单个文件超过
# ulimit 上限或总大小超过 100MB（zip 里声明的大小可以伪造，这里看的是解出后的实际大小）；
# 加密（不会停下来等人输入密码）；解压命令报任何错误。
# 测试用：UNPACK_ZIP_FORCE=python3 / bsdtar 跳过 unzip，直接走后备路径。平时不要设置。
set -u
Z="${1:?zip 路径}"; T="${2:?临时目录}"; I="${3:?_inbox 目录}"; P="${4:?文件名前缀}"
MAX_ENTRIES=50
MAX_TOTAL_KB=102400

fail() { rm -rf "$T"; printf 'FAIL\t%s\n' "$1"; exit 1; }

rm -rf "$T" && mkdir -p "$T" || fail '无法创建临时目录'

if command -v unzip >/dev/null 2>&1 && [ -z "${UNPACK_ZIP_FORCE:-}" ]; then
  count=$(unzip -Z1 "$Z" 2>/dev/null </dev/null | wc -l | tr -d ' ')
  [ -n "$count" ] && [ "$count" -gt 0 ] || fail '读不出条目清单（文件损坏或不是 zip）'
  [ "$count" -le "$MAX_ENTRIES" ] || fail "条目 $count 个，超过 $MAX_ENTRIES 个"
  # -P '' 让加密条目直接报错；ulimit 限制单个解出文件的大小
  ( ulimit -f 40960; unzip -o -P '' "$Z" -d "$T" ) </dev/null >/dev/null 2>&1 \
    || fail '解压失败（可能加密、损坏、单个文件过大，或条目名编码无法识别）'
elif command -v python3 >/dev/null 2>&1 && [ "${UNPACK_ZIP_FORCE:-python3}" = python3 ]; then
  ( ulimit -f 40960; python3 - "$Z" "$T" "$MAX_ENTRIES" <<'PY'
import sys, zipfile
z, t, limit = sys.argv[1], sys.argv[2], int(sys.argv[3])
with zipfile.ZipFile(z) as f:
    infos = f.infolist()
    if len(infos) > limit:
        sys.exit(3)
    if any(i.flag_bits & 0x1 for i in infos):   # 加密条目
        sys.exit(4)
    f.extractall(t)   # extractall 会去掉 .. 与绝对路径
PY
  ) </dev/null >/dev/null 2>&1 || fail '解压失败（可能加密、损坏、条目过多或单个文件过大）'
elif tar --version 2>/dev/null | grep -qi bsdtar; then
  count=$(tar -tf "$Z" 2>/dev/null </dev/null | wc -l | tr -d ' ')
  [ -n "$count" ] && [ "$count" -gt 0 ] || fail '读不出条目清单（文件损坏或不是 zip）'
  [ "$count" -le "$MAX_ENTRIES" ] || fail "条目 $count 个，超过 $MAX_ENTRIES 个"
  # --passphrase 给一个必错的口令：加密包立刻报错，不会停下来等输入
  ( ulimit -f 40960; tar -xf "$Z" -C "$T" --passphrase not-the-password ) </dev/null >/dev/null 2>&1 \
    || fail '解压失败（可能加密、损坏、单个文件过大，或条目名编码无法识别）'
else
  fail '本机没有 unzip、python3 或 bsdtar，无法解压'
fi

[ -z "$(find "$T" ! -type f ! -type d | head -n 1)" ] || fail '含符号链接等非普通文件'
total=$(du -sk "$T" | cut -f1)
[ "$total" -le "$MAX_TOTAL_KB" ] || fail "解出总大小 ${total}KB，超过 ${MAX_TOTAL_KB}KB"

n=0
while IFS= read -r -d '' f; do
  rel="${f#"$T"/}"
  ext=$(printf '%s' "${f##*.}" | tr 'A-Z' 'a-z')
  case "$ext" in
    pdf|ofd|xml|jpg|jpeg|png) ;;
    *) printf 'SKIP\t%s\n' "$rel"; continue ;;
  esac
  n=$((n + 1))
  dst="$I/${P}-${n}.${ext}"
  mv "$f" "$dst" || fail '移入 _inbox/ 失败'
  printf 'OK\t%s\t%s\n' "${dst##*/}" "$rel"
done < <(find "$T" -type f -print0 | LC_ALL=C sort -z)

rm -rf "$T"
printf 'DONE\t%s\n' "$n"
