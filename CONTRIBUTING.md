# 参与贡献

欢迎提 Issue 和 Pull Request。这是个只用 Python 标准库的小工具，改动前请先确认**不引入运行时第三方依赖**——这是项目最核心的约束。

## 本地开发

```bash
git clone https://github.com/<your-username>/todo-dock.git
cd todo-dock
python src/todo_dock.py --version       # 确认环境
python src/todo_dock.py --nogold        # 关掉联网，方便离线调试
```

建议用独立的临时数据目录，避免改到自己的真实待办：

```bash
python src/todo_dock.py --data-dir ./_devdata --nogold
python src/todo.py status
```

Windows 下也可设置环境变量 `TODO_WIDGET_HOME` 达到同样效果。

## 提交前检查

1. `python -m py_compile src/todo_dock.py src/todo.py`
2. 界面改动请实际截图核对折叠态 / 展开态 / 提醒弹窗三种状态，尤其注意胶囊第二行高度只有 21px，字号过大会裁掉字符。
3. 涉及 `data.json` 的改动，务必验证：文件读取失败时**不能**回退成空数据后写回磁盘。
4. 新字段必须可选，旧数据缺字段也要能正常显示。

## 提交信息

使用 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/v1.0.0/)：

```
feat: 支持近期待办不绑定日期
fix: 修正长标题挤掉右侧日期标签
docs: 补充构建说明
build: 升级 PyInstaller 版本
```

## 报告问题

请附上：操作系统与版本、Python 版本（或 exe 版本）、复现步骤、`dock.log` 末尾若干行。注意 `data.json` 里是你的个人待办，**贴之前请先脱敏**。
