# 🎵 MPlay-ThemeUI - 簡易音樂播放器與主題試驗介面

> **⚠️ 免責聲明與開源說明**  
> 本專案開發此介面主要用於**個人娛樂測試使用，非商業用途**。  
> 初始想法是希望有一個可以快速簡易收聽音樂的程式，支援一鍵匯入與自動分類，並且具有極高客製化彈性的介面，讓音樂不僅止於聽，更能享受視覺娛樂！  
> 本專案基於 **Python + MPV 核心播放引擎 + HTML/CSS/JS 介面** 開發，並由 **AI 輔助完成**。未來如需體驗不同風格，可以直接撰寫或替換全新的 HTML 介面！

---

## 🌟 核心特色 (Features)

* 🎵 **MPV 播放引擎**：支援音訊 (FLAC, MP3, WAV, AAC, M4A...) 與影片 (MP4, MKV, WEBM...) 。
* 🎛️ **音訊過濾器**：
  * **10-Band 參量等化器 (Parametric EQ)**
  * **192kHz 重取樣 (Audio Upsampling)**
  * **EBUR128 音量響度正規化**
* 📁 **一鍵掃描與自動分類**：點擊 `+ SCAN` 自動掃描音樂庫，按資料夾與專輯自動分類。
* 🔁 **4 大播放模式 (Play Modes)**：
  * 🔁 **Repeat All**（列表循環）
  * 🔀 **Shuffle**（隨機播放）
  * 🔂 **Repeat One**（單曲循環）
  * 📀 **Album Continue**（專輯連播 — 播完此專輯自動銜接下一個專輯）
。
* 🎨 **多款主題風格**：測試開發主題（Needy Girl Overdose、Cyberpunk、Cyber Mech HUD、Glass、Modern Dark、Clean Light），並可自由編寫 HTML 擴充。

---

## 🛠️ 開發環境需求與依賴 (Environment Requirements)

專案開發建議使用 Conda 虛擬環境

### `requirements.txt` 內容：

```txt
pywebview>=5.0.0
python-mpv>=1.0.8
pythonnet>=3.0.0
clr-loader>=0.2.10
cffi>=1.15.0
pyinstaller>=6.10.0
pywin32-ctypes>=0.2.3
```

---

## 🚀 快速開始 (Quick Start)

### 1. 安裝依賴庫

```bash
# 建議啟動您的 Conda 環境
conda activate mpv_ctrl

# 安裝所需依賴
pip install -r requirements.txt
```

### 2. 運行開發版本

```bash
python main.py
```

### 3. 一鍵打包為單檔 EXE (PyInstaller)

```bash
pyinstaller --noconfirm MPlay.spec
```
打包完成後，獨立執行檔將產生於 `dist/MPlay.exe`。

---

## 🎨 客製化 HTML 主題指南 (Customizing Themes)

前端介面完全由 HTML/CSS/JS 構成，放置於 `ui/` 資料夾內。  
若您想創作屬於自己的風格：

1. 於 `ui/` 下新增或修改 HTML 檔案（例如 `my_custom_theme.html`）。
2. 使用 `pywebview.api` 進行前後端 IPC 交互：
   * `pywebview.api.play_video(path)`
   * `pywebview.api.toggle_play_mode()`
   * `pywebview.api.get_playback_status()`
   * `pywebview.api.set_eq_gains(gains)`
3. 自由調配 Tailwind CSS、Glassmorphic 風格或動畫貼圖，輕鬆打造專屬音樂播放介面！

---

## 📄 授權條款 (License)

本專案採開源方式分享，僅供個人學習、測試與非商業娛樂用途使用。
