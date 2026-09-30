#!/usr/bin/env bash
# 解开邮件附件里的一个 .zip，把白名单格式的文件改成生成名放进 _inbox/。
#
# 用法：bash unpack-zip.sh <zip 绝对路径> <临时目录> <_inbox 目录> <文件名前缀>
#   临时目录：<工作目录>/发票/.index/tmp/<zip 的 SHA-256 前 12 位>，必须是这个形状，
#             否则脚本拒绝执行（它会被清空，写错成 _inbox/ 就会删掉用户的文件）
#   文件名前缀：<邮件 id>_<附件下标>，与这个 zip 自己在 _inbox/ 里的前缀相同
#
# 输出（逐行，制表符分隔）：
#   OK    <生成名>    <zip 里的原条目路径>     放进 _inbox/ 的文件
#   SKIP  <zip 里的原条目路径>                不在白名单里，丢弃
#   DONE  <放进 _inbox/ 的文件数>             成功，退出码 0
#   FAIL  <原因>                              失败，退出码 1；这次放进 _inbox/ 的文件已撤回
# 原条目路径里的控制字符（换行、制表符等）打印前已删掉，一个条目只占一行。
#
# 为什么是脚本而不是让 Agent 自己敲命令：zip 的条目名由发件方决定，可以写成
# `inv$(命令).pdf` 这样的形状。条目名只经过这个脚本里的变量传递、只被打印，
# 不会出现在任何一条被 shell 重新解析的命令里。
#
# 挡住的情况：条目超过 50 个；条目是符号链接（解压前按条目属性查，解压后再查一次
# 非普通文件）；单个文件超过 ulimit 上限或总大小超过 100MB（zip 里声明的大小可以伪造，
# 这里看的是解出后的实际大小；Windows 原生程序不受 ulimit 约束，只剩事后检查）；
# 加密（不会停下来等人输入密码）；解压命令报任何错误。
#
# 解压方式依次尝试 unzip、python3、bsdtar，前一种失败（比如 GBK 条目名 unzip 解不开）
# 就换下一种；符号链接、条目过多、总大小超限这类安全拒绝不再换方式重试。
# 测试用：UNPACK_ZIP_ONLY=unzip / python3 / bsdtar 只用指定的一种。平时不要设置。
set -u
Z="${1:?zip 路径}"; T="${2:?临时目录}"; I="${3:?_inbox 目录}"; P="${4:?文件名前缀}"
MAX_ENTRIES=50
MAX_TOTAL_KB=102400

say() { printf '%s\t%s\n' "$1" "$2"; }
reject() { say FAIL "$1"; exit 1; }   # 还没碰任何目录时的失败

# ---- 参数校验：临时目录会被清空，必须确认它真的是 .index/tmp/<12 位十六进制> ----
T="${T%/}"; I="${I%/}"
tmp_leaf="${T##*/}"
case "$T" in */.index/tmp/*) ;; *) reject '临时目录必须位于 .index/tmp/ 下' ;; esac
[[ "$tmp_leaf" =~ ^[0-9a-f]{12}$ ]] || reject '临时目录名必须是 zip SHA-256 的前 12 位'
[ "${T%/*}" = "${T%/.index/tmp/*}/.index/tmp" ] || reject '临时目录必须直接位于 .index/tmp/ 下'
case "$I/" in "$T"/*) reject '_inbox 目录不能在临时目录里' ;; esac
case "$T/" in "$I"/*) reject '临时目录不能在 _inbox 目录里' ;; esac
[ -d "$I" ] || reject '_inbox 目录不存在'
[ -f "$Z" ] || reject 'zip 文件不存在'
[[ "$P" =~ ^[A-Za-z0-9._-]+$ ]] || reject '文件名前缀只能含字母、数字和 . _ -'

moved=()
fail() {
  local f
  for f in "${moved[@]+"${moved[@]}"}"; do rm -f "$f"; done
  rm -rf "$T"
  reject "$1"
}
clean_tmp() { rm -rf "$T" && mkdir -p "$T"; }

# ---- 解压方式 ----
have_unzip()   { command -v unzip >/dev/null 2>&1 && unzip -Z1 "$Z" >/dev/null 2>&1 </dev/null; }
have_python()  { command -v python3 >/dev/null 2>&1 && python3 -c 'import zipfile' >/dev/null 2>&1 </dev/null; }
bsdtar_bin() {
  if tar --version 2>/dev/null </dev/null | grep -qi bsdtar; then echo tar; return 0; fi
  local w="${SYSTEMROOT:-${SystemRoot:-}}"
  if [ -n "$w" ]; then
    local p
    p="$(cygpath -u "$w" 2>/dev/null || printf '%s' "$w")/System32/tar.exe"
    [ -x "$p" ] && { echo "$p"; return 0; }
  fi
  return 1
}

# 安全检查：返回 0 通过；打印 REJECT 原因并返回 2 表示安全拒绝；返回 1 表示这种方式读不了
precheck_unzip() {
  local n
  n=$(unzip -Z1 "$Z" 2>/dev/null </dev/null | wc -l | tr -d ' ') || return 1
  [ -n "$n" ] && [ "$n" -gt 0 ] || return 1
  [ "$n" -le "$MAX_ENTRIES" ] || { echo "条目 $n 个，超过 $MAX_ENTRIES 个"; return 2; }
  if unzip -Z "$Z" 2>/dev/null </dev/null | grep -q '^l'; then echo '含符号链接条目'; return 2; fi
  return 0
}
precheck_bsdtar() {
  local n
  n=$("$1" -tf "$Z" 2>/dev/null </dev/null | wc -l | tr -d ' ') || return 1
  [ -n "$n" ] && [ "$n" -gt 0 ] || return 1
  [ "$n" -le "$MAX_ENTRIES" ] || { echo "条目 $n 个，超过 $MAX_ENTRIES 个"; return 2; }
  if "$1" -tvf "$Z" 2>/dev/null </dev/null | grep -q '^l'; then echo '含符号链接条目'; return 2; fi
  return 0
}

extract_unzip() { ( ulimit -f 40960; unzip -o -P '' "$Z" -d "$T" ) </dev/null >/dev/null 2>&1; }
extract_bsdtar() { ( ulimit -f 40960; "$1" -xf "$Z" -C "$T" --passphrase not-the-password ) </dev/null >/dev/null 2>&1; }
# python：退出码 3 条目过多、5 符号链接（安全拒绝），其余非 0 为读不了
extract_python() {
  ( ulimit -f 40960; python3 - "$Z" "$T" "$MAX_ENTRIES" <<'PY'
import stat, sys, zipfile
z, t, limit = sys.argv[1], sys.argv[2], int(sys.argv[3])
with zipfile.ZipFile(z) as f:
    infos = f.infolist()
    if len(infos) > limit:
        sys.exit(3)
    if any(stat.S_ISLNK(i.external_attr >> 16) for i in infos):
        sys.exit(5)
    if any(i.flag_bits & 0x1 for i in infos):   # 加密条目
        sys.exit(4)
    # zipfile 不会写出超过声明大小的数据，所以声明大小之和就是硬上限；
    # Windows 原生 python 不受 ulimit 约束，靠这一条挡住压缩炸弹
    if sum(i.file_size for i in infos) > 100 * 1024 * 1024:
        sys.exit(6)
    f.extractall(t)   # extractall 会去掉 .. 与绝对路径
PY
  ) </dev/null >/dev/null 2>&1
}

only="${UNPACK_ZIP_ONLY:-}"
ok=0; last='没有能读这个包的解压方式（本机缺 unzip / python3 / bsdtar，或都读不了它）'
for m in unzip python3 bsdtar; do
  [ -z "$only" ] || [ "$only" = "$m" ] || continue
  clean_tmp || fail '无法创建临时目录'
  case "$m" in
    unzip)
      have_unzip || continue
      why=$(precheck_unzip); rc=$?
      [ $rc -eq 2 ] && fail "$why"
      [ $rc -eq 0 ] || continue
      extract_unzip && { ok=1; break; }
      last='解压失败（可能加密、损坏、单个文件过大，或条目名编码无法识别）' ;;
    python3)
      have_python || continue
      extract_python; rc=$?
      [ $rc -eq 0 ] && { ok=1; break; }
      [ $rc -eq 3 ] && fail "条目超过 $MAX_ENTRIES 个"
      [ $rc -eq 5 ] && fail '含符号链接条目'
      [ $rc -eq 6 ] && fail '条目声明的总大小超过 100MB'
      last='解压失败（可能加密、损坏或单个文件过大）' ;;
    bsdtar)
      tb=$(bsdtar_bin) || continue
      why=$(precheck_bsdtar "$tb"); rc=$?
      [ $rc -eq 2 ] && fail "$why"
      [ $rc -eq 0 ] || continue
      extract_bsdtar "$tb" && { ok=1; break; }
      last='解压失败（可能加密、损坏、单个文件过大，或条目名编码无法识别）' ;;
  esac
done
[ "$ok" -eq 1 ] || fail "$last"

[ -z "$(find "$T" ! -type f ! -type d | head -n 1)" ] || fail '含符号链接等非普通文件'
total=$(du -sk "$T" | cut -f1)
[ "$total" -le "$MAX_TOTAL_KB" ] || fail "解出总大小 ${total}KB，超过 ${MAX_TOTAL_KB}KB"

[ -d "$T" ] || fail '临时目录在解包途中被删'
n=0
while IFS= read -r -d '' f; do
  rel=$(printf '%s' "${f#"$T"/}" | LC_ALL=C tr -d '\001-\037\177')
  # macOS「压缩」顺带打进去的元数据（__MACOSX/、._ 开头的文件）不是发票
  case "/$rel" in */__MACOSX/*|*/._*) say SKIP "$rel"; continue ;; esac
  ext=$(printf '%s' "${f##*.}" | tr 'A-Z' 'a-z')
  case "$ext" in
    pdf|ofd|xml|jpg|jpeg|png) ;;
    *) say SKIP "$rel"; continue ;;
  esac
  n=$((n + 1))
  dst="$I/${P}-${n}.${ext}"
  mv "$f" "$dst" || fail '移入 _inbox/ 失败'
  moved+=("$dst")
  printf 'OK\t%s\t%s\n' "${dst##*/}" "$rel"
done < <(find "$T" -type f -print0 | LC_ALL=C sort -z)
# 进程替换里的 find 失败不会让循环报错：临时目录要是在上面这段途中被删，这里会拿到 0 个文件、
# 输出 DONE 0，zip 被当成「已解包」记下再删掉——里面的票就静默丢了
[ -d "$T" ] || fail '临时目录在解包途中被删'

rm -rf "$T"
say DONE "$n"
