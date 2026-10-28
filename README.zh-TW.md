<p align="center"><img src="docs/assets/bot-avatar.jpg" alt="Local Companion 專案角色圖" width="240"><br><sub>Malfoid｜性轉馬份</sub></p>

# Local Companion

**在本機生活、與你聊天，也讓你看見判斷流程的 AI 角色引擎。**

[English](README.md) · [架構](docs/ARCHITECTURE.md) · [限制](docs/LIMITATIONS.md)

這是實驗版，不保證角色永遠記得或判斷正確。主打三件事：

- 持續生活：行程、換場、目前衣著與日常小事件。
- 決策與措辭分離：先根據狀態選行為，再讓 Writer 表達。
- 可追查：同輪 Observer 輸入、原始輸出與 Writer Prompt 都能看。

## 核心架構一眼看懂

![六個核心與資料交接](docs/diagrams/cores.svg)

**理解提出事件 → 規則選行為 → 口吻決定說法 → 編輯組Prompt → Writer開口。**
故事核提供有來源的過去資訊，狀態機確認當前事實；六個核心不代表六個模型。

[四張架構圖與完整中文說明](docs/CORES.zh-TW.md) · [程式對照與Mermaid來源](docs/ARCHITECTURE.md)

## 先看展示，不用下載模型

```sh
python3 scripts/prompt_viewer.py --demo --port 8768
```

開啟 http://127.0.0.1:8768/ 。全部是合成資料，只展示檢視介面。

## 安裝聊天服務

目前以 Apple Silicon Mac、Python 3.12 為主；過去 M4 16GB 已運行9B Q4與8K。
Linux與Windows尚未驗證。先安裝 [uv](https://docs.astral.sh/uv/getting-started/installation/)。

```sh
uv sync --python 3.12 --extra setup
cp .env.example .env
uv run scripts/init_demo.py
uv run --extra setup scripts/setup_local.py --chat
uv run scripts/configure_telegram.py
```

接著分兩個終端執行 `uv run scripts/start_chat.py` 與 `uv run -m companion`。
模型另行下載，未附權重。公開預設選用一般Qwen3.5-9B GGUF，並未沿用私人微調模型，
因此不能照搬先前速度或品質結果。Telegram Token在自己的終端輸入。

原創角色林青28歲，居住於虛構星港世界；初始使用者Profile為空。
主動訊息預設關閉，可從 `/settings` 啟用。普通文字最後一則輸入後等10秒再合併處理。
`/outfit` 改現況衣著，`/language CH` 設語言，`/debug last` 追查上輪。

```sh
uv run scripts/prompt_viewer.py --database .data/telegram/memory.sqlite3
uv run -m companion.workbench --port 8765
```

前者看實際Prompt，後者測決策規則，彼此獨立。媒體與Mini App的額外設定見
[SETUP](docs/SETUP.md)。模型會混淆人物、階段或補寫細節，數值是工程規則而非心理測量。
本機會記錄私人原文與完整Prompt；請勿上傳資料庫、截圖或未清理的log。

歡迎針對「狀態正確性、安裝可重現、記憶取材」提issue或PR；請使用合成例子。

公開候選版已於乾淨解壓目錄完成安裝與830項合成測試；詳見[驗證範圍](docs/VALIDATION.md)，不代表實際模型語義全部正確。
