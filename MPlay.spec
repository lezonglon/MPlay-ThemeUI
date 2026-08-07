import sys

CONDA_ENV = sys.prefix
SITE_PACKAGES = os.path.join(CONDA_ENV, 'Lib', 'site-packages')
CONDA_LIB_BIN = os.path.join(CONDA_ENV, 'Library', 'bin')
WEBVIEW_LIB   = os.path.join(SITE_PACKAGES, 'webview', 'lib')

# --- 收集 webview lib ---
webview_binaries = []
for root, dirs, files in os.walk(WEBVIEW_LIB):
    for f in files:
        src = os.path.join(root, f)
        rel = os.path.relpath(root, SITE_PACKAGES)
        webview_binaries.append((src, rel))

# --- Conda Library\bin 中 PyInstaller 找不到的關鍵 DLL ---
conda_missing_dlls = [
    'ffi.dll',
    'liblzma.dll',
    'LIBBZ2.dll',
    'libexpat.dll',
    'libcrypto-3-x64.dll',
    'libssl-3-x64.dll',
]
conda_binaries = []
for dll in conda_missing_dlls:
    src = os.path.join(CONDA_LIB_BIN, dll)
    if os.path.exists(src):
        conda_binaries.append((src, '.'))

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=webview_binaries + conda_binaries,
    datas=[
        ('ui', 'ui'),
        ('bin', 'bin'),
        ('database.py', '.'),
        ('config.json', '.'),
    ],
    hiddenimports=[
        'webview',
        'webview.platforms.winforms',
        'clr',
        'mpv',
        'sqlite3',
        '_sqlite3',
        'ctypes',
        'ctypes.wintypes',
        'threading',
        'json',
        'base64',
        'gc',
        'atexit',
        'msvcrt',
        'http',
        'http.server',
        'wsgiref',
        'wsgiref.simple_server',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'numpy',
        'PIL',
        'PyQt5',
        'PyQt6',
        'PySide2',
        'PySide6',
        'wx',
        'unittest',
        'pydoc',
        'doctest',
        'curses',
        'test',
    ],
    noarchive=False,
    optimize=2,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='MPlayThemeUI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[
        'libmpv-2.dll',
        'Microsoft.Web.WebView2.Core.dll',
        'WebView2Loader.dll',
        'python313.dll',
        'python3.dll',
        'ffi.dll',
        'libcrypto-3-x64.dll',
        'libssl-3-x64.dll',
    ],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='file_version_info.txt',
    icon=None,
)
