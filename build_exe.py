#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键构建 Windows 单文件 exe 与发布压缩包。

    pip install -r requirements-build.txt
    python build_exe.py

产物（均在 dist/）：
    TodoDock.exe                     单文件、无控制台窗口
    TodoDock-v1.0.0-win64.zip        发布压缩包
    SHA256SUMS.txt                   校验和

兼容性处理：某些 Windows 安全软件会持续删除 PyInstaller 的无控制台
bootloader（runw.exe），导致 --windowed 构建直接失败。此时自动改用
console bootloader 构建，再把成品 PE 头的 Subsystem 从 CUI 改成 GUI，
效果与 --windowed 一致（详见 docs/BUILD.md）。
"""

import hashlib
import os
import struct
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
VERSION = "1.0.0"
EXE_NAME = "TodoDock"
DIST = os.path.join(ROOT, "dist")
BUILD = os.path.join(ROOT, "build")
ICON = os.path.join(ROOT, "assets", "tododock.ico")
ENTRY = os.path.join(ROOT, "src", "todo_dock.py")
ZIP_NAME = "TodoDock-v%s-win64.zip" % VERSION
ZIP_PATH = os.path.join(DIST, ZIP_NAME)

VERSION_INFO = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=(1, 0, 0, 0),
    prodvers=(1, 0, 0, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('080404B0', [
        StringStruct('CompanyName', 'TodoDock contributors'),
        StringStruct('FileDescription', 'TodoDock - desktop floating todo capsule'),
        StringStruct('FileVersion', '1.0.0'),
        StringStruct('InternalName', 'TodoDock'),
        StringStruct('LegalCopyright', 'Copyright (c) 2026 TodoDock contributors. MIT License.'),
        StringStruct('OriginalFilename', 'TodoDock.exe'),
        StringStruct('ProductName', 'TodoDock'),
        StringStruct('ProductVersion', '1.0.0')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""

EXE_README = """TodoDock v1.0.0 - Windows 免安装版
====================================

【启动】双击 TodoDock.exe，屏幕右下角出现两行小胶囊即成功。
        点胶囊展开面板，点面板外自动收回。

【数据】首次运行自动在 %USERPROFILE%\\.workbuddy\\todo-widget\\ 下创建 data.json。
        待办、窗口位置、提醒记录都在这里；程序不会覆盖已有待办。

【自启】Win + R 输入 shell:startup，把 TodoDock.exe 的快捷方式放进去。

【便携】在 exe 同目录放一个空的 portable.txt，数据会改写到 exe 同目录的 data\\ 下，
        方便放进 U 盘。也可用命令行 TodoDock.exe --data-dir D:\\mydata 指定。

【离线】TodoDock.exe --nogold 完全关闭金价联网，其余功能照常。

【金价】数据来自京东金融平台「浙商银行积存金」公开报价接口，属渠道实时报价，
        不是银行柜面成交价，仅供趋势参考，实际交易以银行 App 为准。

【安全】未签名的 exe 可能触发 Windows SmartScreen 的「未知发布者」提示，
        选择「更多信息 → 仍要运行」即可；也可自行用源码构建（见 docs/BUILD.md）。

【排错】日志在 %USERPROFILE%\\.workbuddy\\todo-widget\\dock.log。

项目主页、源码与许可证见同目录 LICENSE 及 使用说明.html。
"""


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def windowed_bootloader_available():
    """PyInstaller 的无控制台 bootloader 是否还在（可能被安全软件删除）。"""
    if sys.platform != "win32":
        return True
    try:
        import PyInstaller
    except ImportError:
        return False
    bd = os.path.join(os.path.dirname(PyInstaller.__file__),
                      "bootloader", "Windows-64bit-intel")
    return os.path.isfile(os.path.join(bd, "runw.exe"))


def patch_subsystem_gui(path):
    """把 PE 头 Subsystem 从 CUI(3) 改为 GUI(2)，只改 2 个字节。

    onefile exe 的 Python 运行时是附加在 PE 尾部的 PKG 数据，绝不能用
    pefile.write() 之类重写整个 PE（会丢掉 overlay），所以这里直接定位
    Optional Header + 68 的 Subsystem 字段原地打补丁。
    """
    with open(path, "r+b") as f:
        f.seek(0x3C)
        pe_off = struct.unpack("<I", f.read(4))[0]
        f.seek(pe_off)
        if f.read(4) != b"PE\0\0":
            raise ValueError("不是有效的 PE 文件：%s" % path)
        opt = pe_off + 4 + 20              # COFF File Header 固定 20 字节
        f.seek(opt)
        magic = struct.unpack("<H", f.read(2))[0]
        if magic not in (0x10B, 0x20B):    # PE32 / PE32+
            raise ValueError("未知的 Optional Header magic: 0x%x" % magic)
        # PE32 与 PE32+ 的 Subsystem 都在 Optional Header 偏移 68
        f.seek(opt + 68)
        old = struct.unpack("<H", f.read(2))[0]
        f.seek(opt + 68)
        f.write(struct.pack("<H", 2))      # IMAGE_SUBSYSTEM_WINDOWS_GUI
    return old


def main():
    if not os.path.isfile(ENTRY):
        sys.exit("找不到入口文件：%s" % ENTRY)
    if not os.path.isfile(ICON):
        sys.exit("找不到图标：%s" % ICON)

    os.makedirs(DIST, exist_ok=True)
    os.makedirs(BUILD, exist_ok=True)
    ver_file = os.path.join(BUILD, "version_info.txt")
    with open(ver_file, "w", encoding="utf-8") as f:
        f.write(VERSION_INFO)

    windowed = windowed_bootloader_available()
    if not windowed:
        print("[注意] 无控制台 bootloader (runw.exe) 缺失，"
              "改用 console bootloader + PE Subsystem 修正。")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--windowed" if windowed else "--console",
        "--name", EXE_NAME,
        "--icon", ICON,
        "--version-file", ver_file,
        "--distpath", DIST,
        "--workpath", BUILD,
        "--specpath", BUILD,
        "--log-level", "WARN",
        ENTRY,
    ]
    print("构建：", " ".join(cmd[2:]))
    subprocess.run(cmd, cwd=ROOT, check=True)

    exe = os.path.join(DIST, EXE_NAME + ".exe")
    if not os.path.isfile(exe):
        sys.exit("构建结束但没有找到 %s" % exe)
    size = os.path.getsize(exe)
    if size < 3 * 1024 * 1024:
        sys.exit("exe 体积异常（%d 字节），可能漏打了运行时" % size)

    if not windowed:
        old = patch_subsystem_gui(exe)
        print("PE Subsystem: %d -> 2 (GUI，无控制台窗口)" % old)
    print("exe: %s (%.2f MiB)" % (exe, size / 1024 / 1024))

    # 发布压缩包
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.write(exe, EXE_NAME + ".exe")
        z.writestr("使用说明.txt", EXE_README)
        html = os.path.join(ROOT, "assets", "使用说明.html")
        if os.path.isfile(html):
            z.write(html, "使用说明.html")
        z.write(os.path.join(ROOT, "LICENSE"), "LICENSE")
    print("zip: %s (%.2f MiB)" %
          (ZIP_PATH, os.path.getsize(ZIP_PATH) / 1024 / 1024))

    with open(os.path.join(DIST, "SHA256SUMS.txt"), "w", encoding="utf-8") as f:
        for p in (ZIP_PATH, exe):
            f.write("%s  %s\n" % (sha256(p), os.path.basename(p)))
    print("SHA256 已写入 dist/SHA256SUMS.txt")


if __name__ == "__main__":
    main()
