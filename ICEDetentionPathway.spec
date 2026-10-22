# -*- mode: python ; coding: utf-8 -*-

import sys


a = Analysis(
    ["ice_detention_pathway_gui.py"],
    pathex=[],
    binaries=[],
    # The window icon at runtime; the same art becomes app.ico / app.icns.
    # The dataset URLs travel inside the bundle too, so a released app can
    # find them even when it has been moved away from its source tree.
    datas=[
        ("assets/ice_cube.png", "assets"),
        ("data-sources.json", "."),
    ],
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
        name="ICEDetentionPathway",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        argv_emulation=False,
        icon=None,
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
        name="ICEDetentionPathway",
    )
    app = BUNDLE(
        collection,
        name="ICEDetentionPathway.app",
        bundle_identifier="dev.remypicciano.icedetentionpathway",
        icon="assets/app.icns",
        info_plist={
            "CFBundleDisplayName": "ICE Detention Pathway",
            "CFBundleShortVersionString": "2.1.6",
            "CFBundleVersion": "2.1.6",
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
        name="ICEDetentionPathway",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        version="version_info.txt" if sys.platform == "win32" else None,
        icon="assets/app.ico" if sys.platform == "win32" else None,
    )
