# -*- mode: python ; coding: utf-8 -*-

import sys


a = Analysis(
    ["detention_lookup_gui.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=["pytz", "uuid"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="NYCDetentionLookup",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    collection = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="NYCDetentionLookup",
    )
    app = BUNDLE(
        collection,
        name="NYCDetentionLookup.app",
        icon=None,
        bundle_identifier="org.local.nycdetentionlookup",
        info_plist={
            "CFBundleDisplayName": "NYC Detention Lookup",
            "CFBundleShortVersionString": "1.1",
            "CFBundleVersion": "1.1.0",
            "NSHighResolutionCapable": True,
        },
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="NYCDetentionLookup",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        version="version_info.txt" if sys.platform == "win32" else None,
    )
