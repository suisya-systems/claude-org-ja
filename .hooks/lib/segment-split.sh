#!/usr/bin/env bash
# Shared helper for PreToolUse hooks.
# `split_segments` reads a Bash command string from stdin and writes
# one command-segment per output line, splitting on shell separators
# (; && || | and unquoted newlines) while respecting basic quote
# boundaries.
#
# 引用符対応:
#   - " ... " と ' ... ' の中の ; && || | 改行 は区切らない。
#   - バックスラッシュエスケープ（例: `\"`、`\;`）は扱わない（簡略化）。
#   - $(...) や `...` のサブシェル境界も扱わない。worker の通常コマンドでは
#     これらを含むケースは稀なため、Phase 1 のスコープでは許容する。
#
# 使い方:
#   while IFS= read -r segment; do ... ; done < <(printf '%s' "$cmd" | split_segments)

split_segments() {
  awk '
    BEGIN { in_dq=0; in_sq=0; in_bt=0; paren_depth=0; seg=""; }
    {
      if(NR>1 && in_dq==0 && in_sq==0 && in_bt==0 && paren_depth==0) { print seg; seg=""; }
      else if(NR>1) { seg = seg "\n"; }
      line=$0
      n=length(line)
      i=1
      while(i<=n) {
        c=substr(line,i,1)
        next_c = (i<n) ? substr(line,i+1,1) : ""
        if(in_sq==1) {
          if(c=="\x27"){ in_sq=0 }
          seg=seg c; i++; continue
        }
        if(in_dq==1) {
          if(c=="\""){ in_dq=0; seg=seg c; i++; continue }
          if(c=="$" && next_c=="("){ paren_depth++; seg=seg c; i++; continue }
          if(paren_depth>0) {
            if(c=="(") paren_depth++
            if(c==")") paren_depth--
          }
          seg=seg c; i++; continue
        }
        if(in_bt==1) {
          if(c=="`"){ in_bt=0 }
          seg=seg c; i++; continue
        }
        # Outside quotes; track $() and backticks separately to avoid splitting
        # on separators that appear inside command substitutions.
        if(c=="\""){ in_dq=1; seg=seg c; i++; continue }
        if(c=="\x27"){ in_sq=1; seg=seg c; i++; continue }
        if(c=="`"){ in_bt=1; seg=seg c; i++; continue }
        if(c=="$" && next_c=="("){ paren_depth++; seg=seg c; i++; continue }
        if(paren_depth>0) {
          if(c=="(") paren_depth++
          if(c==")") paren_depth--
          seg=seg c; i++; continue
        }
        if(c==";"){ print seg; seg=""; i++; continue }
        if(c=="&" && next_c=="&"){ print seg; seg=""; i+=2; continue }
        if(c=="|" && next_c=="|"){ print seg; seg=""; i+=2; continue }
        if(c=="|"){ print seg; seg=""; i++; continue }
        seg=seg c; i++
      }
    }
    END { if(length(seg)>0) print seg }
  '
}

# `flatten_substitutions` reads a single segment from stdin and writes the
# segment with the bodies of $(...) and `...` substitutions appended at the
# end (separated by spaces). This lets downstream regex matching catch
# dangerous flags hidden behind command substitution, e.g.
#   git commit $(printf -- '--no-verify') -m x
# becomes
#   git commit $(printf -- '--no-verify') -m x  printf -- '--no-verify'
# so the `--no-verify` flag is visible to the flag-detection regex.
#
# 既知の制限:
#   - 1 段ネスト ($(... $(inner) ...)) の inner は捕捉しない（外側のみ）。
#   - $((arith)) は対象外。バッククォートと dollar-paren のみ扱う。
#   - 純粋な変数展開 $VAR / ${VAR} は flatten_substitutions では扱わない。
#     代入の収集と展開は collect_assignments / expand_known_vars が行う。
flatten_substitutions() {
  awk '
    {
      out = $0
      # Extract $(...) bodies (non-nested)
      s = $0
      while (match(s, /\$\([^()]*\)/)) {
        body = substr(s, RSTART+2, RLENGTH-3)
        out = out " " body
        s = substr(s, RSTART+RLENGTH)
      }
      # Extract `...` bodies
      s = $0
      while (match(s, /`[^`]*`/)) {
        body = substr(s, RSTART+1, RLENGTH-2)
        out = out " " body
        s = substr(s, RSTART+RLENGTH)
      }
      # Replace quote characters with spaces in the appended portion so that
      # flag tokens like --force inside printf arguments become space-delimited
      # for downstream regex matching. We replace globally; the appended bodies
      # are only used for flag detection, never for execution.
      gsub(/[\047\042]/, " ", out)
      print out
    }
  '
}

# `collect_assignments` reads multiple segments from stdin (one per line) and
# writes one `VAR=value` line per detected assignment. Quote characters around
# the value are stripped so downstream matching works on the literal token.
#
# 抽出対象:
#   - セグメント先頭の単純な VAR=val / VAR="val" / VAR='val'
#   - `export VAR=val` 形（`export` プレフィックス）
#   - インライン複数代入 `A=1 B=2 cmd ...`（コマンドの直前まで連続して並ぶ
#     全ての VAR=val を捕捉する）
#   - 代入値内のコマンド置換 `VAR=$(cmd ...)` — 値を flatten_substitutions
#     と同じ規則で平坦化する（$(...) と `...` の中身を appended）
#
# 既知の制限:
#   - eval / bash -c / 関数経由の動的構築は対象外（Phase 2 の sandbox 領域）。
collect_assignments() {
  awk '
    function emit_assign(var, val,    flat, body, s) {
      # Flatten command substitutions inside the value
      flat = val
      s = val
      while (match(s, /\$\([^()]*\)/)) {
        body = substr(s, RSTART+2, RLENGTH-3)
        flat = flat " " body
        s = substr(s, RSTART+RLENGTH)
      }
      s = val
      while (match(s, /`[^`]*`/)) {
        body = substr(s, RSTART+1, RLENGTH-2)
        flat = flat " " body
        s = substr(s, RSTART+RLENGTH)
      }
      # Strip residual quotes from flat (only used for downstream regex match)
      gsub(/[\047\042]/, " ", flat)
      print var "=" flat
    }
    {
      seg = $0
      sub(/^[ \t]+/, "", seg)
      # Strip optional `export` prefix
      if (match(seg, /^export[ \t]+/)) {
        seg = substr(seg, RLENGTH + 1)
        sub(/^[ \t]+/, "", seg)
      }
      # Loop: extract leading VAR=val tokens (one per iteration). Stop when the
      # next token is not a VAR= form (i.e. when we reach the actual command).
      while (match(seg, /^[A-Za-z_][A-Za-z0-9_]*=/)) {
        var = substr(seg, 1, RLENGTH - 1)
        rest = substr(seg, RLENGTH + 1)
        # Walk the value, tracking double-quote, single-quote, backtick, and
        # $(...) nesting so a whitespace inside any of these does not end the
        # value. Quote characters themselves are stripped from `val` so the
        # downstream regex matches the literal token; parens / backticks are
        # kept so flatten_substitutions can locate them later.
        val = ""; n = length(rest)
        in_dq = 0; in_sq = 0; in_bt = 0; paren_depth = 0; i = 1
        while (i <= n) {
          c = substr(rest, i, 1)
          next_c = (i < n) ? substr(rest, i+1, 1) : ""
          if (in_sq) {
            if (c == "\x27") { in_sq = 0; i++; continue }
            val = val c; i++; continue
          }
          if (in_dq) {
            if (c == "\"") { in_dq = 0; i++; continue }
            if (c == "$" && next_c == "(") { paren_depth++; val = val c; i++; continue }
            if (paren_depth > 0) {
              if (c == "(") paren_depth++
              if (c == ")") paren_depth--
            }
            val = val c; i++; continue
          }
          if (in_bt) {
            if (c == "`") { in_bt = 0 }
            val = val c; i++; continue
          }
          if (c == "\"") { in_dq = 1; i++; continue }
          if (c == "\x27") { in_sq = 1; i++; continue }
          if (c == "`") { in_bt = 1; val = val c; i++; continue }
          if (c == "$" && next_c == "(") { paren_depth++; val = val c; i++; continue }
          if (paren_depth > 0) {
            if (c == "(") paren_depth++
            if (c == ")") paren_depth--
            val = val c; i++; continue
          }
          if (c == " " || c == "\t") break
          val = val c; i++
        }
        if (length(val) > 0) emit_assign(var, val)
        # Advance past this assignment + any following whitespace
        seg = substr(rest, i + 1)
        sub(/^[ \t]+/, "", seg)
      }
    }
  '
}

# `expand_known_vars` reads a single segment from stdin and writes the segment
# with `$VAR` and `${VAR}` references replaced by the values supplied as
# arguments (each formatted as `VAR=value`). References to variables not in
# the supplied list are left untouched. Word-boundary aware so `$FOOBAR` is
# not replaced when only `FOO` is known.
expand_known_vars() {
  local segment
  segment=$(cat)
  local pair var val
  for pair in "$@"; do
    var="${pair%%=*}"
    val="${pair#*=}"
    segment="${segment//\$\{$var\}/$val}"
    segment=$(printf '%s' "$segment" | awk -v v="$var" -v r="$val" '
      {
        out = ""; n = length($0); i = 1
        while (i <= n) {
          c = substr($0, i, 1)
          if (c == "$" && i < n) {
            rest = substr($0, i+1)
            if (match(rest, "^" v "([^A-Za-z0-9_]|$)")) {
              out = out r
              i = i + 1 + length(v)
              continue
            }
          }
          out = out c
          i = i + 1
        }
        print out
      }
    ')
  done
  printf '%s\n' "$segment"
}
