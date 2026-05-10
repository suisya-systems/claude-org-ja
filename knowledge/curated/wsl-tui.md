# WSL2 / Windows Terminal の TUI 実装知見

WSL2 + Windows Terminal でターミナル UI を作るときに踏みやすい入力イベントの差異。

## Ctrl+Enter は crossterm では Ctrl+J として届く

TUI アプリで「WSL2 / Windows Terminal でも Ctrl+Enter を commit キーとして使いたい」場合、kitty keyboard protocol を有効化しなくても `KeyCode::Char('j') + CONTROL`（= Ctrl+J）を受け付ければよい。WSL / Windows Terminal は extended-key reporting 無効時に Ctrl+Enter を素の LF バイト (0x0A) として送ってくる。crossterm の raw-mode parser はこれを Ctrl+J に decode する。

### なぜ非自明か

- 一般的な認識は「Ctrl+Enter は kitty / modifyOtherKeys / Win32 input mode が有効でないと distinct event にならない」。
- 実際には WSL / Windows Terminal はデフォルトで Ctrl+Enter に対し LF バイトを送出している。
- crossterm 0.29 (`src/event/sys/unix/parse.rs:107` 付近) には以下のロジックがある:

  ```rust
  c @ b'\x01'..=b'\x1A' => Ok(Some(InternalEvent::Event(Event::Key(KeyEvent::new(
      KeyCode::Char((c - 0x1 + b'a') as char),
      KeyModifiers::CONTROL,
  ))))),
  ```

  これにより 0x0A (LF) → `Char('j') + CONTROL`（= Ctrl+J）。
- 0x0D (CR) は `b'\r' => KeyCode::Enter` の別分岐（Ctrl+M ではなく Enter）。
- raw mode 限定。raw mode 無効時は LF も Enter として処理されるので、TUI アプリに限り通用するルート。

### 設計上の含意（renga issue #226）

`is_overlay_commit_key` で `Char('j') + CONTROL` を accept すれば、kitty protocol を有効化せずに WSL でも Ctrl+Enter で commit できる。kitty protocol の全面有効化は Esc 処理や他キーのレポート方法を変えてしまうため、IME overlay のような modal UI に局所適用する方が副作用が少ない。

ただし「modifier が完全一致 CONTROL」に絞ること。`.contains(CONTROL)` だと `Ctrl+Shift+J` / `Ctrl+Alt+J` も commit してしまい、extended-key reporting が有効なホストで誤動作する。

### 再現方法

- 環境: WSL2 + Windows Terminal (default settings)
- アプリ側で `crossterm::event::read()` を loop し、KeyEvent を debug dump
- Ctrl+Enter を押す → `KeyEvent { code: Char('j'), modifiers: CONTROL, ... }` が出る
- 比較: 純 Linux gnome-terminal は modifyOtherKeys を有効化しない限り Ctrl+Enter で何も distinct イベントを送らない（Enter と同じ 0x0D）

### 関連リンク

- crossterm Issue #371（`\n` vs `\r` vs Ctrl+J の扱い）
- Windows Terminal Issue #879（Ctrl+Enter sends `\n` by default）
- renga PR (Issue #226 修正)

出典: `2026-05-09-wsl-ctrl-enter-arrives-as-ctrl-j.md`
