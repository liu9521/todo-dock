# 构建 Windows exe

## 环境

- Windows 10/11 x64
- Python 3.9+（推荐 3.13），需带 `tkinter`
- `pip install -r requirements-build.txt`（只装 PyInstaller，程序运行不需要它）

## 一键构建

```powershell
pip install -r requirements-build.txt
python build_exe.py
```

产物在 `dist/`：

| 文件 | 说明 |
| --- | --- |
| `TodoDock.exe` | 单文件、无控制台窗口，约 10–20 MiB |
| `TodoDock-v1.0.0-win64.zip` | 发布包：exe + 使用说明 + LICENSE |
| `SHA256SUMS.txt` | 上面两者的 SHA256 |

`build/`、`dist/`、`*.spec` 都已在 `.gitignore` 中，不会入库。二进制文件只通过 GitHub Release 分发。

## 构建脚本做了什么

1. 生成 PyInstaller 版本资源（`build/version_info.txt`）：公司、产品名、文件版本、版权信息，写入 exe 属性。
2. 调用：

   ```text
   python -m PyInstaller --noconfirm --clean --onefile --windowed \
     --name TodoDock --icon assets/tododock.ico \
     --version-file build/version_info.txt \
     --distpath dist --workpath build --specpath build \
     src/todo_dock.py
   ```

3. 校验 exe 大小，打包 zip，计算 SHA256。

`--onefile` 把 Python 解释器、Tcl/Tk 运行时与标准库打进单个 exe；`--windowed` 去掉控制台窗口（启动异常时会改为弹窗 + 写 `dock.log`）。

## 手动构建（不用脚本）

```powershell
python -m PyInstaller --onefile --windowed --name TodoDock `
  --icon assets/tododock.ico src/todo_dock.py
```

产物在 `dist/TodoDock.exe`。这种方式没有版本资源和发布包，仅适合本地调试。

## 验证

```powershell
# 语法
python -m py_compile src/todo_dock.py src/todo.py

# 校验和
Get-FileHash .\dist\TodoDock-v1.0.0-win64.zip -Algorithm SHA256

# 便携模式实测（不碰真实数据）
New-Item -ItemType Directory -Force .\_smoke | Out-Null
.\dist\TodoDock.exe --data-dir .\_smoke --nogold
Start-Sleep 3
Get-Process TodoDock
Get-Content .\_smoke\dock.log -Tail 5
Stop-Process -Name TodoDock
```

## 常见问题

**exe 双击没反应 / 一闪而过**
先看 `%USERPROFILE%\.workbuddy\todo-widget\dock.log`。打包版对未捕获异常会弹窗说明。常见原因是已有实例占住 `.running` 锁——删掉该目录下的 `.running` 再试。

**构建时报 `PyInstaller does not include a pre-compiled bootloader for your platform`**
部分 Windows 安全软件会把无控制台的 bootloader `runw.exe` 持续误删（其它三个 bootloader 通常还在），导致 `--windowed` 构建失败，而且补回去也会被再次删除。

`build_exe.py` 已自动处理：检测到 `runw.exe` 缺失时，改用 console bootloader (`run.exe`) 构建，随后把成品 PE 头的 `Subsystem` 从 CUI(3) 原地改成 GUI(2)——只改 2 个字节，**不会碰 onefile 附加在 PE 尾部的 PKG 数据**。产物同样双击不弹黑框。构建日志会打印：

```text
[注意] 无控制台 bootloader (runw.exe) 缺失，改用 console bootloader + PE Subsystem 修正。
PE Subsystem: 3 -> 2 (GUI，无控制台窗口)
```

可用下面命令确认子系统：

```powershell
python -c "import struct;f=open(r'dist\TodoDock.exe','rb');f.seek(0x3c);o=struct.unpack('<I',f.read(4))[0];f.seek(o+24+68);print('subsystem =',struct.unpack('<H',f.read(2))[0],'(2=GUI)')"
```

**杀毒软件报毒**
PyInstaller 单文件 exe 会自解压到临时目录，容易被启发式误报。可改用 `--onedir` 目录版，或自行签名、把源码提交给厂商白名单。

**体积太大**
`--onedir` 会小很多但变成多文件；也可排除不需要的模块（`--exclude-module`）。本项目没有第三方运行时依赖，体积主要来自 Python + Tcl/Tk。

**想改图标 / 版本**
图标在 `assets/tododock.ico`（多尺寸 ICO）；版本字符串在 `build_exe.py` 的 `VERSION_INFO` 与 `VERSION` 常量，同时记得同步 `pyproject.toml`、`src/todo_dock.py` 的 `VERSION`、`CHANGELOG.md`。

## CI

`.github/workflows/build.yml` 在 push / PR 时用 `windows-latest` 自动构建并上传 artifact；推送 `v*` 标签时自动创建 Release 并附上 zip 与校验和。
