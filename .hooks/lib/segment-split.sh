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
    BEGIN { in_dq=0; in_sq=0; seg=""; }
    {
      if(NR>1 && in_dq==0 && in_sq==0) { print seg; seg=""; }
      else if(NR>1) { seg = seg "\n"; }
      line=$0
      n=length(line)
      i=1
      while(i<=n) {
        c=substr(line,i,1)
        if(in_dq==0 && in_sq==0) {
          if(c=="\""){ in_dq=1; seg=seg c; i++; continue }
          if(c=="\x27"){ in_sq=1; seg=seg c; i++; continue }
          if(c==";"){ print seg; seg=""; i++; continue }
          if(c=="&" && i<n && substr(line,i+1,1)=="&"){ print seg; seg=""; i+=2; continue }
          if(c=="|" && i<n && substr(line,i+1,1)=="|"){ print seg; seg=""; i+=2; continue }
          if(c=="|"){ print seg; seg=""; i++; continue }
        } else if(in_dq==1) {
          if(c=="\""){ in_dq=0 }
        } else if(in_sq==1) {
          if(c=="\x27"){ in_sq=0 }
        }
        seg=seg c
        i++
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
#   - 純粋な変数展開 $VAR / ${VAR} は元の segment にそのまま残るため、
#     変数経由で危険フラグを与えられた場合は依然として検知できない。
#     これは Phase 1 のスコープ外（Phase 2 の sandbox / allowlist で対処）。
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
