# PyInstaller 打包配置：`uv run pyinstaller package.spec --noconfirm`
import os

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None
ROOT = os.path.abspath(SPECPATH)

datas = [
    # 内置 skills 的 SKILL.md 与 references，运行时由 SkillManager 按路径读取
    (os.path.join(ROOT, "src", "drpaper", "skills", "builtin"), "drpaper/skills/builtin"),
    # 内置排版样式档案 YAML，运行时由 load_profile 按路径读取
    (os.path.join(ROOT, "src", "drpaper", "export", "profiles"), "drpaper/export/profiles"),
    # 内置检索源配置，运行时由 load_sources_config 按路径读取
    (os.path.join(ROOT, "src", "drpaper", "literature", "sources.yaml"), "drpaper/literature"),
    # 内置评估配置与基线，运行时由 eval.config 按路径读取
    (os.path.join(ROOT, "src", "drpaper", "eval", "eval.yaml"), "drpaper/eval"),
    (os.path.join(ROOT, "src", "drpaper", "eval", "baseline.yaml"), "drpaper/eval"),
    # python-docx 的默认模板 docx、openai 的接口描述等数据文件
    *collect_data_files("docx"),
    *collect_data_files("openai"),
]

hiddenimports = []

a = Analysis(
    [os.path.join(ROOT, "src", "drpaper", "__main__.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="drpaper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # CLI REPL，需要保留控制台
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="drpaper",
)
