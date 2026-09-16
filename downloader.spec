# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Bilibili 视频下载器。

打包命令：
    py -m PyInstaller downloader.spec

产物：
    dist/Bilibili视频下载器/Bilibili视频下载器.exe  (onedir 模式)

说明：
- onedir 模式：启动更快，ffmpeg.exe 可在 dist 目录下替换。
- 用户文件（settings.json、download/、history.json）写入 exe 同级目录。
- 内置资源（icon.ico、download.png、folder.png、ffmpeg.exe）打包进应用目录。
"""

import sys
from pathlib import Path

block_cipher = None
project_root = Path(SPECPATH).resolve()

datas = []
binaries = []

# 内置只读资源（icon.png 必须包含：._app_icon_path 优先用 icon.png 作为侧边栏看板娘）
for resource in ("icon.ico", "icon.png", "download.png", "folder.png"):
    res_path = project_root / resource
    if res_path.exists():
        datas.append((str(res_path), "."))

# ffmpeg.exe 作为二进制资源内置
ffmpeg_path = project_root / "ffmpeg.exe"
if ffmpeg_path.exists():
    binaries.append((str(ffmpeg_path), "."))

# 音效资源（缺失时程序会在只读临时目录里生成，打包进去更干净）
sounds_dir = project_root / "sounds"
if sounds_dir.is_dir():
    datas.append((str(sounds_dir), "sounds"))

a = Analysis(
    ["gui_download_qt.py"],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "yt_dlp",
        "yt_dlp.extractor",
        "yt_dlp.postprocessor",
        "browser_cookie3",
        "browser_cookie3.chrome",
        "browser_cookie3.firefox",
        "qrcode",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy",
        "scipy",
        "pandas",
        "PySide6",
        "PyQt6",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Bilibili视频下载器",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_root / "icon.ico") if (project_root / "icon.ico").exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Bilibili视频下载器",
)
