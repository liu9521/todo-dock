# TodoDock v1.0.0

桌面右下角常驻的待办胶囊。首个公开发布版本。

## 下载

| 文件 | 说明 |
| --- | --- |
| `TodoDock-v1.0.0-win64.zip` | Windows x64 单文件 exe + 使用说明，**免安装、无需 Python**，解压双击即用 |
| `Source code (zip)` | 完整源码，需 Python 3.9+（含 tkinter） |

其他系统请用源码运行 `python src/todo_dock.py`。

## 这版能做什么

- 右下角两行小胶囊：第一行待办概况，第二行**浙商银行积存金实时价**（每 5 秒刷新、涨红跌绿、点击立即刷新）。
- 点开展开面板：最近待办 + 月历 + 选中日的待办与输入框；点日历某天即可给那天建待办。
- **近期待办**：不绑定日期、不提醒，随手记也可；与日期待办分开排序。
- 提醒：当天 / 提前 1 天两个开关，可设时刻，到点弹窗 + 提示音，可一键标记完成。
- 长标题悬停看全文，右侧日期标签始终完整；提醒弹窗长标题自动换行。
- 数据全在本机 `data.json`，运行时零第三方依赖，除金价接口外不联网。

## 安装与使用

1. 解压 zip，双击 `TodoDock.exe`。
2. 想开机自启：`Win + R` → `shell:startup` → 把 exe 的快捷方式放进去。
3. 想完全离线：用 `TodoDock.exe --nogold` 启动。
4. 想让数据跟着 U 盘走：在 exe 同目录放一个空的 `portable.txt`，或用 `--data-dir <路径>`。

详细图文说明见 zip 内的 `使用说明.html` 与仓库 README。

## 校验

压缩包同目录下的 `SHA256SUMS.txt` 提供校验值。PowerShell 校验：

```powershell
Get-FileHash .\TodoDock-v1.0.0-win64.zip -Algorithm SHA256
```

## 注意

- 金价取自京东金融平台「浙商银行积存金」公开 H5 报价接口，是渠道实时报价，**不是银行柜面成交价**，仅供趋势参考，实际交易以银行 App 为准。
- 首次运行会在 `%USERPROFILE%\.workbuddy\todo-widget\` 创建数据目录；程序不会覆盖已有待办。
- Windows SmartScreen 可能对未签名的 exe 提示「未知发布者」，点「更多信息 → 仍要运行」即可。
