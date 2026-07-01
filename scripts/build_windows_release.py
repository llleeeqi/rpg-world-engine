from __future__ import annotations

import argparse
import json
import shutil
import urllib.request
import zipfile
from pathlib import Path


PYTHON_VERSION = "3.12.10"
PYTHON_ZIP = f"python-{PYTHON_VERSION}-embed-amd64.zip"
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/{PYTHON_ZIP}"

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
BUILD = DIST / "windows-build"


RUN_BAT = r"""@echo off
setlocal
set "ROOT=%~dp0"
set "RPG_WORLD_HOST=127.0.0.1"
if not defined RPG_WORLD_PORT set "RPG_WORLD_PORT=54925"
set "RPG_WORLD_DATA=%ROOT%data"
set "RPG_WORLD_LLM_CONFIG=%ROOT%config.json"

if exist "%ROOT%api_key.txt" (
  for /f "usebackq delims=" %%A in ("%ROOT%api_key.txt") do (
    if not defined DEEPSEEK_API_KEY set "DEEPSEEK_API_KEY=%%A"
  )
)

if not exist "%ROOT%data" mkdir "%ROOT%data"

echo RPG World Engine is starting...
echo Open: http://127.0.0.1:%RPG_WORLD_PORT%
start "" "http://127.0.0.1:%RPG_WORLD_PORT%"
cd /d "%ROOT%app"
"%ROOT%python\python.exe" server.py
pause
"""


WINDOWS_README = """RPG World Engine - Windows x64

我是 llleeeqi。这个包就是给 Windows 解压即跑用的。

怎么用：

1. 解压整个 zip。
2. 双击 run.bat。
3. 浏览器会打开 http://127.0.0.1:54925
4. 存档会放在当前文件夹的 data\\ 下面。

如果端口 54925 被占了：

1. 右键 run.bat，选编辑。
2. 在文件前面加一行，例如：
   set "RPG_WORLD_PORT=54924"
3. 保存后重新双击 run.bat。

如果要接 DeepSeek / OpenAI-compatible 模型：

1. 打开 api_key.txt。
2. 把 key 粘进去，文件里只放 key 本身。
3. 如果要改模型、base_url，就编辑 config.json。

不填 api_key.txt 也能打开网页，本地流程能跑；真正让 AI 推演，需要可用的 API key。

这个包自带：

- Python embeddable runtime
- 前端页面
- RPG World Engine 后端
- 默认 config.json
- 相对路径 data\\ 存档目录

不要把 data\\、config.json、api_key.txt 发给别人，里面可能有你的存档和 key。
"""


def copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))


def download_python(cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / PYTHON_ZIP
    if not target.exists():
        print(f"Downloading {PYTHON_URL}")
        urllib.request.urlretrieve(PYTHON_URL, target)
    return target


def write_config(bundle: Path) -> None:
    config = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    (bundle / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def patch_python_path(python_dir: Path) -> None:
    pth_files = list(python_dir.glob("python*._pth"))
    if not pth_files:
        raise RuntimeError("Python embeddable runtime did not include python*._pth")
    pth = pth_files[0]
    lines = pth.read_text(encoding="utf-8").splitlines()
    if "..\\app" not in lines:
        lines.insert(1, "..\\app")
    pth.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build(version: str) -> Path:
    bundle_name = f"rpg-world-engine-{version}-windows-x64"
    bundle = BUILD / bundle_name
    if bundle.exists():
        shutil.rmtree(bundle)
    bundle.mkdir(parents=True)

    python_zip = download_python(DIST / "cache")
    python_dir = bundle / "python"
    python_dir.mkdir()
    with zipfile.ZipFile(python_zip) as zf:
        zf.extractall(python_dir)
    patch_python_path(python_dir)

    app = bundle / "app"
    app.mkdir()
    for file_name in ["server.py", "config.example.json", "README.md", "DESIGN.md", "MVP_TASKS.md"]:
        shutil.copy2(ROOT / file_name, app / file_name)
    copy_tree(ROOT / "rpg_world_engine", app / "rpg_world_engine")
    copy_tree(ROOT / "web", app / "web")

    (bundle / "data").mkdir()
    (bundle / "api_key.txt").write_text("", encoding="utf-8")
    write_config(bundle)
    (bundle / "run.bat").write_text(RUN_BAT, encoding="utf-8", newline="\r\n")
    (bundle / "README-WINDOWS.txt").write_text(WINDOWS_README, encoding="utf-8", newline="\r\n")

    out = DIST / f"{bundle_name}.zip"
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(bundle.rglob("*")):
            zf.write(path, path.relative_to(BUILD))
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="0.1.0")
    args = parser.parse_args()
    out = build(args.version)
    print(out)


if __name__ == "__main__":
    main()
