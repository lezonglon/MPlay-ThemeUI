# -*- coding: utf-8 -*-
"""
Audio Bridge & Resampler Module for MPlay (DEV Edition)
提供 Windows 音訊端點串接、系統輸出內部即時側錄 (Event-Driven WASAPI Loopback)、
虛擬聲卡/麥克風原生 DirectShow 即時擷取、MPV 發燒級重採樣濾鏡 (SOXR VHQ/HQ, SWR Polyphase, Sinc Native)，
具備抽插式隨插即拔 (Hotplug)、關閉還原、全通用動態設備檢測 (動態增刪無殘留)、嚴格防巡迴反饋迴路保護與雙音訊共存機制。
"""

import os
import sys
import re
import time
import uuid
import socket
import atexit
import ctypes
import threading
from ctypes import wintypes, POINTER, c_void_p, c_wchar_p, c_float, c_int, byref, cast, Structure, string_at

# 確保 libmpv-2.dll 所在路徑已加入 PATH
bin_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "bin"))
if os.path.exists(bin_path):
    os.environ["PATH"] = bin_path + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, 'add_dll_directory'):
        try: os.add_dll_directory(bin_path)
        except: pass

try:
    import mpv
except Exception as _e:
    mpv = None

ole32 = ctypes.windll.ole32
kernel32 = ctypes.windll.kernel32
winmm = ctypes.windll.winmm

try: ole32.CoInitialize(None)
except: pass

# 啟用 Windows 全域 1ms 高精度定時器，大幅消除線程休眠/排程抖動延遲
try: winmm.timeBeginPeriod(1)
except: pass
atexit.register(lambda: winmm.timeEndPeriod(1) if winmm else None)

class GUID(Structure):
    _fields_ = [
        ('Data1', wintypes.DWORD),
        ('Data2', wintypes.WORD),
        ('Data3', wintypes.WORD),
        ('Data4', ctypes.c_byte * 8)
    ]

def str_to_guid(s):
    u = uuid.UUID(s)
    g = GUID()
    g.Data1 = u.time_low
    g.Data2 = u.time_mid
    g.Data3 = u.time_hi_version
    for i in range(8):
        g.Data4[i] = u.bytes[8 + i]
    return g

CLSID_MMDeviceEnumerator = str_to_guid('{BCDE0395-E52F-467C-8E3D-C4579291692E}')
IID_IMMDeviceEnumerator = str_to_guid('{A95664D2-9614-4F35-A746-DE8DB63617E6}')
IID_IAudioClient = str_to_guid('{1CB9AD4C-DBFA-4c32-B178-C2F568A703B2}')
IID_IAudioCaptureClient = str_to_guid('{C8ADBD64-E71E-48a0-A4DE-185C395CD317}')
IID_IAudioEndpointVolume = str_to_guid('{5CDF2C82-841E-4546-9722-0CF74078229A}')
CLSID_PolicyConfigClient = str_to_guid('{870AF99C-171D-4F9E-AF0D-E63DF40C2BC9}')
IID_IPolicyConfig = str_to_guid('{F8679F50-850A-41CF-9C72-430F290290C8}')

class PROPERTYKEY(Structure):
    _fields_ = [('fmtid', GUID), ('pid', wintypes.DWORD)]

PKEY_Device_FriendlyName = PROPERTYKEY(str_to_guid('{a45c254e-df1c-4efd-8020-67d146a850e0}'), 14)

class PROPVARIANT(Structure):
    _fields_ = [
        ('vt', ctypes.c_ushort),
        ('wReserved1', wintypes.WORD),
        ('wReserved2', wintypes.WORD),
        ('wReserved3', wintypes.WORD),
        ('pwszVal', c_wchar_p)
    ]

class WAVEFORMATEX(Structure):
    _fields_ = [
        ('wFormatTag', wintypes.WORD),
        ('nChannels', wintypes.WORD),
        ('nSamplesPerSec', wintypes.DWORD),
        ('nAvgBytesPerSec', wintypes.DWORD),
        ('nBlockAlign', wintypes.WORD),
        ('wBitsPerSample', wintypes.WORD),
        ('cbSize', wintypes.WORD),
    ]


def _is_virtual_device_name(name):
    """通用特徵判定是否為虛擬聲卡/軟體端點 (無任何私有型號洩漏)"""
    if not name:
        return False
    name_lower = name.lower()
    virtual_keywords = ['cable', 'virtual', 'loopback', 'relay', 'null', 'dummy', 'voicemeeter', 'vac', 'utility', 'vb-audio', 'audiorelay']
    return any(k in name_lower for k in virtual_keywords)


def get_default_render_endpoint():
    """取得當前 Windows 預設播放設備 (Render Endpoint) 的 ID 與名稱"""
    try:
        try: ole32.CoInitialize(None)
        except: pass
        pEnumerator = c_void_p()
        if ole32.CoCreateInstance(byref(CLSID_MMDeviceEnumerator), None, 1, byref(IID_IMMDeviceEnumerator), byref(pEnumerator)) != 0:
            return None
        pVtbl = cast(pEnumerator, POINTER(POINTER(c_void_p))).contents
        GetDefaultAudioEndpoint_proto = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, ctypes.c_int, ctypes.c_int, POINTER(c_void_p))
        GetDefaultAudioEndpoint = GetDefaultAudioEndpoint_proto(pVtbl[4])
        
        pDevice = c_void_p()
        if GetDefaultAudioEndpoint(pEnumerator, 0, 0, byref(pDevice)) != 0: # eRender=0, eConsole=0
            return None
            
        pDevVtbl = cast(pDevice, POINTER(POINTER(c_void_p))).contents
        GetId_proto = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, POINTER(c_wchar_p))
        GetId = GetId_proto(pDevVtbl[5])
        pId = c_wchar_p()
        GetId(pDevice, byref(pId))
        
        OpenPropertyStore_proto = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, wintypes.DWORD, POINTER(c_void_p))
        OpenPropertyStore = OpenPropertyStore_proto(pDevVtbl[4])
        pStore = c_void_p()
        OpenPropertyStore(pDevice, 0, byref(pStore))
        name = ''
        if pStore:
            pStoreVtbl = cast(pStore, POINTER(POINTER(c_void_p))).contents
            GetValue_proto = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, POINTER(PROPERTYKEY), POINTER(PROPVARIANT))
            GetValue = GetValue_proto(pStoreVtbl[5])
            pv = PROPVARIANT()
            GetValue(pStore, byref(PKEY_Device_FriendlyName), byref(pv))
            name = pv.pwszVal or ''
            
        return {"id": pId.value, "name": name}
    except Exception as e:
        print(f"[CoreAudio Error] get_default_render_endpoint: {e}")
        return None


def set_default_render_endpoint(device_id):
    """設定 Windows 預設播放設備 (IPolicyConfig::SetDefaultEndpoint)"""
    if not device_id:
        return False
    try:
        try: ole32.CoInitialize(None)
        except: pass
        pPolicyConfig = c_void_p()
        hr = ole32.CoCreateInstance(byref(CLSID_PolicyConfigClient), None, 1, byref(IID_IPolicyConfig), byref(pPolicyConfig))
        if hr != 0:
            return False
        pPolVtbl = cast(pPolicyConfig, POINTER(POINTER(c_void_p))).contents
        SetDefaultEndpoint_proto = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, c_wchar_p, ctypes.c_int)
        SetDefaultEndpoint = SetDefaultEndpoint_proto(pPolVtbl[13])
        # 設定 Console (0), Multimedia (1), Communications (2)
        SetDefaultEndpoint(pPolicyConfig, device_id, 0)
        SetDefaultEndpoint(pPolicyConfig, device_id, 1)
        return True
    except Exception as e:
        print(f"[CoreAudio Error] set_default_render_endpoint: {e}")
        return False


def get_all_capture_endpoints():
    """動態枚舉所有當前可用之 Windows 錄音/麥克風端點 (eCapture, DEVICE_STATE_ACTIVE)"""
    devs = []
    try:
        try: ole32.CoInitialize(None)
        except: pass
        pEnumerator = c_void_p()
        if ole32.CoCreateInstance(byref(CLSID_MMDeviceEnumerator), None, 1, byref(IID_IMMDeviceEnumerator), byref(pEnumerator)) != 0:
            return devs
        pVtbl = cast(pEnumerator, POINTER(POINTER(c_void_p))).contents
        EnumAudioEndpoints_proto = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, ctypes.c_int, wintypes.DWORD, POINTER(c_void_p))
        EnumAudioEndpoints = EnumAudioEndpoints_proto(pVtbl[3])
        pCol = c_void_p()
        if EnumAudioEndpoints(pEnumerator, 1, 1, byref(pCol)) != 0: # eCapture=1, DEVICE_STATE_ACTIVE=1
            return devs
        pColVtbl = cast(pCol, POINTER(POINTER(c_void_p))).contents
        GetCount_proto = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, POINTER(wintypes.UINT))
        GetCount = GetCount_proto(pColVtbl[3])
        Item_proto = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, wintypes.UINT, POINTER(c_void_p))
        Item = Item_proto(pColVtbl[4])
        count = wintypes.UINT()
        GetCount(pCol, byref(count))
        for i in range(count.value):
            pDev = c_void_p()
            Item(pCol, i, byref(pDev))
            pDevVtbl = cast(pDev, POINTER(POINTER(c_void_p))).contents
            GetId_proto = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, POINTER(c_wchar_p))
            GetId = GetId_proto(pDevVtbl[5])
            pId = c_wchar_p()
            GetId(pDev, byref(pId))
            OpenPropertyStore_proto = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, wintypes.DWORD, POINTER(c_void_p))
            OpenPropertyStore = OpenPropertyStore_proto(pDevVtbl[4])
            pStore = c_void_p()
            OpenPropertyStore(pDev, 0, byref(pStore))
            name = ''
            if pStore:
                pStoreVtbl = cast(pStore, POINTER(POINTER(c_void_p))).contents
                GetValue_proto = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, POINTER(PROPERTYKEY), POINTER(PROPVARIANT))
                GetValue = GetValue_proto(pStoreVtbl[5])
                pv = PROPVARIANT()
                GetValue(pStore, byref(PKEY_Device_FriendlyName), byref(pv))
                name = pv.pwszVal or ''
            is_virtual = _is_virtual_device_name(name)
            devs.append({"id": pId.value, "name": name, "is_virtual": is_virtual})
    except Exception as e:
        print(f"[CoreAudio Error] get_all_capture_endpoints: {e}")
    return devs


def get_all_playback_devices(mpv_instance=None):
    """
    動態枚舉所有當前活躍的 Windows 輸出播放端點 (eRender, DEVICE_STATE_ACTIVE)。
    直接透過 CoreAudio COM 即時枚舉，即插即現、拔除即刪，並產生 MPV wasapi 相容 ID。
    """
    devices = [{"id": "auto", "endpoint_id": "auto", "name": "Windows 預設輸出", "is_virtual": False}]
    try:
        try: ole32.CoInitialize(None)
        except: pass
        pEnumerator = c_void_p()
        if ole32.CoCreateInstance(byref(CLSID_MMDeviceEnumerator), None, 1, byref(IID_IMMDeviceEnumerator), byref(pEnumerator)) != 0:
            return devices
        pVtbl = cast(pEnumerator, POINTER(POINTER(c_void_p))).contents
        EnumAudioEndpoints_proto = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, ctypes.c_int, wintypes.DWORD, POINTER(c_void_p))
        EnumAudioEndpoints = EnumAudioEndpoints_proto(pVtbl[3])
        pCol = c_void_p()
        if EnumAudioEndpoints(pEnumerator, 0, 1, byref(pCol)) != 0: # eRender=0, DEVICE_STATE_ACTIVE=1
            return devices
        pColVtbl = cast(pCol, POINTER(POINTER(c_void_p))).contents
        GetCount_proto = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, POINTER(wintypes.UINT))
        GetCount = GetCount_proto(pColVtbl[3])
        Item_proto = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, wintypes.UINT, POINTER(c_void_p))
        Item = Item_proto(pColVtbl[4])
        count = wintypes.UINT()
        GetCount(pCol, byref(count))
        for i in range(count.value):
            pDev = c_void_p()
            Item(pCol, i, byref(pDev))
            pDevVtbl = cast(pDev, POINTER(POINTER(c_void_p))).contents
            GetId_proto = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, POINTER(c_wchar_p))
            GetId = GetId_proto(pDevVtbl[5])
            pId = c_wchar_p()
            GetId(pDev, byref(pId))
            raw_id = pId.value or ''
            OpenPropertyStore_proto = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, wintypes.DWORD, POINTER(c_void_p))
            OpenPropertyStore = OpenPropertyStore_proto(pDevVtbl[4])
            pStore = c_void_p()
            OpenPropertyStore(pDev, 0, byref(pStore))
            name = ''
            if pStore:
                pStoreVtbl = cast(pStore, POINTER(POINTER(c_void_p))).contents
                GetValue_proto = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, POINTER(PROPERTYKEY), POINTER(PROPVARIANT))
                GetValue = GetValue_proto(pStoreVtbl[5])
                pv = PROPVARIANT()
                GetValue(pStore, byref(PKEY_Device_FriendlyName), byref(pv))
                name = pv.pwszVal or ''
            is_virtual = _is_virtual_device_name(name)
            guid_part = raw_id.split('.')[-1] if '.' in raw_id else raw_id
            wasapi_id = f"wasapi/{guid_part}"
            devices.append({
                "id": wasapi_id,
                "endpoint_id": raw_id,
                "name": name,
                "is_virtual": is_virtual
            })
    except Exception as e:
        print(f"[CoreAudio Error] get_all_playback_devices: {e}")
    return devices


def resolve_device_endpoint_id(device_tag):
    """
    將前端傳入之各種設備標識 (loopback:default, wasapi/{GUID}, dshow:名稱, auto 等)
    解析為 Windows CoreAudio 實際端點 ID ({0.0.0.00000000}.{...} 或 {0.0.1.00000000}.{...})
    """
    if not device_tag:
        return None
    s = str(device_tag).strip()
    if s == "auto" or s == "loopback:default":
        d = get_default_render_endpoint()
        return d["id"] if d else None

    if s.startswith("loopback:"):
        return s.split(":", 1)[1]

    if s.startswith("dshow:"):
        name = s.split(":", 1)[1]
        for cap in get_all_capture_endpoints():
            if cap.get("name") == name:
                return cap.get("id")
        return None

    if s.startswith("wasapi/"):
        guid_part = s.split("/", 1)[1]
        for pb in get_all_playback_devices():
            if pb.get("id") == s or guid_part in (pb.get("endpoint_id") or ""):
                return pb.get("endpoint_id")
        return None

    if s.startswith("{"):
        return s

    for pb in get_all_playback_devices():
        if pb.get("id") == s or pb.get("name") == s:
            return pb.get("endpoint_id")

    for cap in get_all_capture_endpoints():
        if cap.get("id") == s or cap.get("name") == s:
            return cap.get("id")

    return None


def get_endpoint_volume(endpoint_id_or_tag):
    """取得指定音訊端點的硬體主音量 (0-100) 與靜音狀態 (True/False)"""
    ep_id = resolve_device_endpoint_id(endpoint_id_or_tag)
    if not ep_id:
        return None
    try:
        try: ole32.CoInitialize(None)
        except: pass
        pEnum = c_void_p()
        hr = ole32.CoCreateInstance(byref(CLSID_MMDeviceEnumerator), None, 1, byref(IID_IMMDeviceEnumerator), byref(pEnum))
        if hr != 0: return None
        pVtbl = cast(pEnum, POINTER(POINTER(c_void_p))).contents
        GetDevice = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, c_wchar_p, POINTER(c_void_p))(pVtbl[5])
        pDevice = c_void_p()
        hr = GetDevice(pEnum, ep_id, byref(pDevice))
        if hr != 0 or not pDevice: return None

        pDevVtbl = cast(pDevice, POINTER(POINTER(c_void_p))).contents
        Activate = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, POINTER(GUID), wintypes.DWORD, c_void_p, POINTER(c_void_p))(pDevVtbl[3])
        pVol = c_void_p()
        hr = Activate(pDevice, byref(IID_IAudioEndpointVolume), 1, None, byref(pVol))
        if hr != 0 or not pVol: return None

        pVolVtbl = cast(pVol, POINTER(POINTER(c_void_p))).contents
        GetMasterVolumeLevelScalar = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, POINTER(c_float))(pVolVtbl[9])
        GetMute = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, POINTER(c_int))(pVolVtbl[15])

        level = c_float()
        mute = c_int()
        GetMasterVolumeLevelScalar(pVol, byref(level))
        GetMute(pVol, byref(mute))

        return {
            "volume": int(round(level.value * 100)),
            "mute": bool(mute.value),
            "endpoint_id": ep_id
        }
    except Exception as e:
        print(f"[CoreAudio Error] get_endpoint_volume: {e}")
        return None


def set_endpoint_volume(endpoint_id_or_tag, vol_pct):
    """設定指定音訊端點的硬體主音量 (0-100)"""
    ep_id = resolve_device_endpoint_id(endpoint_id_or_tag)
    if not ep_id:
        return False
    try:
        try: ole32.CoInitialize(None)
        except: pass
        pEnum = c_void_p()
        hr = ole32.CoCreateInstance(byref(CLSID_MMDeviceEnumerator), None, 1, byref(IID_IMMDeviceEnumerator), byref(pEnum))
        if hr != 0: return False
        pVtbl = cast(pEnum, POINTER(POINTER(c_void_p))).contents
        GetDevice = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, c_wchar_p, POINTER(c_void_p))(pVtbl[5])
        pDevice = c_void_p()
        hr = GetDevice(pEnum, ep_id, byref(pDevice))
        if hr != 0 or not pDevice: return False

        pDevVtbl = cast(pDevice, POINTER(POINTER(c_void_p))).contents
        Activate = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, POINTER(GUID), wintypes.DWORD, c_void_p, POINTER(c_void_p))(pDevVtbl[3])
        pVol = c_void_p()
        hr = Activate(pDevice, byref(IID_IAudioEndpointVolume), 1, None, byref(pVol))
        if hr != 0 or not pVol: return False

        pVolVtbl = cast(pVol, POINTER(POINTER(c_void_p))).contents
        SetMasterVolumeLevelScalar = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, c_float, c_void_p)(pVolVtbl[7])
        val = max(0.0, min(1.0, float(vol_pct) / 100.0))
        hr = SetMasterVolumeLevelScalar(pVol, c_float(val), None)
        return hr >= 0
    except Exception as e:
        print(f"[CoreAudio Error] set_endpoint_volume: {e}")
        return False


def set_endpoint_mute(endpoint_id_or_tag, is_muted):
    """設定指定音訊端點的硬體靜音狀態 (True/False)"""
    ep_id = resolve_device_endpoint_id(endpoint_id_or_tag)
    if not ep_id:
        return False
    try:
        try: ole32.CoInitialize(None)
        except: pass
        pEnum = c_void_p()
        hr = ole32.CoCreateInstance(byref(CLSID_MMDeviceEnumerator), None, 1, byref(IID_IMMDeviceEnumerator), byref(pEnum))
        if hr != 0: return False
        pVtbl = cast(pEnum, POINTER(POINTER(c_void_p))).contents
        GetDevice = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, c_wchar_p, POINTER(c_void_p))(pVtbl[5])
        pDevice = c_void_p()
        hr = GetDevice(pEnum, ep_id, byref(pDevice))
        if hr != 0 or not pDevice: return False

        pDevVtbl = cast(pDevice, POINTER(POINTER(c_void_p))).contents
        Activate = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, POINTER(GUID), wintypes.DWORD, c_void_p, POINTER(c_void_p))(pDevVtbl[3])
        pVol = c_void_p()
        hr = Activate(pDevice, byref(IID_IAudioEndpointVolume), 1, None, byref(pVol))
        if hr != 0 or not pVol: return False

        pVolVtbl = cast(pVol, POINTER(POINTER(c_void_p))).contents
        SetMute = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, c_int, c_void_p)(pVolVtbl[14])
        hr = SetMute(pVol, c_int(1 if is_muted else 0), None)
        return hr >= 0
    except Exception as e:
        print(f"[CoreAudio Error] set_endpoint_mute: {e}")
        return False


def get_all_input_endpoints(default_render=None):
    """
    【核心全通用輸入端點清單】
    完整支援：
    1. 🌟 Windows 系統預設輸出側錄 (WASAPI Loopback)
    2. 🔊 所有活躍的 Windows 輸出設備 (如 Speakers, Headphones, HDMI Audio, CABLE Input 等 WASAPI 側錄)
    3. 🎤 所有活躍的 Windows 錄音/麥克風端點 (DirectShow Capture)
    動態即時查詢，插拔即時增刪，絕不殘留無效設備。
    """
    if not default_render:
        default_render = get_default_render_endpoint()
    def_name = default_render.get('name') if default_render else None
    disp_name = f"⭐ Windows 系統預設輸出 (目前: {def_name}) (WASAPI 內部即時側錄)" if def_name else "⭐ Windows 系統預設輸出 (WASAPI 內部即時側錄 / Loopback)"

    inputs = []
    
    # 1. 系統預設輸出內部即時側錄
    inputs.append({
        "id": "loopback:default",
        "name": disp_name,
        "type": "loopback",
        "is_virtual": False,
        "is_recommended": True
    })

    # 2. 所有可用的 Windows 輸出設備 (WASAPI Loopback)
    playback_devs = get_all_playback_devices()
    for d in playback_devs:
        if d.get('id') == 'auto' or not d.get('endpoint_id'):
            continue
        ep_id = d.get('endpoint_id')
        inputs.append({
            "id": f"loopback:{ep_id}",
            "name": f"🔊 [Windows 輸出] {d.get('name')} (WASAPI 側錄)",
            "type": "loopback",
            "is_virtual": d.get('is_virtual', False),
            "is_recommended": False
        })

    # 3. 所有可用的 Windows 錄音/麥克風端點 (DirectShow Capture)
    capture_devs = get_all_capture_endpoints()
    for d in capture_devs:
        c_name = d.get('name')
        if not c_name: continue
        inputs.append({
            "id": f"dshow:{c_name}",
            "name": f"🎤 [錄音/麥克風/虛擬] {c_name}",
            "type": "dshow",
            "is_virtual": d.get('is_virtual', False),
            "is_recommended": False
        })

    return inputs


def find_safe_physical_output(playback_devices=None):
    """
    全通用實體輸出檢測：
    優先尋找真實的實體喇叭、DAC、耳機或音訊控制器，絕不包含任何使用者專屬私有硬體型號。
    """
    if playback_devices is None:
        playback_devices = get_all_playback_devices()
        
    # 優先順序 1：標準實體喇叭、DAC 或耳機
    for d in playback_devices:
        if not d.get('is_virtual') and d.get('id') != 'auto':
            name_lower = d.get('name', '').lower()
            if any(k in name_lower for k in ['speaker', '喇叭', 'dac', 'headphone', 'headset', 'earphone', '耳機']):
                return d.get('id')
                
    # 優先順序 2：板載/獨立音訊控制器或數位音訊端點
    for d in playback_devices:
        if not d.get('is_virtual') and d.get('id') != 'auto':
            name_lower = d.get('name', '').lower()
            if any(k in name_lower for k in ['realtek', 'line out', 'line-out', 'digital out', 'hdmi', 'display', 'audio', 'sound']):
                return d.get('id')
                
    # 優先順序 3：任何未標記為虛擬的設備
    for d in playback_devices:
        if not d.get('is_virtual') and d.get('id') != 'auto':
            return d.get('id')
            
    return "auto"


def _find_available_virtual_render_endpoint():
    """尋找可用於讓 Windows 程式輸出的虛擬 Render 端點 (如 CABLE Input 或其他虛擬設備)"""
    devs = get_all_playback_devices()
    for d in devs:
        if d.get('is_virtual') and d.get('endpoint_id') and d.get('id') != 'auto':
            name_lower = d.get('name', '').lower()
            if 'cable input' in name_lower:
                return d.get('endpoint_id')
    for d in devs:
        if d.get('is_virtual') and d.get('endpoint_id') and d.get('id') != 'auto':
            return d.get('endpoint_id')
    return None


def _extract_guid(endpoint_or_id):
    """提取音訊端點字串中的標準 GUID (小寫，無括號)"""
    if not endpoint_or_id:
        return ""
    s = str(endpoint_or_id).lower()
    m = re.search(r'\{([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\}', s)
    if m:
        return m.group(1)
    m = re.search(r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', s)
    if m:
        return m.group(1)
    return s


def _map_mpv_audio_format(fmt):
    """
    將音訊位元深度映射為 MPV 原生支援之 audio-format 屬性。
    MPV audio-format 原生僅支援：'s16', 's32', 'float', 'double', 'u8'。
    24-Bit 發燒整數在 PC / WASAPI / DAC 硬體標準中均以標準 32-bit 容器 (s32) 封裝傳輸。
    """
    if not fmt:
        return 'float'
    f = str(fmt).lower()
    if f in ('s16', '16'):
        return 's16'
    elif f in ('s24', '24'):
        return 's32'
    elif f in ('s32', '32'):
        return 's32'
    elif f in ('double', '64'):
        return 'double'
    elif f in ('float', '32f'):
        return 'float'
    return 'float'


def is_feedback_loop(input_name, output_device_id, output_device_name=None, default_render=None, will_auto_route=True):
    """
    【核心防巡迴安全檢測】
    1. 輸出設備不可與輸入設備為同一端點 (無論是 WASAPI 側錄還是虛擬聲卡)。
    2. 系統全域串接模式嚴格禁止將任何虛擬聲卡/軟體端點 (CABLE/VoiceMeeter 等) 作為輸出喇叭。
    3. 抽插接管 (will_auto_route) 模式下：
       - 若即將將 Windows 預設輸出導向虛擬端點 (如 CABLE Input)，則輸出端點絕不可包含該虛擬端點或任何虛擬設備。
    4. 嚴格感測 Windows 預設端點衝突：
       - 輸入選「Windows 預設輸出」，且輸出亦選「Windows 預設」-> 巡迴衝突！
       - 輸入選「Windows 預設輸出」，且選定輸出恰為當前 Windows 系統預設設備 -> 巡迴衝突！
       - 輸入與輸出指向相同之 GUID 端點 -> 巡迴衝突！
    """
    if not input_name:
        return False, "未選定輸入設備"

    if not default_render:
        default_render = get_default_render_endpoint()

    def_id = (default_render.get('id') if default_render else '') or ''
    def_name = (default_render.get('name') if default_render else '') or 'Windows 預設'
    def_guid = _extract_guid(def_id)

    out_raw = output_device_id or ''
    is_out_default = (out_raw == 'auto' or not out_raw)
    out_guid = def_guid if is_out_default else _extract_guid(out_raw)

    if not output_device_name and not is_out_default:
        for d in get_all_playback_devices():
            if d.get('id') == out_raw:
                output_device_name = d.get('name', '')
                break

    out_desc_lower = (output_device_name or '').lower()
    out_id_lower = str(out_raw).lower()
    combined_out = f"{out_id_lower} {out_desc_lower}"

    # 1. 系統串接模式下：嚴格禁止將任何虛擬聲卡 (如 VB-Audio CABLE、VoiceMeeter 等) 作為輸出端點
    if _is_virtual_device_name(combined_out):
        return True, "【迴路衝突】系統串接廣播模式嚴格禁止將虛擬聲卡/軟體端點 (如 CABLE) 作為輸出喇叭！請指定實體耳機、喇叭或 DAC 設備。"

    # 2. 自動接管模式 (will_auto_route) 檢查：
    if will_auto_route:
        virt_ep_id = _find_available_virtual_render_endpoint()
        if virt_ep_id:
            virt_guid = _extract_guid(virt_ep_id)
            if out_guid and virt_guid and out_guid == virt_guid:
                return True, "【迴路衝突】不可將即將接管之虛擬線路 (如 CABLE Input) 作為輸出端點，否則將造成自身閉環巡迴嘯叫！"

    # 3. 兩者皆為 Windows 預設輸出
    if input_name == "loopback:default" and is_out_default:
        return True, "【迴路衝突】輸入選「Windows 預設輸出」且輸出亦選「Windows 預設」，兩者指向同一端點，嚴禁巡迴反饋！請將輸出指定為具體實體設備。"

    # 4. 輸入選「Windows 預設輸出」，且選定之輸出端點恰為當前 Windows 預設端點 (當非 auto_route 或接管前)
    if input_name == "loopback:default":
        if not will_auto_route and def_guid and out_guid and def_guid == out_guid:
            return True, f"【迴路衝突】當前 Windows 系統預設輸出即為選定輸出「{def_name}」，端點相同將造成自身循環反饋！請將輸出改為其他實體設備。"

    # 5. 輸入選具體 Loopback 設備端點
    if input_name.startswith("loopback:"):
        in_guid = _extract_guid(input_name)
        if in_guid:
            # 輸出為預設端點，且輸入端點恰為當前 Windows 預設端點
            if is_out_default and def_guid and in_guid == def_guid:
                return True, f"【迴路衝突】當前 Windows 系統預設輸出即為「{def_name}」，與選定輸入端點相同，嚴禁巡迴反饋！"
            # 輸出為具體設備端點，且兩者 GUID 相同
            if not is_out_default and out_guid and in_guid == out_guid:
                return True, "【迴路衝突】輸出端點不可與輸入端點指向相同之 Windows 輸出設備！"

    # 6. 虛擬聲卡輸入與輸出短路檢測 (例如 CABLE Output -> CABLE Input)
    input_lower = input_name.lower()
    if _is_virtual_device_name(input_lower):
        if 'cable' in input_lower and 'cable' in combined_out:
            return True, "【迴路衝突】不可將同一虛擬線路 (CABLE) 同時作為輸入與輸出，避免短路巡迴！"
        if 'audiorelay' in input_lower and 'audiorelay' in combined_out:
            return True, "【迴路衝突】輸出端點不可為同一虛擬音訊設備！"

    return False, "迴路安全"


def check_multiple_outputs_loop(input_name, output_device_ids, default_render=None, will_auto_route=True):
    """
    【多輸出並接防巡迴安全檢測】
    檢測清單中的每一個輸出端點，若有任何端點造成迴路反饋，立即回傳阻擋原因。
    """
    if not output_device_ids:
        return False, "迴路安全"
    for out_id in output_device_ids:
        if not out_id:
            continue
        is_loop, reason = is_feedback_loop(input_name, out_id, None, default_render, will_auto_route)
        if is_loop:
            return True, reason
    return False, "迴路安全"


class WasapiLoopbackStreamer:
    """
    【發燒級 Event-Driven WASAPI Loopback 廣播引擎 (Multi-Client Broadcast)】
    透過 Windows CoreAudio 原生 AUDCLNT_STREAMFLAGS_EVENTCALLBACK | AUDCLNT_STREAMFLAGS_LOOPBACK，
    以硬體事件驅動 (Event-Driven) 零等待、零輪詢、零人工靜音填充，達成真正無失真、零延遲的內部即時音訊側錄。
    支援多客戶端 Socket 同步廣播，可同時為多個並接 MPV 喇叭實例分發同一路 PCM 音訊串流。
    """
    def __init__(self, endpoint_id=None, host="127.88.99.1", port=0):
        self.endpoint_id = endpoint_id # None 或 'default' 代表預設設備
        self.host = host or "127.88.99.1"
        self.port = port or 0
        self.channels = 2
        self.sample_rate = 48000
        self.sample_format = "float" # float, s32, s16
        self.stop_event = threading.Event()
        self.server_sock = None
        self.client_socks = []
        self.socks_lock = threading.Lock()
        self.worker_thread = None
        self.accept_thread = None
        self.is_running = False
        self.last_error = None

    def start(self):
        try:
            self.stop_event.clear()
            self.client_socks = []

            # 建立專屬隔離 Socket 通道 (具備 SO_EXCLUSIVEADDRUSE 獨占防劫持保護)
            # 優先採用 127.88.99.1 等專屬子網迴路 IP，徹底避開絕大多數程式使用的 127.0.0.1
            candidates_ip = [self.host] if self.host else []
            for fallback_ip in ["127.88.99.1", "127.42.0.1", "127.0.0.1"]:
                if fallback_ip not in candidates_ip:
                    candidates_ip.append(fallback_ip)

            SO_EXCLUSIVEADDRUSE = getattr(socket, 'SO_EXCLUSIVEADDRUSE', -5)
            server_bound = False
            last_bind_err = None

            for ip in candidates_ip:
                self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    # Windows 核心獨占保護：防止任何其他外部或惡意進程強制搶佔/竊聽同一埠號
                    try:
                        self.server_sock.setsockopt(socket.SOL_SOCKET, SO_EXCLUSIVEADDRUSE, 1)
                    except Exception:
                        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

                    target_port = self.port
                    if target_port > 0:
                        try:
                            self.server_sock.bind((ip, target_port))
                            server_bound = True
                        except Exception:
                            # 智慧避讓機制：若指定 Port 被佔用，自動在 +1 ~ +30 範圍內搜尋閒置 Port
                            for p_try in range(target_port + 1, target_port + 31):
                                try:
                                    self.server_sock.bind((ip, p_try))
                                    target_port = p_try
                                    server_bound = True
                                    break
                                except Exception:
                                    continue
                    if not server_bound:
                        # 核心動態分配：由 OS Winsock 內核自動分配 49152~65535 絕無衝突的高位閒置埠
                        self.server_sock.bind((ip, 0))
                        server_bound = True

                    self.host = ip
                    self.port = self.server_sock.getsockname()[1]
                    self.server_sock.listen(5)
                    break
                except Exception as be:
                    last_bind_err = be
                    try: self.server_sock.close()
                    except: pass
                    self.server_sock = None

            if not server_bound or not self.server_sock:
                raise RuntimeError(f"無法建立專屬隔離通訊通道: {last_bind_err}")

            try: ole32.CoInitialize(None)
            except: pass
            pEnum = c_void_p()
            if ole32.CoCreateInstance(byref(CLSID_MMDeviceEnumerator), None, 1, byref(IID_IMMDeviceEnumerator), byref(pEnum)) != 0:
                raise RuntimeError("無法初始化 MMDeviceEnumerator")
            pVtbl = cast(pEnum, POINTER(POINTER(c_void_p))).contents

            pDevice = c_void_p()
            if not self.endpoint_id or self.endpoint_id == 'default':
                GetDefaultAudioEndpoint = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, ctypes.c_int, ctypes.c_int, POINTER(c_void_p))(pVtbl[4])
                GetDefaultAudioEndpoint(pEnum, 0, 0, byref(pDevice)) # eRender, eConsole
            else:
                GetDevice = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, c_wchar_p, POINTER(c_void_p))(pVtbl[5])
                GetDevice(pEnum, self.endpoint_id, byref(pDevice))

            if not pDevice:
                raise RuntimeError(f"找不到目標音訊端點: {self.endpoint_id}")

            pDevVtbl = cast(pDevice, POINTER(POINTER(c_void_p))).contents
            Activate = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, POINTER(GUID), wintypes.DWORD, c_void_p, POINTER(c_void_p))(pDevVtbl[3])
            pAudioClient = c_void_p()
            Activate(pDevice, byref(IID_IAudioClient), 7, None, byref(pAudioClient))

            pClientVtbl = cast(pAudioClient, POINTER(POINTER(c_void_p))).contents
            GetMixFormat = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, POINTER(POINTER(WAVEFORMATEX)))(pClientVtbl[8])
            pWfx = POINTER(WAVEFORMATEX)()
            GetMixFormat(pAudioClient, byref(pWfx))
            wfx = pWfx.contents

            self.channels = wfx.nChannels
            self.sample_rate = wfx.nSamplesPerSec
            if wfx.wBitsPerSample == 32:
                self.sample_format = "float"
            elif wfx.wBitsPerSample == 24:
                self.sample_format = "s24"
            else:
                self.sample_format = "s16"

            self.is_running = True
            self.accept_thread = threading.Thread(target=self._run_accept, daemon=True)
            self.accept_thread.start()

            self.worker_thread = threading.Thread(target=self._run_loopback, args=(pAudioClient, pWfx), daemon=True)
            self.worker_thread.start()

            return {
                "success": True,
                "host": self.host,
                "port": self.port,
                "url": f"tcp://{self.host}:{self.port}",
                "rate": self.sample_rate,
                "channels": self.channels,
                "format": self.sample_format
            }
        except Exception as e:
            self.last_error = str(e)
            print(f"[WasapiLoopback Error] {e}")
            self.stop()
            return {"success": False, "error": str(e)}

    def _run_accept(self):
        while not self.stop_event.is_set():
            try:
                self.server_sock.settimeout(0.5)
                s, _ = self.server_sock.accept()
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1) # 關閉 Nagle 演算法
                s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096) # 最小化緩衝杜絕延遲
                with self.socks_lock:
                    self.client_socks.append(s)
            except socket.timeout:
                continue
            except Exception:
                break

    def _run_loopback(self, pAudioClient, pWfx):
        wfx = pWfx.contents
        hEvent = None
        try:
            try: ole32.CoInitialize(None)
            except: pass

            pClientVtbl = cast(pAudioClient, POINTER(POINTER(c_void_p))).contents
            Initialize = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, ctypes.c_int, wintypes.DWORD, ctypes.c_longlong, ctypes.c_longlong, c_void_p, POINTER(GUID))(pClientVtbl[3])
            # AUDCLNT_STREAMFLAGS_LOOPBACK (0x20000) | AUDCLNT_STREAMFLAGS_EVENTCALLBACK (0x40000) = 0x60000
            # 10ms - 20ms 硬體事件驅動緩衝，實現極致超低延遲
            hr_init = Initialize(pAudioClient, 0, 0x00060000, 100000, 0, pWfx, None)
            if hr_init != 0:
                hr_init = Initialize(pAudioClient, 0, 0x00060000, 200000, 0, pWfx, None)
            if hr_init != 0:
                print(f"[WasapiLoopback] Initialize failed: {hex(hr_init)}")
                return

            hEvent = kernel32.CreateEventW(None, False, False, None)
            SetEventHandle = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, wintypes.HANDLE)(pClientVtbl[13])
            SetEventHandle(pAudioClient, hEvent)

            GetService = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, POINTER(GUID), POINTER(c_void_p))(pClientVtbl[14])
            pCaptureClient = c_void_p()
            GetService(pAudioClient, byref(IID_IAudioCaptureClient), byref(pCaptureClient))

            pCapVtbl = cast(pCaptureClient, POINTER(POINTER(c_void_p))).contents
            GetNextPacketSize = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, POINTER(wintypes.UINT))(pCapVtbl[5])
            GetBuffer = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, POINTER(c_void_p), POINTER(wintypes.UINT), POINTER(wintypes.DWORD), POINTER(ctypes.c_ulonglong), POINTER(ctypes.c_ulonglong))(pCapVtbl[3])
            ReleaseBuffer = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, wintypes.UINT)(pCapVtbl[4])

            Start = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p)(pClientVtbl[10])
            Stop = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p)(pClientVtbl[11])

            Start(pAudioClient)

            pData = c_void_p()
            numFrames = wintypes.UINT()
            flags = wintypes.DWORD()
            devPos = ctypes.c_ulonglong()
            qpcPos = ctypes.c_ulonglong()
            packet_size = wintypes.UINT()
            bytes_per_frame = wfx.nBlockAlign

            while not self.stop_event.is_set():
                # 硬體事件驅動：當音訊硬體產生緩衝時即時喚醒 (最大超時 20ms)
                res = kernel32.WaitForSingleObject(hEvent, 20)
                if res != 0:
                    continue

                while not self.stop_event.is_set():
                    GetNextPacketSize(pCaptureClient, byref(packet_size))
                    if packet_size.value == 0:
                        break

                    hr = GetBuffer(pCaptureClient, byref(pData), byref(numFrames), byref(flags), byref(devPos), byref(qpcPos))
                    if hr != 0: break
                    nbytes = numFrames.value * bytes_per_frame

                    # 真正音訊數據：直接提取發送，絕不任意注入假靜音破壞連續波形
                    if flags.value & 0x2: # AUDCLNT_BUFFERFLAGS_SILENT
                        data = b'\x00' * nbytes
                    else:
                        data = string_at(pData, nbytes)

                    with self.socks_lock:
                        dead = []
                        for s in self.client_socks:
                            try:
                                s.sendall(data)
                            except Exception:
                                dead.append(s)
                        for d in dead:
                            try: d.close()
                            except: pass
                            self.client_socks.remove(d)

                    ReleaseBuffer(pCaptureClient, numFrames.value)

            Stop(pAudioClient)
        except Exception as e:
            print(f"[WasapiLoopback Worker Error] {e}")
        finally:
            if hEvent:
                try: kernel32.CloseHandle(hEvent)
                except: pass
            with self.socks_lock:
                for s in self.client_socks:
                    try: s.close()
                    except: pass
                self.client_socks.clear()
            self.is_running = False

    def stop(self):
        self.stop_event.set()
        if self.server_sock:
            try: self.server_sock.close()
            except: pass
            self.server_sock = None
        with self.socks_lock:
            for s in self.client_socks:
                try: s.close()
                except: pass
            self.client_socks.clear()
        self.is_running = False


class LiveAudioBridge:
    """
    獨立 MPV DSP 音訊串接核心 (Dual-Engine 架構，不干擾主音樂播放器)
    支援：
    - 即時 Windows 系統輸出內部側錄 (Event-Driven WASAPI Loopback，超低延遲)
    - 即時 DirectShow 音訊輸入 (麥克風 / 虛擬音效卡，零開銷)
    - 發燒級重採樣濾鏡 (SoX Resampler VHQ/HQ, FFmpeg SWR, Native Sinc)
    - 多喇叭並接輸出 (Multi-Output 廣播，一對多同時播放)
    - 輸入/輸出端點實體音量與靜音讀寫
    - 抽插式隨插即拔 (Hotplug)，關閉時自動還原 Windows 實體喇叭
    - 防巡迴反饋迴路保護
    """
    def __init__(self):
        self._players = []          # 活躍的 MPV 輸出實例清單 (支援多喇叭並接)
        self._player_map = {}       # 端點 ID -> MPV 實例字典 (支援精準差分熱插拔)
        self.loopback_streamer = None
        self.is_active = False
        self.lock = threading.RLock()
        
        # 串接即時來源狀態快取 (供熱插拔新喇叭即時載入，無須重啟串流)
        self._use_direct_show = False
        self._stream_res = None
        self._source_url = None

        # 串接參數：預設直接側錄 Windows 系統預設輸出，開箱即用支援所有 Windows 程式
        # 雙模式架構：'player' (本機播放器專用模式) vs 'system' (系統全域串接模式)
        self.mode = "player"        # 預設為本機播放器模式 (Local MPV Only)
        self.player_active = False  # 本機播放器濾鏡套用狀態
        self.player_instance = None # MPVSatellite 實例

        self.input_device = "loopback:default"
        
        # 自動選取非虛擬之實體硬體喇叭，確保開箱即用不巡迴
        safe_dev = find_safe_physical_output()
        self.output_device = safe_dev if safe_dev else "auto"
        
        # 多喇叭並接輸出設定
        self.multi_output_enabled = False
        self.output_devices = [self.output_device] if self.output_device != "auto" else []
        
        self.sample_rate = 192000 # 預設 192kHz 發燒級重採樣
        self.resampler_engine = "soxr_vhq" # soxr_vhq, soxr_hq, swr_poly, sinc_native, none
        self.sample_format = "float" # float, s32, s24, s16
        self.volume = 80
        
        
        # 抽插式設計：記錄接管前的原始 Windows 實體預設播放設備 ID
        self.auto_route_windows_default = True
        self.original_default_endpoint_id = None

        # 專屬隔離迴路通道設定 (127.88.99.1 + 核心動態/智慧避讓埠號，徹底防衝突)
        self.stream_host = "127.88.99.1"
        self.stream_port = 0
        
        # 狀態監視
        self.last_error = None
        atexit.register(self.stop)

    @property
    def players(self):
        return self._players

    @property
    def player(self):
        return self._players[0] if self._players else None

    @player.setter
    def player(self, val):
        if val is None:
            self._players = []
        else:
            self._players = [val]

    def set_player_instance(self, player_inst):
        """綁定主程式本機 MPVSatellite 播放器實例"""
        with self.lock:
            self.player_instance = player_inst
            if self.mode == "player" and self.player_active:
                self._apply_to_player()

    def _sanitize_system_outputs(self):
        """
        【系統全域串接防迴路安全淨化】
        當切換至 system 模式時，嚴格審查當前所有選取之輸出端點：
        若包含虛擬聲卡 (如 VB-Audio CABLE) 或與輸入端點指向相同設備 (同喇叭反饋)，
        自動將其自輸出清單排除，並自動鎖定至安全的實體喇叭/耳機/DAC，杜絕任何短路巡迴反饋！
        """
        try:
            default_render = get_default_render_endpoint()
            all_playback = get_all_playback_devices()
            safe_phys = find_safe_physical_output(all_playback)

            # 1. 淨化多輸出清單
            clean_outputs = []
            for dev_id in self.output_devices:
                if not dev_id:
                    continue
                dev_obj = next((d for d in all_playback if d.get('id') == dev_id), None)
                dev_name = dev_obj.get('name', '') if dev_obj else ''
                is_loop, _ = is_feedback_loop(
                    self.input_device, dev_id, dev_name, default_render, self.auto_route_windows_default
                )
                if not is_loop and not _is_virtual_device_name(dev_id) and not _is_virtual_device_name(dev_name):
                    clean_outputs.append(dev_id)

            if not clean_outputs and safe_phys and safe_phys != 'auto':
                clean_outputs = [safe_phys]
            self.output_devices = clean_outputs

            # 2. 淨化單一輸出端點
            single_obj = next((d for d in all_playback if d.get('id') == self.output_device), None)
            single_name = single_obj.get('name', '') if single_obj else ''
            is_single_loop, _ = is_feedback_loop(
                self.input_device, self.output_device, single_name, default_render, self.auto_route_windows_default
            )
            if is_single_loop or _is_virtual_device_name(self.output_device) or _is_virtual_device_name(single_name):
                self.output_device = safe_phys or 'auto'

            print(f"[AudioBridge] 系統串接輸出設備安全淨化完成: output={self.output_device}, outputs={self.output_devices}")
        except Exception as e:
            print(f"[AudioBridge Sanitize Error] {e}")

    def set_mode(self, mode):
        """切換模式: 'player' (本機播放器專用) 或 'system' (系統全域串接)"""
        with self.lock:
            if mode not in ("player", "system"):
                return {"success": False, "error": f"Invalid mode: {mode}"}
            if self.mode == mode:
                return {"success": True, "mode": self.mode, "status": self.get_status()}

            # 切換模式時清理前一模式的運行狀態
            if self.mode == "system" and self.is_active:
                self.stop()
            elif self.mode == "player" and self.player_active:
                self.player_active = False
                self._clear_player_filters()

            self.mode = mode
            if self.mode == "system":
                self._sanitize_system_outputs()
            print(f"[AudioBridge] 模式切換為: {self.mode}")
            return {"success": True, "mode": self.mode, "status": self.get_status()}

    def _apply_to_player(self):
        """將當前之發燒重採樣熱套用至本機 MPV 播放器"""
        if self.player_instance:
            try:
                self.player_instance.update_audio_filters(
                    resampler_engine=self.resampler_engine,
                    sample_rate=self.sample_rate,
                    sample_format=self.sample_format,
                    player_active=True
                )
            except Exception as e:
                print(f"[AudioBridge Player Apply Error] {e}")

    def _clear_player_filters(self):
        """清除本機 MPV 播放器之空間濾鏡與發燒重採樣，還原原生直通輸出"""
        if self.player_instance:
            try:
                self.player_instance.update_audio_filters(
                    resampler_engine="none",
                    sample_rate=0,
                    player_active=False
                )
            except Exception as e:
                print(f"[AudioBridge Player Clear Error] {e}")

    def _update_params_from_dict(self, config_dict):
        if not config_dict: return
        self.input_device = config_dict.get("input_device", self.input_device)
        self.output_device = config_dict.get("output_device", self.output_device)
        self.sample_rate = int(config_dict.get("sample_rate", self.sample_rate))
        self.resampler_engine = config_dict.get("resampler_engine", self.resampler_engine)
        self.sample_format = config_dict.get("sample_format", self.sample_format)
        self.volume = int(config_dict.get("volume", self.volume))
        self.auto_route_windows_default = bool(config_dict.get("auto_route_default", self.auto_route_windows_default))
        if "multi_output_enabled" in config_dict:
            self.multi_output_enabled = bool(config_dict["multi_output_enabled"])
        if "output_devices" in config_dict:
            self.output_devices = list(config_dict["output_devices"])
        if "stream_host" in config_dict:
            self.stream_host = str(config_dict["stream_host"]).strip()
        if "stream_port" in config_dict:
            self.stream_port = int(config_dict["stream_port"])

    def _clean_dead_players(self):
        """檢查並清理已失效或已終止之 MPV 實例 (例如 Windows 端點被實體拔除)"""
        with self.lock:
            dead_keys = []
            for dev, p in list(self._player_map.items()):
                if p is None or getattr(p, 'handle', None) is None:
                    dead_keys.append(dev)
            for k in dead_keys:
                self._player_map.pop(k, None)
                print(f"[AudioBridge] 端點已中斷並自活躍清單移除: {k}")
            if dead_keys:
                self._players = list(self._player_map.values())

    def get_status(self):
        with self.lock:
            self._clean_dead_players()
            default_render = get_default_render_endpoint()
            if self.mode == "player":
                is_loop = False
                loop_reason = "本機直通模式 (Direct DSP)：零延遲、免選輸入輸出設備、零迴路風險"
            elif self.multi_output_enabled:
                target_outs = [d for d in self.output_devices if d]
                if not target_outs:
                    target_outs = [self.output_device] if self.output_device else ["auto"]
                is_loop, loop_reason = check_multiple_outputs_loop(
                    self.input_device, 
                    target_outs, 
                    default_render,
                    self.auto_route_windows_default
                )
            else:
                is_loop, loop_reason = is_feedback_loop(
                    self.input_device, 
                    self.output_device, 
                    "", 
                    default_render,
                    self.auto_route_windows_default
                )
            is_active_now = self.player_active if self.mode == "player" else self.is_active

            # 讀取輸入與輸出端點硬體音量資訊
            in_vol = get_endpoint_volume(self.input_device)
            out_vol = get_endpoint_volume(self.output_device)
            multi_vols = {}
            if self.output_devices:
                for d in self.output_devices:
                    if d:
                        v = get_endpoint_volume(d)
                        if v: multi_vols[d] = v

            active_host = getattr(self.loopback_streamer, 'host', self.stream_host) if self.loopback_streamer else self.stream_host
            active_port = getattr(self.loopback_streamer, 'port', self.stream_port) if self.loopback_streamer else self.stream_port

            return {
                "mode": self.mode,
                "active": is_active_now,
                "player_active": self.player_active,
                "system_active": self.is_active,
                "input_device": self.input_device,
                "output_device": self.output_device,
                "multi_output_enabled": self.multi_output_enabled,
                "output_devices": self.output_devices,
                "active_output_devices": list(self._player_map.keys()),
                "stream_host": active_host,
                "stream_port": active_port,
                "stream_url": self._source_url if self.is_active else None,
                "input_volume": in_vol,
                "output_volume": out_vol,
                "multi_volumes": multi_vols,
                "sample_rate": self.sample_rate,
                "resampler_engine": self.resampler_engine,
                "sample_format": self.sample_format,
                "volume": self.volume,
                "auto_route_default": self.auto_route_windows_default,
                "is_loop": is_loop,
                "loop_reason": loop_reason,
                "default_render": default_render,
                "last_error": self.last_error
            }

    def _build_filter_chain(self):
        """根據所選重採樣演算法建立 MPV 統一音訊濾鏡鏈"""
        rate = self.sample_rate
        engine = self.resampler_engine
        resampler_str = ""

        if engine == "soxr_vhq":
            osr_param = f":osr={rate}" if rate > 0 else ""
            resampler_str = f"aresample=resampler=soxr:precision=28:cutoff=0.99{osr_param}"
        elif engine == "soxr_hq":
            osr_param = f":osr={rate}" if rate > 0 else ""
            resampler_str = f"aresample=resampler=soxr:precision=20:cutoff=0.95{osr_param}"
        elif engine == "swr_poly":
            osr_param = f":osr={rate}" if rate > 0 else ""
            resampler_str = f"aresample=resampler=swr:filter_type=kaiser{osr_param}"
        elif engine == "sinc_native":
            if rate > 0:
                resampler_str = f"aresample={rate}:resampler=swr:filter_size=64"
        elif engine == "none":
            resampler_str = ""

        parts = []
        if resampler_str:
            parts.append(resampler_str)
        parts.append("alimiter=limit=0.98:attack=5:release=50:asc=1")
        return f"lavfi=[{','.join(parts)}]"

    def _create_mpv_player(self, use_direct_show, stream_res, filter_chain, mpv_fmt, out_dev, source_url):
        """建立並配置單一低延遲 MPV 輸出實例"""
        if use_direct_show:
            p = mpv.MPV(
                profile='low-latency',
                idle=True,
                keep_open=True,
                cache='no',
                demuxer_readahead_secs=0.0,
                audio_buffer=0.04, # 40ms 超低延遲輸出緩衝
                demuxer_lavf_o='audio_buffer_size=20,fflags=nobuffer',
                stream_buffer_size=4096,
                audio_pitch_correction=False,
                untimed=True,
                video=False
            )
        else:
            p = mpv.MPV(
                profile='low-latency',
                idle=True,
                keep_open=True,
                demuxer='rawaudio',
                demuxer_rawaudio_rate=stream_res['rate'],
                demuxer_rawaudio_channels=stream_res['channels'],
                demuxer_rawaudio_format=stream_res['format'],
                cache='no',
                demuxer_readahead_secs=0.0,
                audio_buffer=0.04,
                stream_buffer_size=4096,
                audio_pitch_correction=False,
                untimed=True,
                video=False
            )

        if self.sample_rate > 0:
            p.audio_samplerate = self.sample_rate
        if mpv_fmt and mpv_fmt != "float":
            try:
                p.audio_format = mpv_fmt
            except Exception as e:
                print(f"[AudioBridge Format Warning] audio_format={mpv_fmt} failed: {e}")

        if filter_chain:
            p.af = filter_chain

        p.audio_device = out_dev if (out_dev and out_dev != "auto") else "auto"
        p.volume = self.volume
        p.loadfile(source_url)
        return p

    def sync_output_devices(self, target_outputs=None, multi_enabled=None):
        """
        【差分式動態安全熱插拔 (Differential Hotplug)】
        在串接運行中動態增減並接輸出喇叭，或切換單一/多喇叭模式：
        1. 針對被移除/取消勾選的端點：僅終止該端點 MPV 實例，不影響其他喇叭。
        2. 針對新增加/新勾選的端點：執行防迴路檢測，並接入既有廣播串流，即時發聲。
        3. 未變更的端點：保持持續播放，零中斷、無爆音、無感無縫熱插拔！
        """
        with self.lock:
            if multi_enabled is not None:
                self.multi_output_enabled = bool(multi_enabled)
            if target_outputs is not None:
                if isinstance(target_outputs, list):
                    self.output_devices = [d for d in target_outputs if d]
                elif isinstance(target_outputs, str):
                    self.output_device = target_outputs

            # 若尚未在系統串接運行模式或未處於活躍狀態，僅更新設備清單狀態
            if self.mode != "system" or not self.is_active:
                return {"success": True, "status": self.get_status()}

            # 計算當前預期目標輸出端點清單
            if self.multi_output_enabled:
                desired_targets = [d for d in self.output_devices if d]
            else:
                desired_targets = [self.output_device if self.output_device else "auto"]

            # 防迴路安全檢測
            default_render = get_default_render_endpoint()
            if self.multi_output_enabled:
                if desired_targets:
                    is_loop, reason = check_multiple_outputs_loop(
                        self.input_device,
                        desired_targets,
                        default_render,
                        self.auto_route_windows_default
                    )
                    if is_loop:
                        print(f"[AudioBridge Hotplug Blocked] 迴路防護阻擋: {reason}")
                        return {"success": False, "error": reason, "status": self.get_status()}
            else:
                is_loop, reason = is_feedback_loop(
                    self.input_device,
                    self.output_device,
                    "",
                    default_render,
                    self.auto_route_windows_default
                )
                if is_loop:
                    print(f"[AudioBridge Hotplug Blocked] 迴路防護阻擋: {reason}")
                    return {"success": False, "error": reason, "status": self.get_status()}

            self._clean_dead_players()
            current_keys = set(self._player_map.keys())
            desired_set = set(desired_targets)

            to_remove = current_keys - desired_set
            to_add = desired_set - current_keys

            # 1. 安全熱移除 (Hot-Remove)
            for out_dev in to_remove:
                p = self._player_map.pop(out_dev, None)
                if p:
                    try:
                        p.terminate()
                        print(f"[AudioBridge Hotplug] 安全移除並停止喇叭: {out_dev}")
                    except Exception as e:
                        print(f"[AudioBridge Hotplug Error] 移除喇叭 {out_dev} 異常: {e}")

            # 2. 安全熱插入 (Hot-Add)
            if to_add and self._source_url:
                filter_chain = self._build_filter_chain()
                mpv_fmt = _map_mpv_audio_format(self.sample_format)
                for out_dev in to_add:
                    try:
                        p = self._create_mpv_player(
                            self._use_direct_show,
                            self._stream_res,
                            filter_chain,
                            mpv_fmt,
                            out_dev,
                            self._source_url
                        )
                        self._player_map[out_dev] = p
                        print(f"[AudioBridge Hotplug] 安全熱接入新喇叭發聲: {out_dev}")
                    except Exception as e:
                        print(f"[AudioBridge Hotplug Error] 加入喇叭 {out_dev} 異常: {e}")

            self._players = list(self._player_map.values())
            return {"success": True, "status": self.get_status()}

    def start(self, config_dict=None):
        """啟動音訊串接 (抽插式插上 / 本機濾鏡啟用 / 多喇叭並接輸出)"""
        with self.lock:
            if self.mode == "player":
                if config_dict:
                    self._update_params_from_dict(config_dict)
                self.player_active = True
                self._apply_to_player()
                return {"success": True, "active": True, "mode": "player", "status": self.get_status()}

            if not mpv:
                return {"success": False, "error": "mpv library not loaded"}

            # 若當前已處於活躍運行狀態且已有串流來源：
            if self.is_active and (self.loopback_streamer or self._use_direct_show or self._source_url):
                if config_dict:
                    new_in = config_dict.get("input_device", self.input_device)
                    new_out = config_dict.get("output_device", self.output_device)
                    new_rate = int(config_dict.get("sample_rate", self.sample_rate))
                    new_eng = config_dict.get("resampler_engine", self.resampler_engine)
                    new_fmt = config_dict.get("sample_format", self.sample_format)
                    new_auto = bool(config_dict.get("auto_route_default", self.auto_route_windows_default))
                    new_vol = int(config_dict.get("volume", self.volume))
                    new_multi = bool(config_dict.get("multi_output_enabled", self.multi_output_enabled))
                    new_outs = list(config_dict.get("output_devices", self.output_devices)) if "output_devices" in config_dict else self.output_devices

                    source_unchanged = (
                        new_in == self.input_device and
                        new_rate == self.sample_rate and
                        new_eng == self.resampler_engine and
                        new_fmt == self.sample_format and
                        new_auto == self.auto_route_windows_default
                    )

                    if source_unchanged:
                        if new_vol != self.volume:
                            self.set_volume(new_vol)
                        self.output_device = new_out
                        return self.sync_output_devices(new_outs if new_multi else [new_out], new_multi)
                else:
                    return self.sync_output_devices()

            if config_dict:
                self._update_params_from_dict(config_dict)

            # 1. 計算目標輸出設備清單
            target_outputs = []
            if self.multi_output_enabled and self.output_devices:
                target_outputs = [d for d in self.output_devices if d]
            if not target_outputs:
                target_outputs = [self.output_device if self.output_device else "auto"]

            # 2. 嚴格防巡迴安全驗證
            default_render = get_default_render_endpoint()
            if self.multi_output_enabled:
                is_loop, reason = check_multiple_outputs_loop(
                    self.input_device, 
                    target_outputs, 
                    default_render,
                    self.auto_route_windows_default
                )
            else:
                is_loop, reason = is_feedback_loop(
                    self.input_device, 
                    self.output_device, 
                    "", 
                    default_render,
                    self.auto_route_windows_default
                )
            if is_loop:
                self.last_error = reason
                print(f"[AudioBridge Blocked] {reason}")
                return {"success": False, "error": reason}

            # 3. 若先前已運行，先終止舊實例
            for p in self._players:
                try: p.terminate()
                except: pass
            self._players = []
            self._player_map = {}

            if self.loopback_streamer:
                try: self.loopback_streamer.stop()
                except: pass
                self.loopback_streamer = None

            # 4. 抽插式熱插拔：自動將 Windows 預設輸出切換至虛擬設備 (達成音效完全隔離，無回音反饋)
            dshow_auto_device = None
            if self.auto_route_windows_default:
                virt_endpoint_id = _find_available_virtual_render_endpoint()
                if virt_endpoint_id:
                    if not self.original_default_endpoint_id and default_render:
                        self.original_default_endpoint_id = default_render.get("id")
                    set_default_render_endpoint(virt_endpoint_id)
                    print(f"[AudioBridge] 抽插接管：已將 Windows 預設輸出導向虛擬端點 ({virt_endpoint_id})")

                    for c_dev in get_all_capture_endpoints():
                        if 'cable output' in c_dev.get('name', '').lower():
                            dshow_auto_device = c_dev.get('name')
                            break

            try:
                try: winmm.timeBeginPeriod(1)
                except: pass

                # 5. 判斷使用 DirectShow 還是 WASAPI Loopback 捕捉
                use_direct_show = False
                dshow_name = None

                if self.input_device.startswith("dshow:"):
                    use_direct_show = True
                    dshow_name = self.input_device.split(":", 1)[1]
                elif dshow_auto_device and (self.input_device == "loopback:default" or "cable" in self.input_device.lower()):
                    use_direct_show = True
                    dshow_name = dshow_auto_device

                stream_res = None
                source_url = None

                if use_direct_show:
                    source_url = f"av://dshow:audio={dshow_name}"
                    print(f"[AudioBridge] 啟動 DirectShow 原生直連來源: {source_url}")
                else:
                    target_ep = self.input_device.split(":", 1)[1] if self.input_device.startswith("loopback:") else 'default'
                    self.loopback_streamer = WasapiLoopbackStreamer(
                        endpoint_id=target_ep,
                        host=self.stream_host,
                        port=self.stream_port
                    )
                    stream_res = self.loopback_streamer.start()
                    if not stream_res.get("success"):
                        raise RuntimeError(f"WASAPI Loopback 側錄啟動失敗: {stream_res.get('error')}")
                    source_url = f"tcp://{self.loopback_streamer.host}:{self.loopback_streamer.port}"
                    print(f"[AudioBridge] 啟動 Event-Driven WASAPI Loopback 專屬隔離通道: {self.input_device} ({source_url})")

                self._use_direct_show = use_direct_show
                self._stream_res = stream_res
                self._source_url = source_url

                filter_chain = self._build_filter_chain()
                mpv_fmt = _map_mpv_audio_format(self.sample_format)

                # 6. 為清單中的每一個目標輸出端點建立一個獨立的 MPV 播放器實例 (實現多喇叭並接輸出)
                self._player_map = {}
                for out_dev in target_outputs:
                    p = self._create_mpv_player(use_direct_show, stream_res, filter_chain, mpv_fmt, out_dev, source_url)
                    self._player_map[out_dev] = p
                    print(f"[AudioBridge] 啟動輸出實例 -> 目標喇叭: {out_dev}")

                self._players = list(self._player_map.values())
                self.is_active = len(self._players) > 0
                self.last_error = None
                return {"success": True, "status": self.get_status()}

            except Exception as e:
                self.last_error = str(e)
                self.is_active = False
                print(f"[AudioBridge Launch Error] {e}")
                self.stop()
                return {"success": False, "error": str(e)}

    def stop(self):
        """停止音訊串接 (抽插式拔除 / 關閉還原 / 停用本機濾鏡)"""
        with self.lock:
            if self.player_active:
                self.player_active = False
                self._clear_player_filters()

            for p in self._players:
                try: p.terminate()
                except Exception as e:
                    print(f"[AudioBridge Terminate Error] {e}")
            self._players = []
            self._player_map = {}
            self._source_url = None
            self._stream_res = None
            self._use_direct_show = False

            if self.loopback_streamer:
                try: self.loopback_streamer.stop()
                except: pass
                self.loopback_streamer = None

            self.is_active = False

            try: winmm.timeEndPeriod(1)
            except: pass

            # 抽插式拔除核心：還原原始 Windows 實體預設播放設備！
            if self.original_default_endpoint_id:
                try:
                    set_default_render_endpoint(self.original_default_endpoint_id)
                    print(f"[AudioBridge] 抽插拔除：已成功還原 Windows 原始實體喇叭 ({self.original_default_endpoint_id})")
                except Exception as e:
                    print(f"[AudioBridge Restore Error] {e}")
                self.original_default_endpoint_id = None

            return {"success": True, "status": self.get_status()}

    def toggle(self, config_dict=None):
        """開關音訊橋接 (本機濾鏡切換 或 系統串接抽插)"""
        with self.lock:
            if self.mode == "player":
                if config_dict:
                    self._update_params_from_dict(config_dict)
                if self.player_active:
                    self.player_active = False
                    self._clear_player_filters()
                    return {"success": True, "active": False, "mode": "player", "status": self.get_status()}
                else:
                    self.player_active = True
                    self._apply_to_player()
                    return {"success": True, "active": True, "mode": "player", "status": self.get_status()}
            else:
                if self.is_active:
                    res = self.stop()
                    res["active"] = False
                    res["mode"] = "system"
                    return res
                else:
                    res = self.start(config_dict)
                    res["active"] = self.is_active
                    res["mode"] = "system"
                    return res

    def set_volume(self, vol):
        """即時調整串接音量 (熱套用至所有並接喇叭)"""
        with self.lock:
            self.volume = max(0, min(100, int(vol)))
            for p in self._players:
                try: p.volume = self.volume
                except: pass
            return {"success": True, "volume": self.volume}

    def set_resampler(self, engine, rate, fmt="float"):
        """即時更新重採樣設定 (重新套用至串接引擎或本機播放器)"""
        with self.lock:
            self.resampler_engine = engine
            self.sample_rate = int(rate)
            self.sample_format = fmt

            if self.mode == "player" and self.player_active:
                self._apply_to_player()
                print(f"[AudioBridge] 本機播放器即時熱套用重採樣: engine={self.resampler_engine}, rate={self.sample_rate}")
            elif self.mode == "system" and self.is_active:
                config_copy = {
                    "input_device": self.input_device,
                    "output_device": self.output_device,
                    "multi_output_enabled": self.multi_output_enabled,
                    "output_devices": self.output_devices,
                    "sample_rate": self.sample_rate,
                    "resampler_engine": self.resampler_engine,
                    "sample_format": self.sample_format,
                    "volume": self.volume,
                    "auto_route_default": False,
                    "stream_host": self.stream_host,
                    "stream_port": self.stream_port
                }
                threading.Thread(target=self.start, args=(config_copy,), daemon=True).start()
            return {"success": True, "status": self.get_status()}


# 單例全域實例
live_audio_bridge = LiveAudioBridge()

