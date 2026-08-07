# 🎵 MPlay-ThemeUI - Lightweight Music Player & Customizable Theme Interface

Language: **English** | [繁體中文](./README.md)

> **⚠️ Disclaimer & Open Source Notice**  
> This project is developed primarily for **personal entertainment, testing, and non-commercial purposes**.  
> The initial vision was to create a fast and simple music player featuring one-click library scanning, automatic categorization, and highly customizable UI themes—making music not just an audio experience, but a visual delight!  
> Built with **Python + MPV playback engine + HTML/CSS/JS frontend** and assisted by AI. Users can easily create or substitute different HTML interfaces to customize their visual experience.

---

<img width="1172" height="752" alt="image" src="https://github.com/user-attachments/assets/edb53488-7334-4a09-974d-f847c998ca6b" />
<img width="1172" height="752" alt="image" src="https://github.com/user-attachments/assets/857865c7-ce0d-4b9a-958b-bbe9236bc0db" />
<img width="1155" height="738" alt="image" src="https://github.com/user-attachments/assets/f242c00e-2a68-483c-880c-5f97122b3bc1" />

## 🌟 Key Features

* 🎵 **MPV Playback Engine**: High-fidelity support for popular audio formats (FLAC, MP3, WAV, AAC, M4A...) and video formats (MP4, MKV, WEBM...).
* 🎛️ **Audio Filtering & Enhancement**:
  * **10-Band Parametric Equalizer (Parametric EQ)** with real-time SVG frequency response curve rendering.
  * **192kHz Audio Resampling (Upsampling)** with smooth transition processing to eliminate stuttering.
  * **EBUR128 Loudness Normalization** for comfortable, balanced output across all tracks.
* 📁 **One-Click Scan & Auto Categorization**: Click `+ SCAN` to index your media folders and group tracks by folders and albums automatically.
* 🔁 **4 Playback Modes**:
  * 🔁 **Repeat All** (Playlist Loop)
  * 🔀 **Shuffle** (Random Playback)
  * 🔂 **Repeat One** (Single Track Loop)
  * 📀 **Album Continue** (Auto-advances to the next album upon completing the current one)
* 🖼️ **Full Artwork Viewer**: High-resolution cover art viewer modal accessible by clicking album art anywhere.
* 📌 **Windows PiP Mini Player**: Always-on-top Picture-in-Picture floating mini widget with full playback controls and stickers.
* 🏆 **TOP 10 Ranking & Favorites**: Automated track retention tracking with `▶ PLAY ALL` queue loading.
* 🎨 **Multiple Preset Themes**: Built-in themes including *Needy Girl Overdose*, *Cyberpunk*, *Cyber Mech HUD*, *Glassmorphism*, *Modern Dark*, and *Clean Light*, with full support for custom HTML/CSS/JS themes.

---

## 📦 Portable Download (For End Users)

If you prefer using the precompiled binary without installing Python:

1. Navigate to the **[Releases](../../releases)** page on GitHub and download the latest:
   * **`MPlayThemeUI.exe`** (Main Executable)
   * **`bin.zip`** (Core Binary Dependencies)
2. Extract `bin.zip` to obtain a `bin` folder.
3. Place `MPlayThemeUI.exe` and the `bin` folder in the **same directory**:
   ```text
   📁 MPlay-Folder/
   ├── 📄 MPlayThemeUI.exe
   └── 📁 bin/
       └── 📄 libmpv-2.dll (and other binaries)
   ```
4. Double-click `MPlayThemeUI.exe` to start enjoying your music!

---

## 🛠️ Developer Setup & Build Instructions

If you wish to modify the source code or compile the project manually:

### 1. Create Conda Environment & Install Dependencies

```bash
# Create and activate Python 3.11 environment
conda create -n mpv_ctrl python=3.11 -y
conda activate mpv_ctrl

# Install requirements
pip install -r requirements.txt
```

`requirements.txt` manifest:

```txt
pywebview>=5.0.0
python-mpv>=1.0.8
pythonnet>=3.0.0
clr-loader>=0.2.10
cffi>=1.15.0
pyinstaller>=6.10.0
pywin32-ctypes>=0.2.3
```

### 2. Run Development Version

```bash
python main.py
```

### 3. Build Standalone Executable (PyInstaller)

```bash
pyinstaller --noconfirm MPlay.spec
```

Upon completion, the standalone executable will be generated at `dist/MPlayThemeUI.exe`.

---

## 🎨 Custom HTML Theme Guide

The frontend interface is constructed purely with standard HTML/CSS/JS stored under the `ui/` directory.  
To build your own theme:

1. Add or modify an HTML file under `ui/` (e.g., `ui/my_custom_theme.html`).
2. Utilize `pywebview.api` for Python-JS IPC communication:
   - `pywebview.api.play_video(path)`
   - `pywebview.api.toggle_play_mode()`
   - `pywebview.api.get_playback_status()`
   - `pywebview.api.set_eq_gains(gains)`
3. Integrate Tailwind CSS, Glassmorphic effects, animations, or stickers to craft your personalized music interface!

---

## 📄 License & Third-Party Notices

- This project's source code is licensed under the **MIT License** for personal learning, testing, and non-commercial entertainment use.
- Core multimedia components utilize **MPV** (licensed under **LGPL-2.1 / GPL-2.0**).
