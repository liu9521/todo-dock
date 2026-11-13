#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桌面待办悬浮窗 —— 命令行入口（技能的执行脚本）

所有对「桌面待办悬浮窗」的增删改查一律通过本脚本完成，不要直接手改 data.json。

用法：
    python3 todo.py add "标题"          # 新增「近期待办」：不绑定日期、不提醒
    python3 todo.py add "标题" --bottom  # 新增一条（追加到最后）
    python3 todo.py add "标题" --due 2026-11-15 --time 09:00
                                        # 带日期新增（当天 + 提前一天提醒，默认 both）
                                        # --remind day|eve|both|none 可改提醒组合
    python3 todo.py setdue <匹配词> 2026-11-15 [--time 09:00]
    python3 todo.py setdue <匹配词> none  # 去掉日期，改回不设日期的近期待办
    python3 todo.py list                # 看进行中的待办（含日期 / 近期标记）
    python3 todo.py list --all          # 连已完成的一起看
    python3 todo.py list --full         # 长标题不截断，显示全文
    python3 todo.py gold                # 立刻查一次浙商银行积存金实时价
    python3 todo.py done <匹配词>       # 完成一条（归入已完成）
    python3 todo.py remove <匹配词>     # 删除一条
    python3 todo.py progress <匹配词> <0-100>
    python3 todo.py rename <匹配词> "新标题"
    python3 todo.py status              # 挂件状态、数据位置
    python3 todo.py install             # 把挂件装到固定位置（不动数据）
    python3 todo.py start               # 启动右下角待办胶囊（没装会先装）
    python3 todo.py start --legacy      # 改用旧版进度条悬浮窗
    python3 todo.py stop                # 关掉悬浮窗
    python3 todo.py autostart on|off|status   # 开机自启（提醒需要常驻）

<匹配词> 支持三种写法：待办编号（list 里最前面的数字）、id 前缀、标题里的一段文字（须唯一）。

挂件位置：默认 ~/.workbuddy/todo-widget/（可用环境变量 TODO_WIDGET_HOME 覆盖）。
挂件带热加载：本脚本改完 data.json，悬浮窗约 0.3 秒内自动刷新，无需重启。
默认挂件 = todo_dock.py（右下角胶囊 + 小日历 + 当天/提前一天提醒）；
折叠态胶囊第二行会定时显示浙商银行积存金实时价，缓存在 gold_cache.json，
与 data.json 分开存放，不会触发挂件热加载。
旧版 todo_float.py（进度条版）仍保留，用 start --legacy 启动，两者共用 data.json。
"""

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SRC_DIR)                 # 仓库 / 技能包根目录

# 两种布局都支持：本仓库把 CLI 与 GUI 同放 src/；
# WorkBuddy 技能包把 CLI 放 scripts/、GUI 放 assets/。
ASSETS_DIR = (SRC_DIR if os.path.exists(os.path.join(SRC_DIR, "todo_dock.py"))
              else os.path.join(SKILL_DIR, "assets"))
DOCS_DIR = os.path.join(SKILL_DIR, "docs")

WIDGET_HOME = os.environ.get("TODO_WIDGET_HOME") or os.path.join(
    os.path.expanduser("~"), ".workbuddy", "todo-widget")
DATA_FILE = os.path.join(WIDGET_HOME, "data.json")
RUNNING_FILE = os.path.join(WIDGET_HOME, ".running")
APP_BUNDLE = os.path.join(WIDGET_HOME, "待办悬浮窗.app")
WIDGET_SCRIPT = os.path.join(WIDGET_HOME, "todo_dock.py")     # 默认：日历胶囊
LEGACY_SCRIPT = os.path.join(WIDGET_HOME, "todo_float.py")    # 旧版：进度条悬浮窗
GOLD_CACHE = os.path.join(WIDGET_HOME, "gold_cache.json")
GOLD_URL = ("https://api.jdjygold.com/gw2/generic/jrm/h5/m/"
            "stdLatestPrice?productSku=1961543816")


# ---------------------------------------------------------------- 数据读写

def default_data():
    return {"active": [], "done": [],
            "ui": {"x": None, "y": None, "topmost": True, "alpha": 0.9,
                   "collapsed": False, "done_open": True, "follow": True}}


def load_data(need=False):
    if not os.path.exists(DATA_FILE):
        if need:
            return default_data()
        return None
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("data.json 读不了（%r）。先修好文件再操作，避免把数据写坏。" % e)
        sys.exit(1)


def save_data(data):
    tmp = DATA_FILE + ".tmp"
    os.makedirs(WIDGET_HOME, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_FILE)


def backup_data():
    d = load_data()
    if d is None:
        return
    try:
        with open(DATA_FILE + ".backup-cli", "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def new_id():
    return "%x%04x" % (int(time.time() * 1000),
                       int.from_bytes(os.urandom(2), "big"))


# ---------------------------------------------------------------- 匹配待办

def _match_one(data, arg, *, pool="both"):
    """按 编号 / D编号 / id前缀 / 标题片段 找一条待办。

    编号与 list 输出一致：进行中是 1、2、3…，已完成是 D1、D2…。
    找到返回 (任务, 列表名)；找不到或歧义就退出。
    """
    arg = arg.strip()
    lists = ["active"] if pool == "active" else ["active", "done"]

    # 1) D 编号：已完成列表里的第几条
    if len(arg) >= 2 and arg[0] in "Dd" and arg[1:].isdigit():
        idx = int(arg[1:])
        done = data.get("done", [])
        if 1 <= idx <= len(done):
            return done[idx - 1], "done"
        print("D%d 超出范围（已完成一共 %d 条）。" % (idx, len(done)))
        sys.exit(2)

    # 2) 普通编号：进行中列表里的第几条
    if arg.isdigit():
        idx = int(arg)
        act = data.get("active", [])
        if 1 <= idx <= len(act):
            return act[idx - 1], "active"
        print("编号 %d 超出范围（进行中一共 %d 条）。" % (idx, len(act)))
        sys.exit(2)

    # 2) id 前缀
    hits = [(lst, t) for lst in lists for t in data.get(lst, [])
            if t.get("id", "").startswith(arg)]

    # 3) 标题片段（不区分大小写）
    if not hits:
        low = arg.lower()
        hits = [(lst, t) for lst in lists for t in data.get(lst, [])
                if low in t.get("title", "").lower()]

    if not hits:
        scope = "（只在进行中的待办里找）" if pool == "active" else ""
        print("没找到能对上「%s」的待办%s。运行 list 看看现在的清单。" % (arg, scope))
        sys.exit(2)
    if len(hits) > 1:
        print("「%s」对到了 %d 条，说不清是哪条。用编号或更长的标题片段再试：" % (arg, len(hits)))
        for lst, t in hits:
            print("  [%s] %s  %s" % (lst, t["id"][:8], _clip(t["title"])))
        sys.exit(2)
    return hits[0][1], hits[0][0]


def _clip(s, n=28):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:n] + "…"


# ---------------------------------------------------------------- 输出

def print_list(data, show_all=False, full=False):
    if data is None:
        print("还没有数据文件（挂件没装/没用过）。运行 install 再 start 就有了。")
        return

    def fmt(s, n):
        return " ".join(str(s).split()) if full else _clip(s, n)

    act, done = data.get("active", []), data.get("done", [])
    if not act:
        print("进行中：空")
    else:
        print("进行中（%d 条）：" % len(act))
        for i, t in enumerate(act, 1):
            due = t.get("due") or "近期(不设日期)"
            when = (" " + str(t["time"])) if t.get("due") and t.get("time") else ""
            print("  %2d. [%3d%%] %-38s  %s%s  ·%s" %
                  (i, t.get("progress", 0), fmt(t["title"], 38),
                   due, when, t["id"][:10]))
    if show_all:
        if not done:
            print("已完成：空")
        else:
            print("已完成（%d 条，最近在前）：" % len(done))
            for i, t in enumerate(done, 1):
                print("  D%d. %s  ·%s" % (i, fmt(t["title"], 40), t["id"][:10]))
    else:
        print("（已完成 %d 条，--all 可展开）" % len(done))


# ---------------------------------------------------------------- 安装/启停

def _same_file(a, b):
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
        with open(a, "rb") as fa, open(b, "rb") as fb:
            while True:
                x, y = fa.read(65536), fb.read(65536)
                if x != y:
                    return False
                if not x:
                    return True
    except OSError:
        return False


def _ensure_file(path, content, executable=False):
    """文件不存在或内容不一致就写入，返回是否动过。"""
    try:
        cur = None
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                cur = f.read()
        if cur == content:
            if executable:
                os.chmod(path, 0o755)
            return False
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        if executable:
            os.chmod(path, 0o755)
        return True
    except Exception as e:
        print("写 %s 失败：%r" % (os.path.basename(path), e))
        return False


# macOS 启动器不作为目录放进技能包（平台限制：包内最多两级目录），
# 装的时候现场生成，效果和打包一个 .app 完全一样。
APP_INFO_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
\t<key>CFBundleName</key>
\t<string>待办悬浮窗</string>
\t<key>CFBundleDisplayName</key>
\t<string>待办悬浮窗</string>
\t<key>CFBundleIdentifier</key>
\t<string>local.todo.floatwidget</string>
\t<key>CFBundleExecutable</key>
\t<string>launch</string>
\t<key>CFBundlePackageType</key>
\t<string>APPL</string>
\t<key>CFBundleVersion</key>
\t<string>1.0</string>
\t<key>CFBundleShortVersionString</key>
\t<string>1.0</string>
\t<key>CFBundleInfoDictionaryVersion</key>
\t<string>6.0</string>
\t<key>LSMinimumSystemVersion</key>
\t<string>11.0</string>
\t<key>NSHighResolutionCapable</key>
\t<true/>
\t<key>LSUIElement</key>
\t<false/>
</dict>
</plist>
"""

APP_LAUNCH = """#!/bin/bash
# 待办悬浮窗启动脚本（App 包内部使用）
DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
SCRIPT="$DIR/todo_float.py"

PY=""
for c in \\
  "$DIR/runtime/bin/python3" \\
  /Users/juncc/.workbuddy/binaries/python/versions/3.13.12/bin/python3 \\
  /opt/homebrew/bin/python3 \\
  /usr/local/bin/python3 \\
  /usr/bin/python3
do
  [ -x "$c" ] || continue
  v="$("$c" -c 'import tkinter,sys;sys.stdout.write(str(tkinter.TkVersion))' 2>/dev/null)"
  case "$v" in
    8.6|8.7|8.8|8.9|9.*|1[0-9].*) PY="$c"; break ;;
  esac
done

if [ -z "$PY" ]; then
  osascript -e 'display alert "待办悬浮窗" message "找不到可用的图形运行环境（需要 tkinter）。"' >/dev/null 2>&1
  exit 1
fi

exec "$PY" "$SCRIPT"
"""


def _ensure_app_bundle():
    """在挂件目录生成 macOS 的 待办悬浮窗.app。返回是否动过文件。"""
    if sys.platform != "darwin":
        return False
    contents = os.path.join(APP_BUNDLE, "Contents")
    a = _ensure_file(os.path.join(contents, "Info.plist"), APP_INFO_PLIST)
    b = _ensure_file(os.path.join(contents, "MacOS", "launch"), APP_LAUNCH,
                     executable=True)
    return a or b


def cmd_install():
    os.makedirs(WIDGET_HOME, exist_ok=True)
    changed = []
    for name, dst in (("todo_dock.py", WIDGET_SCRIPT),
                      ("todo_float.py", LEGACY_SCRIPT)):
        src = os.path.join(ASSETS_DIR, name)
        if not os.path.exists(src):
            print("技能包里缺 assets/%s，装不了。" % name)
            sys.exit(1)
        if not os.path.exists(dst) or not _same_file(src, dst):
            shutil.copy2(src, dst)
            changed.append(name)
    if _ensure_app_bundle():
        changed.append("待办悬浮窗.app")
    src_doc = os.path.join(ASSETS_DIR, "使用说明.html")
    if not os.path.exists(src_doc):
        src_doc = os.path.join(DOCS_DIR, "使用说明.html")
    if os.path.exists(src_doc):
        dst_doc = os.path.join(WIDGET_HOME, "使用说明.html")
        if not os.path.exists(dst_doc) or not _same_file(src_doc, dst_doc):
            shutil.copy2(src_doc, dst_doc)
            changed.append("使用说明.html")
    if changed:
        print("已安装/更新：%s（位置 %s）" % ("、".join(changed), WIDGET_HOME))
        print("data.json 不会被覆盖，原有待办原样保留。")
        if _pid_alive():
            print("注意：悬浮窗正在运行，程序文件更新后要 stop 再 start 才生效。")
    else:
        print("已经是最新（位置 %s）。" % WIDGET_HOME)


def _pid_alive():
    """Windows 上 os.kill(pid, 0) 判定不可靠，改用 OpenProcess 查退出码。"""
    try:
        with open(RUNNING_FILE) as f:
            pid = int((f.read() or "0").strip() or 0)
        if not pid:
            return 0
        if sys.platform.startswith("win"):
            import ctypes
            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
            if not h:
                return 0
            code = ctypes.c_ulong()
            ok = k.GetExitCodeProcess(h, ctypes.byref(code))
            k.CloseHandle(h)
            return pid if (ok and code.value == 259) else 0   # STILL_ACTIVE
        os.kill(pid, 0)
        return pid
    except Exception:
        return 0


def _python_candidates():
    """列出可能的解释器。Windows 上还要扫 py -0p 与官方安装目录。"""
    import glob
    cands = [sys.executable, shutil.which("python3"), shutil.which("python"),
             "/usr/bin/python3"]
    if sys.platform.startswith("win"):
        cands += sorted(glob.glob(os.path.join(
            os.path.expanduser("~"), "AppData", "Local", "Programs",
            "Python", "Python3*", "python.exe")), reverse=True)
        try:
            out = subprocess.run(["py", "-0p"], capture_output=True,
                                 text=True, timeout=10).stdout
            for line in out.splitlines():
                parts = line.strip().split()
                if parts and os.path.isfile(parts[-1]):
                    cands.append(parts[-1])
        except Exception:
            pass
    return cands


def _find_python():
    """挑一个带 tkinter 的 Python，优先当前解释器。"""
    seen = set()
    for c in _python_candidates():
        if not c or c in seen:
            continue
        seen.add(c)
        try:
            v = subprocess.run(
                [c, "-c", "import tkinter;print(tkinter.TkVersion)"],
                capture_output=True, text=True, timeout=15).stdout.strip()
            float(v)
            return c
        except Exception:
            continue
    return None


def cmd_start(legacy=False):
    script = LEGACY_SCRIPT if legacy else WIDGET_SCRIPT
    if not os.path.exists(script) or not os.path.exists(LEGACY_SCRIPT):
        cmd_install()
    if _pid_alive():
        print("悬浮窗已经在运行（pid %d），不用重复启动。" % _pid_alive())
        return
    if legacy and sys.platform == "darwin" and os.path.exists(APP_BUNDLE):
        subprocess.run(["open", APP_BUNDLE], check=False)
        print("已启动（走 待办悬浮窗.app，旧版）。")
        return
    py = _find_python()
    if not py:
        print("找不到装了 tkinter 的 Python。先装一个（Windows 装官方 Python 勾上 tcl/tk；"
              "macOS 装 python-tk；Linux 装 python3-tk），再运行 start。")
        sys.exit(1)
    kwargs = {}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | \
                                  getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([py, script], cwd=WIDGET_HOME,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, **kwargs)
    print("已启动 %s（%s）。" % (os.path.basename(script), py))
    if not legacy:
        print("右下角小胶囊已就位，点它展开待办和日历。")


def cmd_stop():
    pid = _pid_alive()
    if not pid:
        print("悬浮窗本来就没在运行。")
        return
    try:
        if sys.platform.startswith("win"):
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False)
        else:
            os.kill(pid, 15)
        for _ in range(20):
            if not _pid_alive():
                break
            time.sleep(0.1)
        print("已停止（pid %d）。" % pid)
    except Exception as e:
        print("停止失败：%r。可以手动结束进程 %d。" % (e, pid))


def cmd_status():
    print("挂件目录：%s" % WIDGET_HOME)
    pid = _pid_alive()
    print("悬浮窗：%s" % ("运行中（pid %d）" % pid if pid else "未运行"))
    if os.path.exists(WIDGET_SCRIPT):
        with open(WIDGET_SCRIPT, encoding="utf-8") as f:
            src = f.read()
        hot = ("_check_external_change" in src) or ("_poll" in src)
        print("默认挂件：日历胶囊 todo_dock.py（右下角 / 小日历 / 当天+提前1天提醒）")
        print("热加载：%s（改数据无需重启窗口）" % ("支持" if hot else "不支持（建议 install 更新）"))
    else:
        print("默认挂件：还没装，先跑 install。")
    d = load_data()
    if d is None:
        print("数据：还没有 data.json。")
    else:
        act = d.get("active", [])
        recent = sum(1 for t in act if not t.get("due"))
        print("数据：%d 条进行中（其中近期待办 %d 条）/ %d 条已完成" %
              (len(act), recent, len(d.get("done", []))))
    if os.path.exists(GOLD_CACHE):
        try:
            with open(GOLD_CACHE, encoding="utf-8") as f:
                g = json.load(f)
            print("金价缓存：%.2f 元/克（%s）" %
                  (g["price"], time.strftime(
                      "%m-%d %H:%M", time.localtime(g.get("fetched") or 0))))
        except Exception:
            print("金价缓存：不可读，挂件联网后会自动重建")


def cmd_gold():
    """独立查一次浙商银行积存金实时价，挂件没开也能用。"""
    try:
        req = urllib.request.Request(
            GOLD_URL, headers={"User-Agent": "Mozilla/5.0",
                               "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        q = (j.get("resultData") or {}).get("datas") or {}
        price = float(q["price"])
        ts = int(q.get("time") or 0) / 1000.0 or time.time()
        print("浙商银行积存金（京东金融实时报价）")
        print("  最新价    %.2f 元/克" % price)
        print("  涨跌      %s（%s）" % (q.get("upAndDownAmt", "-"),
                                     q.get("upAndDownRate", "-")))
        print("  昨收      %s 元/克" % q.get("yesterdayPrice", "-"))
        print("  报价时间  %s" % time.strftime("%Y-%m-%d %H:%M:%S",
                                             time.localtime(ts)))
    except Exception as e:
        print("金价获取失败：%s" % e)
        print("挂件会继续显示上次缓存价并自动重试（gold_cache.json）。")
        sys.exit(1)


# ---------------------------------------------------------------- 增删改

def _opt(rest, key, default=None):
    """从 ['--due','2026-11-15', ...] 里取 --key 后面的值。"""
    if key in rest:
        i = rest.index(key)
        if i + 1 < len(rest):
            return rest[i + 1]
    return default


def _valid_due(s):
    """宽松解析日期，统一成 YYYY-MM-DD。"""
    import datetime
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m-%d", "%m/%d"):
        try:
            d = datetime.datetime.strptime(str(s).strip(), fmt).date()
            if d.year == 1900:
                d = d.replace(year=datetime.date.today().year)
            return d.isoformat()
        except ValueError:
            continue
    print("日期看不懂：%s（用 YYYY-MM-DD，或 11-15）" % s)
    sys.exit(2)


def cmd_add(title, bottom=False, rest=None):
    rest = rest or []
    title = " ".join(title.split())
    if not title:
        print("标题是空的，没加。")
        sys.exit(2)
    due = _valid_due(_opt(rest, "--due"))
    tm = _opt(rest, "--time", "09:00")
    rule = _opt(rest, "--remind", "both")
    if rule not in ("both", "day", "eve", "none"):
        print("--remind 只能是 both / day / eve / none")
        sys.exit(2)
    d = load_data(need=True)
    item = {"id": new_id(), "title": title, "progress": 0,
            "createdAt": int(time.time() * 1000), "doneAt": None}
    if due:
        item.update({"due": due, "time": tm, "remind": rule, "notified": {}})
    if bottom:
        d.setdefault("active", []).append(item)
    else:
        d.setdefault("active", []).insert(0, item)
    save_data(d)
    extra = ("，%s %s 提醒 %s" % (due, tm, rule)) if due else ""
    print("已添加：「%s」%s（%d 条进行中）" % (title, extra, len(d["active"])))
    if _pid_alive():
        print("悬浮窗约 0.3 秒内自动刷新，无需重启。")


def cmd_setdue(arg, date_s, rest=None):
    d = load_data(need=True)
    t, _ = _match_one(d, arg, pool="both")
    if str(date_s).strip().lower() in ("none", "clear", "null", "近期", "无"):
        for k in ("due", "time", "remind", "notified"):
            t.pop(k, None)
        save_data(d)
        print("已把「%s」改为近期待办（不设日期、不提醒）。" %
              _clip(t["title"]))
        return
    due = _valid_due(date_s)
    t["due"] = due
    tm = _opt(rest or [], "--time")
    if tm:
        t["time"] = tm
    t.setdefault("time", "09:00")
    t.setdefault("remind", "both")
    t["notified"] = {}          # 改期后重新允许提醒
    save_data(d)
    print("已把「%s」改到 %s %s（提醒 both，可用 --time 改时刻）" %
          (_clip(t["title"]), due, t["time"]))


def cmd_autostart(action):
    """Windows 启动文件夹放一个无窗口的 vbs，让提醒能常驻。"""
    if not sys.platform.startswith("win"):
        print("自动配置目前只支持 Windows；macOS 请把 待办悬浮窗.app 加到登录项。")
        return
    folder = os.path.join(os.environ.get("APPDATA", ""), "Microsoft",
                          "Windows", "Start Menu", "Programs", "Startup")
    path = os.path.join(folder, "TodoDock.vbs")
    if action == "status":
        print("开机自启：%s" %
              ("已开启（%s）" % path if os.path.exists(path) else "未开启"))
        return
    if action == "off":
        if os.path.exists(path):
            os.remove(path)
            print("已关闭开机自启。")
        else:
            print("本来就没开。")
        return
    if action != "on":
        print("用法：todo.py autostart on|off|status")
        sys.exit(2)
    py = _find_python()
    if not py:
        print("找不到带 tkinter 的 Python，开不了自启。")
        sys.exit(1)
    pyw = os.path.join(os.path.dirname(py), "pythonw.exe")
    if not os.path.exists(pyw):
        pyw = py
    if not os.path.exists(WIDGET_SCRIPT):
        cmd_install()
    os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write('Set ws = CreateObject("Wscript.Shell")\n'
                'ws.Run """%s"" ""%s""", 0, False\n' % (pyw, WIDGET_SCRIPT))
    print("已开启开机自启：%s" % path)
    print("（要关掉就跑：todo.py autostart off）")


def cmd_done(arg):
    d = load_data(need=True)
    t, lst = _match_one(d, arg, pool="active")
    if lst == "done":
        print("「%s」本来就是已完成。" % _clip(t["title"]))
        return
    t["progress"] = 100
    t["doneAt"] = int(time.time() * 1000)
    d["active"].remove(t)
    d.setdefault("done", []).insert(0, t)
    save_data(d)
    print("已完成：「%s」" % t["title"])


def cmd_remove(arg):
    d = load_data(need=True)
    backup_data()
    t, lst = _match_one(d, arg)
    d[lst].remove(t)
    save_data(d)
    print("已删除：「%s」（备份在 data.json.backup-cli）" % _clip(t["title"]))


def cmd_progress(arg, val):
    try:
        p = int(val)
    except ValueError:
        print("进度必须是 0-100 的整数。")
        sys.exit(2)
    p = max(0, min(100, p))
    d = load_data(need=True)
    t, lst = _match_one(d, arg)
    t["progress"] = p
    if p >= 100 and lst == "active":
        t["doneAt"] = int(time.time() * 1000)
        d["active"].remove(t)
        d.setdefault("done", []).insert(0, t)
        save_data(d)
        print("「%s」进度 100%%，已归入已完成。" % _clip(t["title"]))
        return
    if p < 100 and lst == "done":
        t["doneAt"] = None
        d["done"].remove(t)
        d.setdefault("active", []).insert(0, t)
        save_data(d)
        print("「%s」改回进行中（进度 %d%%）。" % (_clip(t["title"]), p))
        return
    save_data(d)
    print("「%s」进度改为 %d%%。" % (_clip(t["title"]), p))


def cmd_rename(arg, new_title):
    new_title = " ".join(new_title.split())
    if not new_title:
        print("新标题是空的，没改。")
        sys.exit(2)
    d = load_data(need=True)
    t, _ = _match_one(d, arg, pool="both")
    old = t["title"]
    t["title"] = new_title
    save_data(d)
    print("已改：「%s」→「%s」" % (_clip(old), _clip(new_title)))


# ---------------------------------------------------------------- 入口

USAGE = __doc__.strip()


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return
    cmd, rest = args[0], args[1:]
    if cmd == "add":
        if not rest:
            print('用法：todo.py add "标题" [--bottom] [--due YYYY-MM-DD] '
                  '[--time HH:MM] [--remind both|day|eve|none]')
            sys.exit(2)
        cmd_add(rest[0], "--bottom" in rest[1:], rest[1:])
    elif cmd == "setdue":
        if len(rest) < 2:
            print("用法：todo.py setdue <匹配词> YYYY-MM-DD|none [--time HH:MM]")
            sys.exit(2)
        cmd_setdue(rest[0], rest[1], rest[2:])
    elif cmd == "gold":
        cmd_gold()
    elif cmd == "autostart":
        cmd_autostart(rest[0] if rest else "status")
    elif cmd == "list":
        print_list(load_data(), show_all="--all" in rest,
                   full="--full" in rest)
    elif cmd == "done":
        cmd_done(rest[0])
    elif cmd == "remove":
        cmd_remove(rest[0])
    elif cmd == "progress":
        cmd_progress(rest[0], rest[1])
    elif cmd == "rename":
        cmd_rename(rest[0], rest[1])
    elif cmd == "status":
        cmd_status()
    elif cmd == "install":
        cmd_install()
    elif cmd == "start":
        cmd_start(legacy="--legacy" in rest)
    elif cmd == "stop":
        cmd_stop()
    else:
        print("不认识的命令：%s\n" % cmd)
        print(USAGE)
        sys.exit(2)


if __name__ == "__main__":
    main()
