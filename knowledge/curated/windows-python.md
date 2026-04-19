# Windows Python 実行環境

## py コマンドを使うべき理由
Windows環境では `python` や `python3` コマンドがMicrosoft Store版（`AppData/Local/Microsoft/WindowsApps/python.exe`）にリダイレクトされ、exit code 49 で失敗することがある。`py -3` コマンドを使うことで実際のPython（`AppData/Local/Programs/Python/Python310/python.exe`）を確実に呼び出せる。bash環境でも `py -3` は動作する。

直接パス指定（`/c/Users/iwama/AppData/Local/Programs/Python/Python310/python.exe`）も確実だが、`py` コマンドの方が簡潔。

## 日本語出力の文字化け対策
openpyxlで日本語を含むExcelを読む際、bashのデフォルト出力ではShift_JIS系の文字化けが発生する。対策:
1. `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')` をスクリプト冒頭に入れる
2. または `2>&1 | cat` をパイプする

数値データは文字化けの影響を受けないため、数値検証のみの場合は対策不要。

## 動作確認済み環境
- Python 3.10.11 + openpyxl 3.1.5 で動作確認済み
