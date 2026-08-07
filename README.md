# 🎵 MPlay-ThemeUI - 簡易音樂播放器與主題試驗介面

Language: **繁體中文** | [English](./README_EN.md)

> **⚠️ 免責聲明與開源說明**  
> 本專案開發此介面主要用於**個人娛樂測試使用，非商業用途**。  
> 初始想法是希望有一個可以快速簡易收聽音樂的程式，支援一鍵匯入與自動分類，並且具有極高客製化彈性的介面，讓音樂不僅止於聽，更能享受視覺娛樂！  
> 本專案基於 **Python + MPV 核心播放引擎 + HTML/CSS/JS 介面** 開發，並由 **AI 輔助完成**。未來如需體驗不同風格，可以直接撰寫或替換全新的 HTML 介面！

---
<img width="1172" height="752" alt="image" src="https://github.com/user-attachments/assets/edb53488-7334-4a09-974d-f847c998ca6b" />
<img width="1172" height="752" alt="image" src="https://github.com/user-attachments/assets/857865c7-ce0d-4b9a-958b-bbe9236bc0db" />
<img width="1155" height="738" alt="image" src="https://github.com/user-attachments/assets/f242c00e-2a68-483c-880c-5f97122b3bc1" />

## 🌟 核心特色 (Features)

* 🎵 **MPV 播放引擎**：支援常見音訊 (FLAC, MP3, WAV, AAC, M4A...) 與影片格式 (MP4, MKV, WEBM...)。
* 🎛️ **音訊過濾與強化**：
  * **10-Band 參量等化器 (Parametric EQ)**
  * **192kHz 音訊重取樣 (Audio Upsampling)**
  * **EBUR128 音量響度正規化**
* 📁 **一鍵掃描與自動分類**：點擊 `+ SCAN` 自動掃描音樂庫，按資料夾與專輯自動整理。
* 🔁 **4 大播放模式 (Play Modes)**：
  * 🔁 **Repeat All**（列表循環）
  * 🔀 **Shuffle**（隨機播放）
  * 🔂 **Repeat One**（單曲循環）
  * 📀 **Album Continue**（專輯連播 — 播完當前專輯後自動銜接下一個專輯）
* 🎨 **多款主題風格**：內建測試開發主題（Needy Girl Overdose、Cyberpunk、Cyber Mech HUD、Glass、Modern Dark、Clean Light），並支援自由編寫 HTML/CSS/JS 進行擴充。

---

## 📦 免安裝便攜版下載 (For End Users)

如果您不想安裝 Python 開發環境，只想直接使用播放器：

1. 前往本專案右側的 **[Releases](../../releases)** 頁面下載最新版本的：
   * **`MPlayThemeUI.exe`**（主程式）
2. 雙擊執行 `MPlayThemeUI.exe` 即可開始收聽！

---

## 🛠️ 開發環境建置 (For Developers)

如果您想自行修改源碼或編譯專案，請參考以下步驟：
1. 前往本專案右側的 **[Releases](../../releases)** 頁面下載最新版本的：
   * **`bin.zip`**（核心依賴組件）
2. 將 `bin.zip` 解壓縮，獲得一個 `bin` 資料夾。
3. 將 `bin` 資料夾放置於**同一個目錄下**：
   ```text
   📁 專案資料夾/
   ├── 📄 main.py
   └── 📁 bin/
       └── 📄 libmpv-2.dll (及其他元件)
   ```
---
### 1. 建立 Conda 虛擬環境與安裝依賴

```bash
# 建立並啟動 Python 3.11 虛擬環境
conda create -n mpv_ctrl python=3.11 -y
conda activate mpv_ctrl

# 安裝專案所需依賴
pip install -r requirements.txt
```

`requirements.txt` 清單：

```txt
pywebview>=5.0.0
python-mpv>=1.0.8
pythonnet>=3.0.0
clr-loader>=0.2.10
cffi>=1.15.0
pyinstaller>=6.10.0
pywin32-ctypes>=0.2.3
```

### 2. 運行開發版本

```bash
python main.py
```

### 3. 一鍵打包為單檔 EXE (PyInstaller)

```bash
pyinstaller --noconfirm MPlay.spec
```

打包完成後，獨立執行檔將產生於 `dist/MPlayThemeUI.exe`。

---

## 🎨 客製化 HTML 主題指南 (Customizing Themes)

前端介面完全由 HTML/CSS/JS 構成，儲存於 `ui/` 資料夾內。  
若您想創作屬於自己的客製化主題：

1. 於 `ui/` 資料夾下新增或修改 HTML 檔案（例如 `my_custom_theme.html`）。
2. 使用 `pywebview.api` 進行前後端 IPC 交互：
   - `pywebview.api.play_video(path)`
   - `pywebview.api.toggle_play_mode()`
   - `pywebview.api.get_playback_status()`
   - `pywebview.api.set_eq_gains(gains)`
3. 可自由調配 Tailwind CSS、Glassmorphic 視覺風格、Rive 動畫或貼圖，打造獨一無二的專屬播放介面！

---

## 📄 授權條款與第三方聲明 (License & Notices)

- 本專案原始碼採 MIT License 釋出，僅供個人學習、測試與非商業娛樂用途使用。
- 多媒體核心組件採用 MPV (遵循 LGPL-2.1 / GPL-2.0 開源授權)。
