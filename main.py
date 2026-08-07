import ctypes
import os
import sys
import time
import json
import subprocess

import threading
import base64
import gc
import shutil
import ctypes
import random
import atexit
import msvcrt
from ctypes import windll, byref, c_size_t, sizeof, POINTER, c_void_p
from ctypes.wintypes import HANDLE, BOOL, DWORD, WORD, LONG

# --- 解決 ctypes 相容性 ---
SIZE_T = c_size_t
# 用於視窗控制


import webview
from database import Database

# --- 原生 libmpv-2.dll 動態搜尋與加載 ---
bin_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "bin"))
if os.path.exists(bin_path):
    os.environ["PATH"] = bin_path + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, 'add_dll_directory'):
        try:
            os.add_dll_directory(bin_path)
        except: pass

try:
    import mpv
except Exception as _e:
    print(f"[Warning] Failed to import python-mpv: {_e}")
user32 = windll.user32
SW_MINIMIZE = 6
SW_RESTORE = 9
SW_SHOW = 5
def get_hwnds_for_pid(pid):
    """透過 PID 找出該程序所有的視窗 Handle (修正指標版)"""
    def callback(hwnd, p_hwnds):
        if user32.IsWindowVisible(hwnd) and user32.IsWindowEnabled(hwnd):
            process_id = DWORD()
            user32.GetWindowThreadProcessId(hwnd, byref(process_id))
            
            if process_id.value == pid:
                # 【關鍵修正】
                # p_hwnds 是一個 C 指標，必須用 .contents.value 取得 Python 列表
                p_hwnds.contents.value.append(hwnd)
        return True
    
    hwnds = []
    # 定義 callback 類型：第二個參數是 POINTER(ctypes.py_object)
    WNDENUMPROC = ctypes.WINFUNCTYPE(BOOL, HANDLE, POINTER(ctypes.py_object))
    
    # 傳入 hwnds 列表的指標
    user32.EnumWindows(WNDENUMPROC(callback), ctypes.byref(ctypes.py_object(hwnds)))
    return hwnds

# ==========================================
# 1. 進階 Windows API (用於查找子程序)
# ==========================================
TH32CS_SNAPPROCESS = 0x00000002

class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [("dwSize", DWORD),
                ("cntUsage", DWORD),
                ("th32ProcessID", DWORD),
                ("th32DefaultHeapID", POINTER(LONG)),
                ("th32ModuleID", DWORD),
                ("cntThreads", DWORD),
                ("th32ParentProcessID", DWORD),
                ("pcPriClassBase", LONG),
                ("dwFlags", DWORD),
                ("szExeFile", ctypes.c_char * 260)]

def get_child_pids(parent_pid):
    """找出指定 PID 的所有子程序 PID (包含 WebView2 和 MPV)"""
    pids = []
    snap = windll.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1: return []

    entry = PROCESSENTRY32()
    entry.dwSize = sizeof(PROCESSENTRY32)

    if windll.kernel32.Process32First(snap, byref(entry)):
        while True:
            if entry.th32ParentProcessID == parent_pid:
                pids.append(entry.th32ProcessID)
            if not windll.kernel32.Process32Next(snap, byref(entry)):
                break
    
    windll.kernel32.CloseHandle(snap)
    return pids

def trim_process_memory(pid):
    """對指定 PID 執行記憶體壓縮"""
    try:
        # PROCESS_ALL_ACCESS = 0x1F0FFF, 但我們只需要 PROCESS_SET_QUOTA (0x0100) 和 PROCESS_QUERY_INFORMATION (0x0400)
        # 為了保險，使用 PROCESS_ALL_ACCESS 權限開啟
        handle = windll.kernel32.OpenProcess(0x1F0FFF, False, pid)
        if handle:
            windll.psapi.EmptyWorkingSet(handle)
            windll.kernel32.CloseHandle(handle)
            return True
    except: 
        return False
    return False

# ==========================================
# 2. 記憶體優化工具 (升級版：家族連坐)
# ==========================================
# ==========================================
# 2. 記憶體優化工具 (輕量化 GC)
# ==========================================
def trim_memory():
    """極致輕量化：執行 GC 回收與 Windows EmptyWorkingSet 記憶體壓縮"""
    try:
        gc.collect()
        pid = os.getpid()
        trim_process_memory(pid)
        for child_pid in get_child_pids(pid):
            trim_process_memory(child_pid)
    except: pass

# ==========================================
# 3. Windows Job Object (確保 MPV 關閉)
# ==========================================
class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [('PerProcessUserTimeLimit', c_size_t),
                ('PerJobUserTimeLimit', c_size_t),
                ('LimitFlags', DWORD),
                ('MinimumWorkingSetSize', SIZE_T),
                ('MaximumWorkingSetSize', SIZE_T),
                ('ActiveProcessLimit', DWORD),
                ('Affinity', SIZE_T),
                ('PriorityClass', DWORD),
                ('SchedulingClass', DWORD)]

class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [('BasicLimitInformation', JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ('IoInfo', c_size_t * 4),
                ('ProcessMemoryLimit', SIZE_T),
                ('JobMemoryLimit', SIZE_T),
                ('PeakProcessMemoryUsed', SIZE_T),
                ('PeakJobMemoryUsed', SIZE_T)]

def assign_job_limit(handle):
    try:
        job = windll.kernel32.CreateJobObjectW(None, None)
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x2000 
        windll.kernel32.SetInformationJobObject(job, 9, byref(info), ctypes.sizeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION))
        windll.kernel32.AssignProcessToJobObject(job, handle)
        return job
    except: return None

# --- 全域 PID (用於強制獵殺) ---
GLOBAL_MPV_PID = None

def force_kill_mpv():
    global GLOBAL_MPV_PID
    if GLOBAL_MPV_PID:
        try:
            subprocess.run(f"taskkill /F /T /PID {GLOBAL_MPV_PID}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except: pass
        GLOBAL_MPV_PID = None

atexit.register(force_kill_mpv)

# --- Config ---
class ConfigManager:
    def __init__(self):
        self.config_path = self._get_resource_path('config.json')
        self.default_config = {
            "mpv_path": r"./bin/mpv.exe",
            "ipc_pipe": r"\\.\pipe\mpv_satellite_v3",
            "startup_args": "--geometry=50%x50%",
            "theme": "dark",
            "boot_music": True,
            "theme_file": "needy_theme.html"
        }
        self.data = self.default_config.copy()
        self.load_config()

    def _get_resource_path(self, relative_path):
        try: base_path = sys._MEIPASS
        except: base_path = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base_path, relative_path)

    def load_config(self):
        if not os.path.exists(self.config_path):
            self.save_config(self.default_config)
            return
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.data.update(json.load(f))
        except: pass

    def save_config(self, new_data):
        self.data.update(new_data)
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=4, ensure_ascii=False)
        except: pass

    def get(self, key, default=None):
        val = self.data.get(key)
        if val is not None:
            return val
        if default is not None:
            return default
        return self.default_config.get(key)

config = ConfigManager()

# --- MPV 核心 (原生地解碼 libmpv-2.dll C-API 介面, 零進程開銷) ---
# --- Windows 全局多媒體按鍵與 SMTC 系統監聽器 ---
class GlobalMediaKeyHook(threading.Thread):
    def __init__(self, api_instance):
        super().__init__(daemon=True)
        self.api = api_instance
        self.running = True

    def run(self):
        user32 = ctypes.windll.user32
        # VK_MEDIA_NEXT_TRACK = 0xB0, VK_MEDIA_PREV_TRACK = 0xB1, VK_MEDIA_STOP = 0xB2, VK_MEDIA_PLAY_PAUSE = 0xCD
        keys = {
            0xCD: 'play_pause',
            0xB0: 'next',
            0xB1: 'prev',
            0xB2: 'stop'
        }
        last_states = {k: False for k in keys}
        
        while self.running:
            time.sleep(0.08)
            try:
                for vk, action in keys.items():
                    state = bool(user32.GetAsyncKeyState(vk) & 0x8000)
                    if state and not last_states[vk]:
                        self.api.control(action)
                    last_states[vk] = state
            except Exception:
                pass

class MPVSatellite:
    def __init__(self, on_event_callback=None):
        self.player = None
        self.running = False
        self.callback = on_event_callback
        self.write_lock = threading.Lock()
        
    def launch(self):
        if self.player:
            return True
        try:
            # 優先使用 python-mpv 直接加載 libmpv-2.dll (純音訊嵌入, 整合至單一主視窗, 無彈窗)
            self.player = mpv.MPV(
                idle=True,
                force_window='no',
                vo='null',
                vid='no',
                hr_seek='yes',
                keep_open='yes',
                media_controls=True,
                title='MPlay // INTERNET ANGEL OS',
                user_agent='MPlay/2.0'
            )
            
            # 原生 C-Callback 屬性監聽器 (微秒級零延遲同步)
            @self.player.property_observer('time-pos')
            def _on_time_pos(_name, value):
                if self.callback and value is not None:
                    self.callback('time-pos', value)

            @self.player.property_observer('pause')
            def _on_pause(_name, value):
                if self.callback and value is not None:
                    self.callback('pause', value)

            @self.player.property_observer('duration')
            def _on_duration(_name, value):
                if self.callback and value is not None:
                    self.callback('duration', value)

            @self.player.property_observer('eof-reached')
            def _on_eof(_name, value):
                if self.callback and value is not None:
                    self.callback('eof-reached', value)

            @self.player.property_observer('media-title')
            def _on_title(_name, value):
                if self.callback and value is not None:
                    self.callback('media-title', value)

            @self.player.property_observer('playlist-pos')
            def _on_playlist_pos(_name, value):
                if self.callback and value is not None:
                    self.callback('playlist-pos', value)

            @self.player.property_observer('mute')
            def _on_mute(_name, value):
                if self.callback and value is not None:
                    self.callback('mute', value)

            self.eq_enabled = False
            self.eq_gains = [0] * 10
            self.normalize_enabled = config.get("audio_normalize", False)
            
            theme_settings = config.get("theme_settings") or {}
            if isinstance(theme_settings, dict):
                self.eq_enabled = theme_settings.get("eq_enabled", False)
                self.eq_gains = theme_settings.get("eq_gains", [0] * 10)

            self.apply_audio_upsample_settings(config.get("audio_upsample"))
            self.update_audio_filters()
            self.running = True
            return True
        except Exception as e:
            print(f"[libmpv-2.dll Launch Error] {e}")
            return False

    def update_audio_filters(self, eq_enabled=None, gains=None, normalize_enabled=None):
        """統一音訊濾波器鏈 (非阻塞平滑防抖動, 10-Band FFmpeg Parametric EQ + EBU R128 Loudnorm)"""
        if not self.player: return
        if eq_enabled is not None:
            self.eq_enabled = eq_enabled
        if gains is not None:
            self.eq_gains = [float(g) for g in gains]
        if normalize_enabled is not None:
            self.normalize_enabled = normalize_enabled

        if hasattr(self, '_af_timer') and self._af_timer:
            try: self._af_timer.cancel()
            except Exception: pass

        def _do_update():
            if not self.player or getattr(self, 'is_closing', False): return
            filters = []
            if getattr(self, 'eq_enabled', False):
                g = getattr(self, 'eq_gains', [0] * 10)
                if len(g) < 10:
                    g = (list(g) + [0] * 10)[:10]
                freqs = [60, 170, 310, 600, 1000, 3000, 6000, 12000, 14000, 16000]
                widths = [50, 100, 150, 300, 500, 1500, 3000, 4000, 4000, 4000]
                eq_filters = [f"equalizer=f={f}:width_type=h:width={w}:g={g[i]}" for i, (f, w) in enumerate(zip(freqs, widths))]
                filters.extend(eq_filters)

            if getattr(self, 'normalize_enabled', False):
                filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")

            af_string = ",".join(filters)
            with self.write_lock:
                try:
                    self.player.af = af_string
                except Exception as e:
                    print(f"[libmpv Audio Filter Error] {e}")

        self._af_timer = threading.Timer(0.04, _do_update)
        self._af_timer.start()

    def apply_audio_upsample_settings(self, enabled):
        """將高解析超採音訊 (Hi-Res Audio Upsampling) 設定非阻塞地套用至 libmpv 核心"""
        if not self.player: return
        def _do_upsample():
            if not self.player or getattr(self, 'is_closing', False): return
            with self.write_lock:
                try:
                    if enabled:
                        self.player.audio_samplerate = 192000
                        self.player.audio_format = 'floatp'
                        self.player['audio-resample-filter-size'] = 32
                        self.player['audio-resample-cutoff'] = 0.98
                    else:
                        self.player.audio_samplerate = 0
                except Exception as e:
                    print(f"[libmpv Audio Upsample Error] {e}")
        threading.Thread(target=_do_upsample, daemon=True).start()

    def apply_audio_normalize_settings(self, enabled):
        """將 EBU R128 音量響度均衡 (Loudness Normalization) 設定套用至 libmpv 核心"""
        if not self.player: return
        def _do_norm():
            if not self.player or getattr(self, 'is_closing', False): return
            with self.write_lock:
                try:
                    if enabled:
                        self.player.replaygain = 'track'
                    else:
                        self.player.replaygain = 'no'
                except Exception as e:
                    print(f"[libmpv Audio Normalize Error] {e}")
            self.update_audio_filters(normalize_enabled=enabled)
        threading.Thread(target=_do_norm, daemon=True).start()

    def notify_song_change(self):
        pass

    def send(self, cmd):
        if not self.player: return
        with self.write_lock:
            try:
                op = cmd[0]
                if op == 'loadfile':
                    mode = cmd[2] if len(cmd) > 2 else 'replace'
                    self.player.command('loadfile', cmd[1], mode)
                elif op == 'playlist-clear':
                    self.player.command('playlist-clear')
                elif op == 'set_property':
                    prop = cmd[1]
                    val = cmd[2]
                    key = prop.replace('-', '_')
                    if hasattr(self.player, key):
                        setattr(self.player, key, val)
                    else:
                        self.player[prop] = val
                elif op == 'seek':
                    pos = float(cmd[1])
                    self.player.seek(pos, reference='absolute')
                elif op == 'cycle':
                    # 【修復關鍵】cycle 指令 (play_pause / mute 等) 並即時通知 UI
                    prop = cmd[1]
                    key = prop.replace('-', '_')
                    cur = getattr(self.player, key, None)
                    if isinstance(cur, bool) or cur is None:
                        new_val = not cur if cur is not None else True
                        setattr(self.player, key, new_val)
                        if self.callback:
                            self.callback(prop, new_val)
                    else:
                        self.player.cycle(prop)
                elif op == 'change-list':
                    # Shader 指令: change-list glsl-shaders append/clr
                    prop, action, val = cmd[1], cmd[2], cmd[3]
                    if action == 'clr':
                        self.player[prop] = ''
                    elif action == 'append':
                        cur = self.player[prop] or ''
                        self.player[prop] = (cur + ':' + val).lstrip(':')
                elif op == 'observe_property':
                    pass  # 已透過 property_observer 處理，忽略
            except Exception as e:
                print(f"[libmpv Command Error] {e}")

    def kill(self):
        self.running = False
        if self.player:
            try:
                self.player.terminate()
            except: pass
            self.player = None

# --- API ---
# --- API ---
class API:
    def __init__(self):
        self._db = Database()
        # --- Config 的 MPV 路徑傳給 Database ---
        mpv_path = config.get("mpv_path")
        self._db.update_mpv_path(mpv_path)
        # ---------------------------------------------
        self._mpv = MPVSatellite(self.handle_mpv_event)
        self._window = None
        self.playlist = []
        
        self._target_path = None
        self._last_sent_path = None
        self._ignore_auto_next = False 
        self.shader_enabled = False
        self.current_duration = 0      
        self.is_closing = False
        
        self.is_boot_mode = True   
        self.boot_files = []       

        # --- 【修復】初始化鎖定與計時器變數 ---
        self._play_lock = threading.Lock()
        self._unlock_timer = None
        # ------------------------------------
        # 【新增】播放模式: 'loop' (列表循環), 'shuffle' (隨機), 'one' (單曲)
        saved_mode = config.get("play_mode", "loop")
        self.play_mode = saved_mode
        self._play_mode = saved_mode
        # ------------------------------------
        threading.Thread(target=self._sync_loop, daemon=True).start()
        threading.Thread(target=self._boot_autoplay, daemon=True).start()
        
        # 啟動超級粉碎機 (掃描所有子程序)
        threading.Thread(target=self._startup_memory_crusher, daemon=True).start()
    # 【新增】切換視窗顯示/隱藏的 API
    def toggle_mpv_window(self):
        proc = getattr(self._mpv, 'process', None)
        if not proc or not hasattr(proc, 'pid'): return
        
        try:
            pid = proc.pid
            hwnds = get_hwnds_for_pid(pid)
            
            if hwnds:
                hwnd = hwnds[0] # 抓取主視窗
                
                # 檢查目前是否最小化 (IsIconic 返回非 0 代表最小化)
                if user32.IsIconic(hwnd):
                    # 如果是最小化，就還原並置頂
                    user32.ShowWindow(hwnd, SW_RESTORE)
                    user32.SetForegroundWindow(hwnd)
                else:
                    # 如果是顯示中，就最小化
                    user32.ShowWindow(hwnd, SW_MINIMIZE)
        except Exception as e:
            print(f"Window Toggle Error: {e}")
            
    def set_window(self, window):
        self._window = window
        def _on_closing(*args, **kwargs):
            self.is_closing = True
            self._window = None
        try:
            window.events.closing += _on_closing
            window.events.closed += _on_closing
        except Exception:
            pass

    def _startup_memory_crusher(self):
        """背景持續記憶體粉碎機：定期壓縮記憶體工作集，保持極致輕量"""
        time.sleep(3.0)
        while not self.is_closing:
            trim_memory()
            time.sleep(25.0)

    def _get_valid_boot_files(self):
        """讀取開機音樂檔案，優先檢查自訂開機音樂，自動跳過 GIF 等子資料夾"""
        exts = ('.mp4', '.mkv', '.mp3', '.flac', '.wav', '.webm', '.m4a')
        
        # 0. 優先檢查使用者自訂右鍵設定的開機歌曲
        custom_track = config.get("custom_boot_track")
        if custom_track and os.path.exists(custom_track):
            return [custom_track]

        # 1. 優先檢查 boot2 資料夾 (精確檢查檔案，排除 GIF 資料夾)
        boot2_dir = os.path.join(os.getcwd(), "boot2")
        if os.path.exists(boot2_dir):
            files = [os.path.join(boot2_dir, f) for f in os.listdir(boot2_dir) 
                     if os.path.isfile(os.path.join(boot2_dir, f)) and f.lower().endswith(exts)]
            if files:
                return files

        # 2. 若 boot2 無音訊，降級檢查 boot 資料夾
        boot_dir = os.path.join(os.getcwd(), "boot")
        if os.path.exists(boot_dir):
            files = [os.path.join(boot_dir, f) for f in os.listdir(boot_dir) 
                     if os.path.isfile(os.path.join(boot_dir, f)) and f.lower().endswith(exts)]
            if files:
                return files
                
        return []

    def set_boot_track(self, track_path):
        """將右鍵選擇的特定歌曲設為開機音樂 (Set as Boot Music)"""
        if track_path and os.path.exists(track_path):
            config.save_config({
                "boot_music": True,
                "custom_boot_track": track_path
            })
            if hasattr(self, '_window') and self._window:
                self.safe_evaluate_js("ui.updateBootState(true)")
            filename = os.path.basename(track_path)
            print(f"[Boot BGM] Set custom boot track to: {track_path}")
            return {"success": True, "track": filename}
        return {"success": False}

    # 【新增/修復】切換開機音樂設定並立即觸發播放
    def toggle_boot_music(self):
        current = config.get("boot_music")
        new_state = not current
        config.save_config({"boot_music": new_state})
        self.safe_evaluate_js(f"ui.updateBootState({str(new_state).lower()})")

        if new_state:
            self.is_boot_mode = True
            self.boot_files = self._get_valid_boot_files()
            if self.boot_files:
                self._play_random_boot_file()

    # 【新增】取得目前設定 (用於初始化 UI)
    def get_boot_state(self):
        return config.get("boot_music")


    # 【新增】通用選擇圖片/GIF API (開啟 Windows 原生檔案選擇器)
    def select_image_file(self):
        """開啟 Windows 原生檔案選擇對話框，選擇圖片或 GIF 檔案"""
        if not self._window:
            return None
        file_types = ('Image Files (*.png;*.jpg;*.jpeg;*.gif;*.webp)', 'All files (*.*)')
        try:
            result = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=file_types)
            if result and len(result) > 0:
                return result[0]
        except Exception as e:
            print(f"[File Dialog Filter Fallback] {e}")
            try:
                result = self._window.create_file_dialog(webview.FileDialog.OPEN)
                if result and len(result) > 0:
                    return result[0]
            except Exception as e2:
                print(f"[File Dialog Error] {e2}")
        return None

    # 【新增】Parametric EQ 10-Band 濾波器 API
    def set_eq_settings(self, enabled, gains=None, preset=None):
        """設定 Parametric EQ 增益與開關，即時套用至 MPV 核心並存檔至 config.json"""
        if gains is None:
            gains = [0] * 10
        settings = config.get("theme_settings") or {}
        if not isinstance(settings, dict):
            settings = {}
        settings["eq_enabled"] = bool(enabled)
        settings["eq_gains"] = gains
        if preset is not None:
            settings["eq_preset"] = preset
        config.save_config({"theme_settings": settings})
        self._mpv.update_audio_filters(eq_enabled=enabled, gains=gains)
        return True

    def get_eq_settings(self):
        """讀取 Parametric EQ 增益與開關設定"""
        settings = config.get("theme_settings") or {}
        if not isinstance(settings, dict):
            settings = {}
        return {
            "enabled": settings.get("eq_enabled", False),
            "gains": settings.get("eq_gains", [0] * 10),
            "preset": settings.get("eq_preset", "Flat")
        }

    # 【新增】主題圖片與自訂設定永久存檔 API (記錄寫入 config.json)
    def save_theme_setting(self, key, value):
        """保存主題圖片與自訂設定至 config.json 永久存檔"""
        settings = config.get("theme_settings") or {}
        if not isinstance(settings, dict):
            settings = {}
        if value is None or value == "":
            settings.pop(key, None)
        else:
            settings[key] = value
        config.save_config({"theme_settings": settings})
        return True

    def get_theme_settings(self):
        """讀取全主題永久存檔設定"""
        return config.get("theme_settings") or {}

    def get_theme_setting(self, key, default=None):
        """讀取單項主題永久存檔設定"""
        settings = config.get("theme_settings") or {}
        if isinstance(settings, dict) and key in settings:
            return settings[key]
        return default

    # 【新增】選擇與取得自訂桌布 API
    def select_wallpaper(self):
        """開啟檔案選擇器讓使用者挑選本機圖片當作桌布"""
        return self.select_image_file()

    def get_wallpaper(self):
        """取得目前設定的自訂桌布路徑"""
        return config.get("custom_wallpaper")

    def reset_wallpaper(self):
        """重置桌布為預設主題"""
        config.save_config({"custom_wallpaper": None})
        return True

    # 【新增】超採高解析音訊 (Hi-Res Audio Upsampling) 切換 API
    def toggle_audio_upsample(self):
        """切換超採高解析音訊 (Hi-Res Upsampling) 狀態"""
        current = config.get("audio_upsample")
        new_state = not current
        config.save_config({"audio_upsample": new_state})
        self._mpv.apply_audio_upsample_settings(new_state)
        if self._window:
            self.safe_evaluate_js(f"ui.updateUpsampleState({str(new_state).lower()})")
        return new_state

    def get_audio_upsample_state(self):
        """取得超採高解析音訊設定狀態"""
        return config.get("audio_upsample")

    # 【新增】音量響度均衡 (Loudness Normalization) 切換 API
    def toggle_audio_normalize(self):
        """切換響度均衡 / 自動同步大小聲 (Audio Normalization) 狀態"""
        current = config.get("audio_normalize")
        new_state = not current
        config.save_config({"audio_normalize": new_state})
        self._mpv.apply_audio_normalize_settings(new_state)
        if self._window:
            self.safe_evaluate_js(f"ui.updateNormalizeState({str(new_state).lower()})")
        return new_state

    def get_audio_normalize_state(self):
        """取得響度均衡設定狀態"""
        return config.get("audio_normalize")

    # 【修復】開機自動播放邏輯 (過濾子資料夾)
    def _boot_autoplay(self):
        if not config.get("boot_music"):
            self.is_boot_mode = False
            return

        self.is_boot_mode = True

        # 1. 立即啟動 MPV 核心 (不空等 3 秒)
        if hasattr(self, '_mpv') and self._mpv:
            if not getattr(self._mpv, 'player', None):
                try: self._mpv.launch()
                except Exception: pass

        if self.is_closing or not self.is_boot_mode:
            return

        self.boot_files = self._get_valid_boot_files()
        if self.boot_files and self.is_boot_mode:
            self._play_random_boot_file()
        else:
            self.is_boot_mode = False

    def _play_random_boot_file(self):
        if not self.boot_files or not self.is_boot_mode: return
        target = random.choice(self.boot_files)
        
        self._target_path = target
        self.playlist = [{'path': f, 'filename': os.path.basename(f)} for f in self.boot_files]
        self._mpv.send(["loadfile", target])
        self._mpv.send(["set_property", "pause", False])
        self._mpv.send(["set_property", "volume", 60])
        
        name_only = os.path.splitext(os.path.basename(target))[0]
        safe_title = json.dumps(f"🌸 [Boot BGM] {name_only}")
        if self._window:
            try:
                self.safe_evaluate_js(f"ui.updateTitle({safe_title})")
                self.safe_evaluate_js("ui.updateCover(null)")
                self.safe_evaluate_js("ui.updatePlayState(false)")
                safe_path = target.replace('\\', '/').replace("'", "\\'")
                self.safe_evaluate_js(f"ui.highlightTrack('{safe_path}')")
                
                # 自動延遲 0.3s 和 0.8s 推送實時歌曲時長與播放進度
                threading.Timer(0.3, lambda: self.safe_evaluate_js("if (window.ui && ui.syncState) ui.syncState();")).start()
                threading.Timer(0.8, lambda: self.safe_evaluate_js("if (window.ui && ui.syncState) ui.syncState();")).start()
            except Exception as e:
                pass
        
    def _sync_loop(self):
        # 為了保持結構完整保留此函數，但實際同步邏輯已移至 play_video
        while not self.is_closing:
            time.sleep(0.5)
            if self.is_closing: break
            pass

    def toggle_play_mode(self):
        """切換播放模式：Loop (列表循環) -> Shuffle (隨機播放) -> One (單曲循環) -> Album Continue (專輯連播) -> Loop"""
        modes = ['loop', 'shuffle', 'one', 'album_continue']
        cur = getattr(self, 'play_mode', getattr(self, '_play_mode', 'loop'))
        if cur not in modes: cur = 'loop'
        current_index = modes.index(cur)
        new_mode = modes[(current_index + 1) % len(modes)]
        self.play_mode = new_mode
        self._play_mode = new_mode
        config.save_config({"play_mode": new_mode})
        
        # 通知前端更新圖示
        if self._window:
            self.safe_evaluate_js(f"ui.updatePlayMode('{new_mode}')")
        return new_mode

    def toggle_mute(self):
        """切換靜音/恢復"""
        # 使用 cycle mute，MPV 會自動記憶靜音前的音量並恢復
        self._mpv.send(["cycle", "mute"])

    def handle_mpv_event(self, prop, value):
        if self.is_closing or not self._window: return
        
        if prop == "eof-reached" and value is True:
            if hasattr(self, '_last_eof_time') and (time.time() - self._last_eof_time) < 1.5:
                return # 防抖動：避免 MPV 初始化加載檔名時誤重播
            self._last_eof_time = time.time()

            # 聽完整首，記錄高留存率
            if hasattr(self, '_target_path') and self._target_path and not self.is_boot_mode:
                try:
                    dur = getattr(self, 'current_duration', 0)
                    self._db.record_play(self._target_path, dur, dur)
                except Exception as e:
                    print(f"[DB Record Play Error] {e}")

            if self.is_boot_mode:
                self._play_random_boot_file()
                return 
            if not self._ignore_auto_next: 
                self.control("next")

        js_cmd = ""
        if prop == "time-pos" and value is not None:
            now = time.time()
            if not hasattr(self, '_max_pos'): self._max_pos = 0
            if value > self._max_pos: self._max_pos = value

            if not hasattr(self, '_last_progress_time') or (now - self._last_progress_time) >= 0.15:
                self._last_progress_time = now
                js_cmd = f"ui.updateProgress({round(value, 2)})"
        elif prop == "duration" and value is not None:
            self.current_duration = value
            js_cmd = f"ui.updateDuration({round(value, 2)})"
        elif prop == "pause":
            js_cmd = f"ui.updatePlayState({str(value).lower()})"
        elif prop == "mute":
            js_cmd = f"ui.updateMuteState({str(value).lower()})"
        elif prop == "media-title": 
             if self.is_boot_mode and value and str(value).strip():
                safe = json.dumps(f"🌸 [Boot BGM] {value}")
                js_cmd = f"ui.updateTitle({safe})"
        elif prop == "playlist-pos" and value is not None and not self.is_boot_mode:
            try:
                idx = int(value)
                if hasattr(self, 'playlist') and self.playlist and 0 <= idx < len(self.playlist):
                    if getattr(self, 'current_index', -1) != idx:
                        self.current_index = idx
                        target = self.playlist[idx]
                        name_only = os.path.splitext(target['filename'])[0]
                        self.safe_evaluate_js(f"ui.updateTitle({json.dumps(name_only)})")
                        if target.get('cover_path'):
                            cover = target['cover_path'].replace('\\', '/')
                            cover_url = f"file:///{cover}" if not cover.startswith('file://') else cover
                            self.safe_evaluate_js(f"ui.updateCover('{cover_url}')")
                        else:
                            self.safe_evaluate_js("ui.updateCover(null)")
            except Exception as e:
                print(f"playlist-pos observer error: {e}")
        
        if js_cmd:
            try: self.safe_evaluate_js(js_cmd)
            except: pass
            
    def control(self, action, value=None):
        if action == "play_pause": self._mpv.send(["cycle", "pause"])
        
        elif action == "prev":
            if self.is_boot_mode: self._play_random_boot_file()
            elif self.playlist:
                # 【修改】上一首邏輯
                if self.play_mode == 'one':
                    # 單曲循環時，上一首通常是重播，或者您也可以讓它切到上一首
                    next_index = self.current_index
                elif self.play_mode == 'shuffle':
                    # 隨機模式的上一首：簡單實作為隨機 (或者您希望依序倒退?)
                    # 這裡採用隨機
                    next_index = random.randint(0, len(self.playlist) - 1)
                else:
                    # 列表循環 (Loop)
                    next_index = (self.current_index - 1) % len(self.playlist)
                
                self.play_video(self.playlist[next_index]['path'])

        elif action == "next": 
            if self.is_boot_mode: self._play_random_boot_file()
            elif self.playlist:
                if self.play_mode == 'one':
                    next_index = self.current_index
                elif self.play_mode == 'shuffle':
                    next_index = random.randint(0, len(self.playlist) - 1)
                elif self.play_mode == 'album_continue' and (self.current_index + 1 >= len(self.playlist)):
                    # 播完此專輯最後一首，自動切換至下一個專輯 (Album Continuity)
                    try:
                        folders = self._db.get_folders() if hasattr(self, '_db') and self._db else []
                        cur_path = self.playlist[self.current_index]['path']
                        cur_dir = os.path.dirname(os.path.normpath(cur_path))
                        next_folder = None
                        if folders:
                            folder_paths = [os.path.normpath(f['path']) for f in folders]
                            if cur_dir in folder_paths:
                                idx = folder_paths.index(cur_dir)
                                next_folder = folders[(idx + 1) % len(folders)]
                            else:
                                next_folder = folders[0]
                        if next_folder:
                            new_tracks = self._db.get_videos_in_folder(next_folder['path'])
                            if new_tracks:
                                self.playlist = new_tracks
                                self.current_index = 0
                                next_index = 0
                                if self._window:
                                    f_name = next_folder.get('name', 'Next Album')
                                    self.safe_evaluate_js(f"ui.toast('📀 自動銜接下一專輯: {f_name}')")
                            else:
                                next_index = (self.current_index + 1) % len(self.playlist)
                        else:
                            next_index = (self.current_index + 1) % len(self.playlist)
                    except Exception as e:
                        print(f"[Album Continue Error] {e}")
                        next_index = (self.current_index + 1) % len(self.playlist)
                else:
                    # 列表循環 (Loop)
                    next_index = (self.current_index + 1) % len(self.playlist)
                
                self.play_video(self.playlist[next_index]['path'])

        elif action == "seek": self._mpv.send(["seek", float(value), "absolute"])
        elif action == "volume": 
            # 拖動音量條時，順便解除靜音
            self._mpv.send(["set_property", "mute", False])
            self._mpv.send(["set_property", "volume", int(value)])

    # --- API Methods ---
    def get_config(self): return config.data
    def save_config(self, new_config): config.save_config(new_config); return True
    def pick_file(self):
        result = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=("Executable (*.exe)", "All files (*.*)"))
        return result[0] if result else None
    def play_url(self, url):
        if not url: return
        self.is_boot_mode = False 
        self._last_sent_path = None; self._target_path = url 
    def toggle_shader(self, shader_path):
        if not self.shader_enabled:
            self._mpv.send(["change-list", "glsl-shaders", "append", shader_path])
            self.shader_enabled = True; return True
        else:
            self._mpv.send(["change-list", "glsl-shaders", "clr", ""])
            self.shader_enabled = False; return False
    def get_image_bytes(self, path):
        if not path or not os.path.exists(path): return None
        try:
            ext = os.path.splitext(path)[1].lower().replace('.', '')
            mime = 'image/png' if ext == 'png' else ('image/gif' if ext == 'gif' else 'image/jpeg')
            with open(path, "rb") as f:
                encoded = base64.b64encode(f.read()).decode('utf-8')
                return f"data:{mime};base64,{encoded}"
        except: return None

    def get_gif_b64(self, filename):
        try:
            gif_dir = self._get_resource_path(os.path.join('boot2', 'GIF'))
            gif_path = os.path.join(gif_dir, filename)
            if not os.path.exists(gif_path):
                gif_path = os.path.join(os.path.dirname(__file__), 'boot2', 'GIF', filename)
            return self.get_image_bytes(gif_path)
        except Exception as e:
            print(f"[GIF Load Error] {e}")
            return None
    
    # 追蹤目前視窗標題 (供 FindWindowW 查詢用)
    _current_win_title = 'MPlay'

    # 主題 -> DWM 標題列色盤
    THEME_STYLES = {
        "needy_theme.html": {
            "bg": "#3b1166", "text": "#ffdbf3", "border": "#6b33a8",
            "title": "✨ MPlay // INTERNET ANGEL OS v2.5"
        },
        "cyberpunk.html": {
            "bg": "#0f0b1e", "text": "#00f0ff", "border": "#7c4dff",
            "title": "⚡ MPlay // CYBERPUNK AUDIO ENGINE"
        },
        "index.html": {
            "bg": "#141420", "text": "#d8b4fe", "border": "#3b1166",
            "title": "🎵 MPlay // DEFAULT DARK ENGINE"
        },
        "mech_theme.html": {
            "bg": "#12151e", "text": "#00ff9d", "border": "#2a324b",
            "title": "🤖 MPlay // MECH CORE TERMINAL"
        },
        "light.html": {
            "bg": "#f4ecfd", "text": "#4a148c", "border": "#d1b3ff",
            "title": "🌸 MPlay // LIGHT AMBIENT OS"
        },
        "image_theme.html": {
            "bg": "#1a0d2e", "text": "#ff94eb", "border": "#7540ff",
            "title": "🖼️ MPlay // IMAGE WALLPAPER OS"
        }
    }

    @staticmethod
    def _hex_to_bgr(h):
        h = h.lstrip('#')
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return (b << 16) | (g << 8) | r

    def _find_main_hwnd(self):
        """透過多種策略查找主視窗 GA_ROOT HWND (確保取得 Windows OS 頂層標題列視窗)"""
        GA_ROOT = 2
        raw_hwnd = user32.FindWindowW(None, self._current_win_title)
        if not raw_hwnd:
            for s in self.THEME_STYLES.values():
                raw_hwnd = user32.FindWindowW(None, s["title"])
                if raw_hwnd: break
        if not raw_hwnd:
            raw_hwnd = user32.FindWindowW(None, 'MPlay')

        if raw_hwnd:
            root_hwnd = user32.GetAncestor(raw_hwnd, GA_ROOT)
            return root_hwnd if root_hwnd else raw_hwnd

        # PID 查找 fallback
        try:
            pid = os.getpid()
            hwnds = get_hwnds_for_pid(pid)
            if hwnds:
                for h in hwnds:
                    root = user32.GetAncestor(h, GA_ROOT)
                    if root and user32.IsWindowVisible(root):
                        return root
                return hwnds[0]
        except: pass
        return 0

    def safe_evaluate_js(self, script):
        """安全執行 JS 指令 (防視窗關閉及 disposed 丟出 System.ObjectDisposedException)"""
        if getattr(self, 'is_closing', False):
            return
        win = getattr(self, '_window', None)
        if not win:
            return
        try:
            win.evaluate_js(script)
        except BaseException:
            pass

    def apply_dwm_titlebar_style(self, theme_file=None, retries=5):
        """將上方 Windows OS 原生視窗標題列的顏色與文字融合進當前主題"""
        if not theme_file:
            theme_file = config.get("theme_file") or "needy_theme.html"

        style = self.THEME_STYLES.get(theme_file, self.THEME_STYLES["needy_theme.html"])

        hwnd = 0
        for attempt in range(retries):
            hwnd = self._find_main_hwnd()
            if hwnd:
                break
            time.sleep(0.3)

        if not hwnd:
            print("[DWM] Window HWND not found after retries, skipping")
            return

        try:
            from ctypes import c_int as _ci
            dwmapi = windll.dwmapi

            dark = _ci(1)
            dwmapi.DwmSetWindowAttribute(hwnd, 20, byref(dark), sizeof(dark))

            bg = _ci(self._hex_to_bgr(style["bg"]))
            dwmapi.DwmSetWindowAttribute(hwnd, 35, byref(bg), sizeof(bg))

            txt = _ci(self._hex_to_bgr(style["text"]))
            dwmapi.DwmSetWindowAttribute(hwnd, 36, byref(txt), sizeof(txt))

            bd = _ci(self._hex_to_bgr(style["border"]))
            dwmapi.DwmSetWindowAttribute(hwnd, 34, byref(bd), sizeof(bd))

            # 更新標題並記錄新標題名稱
            new_title = style["title"]
            user32.SetWindowTextW(hwnd, new_title)
            API._current_win_title = new_title

            print(f"[DWM] Applied title bar style for {theme_file} (hwnd={hwnd})")

        except Exception as e:
            print(f"[DWM Titlebar Error] {e}")

    def start_mpv(self): 
        result = self._mpv.launch()
        
        # 1. 立即同步 DWM 頂部標題列顏色樣式
        self.apply_dwm_titlebar_style()

        if result and self._window:
            try:
                # 2. 【修復關鍵】全面同步當前 MPV 實時播放狀態 (僅當有加載歌曲/開機音樂時同步，避免無媒體時瞬間刷新成暫停)
                if self._mpv and self._mpv.player and (getattr(self, '_target_path', None) or getattr(self, 'is_boot_mode', False)):
                    is_paused = self._mpv.player.pause if self._mpv.player.pause is not None else False
                    time_pos = self._mpv.player.time_pos if self._mpv.player.time_pos is not None else 0
                    duration = self._mpv.player.duration if self._mpv.player.duration is not None else 0
                    is_muted = self._mpv.player.mute if self._mpv.player.mute is not None else False

                    self.safe_evaluate_js(f"ui.updatePlayState({str(is_paused).lower()})")
                    self.safe_evaluate_js(f"ui.updateProgress({time_pos})")
                    self.safe_evaluate_js(f"ui.updateDuration({duration})")
                    self.safe_evaluate_js(f"ui.updateMuteState({str(is_muted).lower()})")

                # 3. 重新發送當前歌曲資訊給新主題頁面
                if self.playlist and hasattr(self, 'current_index') and self.current_index < len(self.playlist):
                    target_video = self.playlist[self.current_index]
                    
                    name_only = os.path.splitext(target_video['filename'])[0]
                    self.safe_evaluate_js(f"ui.updateTitle({json.dumps(name_only)})")
                    
                    if target_video.get('cover_path'):
                        b64 = self.get_image_bytes(target_video['cover_path'])
                        self.safe_evaluate_js(f"ui.updateCover('{b64}')")
                    
                    safe_path = target_video['path'].replace('\\', '/').replace("'", "\\'")
                    self.safe_evaluate_js(f"ui.highlightTrack('{safe_path}')")
                    
                # 4. 同步系統設定狀態 (開機音樂/播放模式/超採/均衡)
                if hasattr(self, 'play_mode'):
                    self.safe_evaluate_js(f"ui.updatePlayMode('{self.play_mode}')")
                if hasattr(self, 'get_boot_state'):
                    self.safe_evaluate_js(f"ui.updateBootState({str(self.get_boot_state()).lower()})")
                if hasattr(self, 'get_audio_upsample_state'):
                    self.safe_evaluate_js(f"ui.updateUpsampleState({str(self.get_audio_upsample_state()).lower()})")
                if hasattr(self, 'get_audio_normalize_state'):
                    self.safe_evaluate_js(f"ui.updateNormalizeState({str(self.get_audio_normalize_state()).lower()})")
                        
            except Exception as e:
                print(f"UI Sync Error: {e}")
                    
        return result
    
    def scan_folder(self):
        folder = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if folder:
            self._db.scan_directory(folder[0])
            trim_memory()
            return self.get_library()
        return []
    def get_library(self):
        data = self._db.get_all_videos()
        self.playlist = data 
        return data
    
    def play_playlist(self, tracks, start_index=0):
        """播放自訂 Playlist (如 Favorites, Top 10) 並完整同步後端切歌佇列"""
        if not tracks: return False
        try:
            formatted = []
            for t in tracks:
                if isinstance(t, dict) and t.get('path'):
                    formatted.append({
                        'path': t['path'],
                        'filename': t.get('filename', os.path.basename(t['path'])),
                        'duration': t.get('duration', 0),
                        'is_favorite': t.get('is_favorite', 1)
                    })
                elif isinstance(t, str):
                    formatted.append({'path': t, 'filename': os.path.basename(t)})
            
            if not formatted: return False
            self.playlist = formatted
            if start_index < 0 or start_index >= len(formatted):
                start_index = 0
            
            self.current_index = start_index
            target_path = formatted[start_index]['path']
            return self.play_video(target_path)
        except Exception as e:
            print(f"[play_playlist Error] {e}")
            return False

    # --- 【修復版】play_video 邏輯 ---
    def play_video(self, path):
        """優化版：根據檔案類型決定視窗行為 (音樂縮小/影片彈出)"""
        # 記錄上一首歌曲的收聽留存率
        if hasattr(self, '_target_path') and self._target_path and not self.is_boot_mode:
            try:
                max_pos = getattr(self, '_max_pos', 0)
                dur = getattr(self, 'current_duration', 0)
                if max_pos > 0:
                    self._db.record_play(self._target_path, max_pos, dur)
            except Exception as e:
                print(f"[DB Record Prev Play Error] {e}")

        # 確保變數存在
        if not hasattr(self, '_play_lock'): self._play_lock = threading.Lock()
        if not hasattr(self, '_unlock_timer'): self._unlock_timer = None

        with self._play_lock:
            if self.is_closing: return

            # 1. 重置狀態
            self.is_boot_mode = False
            self.current_duration = 0
            self._max_pos = 0
            self._ignore_auto_next = True 
            
            # 2. 發送播放指令 (同步 MPV 內部播放清單以啟動 Windows SMTC 上下首功能)
            try:
                norm_target = os.path.normpath(path)
                matched_idx = None
                if self.playlist and not self.is_boot_mode:
                    matched_idx = next((i for i, v in enumerate(self.playlist) if os.path.normpath(v.get('path', '')) == norm_target), None)

                if matched_idx is not None:
                    self.current_index = matched_idx
                    mpv_pl = getattr(self._mpv.player, 'playlist', []) if self._mpv and hasattr(self._mpv, 'player') else []
                    if not mpv_pl or len(mpv_pl) != len(self.playlist):
                        self._mpv.send(["playlist-clear"])
                        for idx, item in enumerate(self.playlist):
                            m = "replace" if idx == 0 else "append"
                            self._mpv.send(["loadfile", item['path'], m])

                    self._mpv.send(["set_property", "playlist-pos", matched_idx])
                else:
                    self.playlist = [{'path': path, 'filename': os.path.basename(path)}]
                    self.current_index = 0
                    self._mpv.send(["loadfile", path])

                self._mpv.send(["set_property", "pause", False])
                self._mpv.send(["set_property", "mute", False])
                self._mpv.notify_song_change()
            except Exception as e:
                print(f"MPV Send Error: {e}")

            # 3. 【核心新增】視窗自動控制邏輯
            # 定義音訊格式
            AUDIO_EXTS = {'.mp3', '.flac', '.wav', '.m4a', '.ogg', '.opus', '.wma', '.aac'}
            ext = os.path.splitext(path)[1].lower()
            
            try:
                proc = getattr(self._mpv, 'process', None)
                if proc and hasattr(proc, 'pid'):
                    pid = proc.pid
                    hwnds = get_hwnds_for_pid(pid)
                    if hwnds:
                        hwnd = hwnds[0]
                        ext_lower = ext.lower()
                        if ext_lower in VIDEO_EXTS:
                            # 如果是影片 -> 強制彈出原生地 HW 60fps 影音繪製視窗 (SW_RESTORE = 9)
                            user32.ShowWindow(hwnd, 9)
                            user32.SetForegroundWindow(hwnd)
                        else:
                            # 如果是純音樂 -> 強制縮小後台播放 (SW_MINIMIZE = 6)
                            user32.ShowWindow(hwnd, 6)
            except Exception as e:
                print(f"Window Auto-Control Error: {e}")

            # 4. 前端 UI 同步更新
            if self._window:
                norm_target = os.path.normpath(path)
                target_video = next((v for v in self.playlist if os.path.normpath(v['path']) == norm_target), None)
                
                try:
                    if target_video:
                        try: self.current_index = self.playlist.index(target_video)
                        except: pass
                        
                        if target_video.get('cover_path'):
                            cover = target_video['cover_path'].replace('\\', '/')
                            cover_url = f"file:///{cover}" if not cover.startswith('file://') else cover
                            self.safe_evaluate_js(f"ui.updateCover('{cover_url}')")
                        else:
                            self.safe_evaluate_js("ui.updateCover(null)")
                        
                        name_only = os.path.splitext(target_video['filename'])[0]
                        self.safe_evaluate_js(f"ui.updateTitle({json.dumps(name_only)})")
                    else:
                        name_only = os.path.splitext(os.path.basename(path))[0]
                        self.safe_evaluate_js(f"ui.updateTitle({json.dumps(name_only)})")

                    safe_path = path.replace('\\', '/').replace("'", "\\'")
                    self.safe_evaluate_js(f"ui.highlightTrack('{safe_path}')")
                except Exception as e:
                    print(f"UI Update Error: {e}")

            # 5. 延遲解鎖自動播放
            if self._unlock_timer:
                try: self._unlock_timer.cancel()
                except: pass
            
            def unlock(): self._ignore_auto_next = False
            self._unlock_timer = threading.Timer(1.5, unlock)
            self._unlock_timer.start()

            self._target_path = path
    
    def close_app(self):
        print("[System] 正在關閉系統...")
        self.is_closing = True
        win = getattr(self, '_window', None)
        self._window = None
        
        try: self._mpv.send(["set_property", "pause", False])
        except: pass
        
        try: self._mpv.kill()
        except: pass
        
        trim_memory()
        
        if win:
            try: win.destroy()
            except BaseException: pass
        
        time.sleep(0.2)
        os._exit(0)
    
    def get_library_folders(self):
        folders = self._db.get_folders()
        return folders

    def get_folder_tracks(self, folder_path):
        tracks = self._db.get_videos_in_folder(folder_path)
        self.playlist = tracks
        return tracks
    # 記得保留原本的 get_library 給 Boot Mode 用，但前端不再呼叫它來渲染列表
    def _get_resource_path(self, relative_path):
        """輔助函式：取得資源絕對路徑 (支援打包後環境)"""
        try:
            base_path = sys._MEIPASS
        except Exception:
            base_path = os.path.dirname(os.path.abspath(__file__))
        
        # 優先檢查 ui 資料夾
        ui_path = os.path.join(base_path, 'ui', relative_path)
        if os.path.exists(ui_path):
            return ui_path
        # 其次檢查根目錄
        return os.path.join(base_path, relative_path)

    def change_theme(self, filename):
        """切換主題 (完整修復：同步 DWM 頂部標題列顏色與播放狀態)"""
        full_path = self._get_resource_path(filename)
        
        if os.path.exists(full_path):
            print(f"[System] Switching theme to: {filename}")
            config.save_config({"theme_file": filename})
            url = f'file://{full_path}'
            
            def delayed_load():
                if self._window:
                    self._window.load_url(url)
                    threading.Timer(0.3, lambda: self.safe_evaluate_js("if (window.ui && ui.syncState) ui.syncState();")).start()
                    threading.Timer(0.7, lambda: self.safe_evaluate_js("if (window.ui && ui.syncState) ui.syncState();")).start()
                    threading.Timer(1.0, lambda: self.apply_dwm_titlebar_style(filename)).start()
            
            threading.Timer(0.05, delayed_load).start()
            return True 
        else:
            print(f"[System] Theme file not found: {full_path}")
            return False

    def search_library(self, query):
        return self._db.search_library(query)

    def delete_track(self, path, delete_file=False):
        return self._db.delete_track(path, delete_file)

    def delete_folder(self, folder_path, delete_files=False):
        return self._db.delete_folder(folder_path, delete_files)

    def open_pip_window(self):
        """開啟獨立 Picture-in-Picture 懸浮 Mini Player 接口 (保持主視窗完整不變)"""
        if hasattr(self, '_pip_window') and self._pip_window:
            try:
                self._pip_window.show()
                self._pip_window.focus()
                return True
            except: 
                self._pip_window = None

        try:
            import webview
            pip_html = os.path.abspath(os.path.join("ui", "pip_player.html")).replace('\\\\', '/')
            if not pip_html.startswith('file://'):
                pip_url = 'file:///' + pip_html
            else:
                pip_url = pip_html

            self._pip_window = webview.create_window(
                "📌 MPlay Mini PiP",
                url=pip_url,
                width=450,
                height=135,
                on_top=True,
                resizable=False,
                easy_drag=True,
                js_api=self
            )
            return True
        except Exception as e:
            print(f"[PiP Window Error] {e}")
            return False

    def close_pip_window(self):
        if hasattr(self, '_pip_window') and self._pip_window:
            try:
                self._pip_window.destroy()
            except: pass
            self._pip_window = None
        return True

    def toggle_pip_mode(self):
        """融合 Windows 桌面 (Picture-in-Picture 置頂 Mini Player 接口)"""
        return self.open_pip_window()

    def save_noir_mode(self, is_noir):
        """保存黑/白模式設定"""
        config.save_config({"noir_mode": is_noir})
        return True

    def get_playback_status(self):
        try:
            path = getattr(self, '_target_path', None)
            title = "AWAITING_MEDIA"
            cover_url = ""
            play_count = 0
            like_score = 0.0
            
            if path and os.path.exists(path):
                title = os.path.splitext(os.path.basename(path))[0]
                info = self._db.get_track_info(path) if hasattr(self, '_db') and self._db else None
                if info:
                    if info.get('cover_path'):
                        cpath = info['cover_path']
                        cover_url = 'file:///' + cpath.replace('\\', '/') if not cpath.startswith('file://') else cpath
                    play_count = info.get('play_count', 0)
                    like_score = info.get('like_score', 0.0)

            pos = 0.0
            dur = 0.0
            is_playing = False
            is_muted = False

            if hasattr(self, '_mpv') and self._mpv and hasattr(self._mpv, 'player') and self._mpv.player:
                player = self._mpv.player
                try:
                    p_pos = player.time_pos
                    if p_pos is not None: pos = float(p_pos)
                except: pass
                
                try:
                    p_dur = player.duration
                    if p_dur is not None and float(p_dur) > 0: dur = float(p_dur)
                except: pass

                try:
                    p_pause = player.pause
                    if p_pause is not None: is_playing = not p_pause
                except: pass

                try:
                    p_mute = player.mute
                    if p_mute is not None: is_muted = bool(p_mute)
                except: pass

                try:
                    m_title = player.media_title
                    if m_title and str(m_title).strip() and title == "AWAITING_MEDIA":
                        title = str(m_title)
                except: pass

            if dur <= 0 and hasattr(self, 'current_duration') and self.current_duration:
                dur = float(self.current_duration)
            if dur <= 0 and path and hasattr(self, '_db') and self._db:
                info = self._db.get_track_info(path)
                if info and info.get('duration'):
                    dur = float(info['duration'])

            return {
                "title": title,
                "current_path": path or "",
                "is_playing": is_playing,
                "position": pos,
                "duration": dur,
                "cover_url": cover_url,
                "is_muted": is_muted,
                "play_mode": getattr(self, 'play_mode', getattr(self, '_play_mode', config.get('play_mode', 'loop'))),
                "is_upsample": config.get('audio_upsample', False),
                "is_normalize": config.get('audio_normalize', False),
                "is_boot_mode": config.get('boot_music', True),
                "is_noir_mode": config.get("noir_mode", False),
                "play_count": play_count,
                "like_score": like_score
            }
        except Exception as e:
            print(f"[Playback Status Error] {e}")
            return {
                "title": "AWAITING_MEDIA",
                "current_path": "",
                "is_playing": False,
                "position": 0.0,
                "duration": 0.0,
                "cover_url": "",
                "is_muted": False,
                "play_mode": "loop",
                "is_upsample": False,
                "is_normalize": False,
                "is_boot_mode": True,
                "is_noir_mode": False
            }



    def toggle_favorite(self, path):
        is_fav, score = self._db.toggle_favorite(path)
        return {"is_favorite": is_fav, "like_score": score}

    def get_favorites(self):
        return self._db.get_favorites()

    def get_top10(self):
        return self._db.get_top10()

    def get_track_info(self, path):
        info = self._db.get_track_info(path)
        return info if info else {}



def cleanup_cache():
    # 保持 WebView2 本地快取，讓 EdgeChromium 引擎秒開啟動 (<0.1s)
    pass


if __name__ == "__main__":
    def resource_path(relative_path):
        """取得資源絕對路徑 (支援打包)"""
        try: base_path = sys._MEIPASS
        except: base_path = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base_path, relative_path)

    api = API()
    
    # --- 【核心修改】啟動時讀取記錄的主題 ---
    saved_theme = config.get("theme_file")
    
    # 1. 嘗試讀取設定的主題
    html_path = resource_path(os.path.join('ui', saved_theme))
    
    # 2. 如果設定的檔案不見了，回退到預設的 index.html
    if not os.path.exists(html_path):
        print(f"[System] Saved theme {saved_theme} not found, reverting to index.html")
        html_path = resource_path(os.path.join('ui', 'index.html'))
        # 如果連 index.html 都不在 ui 資料夾，試試根目錄 (相容舊版結構)
        if not os.path.exists(html_path):
            html_path = resource_path('index.html')
            
    html_url = f'file://{html_path}'
    print(f"[System] Loading theme: {html_url}")

    window = webview.create_window(
        title='MPlay // INTERNET ANGEL OS', url=html_url, js_api=api,
        width=1200, height=800, background_color='#190b30', frameless=False
    )
    api.set_window(window)
    
    webview.start(debug=False, private_mode=True)
    
    api.close_app()
    cleanup_cache()
    force_kill_mpv() 
    os._exit(0)