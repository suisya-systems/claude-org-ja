# wsl-windows-chrome-screenshot の経緯と出典

[`.claude/skills/wsl-windows-chrome-screenshot/SKILL.md`](../SKILL.md) の経緯。手順と制約は SKILL.md 側にある。

## 経緯

- Windows 版 Chrome を使う判断は rondo#318 の申し送り（Linux 側 Chrome は共有ライブラリ不足で起動しない）。
- SKILL.md の手順は rondo#314 / #318 / #350 / #351 / #352（2026-09-20〜21）で繰り返し踏んだ失敗を固定したもの。
- §0「sandbox を外す撮影は窓口が行う」は、2026-09-22 に auto mode のワーカーが
  `dangerouslyDisableSandbox: true` の呼び出しを分類器に `[Safety Bypass Flag]` で 2 回拒否されたことによる
  （rondo#411-#413）。

## 出典

いずれも当時の machine-local な振り返りメモ（`knowledge/raw/` は追跡外）。

- `knowledge/raw/archive/2026-09-21-wsl-chrome-screenshot-and-pgrep.md`（rondo#314）
- `knowledge/raw/archive/2026-09-21-windows-chrome-first-shot-refused.md`（rondo#318）
- `knowledge/raw/archive/2026-09-21-before-after-shots-need-two-builds-and-no-sandbox.md`（rondo#350）
- `knowledge/raw/archive/2026-09-21-screenshot-server-and-chrome-in-one-bash-call.md`（rondo#351）
- `knowledge/raw/archive/2026-09-21-wsl-chrome-shots-need-sandbox-off-on-both-legs.md`（rondo#352）
- `knowledge/raw/archive/2026-09-20-wsl-windows-chrome-file-url-screenshots.md`（rondo#319、`file://` とウィンドウ高さ）
- `knowledge/raw/archive/2026-09-22-screenshot-skill-blocked-by-auto-mode-classifier.md`（rondo#411-#413、§0）
