# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for Voice Assistant Pro.
Uses --onedir mode (less antivirus false positives than --onefile).
"""
import os
import sys

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# Paths
APP_DIR = r'C:\Users\gleba\Desktop\Голосовой помощник\VoiceApp'
VENV_SITE = os.path.join(APP_DIR, 'venv', 'Lib', 'site-packages')

# Collect assets for faster_whisper (VAD model, etc.)
fw_datas = collect_data_files('faster_whisper')

a = Analysis(
    ['app.py'],
    pathex=[APP_DIR],
    binaries=[],
    datas=fw_datas + [
        (os.path.join(VENV_SITE, 'faster_whisper', 'assets', 'silero_vad_v6.onnx'), 'faster_whisper/assets'),
        # CustomTkinter needs its theme/assets
        (os.path.join(VENV_SITE, 'customtkinter'), 'customtkinter'),
        # Include clean config
        ('config.json', '.'),
        # Include icon
        ('icon.png', '.'),
        ('icon.ico', '.'),
    ],
    hiddenimports=[
        # Core
        'customtkinter',
        'sounddevice',
        'scipy',
        'scipy.io',
        'scipy.io.wavfile',
        'numpy',
        'keyboard',
        'pyperclip',
        'pystray',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        # AI / Whisper
        'faster_whisper',
        'ctranslate2',
        'onnxruntime',
        'huggingface_hub',
        'tokenizers',
        'openai',
        'httpx',
        'httpcore',
        'anyio',
        'certifi',
        'h11',
        'sniffio',
        'pydantic',
        'pydantic_core',
        'annotated_types',
        'jiter',
        'distro',
        # Windows
        'pywin32',
        'win32api',
        'win32con',
        'winreg',
        'ctypes',
        # Sound
        '_sounddevice_data',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'tkinter.test',
        'unittest',
        'pytest',
        'sphinx',
        'docutils',
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
    name='VoiceAssistant',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # Disable UPX — main cause of antivirus false positives
    console=False,  # No console window (windowed mode)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico',
    version_info=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,  # No UPX compression — avoid AV triggers
    upx_exclude=[],
    name='VoiceAssistant',
)