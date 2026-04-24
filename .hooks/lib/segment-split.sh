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
