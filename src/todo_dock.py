#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桌面右下角「待办胶囊」—— 常驻 / 点击展开 / 小日历 / 当天 + 提前一天提醒。

两类待办：
  · 日期待办：带 due，日历上可点选、可设当天/提前一天提醒；
  · 近期待办：不带 due，不绑定任何日期、不提醒，随时可做。

折叠态胶囊第二行定时显示「浙商银行积存金」实时价（京东金融 H5 报价接口）。

与 floating-todo-widget 技能共用同一份 data.json（active / done / ui），
并向后兼容旧版 todo_float.py：本程序读写时保留所有未知字段。

日期待办的可选字段（旧数据没有也能正常显示）：
    due      "YYYY-MM-DD"                     截止日期
    time     "HH:MM"                         提醒时刻，默认 09:00
    remind   both | day | eve | none         当天/提前一天 提醒组合，默认 both
    notified {"d0":"YYYY-MM-DD", "d1":...}   已提醒标记，避免重复弹窗

近期待办没有上述字段（或 due 为 null），命令行 add 不带 --due 时即创建这类。

只用标准库。Windows 下用 -transparentcolor 实现圆角，其他平台自动降级为方角。
"""

import calendar
import ctypes
import datetime as dt
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import urllib.request

# ---------------------------------------------------------------- 路径 / 外观
APP_NAME = "TodoDock"
APP_TITLE = "待办胶囊"
VERSION = "1.0.0"
FROZEN = bool(getattr(sys, "frozen", False))   # PyInstaller 打包后的 exe


def _data_dir_arg():
    """--data-dir <目录>：便携模式，把数据放在 exe 旁边而不是用户目录。"""
    a = sys.argv
    if "--data-dir" in a:
        i = a.index("--data-dir")
        if i + 1 < len(a):
            return os.path.abspath(a[i + 1])
    return None


def _resolve_app_dir():
    # 优先级：显式 --data-dir > 环境变量 > 用户目录
    explicit = _data_dir_arg()
    if explicit:
        return explicit
    env = os.environ.get("TODO_WIDGET_HOME")
    if env:
        return env
    # 便携模式约定：若 exe 同目录存在 portable.txt，数据就放 exe 同目录
    if FROZEN:
        base = os.path.dirname(os.path.abspath(sys.executable))
        if os.path.exists(os.path.join(base, "portable.txt")):
            return os.path.join(base, "data")
    return os.path.join(os.path.expanduser("~"), ".workbuddy", "todo-widget")


APP_DIR = _resolve_app_dir()
DATA_FILE = os.path.join(APP_DIR, "data.json")
RUNNING_FILE = os.path.join(APP_DIR, ".running")
LOG_FILE = os.path.join(APP_DIR, "dock.log")

TRANS = "#010203"          # Windows -transparentcolor 的隐身色
WHITE = "#ffffff"
CARD = "#f5f8fd"
BORDER = "#dce3ef"
TEXT = "#202a3a"
SUB = "#8b95a8"
BLUE = "#4a6cf7"
BLUE_SOFT = "#e9efff"
ORANGE = "#dd8324"
RED = "#e0484d"
GREEN = "#18a36b"
FONT = "Microsoft YaHei UI"

CAP_W, CAP_H = 264, 58           # 折叠态：两行小胶囊（待办 + 金价）
OPEN_W = 334                     # 展开态宽度
EDGE = 8                         # 圆角留边
MIN_H, MAX_H = 470, 690          # 展开态高度区间
BUILD = "2026-11-13d"           # 金价 5 秒刷新 / 涨红跌绿增强
DEFAULT_TIME = "09:00"
RECENT_H = 130                   # 最近待办滚动区高度（约 5 行）
DAY_H = 46                       # 选中日 / 近期待办区高度（2 行不裁切）
CELL_H = 24                      # 日历每格高度

# 浙商银行积存金实时报价（京东金融 H5 开放接口，无需登录/密钥）
GOLD_NAME = "浙商积存金"
GOLD_URL = ("https://api.jdjygold.com/gw2/generic/jrm/h5/m/"
            "stdLatestPrice?productSku=1961543816")
GOLD_CACHE = os.path.join(APP_DIR, "gold_cache.json")
GOLD_INTERVAL = 5                # 正常刷新间隔（秒）
GOLD_RETRY = 5                   # 失败后的重试间隔（秒），尽快恢复实时价
GOLD_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36")
GOLD_ON = ("--nogold" not in sys.argv and
           "--measure" not in sys.argv)  # 验证脚本可关掉网络轮询
_LAST_GOLD_ERR = [0.0]


def log(msg):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


# ---------------------------------------------------------------- 数据
def default_data():
    return {"active": [], "done": [], "ui": {}}


def load_data():
    """返回 dict 或 None（读失败）。

    绝不在读失败时返回「空的默认数据」—— 那样一旦后面任何一次 save_data
    （提醒标记、拖动保存位置…）触发，就会把磁盘上的待办整份抹掉。
    """
    if not os.path.exists(DATA_FILE):
        return default_data()
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict) or \
           not isinstance(d.get("active"), list) or \
           not isinstance(d.get("done"), list):
            raise ValueError("data.json 结构不完整")
        d.setdefault("ui", {})
        return d
    except Exception as e:
        log("load_data 失败，本次不替换内存数据：%r" % e)
        return None


_MTIME = [None]


def _stat():
    try:
        return os.stat(DATA_FILE).st_mtime_ns
    except OSError:
        return None


def save_data(d):
    tmp = DATA_FILE + ".tmp"
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, DATA_FILE)
        _MTIME[0] = _stat()
    except Exception as e:
        log("save_data 失败：%r" % e)


def new_id():
    return "%x%04x" % (int(time.time() * 1000),
                       int.from_bytes(os.urandom(2), "big"))


# ---------------------------------------------------------------- 日期工具
def parse_date(s):
    """宽松解析：2026-11-15 / 2026/11/15 / 11-15 / 11月15日。"""
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m-%d", "%m/%d", "%m月%d日"):
        try:
            d = dt.datetime.strptime(s, fmt).date()
            if d.year == 1900:                    # 只写了月日 -> 补今年
                d = d.replace(year=dt.date.today().year)
            return d
        except ValueError:
            continue
    return None


def parse_time(s):
    try:
        p = str(s or DEFAULT_TIME).strip().split(":")
        h = int(p[0])
        m = int(p[1]) if len(p) > 1 else 0
        if 0 <= h < 24 and 0 <= m < 60:
            return dt.time(h, m)
    except Exception:
        pass
    return dt.time(9, 0)


def iso(d):
    return d.isoformat()


def sort_key(t):
    """紧急（逾期/今天/明天）→ 近期（不设日期）→ 之后（有明确日期）。

    近期待办没有 deadline，不该盖住今天到期的急事，也不该沉到列表最底。
    """
    d = parse_date(t.get("due"))
    created = int(t.get("createdAt") or 0)
    if d is None:
        return (1, 0, 0, -created)
    n = (d - dt.date.today()).days
    if n <= 1:
        return (0, d.toordinal(), 0, -created)
    return (2, d.toordinal(), 0, -created)


def due_label(t):
    d = parse_date(t.get("due"))
    if not d:
        return "近期", BLUE
    n = (d - dt.date.today()).days
    if n < 0:
        return "逾期%d天" % (-n), RED
    if n == 0:
        return "今天", ORANGE
    if n == 1:
        return "明天", BLUE
    return "%d/%d" % (d.month, d.day), SUB


WEEK_CN = ["一", "二", "三", "四", "五", "六", "日"]


def cn_date(d):
    return "%d月%d日 周%s" % (d.month, d.day, WEEK_CN[d.weekday()])


def clip(s, n=21):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:n] + "…"


def clip_px(s, max_px, font):
    """按真实像素宽度裁剪（中英混排比按字数准），并保留省略号。"""
    s = " ".join(str(s).split())
    try:
        if font.measure(s) <= max_px:
            return s
        lo, hi = 0, len(s)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if font.measure(s[:mid] + "…") <= max_px:
                lo = mid
            else:
                hi = mid - 1
        return (s[:lo] + "…") if lo else "…"
    except Exception:
        return clip(s, 20)


# ---------------------------------------------------------------- 金价
def fetch_gold():
    """取浙商银行积存金实时报价。纯标准库、无密钥；失败返回 None。

    返回字段：price 元/克、rate 涨跌幅、amt 涨跌额、ts 交易所报价时间戳。
    """
    try:
        req = urllib.request.Request(
            GOLD_URL, headers={"User-Agent": GOLD_UA,
                               "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=4) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        d = (j.get("resultData") or {}).get("datas") or {}
        price = float(d.get("price"))
        if not 100 < price < 100000:
            raise ValueError("价格超出合理范围：%r" % price)
        try:
            ts = int(d.get("time") or 0) / 1000.0 or time.time()
        except (TypeError, ValueError):
            ts = time.time()
        try:
            prev = float(d.get("yesterdayPrice") or 0) or None
        except (TypeError, ValueError):
            prev = None
        return {"price": price,
                "rate": str(d.get("upAndDownRate") or "").strip(),
                "amt": str(d.get("upAndDownAmt") or "").strip(),
                "prev": prev,          # 涨跌字段没符号时用它判断方向
                "ts": ts, "fetched": time.time()}
    except Exception as e:
        if time.time() - _LAST_GOLD_ERR[0] > 300:   # 别每 5 秒刷一条日志
            _LAST_GOLD_ERR[0] = time.time()
            log("金价获取失败：%s" % str(e)[:120])
        return None


def read_gold_cache():
    """开机先显示上次报价，避免联网前胶囊空着。"""
    try:
        with open(GOLD_CACHE, "r", encoding="utf-8") as f:
            g = json.load(f)
        if isinstance(g, dict) and isinstance(g.get("price"), (int, float)):
            g["cached"] = True
            return g
    except Exception:
        pass
    return None


def write_gold_cache(g):
    # 绝不写 data.json：那会触发挂件自己的热加载，形成无谓重绘
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        tmp = GOLD_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in g.items() if k != "cached"},
                      f, ensure_ascii=False)
        os.replace(tmp, GOLD_CACHE)
    except Exception as e:
        log("金价缓存写入失败：%r" % e)


def gold_dir(g):
    """涨 → 红，跌 → 绿（A 股习惯），平盘 → 灰。

    优先看涨跌额 / 涨跌幅的符号或数值；接口偶尔不带 + 号时，
    再用最新价与昨收价对比兜底，避免整行退化成灰色。
    """
    g = g or {}
    for key in ("amt", "rate"):
        s = str(g.get(key) or "").strip().replace("＋", "+").replace("−", "-")
        if not s:
            continue
        if s.startswith("-"):
            return "▼", GREEN
        if s.startswith("+"):
            return "▲", RED
        try:
            v = float(s.replace("%", "").replace(",", ""))
            if v > 0:
                return "▲", RED
            if v < 0:
                return "▼", GREEN
            # 0 不急着判平盘，下一个字段也许有方向
        except ValueError:
            continue
    try:
        if g.get("prev"):
            delta = float(g["price"]) - float(g["prev"])
            if delta > 0:
                return "▲", RED
            if delta < 0:
                return "▼", GREEN
    except (TypeError, ValueError, KeyError):
        pass
    return "—", SUB


# ---------------------------------------------------------------- 单实例
def _pid_alive(pid):
    """Windows 上 os.kill(pid, 0) 判定不可靠，用 OpenProcess 查退出码。"""
    if not pid:
        return False
    if sys.platform.startswith("win"):
        try:
            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
            if not h:
                return False
            code = ctypes.c_ulong()
            ok = k.GetExitCodeProcess(h, ctypes.byref(code))
            k.CloseHandle(h)
            return bool(ok) and code.value == 259   # STILL_ACTIVE
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_lock():
    try:
        if os.path.exists(RUNNING_FILE):
            with open(RUNNING_FILE) as f:
                pid = int((f.read() or "0").strip() or 0)
            if _pid_alive(pid):
                log("已有实例 pid=%d，本次退出" % pid)
                return False
        os.makedirs(APP_DIR, exist_ok=True)
        with open(RUNNING_FILE, "w") as f:
            f.write(str(os.getpid()))
        return True
    except Exception as e:
        log("acquire_lock 异常：%r" % e)
        return True


def round_rect(c, x0, y0, x1, y1, r, **kw):
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r,
           x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1,
           x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return c.create_polygon(pts, smooth=True, **kw)


# ================================================================ 主程序
class Dock:

    def __init__(self, root):
        self.root = root
        d = load_data()
        self.data = d if d is not None else default_data()
        _MTIME[0] = _stat()
        self.ui = self.data.setdefault("ui", {})
        self.collapsed = True
        self.sel = dt.date.today()
        self.view_y, self.view_m = self.sel.year, self.sel.month
        self.rem_win = None
        self.rem_queue = []
        self._drag = None
        self._tick = 0

        # 悬停全文气泡
        self.tip_win = None
        self.tip_owner = None
        self.tip_title = None
        self.tip_sub = ""
        self.tip_job = None
        self.tip_hide_job = None

        # 金价：后台线程取数 → queue → 主线程刷新 UI（tkinter 非线程安全）
        self.gold = read_gold_cache() if GOLD_ON else None
        self.gold_q = queue.Queue()
        self._gold_busy = False
        self._gold_ok = False
        self._gold_timer = None

        self.var_time = tk.StringVar(value=DEFAULT_TIME)
        self.var_d0 = tk.IntVar(value=1)
        self.var_d1 = tk.IntVar(value=1)

        self.f_cap = tkfont.Font(family=FONT, size=10, weight="bold")
        self.f_title = tkfont.Font(family=FONT, size=11, weight="bold")
        self.f_body = tkfont.Font(family=FONT, size=10)
        self.f_small = tkfont.Font(family=FONT, size=9)
        # 涨跌用 9pt 加粗：胶囊第二行只有 21px 高，换成 10pt 会裁掉 % 的下半圈
        self.f_small_bold = tkfont.Font(family=FONT, size=9, weight="bold")
        self.f_tiny = tkfont.Font(family=FONT, size=8)
        self.f_cal = tkfont.Font(family=FONT, size=9)
        self.f_cal_bold = tkfont.Font(family=FONT, size=9, weight="bold")
        self.f_price = tkfont.Font(family=FONT, size=10, weight="bold")

        self._setup()
        self._build()
        self.render()
        self._place()
        self.root.after(300, self._poll)
        self.root.after(1600, self._check_reminders)
        if GOLD_ON:
            self.root.after(600, self._gold_refresh)

    # ------------------------------------------------------------ 窗口
    def _setup(self):
        r = self.root
        r.title("待办胶囊")
        r.overrideredirect(True)
        r.resizable(False, False)
        r.configure(bg=TRANS)
        try:
            r.attributes("-topmost", True)
            r.attributes("-transparentcolor", TRANS)
        except Exception as e:
            log("透明窗口不可用，降级为方角：%r" % e)
            r.configure(bg=WHITE)
        r.protocol("WM_DELETE_WINDOW", self.quit)

        # 先建背景画布、后建内容框：Tk 的堆叠顺序天然把画布压在底层
        # （不能用 canvas.lower()，那个是给画布里的图元用的）
        self.bg = tk.Canvas(r, bg=TRANS, highlightthickness=0, bd=0)
        self.content = tk.Frame(r, bg=WHITE)

    def _anchor(self):
        try:
            rx = int(self.ui.get("dock_rx", 18))
            ry = int(self.ui.get("dock_ry", 58))
        except (TypeError, ValueError):
            rx, ry = 18, 58
        return max(4, rx), max(4, ry)

    def _place(self):
        r = self.root
        r.update_idletasks()
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        if self.collapsed:
            w, h = CAP_W, CAP_H
        else:
            w = OPEN_W
            need = self.content.winfo_reqheight() + 2 * EDGE
            h = max(MIN_H, min(MAX_H, int(need), sh - 100))
        rx, ry = self._anchor()
        x = max(4, min(sw - w - rx, sw - w - 4))
        y = max(4, min(sh - h - ry, sh - h - 4))
        r.geometry("%dx%d+%d+%d" % (w, h, x, y))
        self.bg.place(x=0, y=0, width=w, height=h)
        self.bg.delete("all")
        rr = min(16, h // 2 - 3)
        round_rect(self.bg, 1, 1, w - 2, h - 2, rr,
                   fill=WHITE, outline=BORDER, width=1)
        self.content.place(x=EDGE, y=EDGE, width=w - 2 * EDGE,
                           height=h - 2 * EDGE)

    def _bind_drag(self, w):
        w.bind("<Button-1>", self._drag_start, add="+")
        w.bind("<B1-Motion>", self._drag_move, add="+")
        w.bind("<ButtonRelease-1>", self._drag_end, add="+")
        for c in w.winfo_children():
            self._bind_drag(c)

    # ------------------------------------------------------------ 构建界面
    def _build(self):
        c = self.content

        # ---- 折叠态胶囊：第一行待办，第二行浙商积存金实时价 --------------
        self.cap = tk.Frame(c, bg=WHITE, height=CAP_H - 2 * EDGE)
        self.cap.pack_propagate(False)

        top = tk.Frame(self.cap, bg=WHITE, height=21)
        top.pack(fill="x")
        top.pack_propagate(False)
        dot = tk.Canvas(top, width=10, height=10, bg=WHITE,
                        highlightthickness=0, bd=0)
        dot.create_oval(1, 1, 9, 9, fill=BLUE, outline="")
        dot.pack(side="left", padx=(6, 5))
        self.cap_text = tk.Label(top, text="待办", bg=WHITE, fg=TEXT,
                                 font=self.f_cap)
        self.cap_text.pack(side="left")
        self.cap_today = tk.Label(top, text="", bg=WHITE, fg=SUB,
                                  font=self.f_small)
        self.cap_today.pack(side="left", padx=(6, 0))
        self.cap_date = tk.Label(top, text="", bg=WHITE, fg=SUB,
                                 font=self.f_small)
        self.cap_date.pack(side="right", padx=(0, 8))

        bot = tk.Frame(self.cap, bg=CARD, height=21)
        bot.pack(fill="x")
        bot.pack_propagate(False)
        # 时间先 pack 占位，否则会被左侧不断变长的价格挤出胶囊
        self.cap_gold_time = tk.Label(bot, text="", bg=CARD, fg=SUB,
                                      font=self.f_tiny, width=6, anchor="e")
        self.cap_gold_time.pack(side="right", padx=(0, 6))
        self.cap_gold_name = tk.Label(bot, text="浙商金", bg=CARD, fg=SUB,
                                      font=self.f_tiny)
        self.cap_gold_name.pack(side="left", padx=(6, 4))
        self.cap_gold_price = tk.Label(bot, text="--", bg=CARD, fg=TEXT,
                                       font=self.f_price)
        self.cap_gold_price.pack(side="left")
        self.cap_gold_unit = tk.Label(bot, text="元/克", bg=CARD, fg=SUB,
                                      font=self.f_tiny)
        self.cap_gold_unit.pack(side="left", padx=(2, 4))
        # 涨跌用粗体，涨红跌绿一眼可辨
        self.cap_gold_chg = tk.Label(bot, text="", bg=CARD, fg=SUB,
                                     font=self.f_small_bold)
        self.cap_gold_chg.pack(side="left")

        # 金价行 = 点一下立即刷新，不参与拖动/展开，避免点行情时误收面板
        for w in (bot, self.cap_gold_name, self.cap_gold_price,
                  self.cap_gold_unit, self.cap_gold_chg, self.cap_gold_time):
            w._nodrag = True
            w.configure(cursor="hand2")
            w.bind("<Button-1>", lambda e: self._gold_refresh(), add="+")
        self._bind_drag(self.cap)

        # ---- 展开态面板 -------------------------------------------------
        p = self.panel = tk.Frame(c, bg=WHITE)

        head = tk.Frame(p, bg=WHITE, height=30)
        head.pack(fill="x", pady=(2, 4))
        head.pack_propagate(False)
        hd = tk.Canvas(head, width=8, height=8, bg=WHITE,
                       highlightthickness=0, bd=0)
        hd.create_oval(0, 0, 8, 8, fill=BLUE, outline="")
        hd.pack(side="left", padx=(4, 5))
        tk.Label(head, text="我的待办", bg=WHITE, fg=TEXT,
                 font=self.f_title).pack(side="left")
        self.lb_head_date = tk.Label(head, text="", bg=WHITE, fg=SUB,
                                     font=self.f_small)
        self.lb_head_date.pack(side="left", padx=(8, 0))
        self._mk_btn(head, "×", self.quit, side="right", fg=RED)
        self._mk_btn(head, "收起", self._collapse, side="right", width=5)
        self._bind_drag(head)

        # 展开态也保留金价，别让行情在展开后「消失」
        gb = tk.Frame(p, bg=CARD, height=22)
        gb.pack(fill="x", pady=(0, 4))
        gb.pack_propagate(False)
        self.lb_gold_head = tk.Label(gb, text="", bg=CARD, fg=TEXT,
                                     font=self.f_small, cursor="hand2")
        self.lb_gold_head.pack(side="left", padx=(6, 0))
        self.lb_gold_head_chg = tk.Label(gb, text="", bg=CARD, fg=SUB,
                                         font=self.f_small_bold, cursor="hand2")
        self.lb_gold_head_chg.pack(side="left", padx=(6, 0))
        self.lb_gold_head_tip = tk.Label(gb, text="点此刷新", bg=CARD, fg=SUB,
                                         font=self.f_tiny, cursor="hand2")
        self.lb_gold_head_tip.pack(side="right", padx=(0, 6))
        for w in (gb, self.lb_gold_head, self.lb_gold_head_chg,
                  self.lb_gold_head_tip):
            w._nodrag = True
            w.bind("<Button-1>", lambda e: self._gold_refresh(), add="+")

        # 最近待办
        rh = tk.Frame(p, bg=WHITE)
        rh.pack(fill="x", pady=(2, 2))
        tk.Label(rh, text="最近待办", bg=WHITE, fg=TEXT,
                 font=self.f_body).pack(side="left")
        self._mk_btn(rh, "+ 近期", self._goto_recent, side="right", width=6,
                     bg=BLUE_SOFT, fg=BLUE)
        self.lb_recent_count = tk.Label(rh, text="", bg=WHITE, fg=SUB,
                                        font=self.f_small)
        self.lb_recent_count.pack(side="right", padx=(0, 6))

        self.recent_cv = tk.Canvas(p, height=RECENT_H, bg=WHITE,
                                   highlightthickness=0, bd=0)
        self.recent_cv.pack(fill="x")
        self.recent_in = tk.Frame(self.recent_cv, bg=WHITE)
        self.recent_win = self.recent_cv.create_window(
            (0, 0), window=self.recent_in, anchor="nw", width=OPEN_W - 2 * EDGE - 4)
        self.recent_in.bind(
            "<Configure>",
            lambda e: self.recent_cv.configure(
                scrollregion=self.recent_cv.bbox("all")))
        self._bind_wheel(self.recent_cv)

        self._sep(p, 8)

        # 日历：月份导航
        nav = tk.Frame(p, bg=WHITE)
        nav.pack(fill="x")
        self._mk_btn(nav, "‹", lambda: self._shift_month(-1), side="left", width=3)
        self.lb_month = tk.Label(nav, text="", bg=WHITE, fg=TEXT,
                                 font=self.f_body, width=14)
        self.lb_month.pack(side="left", expand=True)
        self._mk_btn(nav, "›", lambda: self._shift_month(1), side="left", width=3)
        self._mk_btn(nav, "今天", self._goto_today, side="right", width=5)
        self.btn_recent = self._mk_btn(nav, "近期", self._goto_recent,
                                       side="right", width=5)

        wk = tk.Frame(p, bg=WHITE)
        wk.pack(fill="x", pady=(4, 0))
        for i, s in enumerate(WEEK_CN):
            lab = tk.Label(wk, text=s, bg=WHITE,
                           fg=(RED if i >= 5 else SUB), font=self.f_tiny)
            lab.grid(row=0, column=i, sticky="nsew")
            wk.grid_columnconfigure(i, weight=1)

        self.cal = tk.Frame(p, bg=WHITE)
        self.cal.pack(fill="x")
        for i in range(7):
            self.cal.grid_columnconfigure(i, weight=1)

        self._sep(p, 8)

        # 选中日的待办 + 新建
        dh = tk.Frame(p, bg=WHITE, height=22)
        dh.pack(fill="x")
        dh.pack_propagate(False)
        self.lb_day = tk.Label(dh, text="", bg=WHITE, fg=TEXT, font=self.f_body)
        self.lb_day.pack(side="left")
        self.lb_day_count = tk.Label(dh, text="", bg=WHITE, fg=SUB,
                                     font=self.f_small)
        self.lb_day_count.pack(side="right")

        self.day_in = tk.Frame(p, bg=WHITE, height=DAY_H)
        self.day_in.pack(fill="x")
        self.day_in.pack_propagate(False)

        add = tk.Frame(p, bg=WHITE)
        add.pack(fill="x", pady=(4, 2))
        self.ent = tk.Entry(add, font=self.f_body, bg=CARD, fg=TEXT,
                            relief="flat", highlightbackground=BORDER,
                            highlightthickness=1, insertbackground=BLUE)
        self.ent.pack(side="left", fill="x", expand=True, ipady=3)
        self.ent.bind("<Return>", lambda e: self._add_from_entry())
        self._mk_btn(add, "添加", self._add_from_entry, side="right",
                     width=5, bg=BLUE, fg=WHITE)

        opt = tk.Frame(p, bg=WHITE)
        opt.pack(fill="x", pady=(2, 2))
        tk.Label(opt, text="提醒", bg=WHITE, fg=SUB,
                 font=self.f_small).pack(side="left")
        self.ent_time = tk.Entry(opt, textvariable=self.var_time, width=6,
                                 font=self.f_small, bg=CARD, fg=TEXT,
                                 relief="flat", highlightbackground=BORDER,
                                 highlightthickness=1, justify="center",
                                 disabledbackground=WHITE,
                                 disabledforeground=SUB)
        self.ent_time.pack(side="left", padx=(4, 8), ipady=2)
        self.cb_d0 = tk.Checkbutton(opt, text="当天", variable=self.var_d0,
                                    bg=WHITE, fg=TEXT, font=self.f_small,
                                    activebackground=WHITE, selectcolor=WHITE,
                                    disabledforeground=SUB,
                                    bd=0, highlightthickness=0, padx=0)
        self.cb_d0.pack(side="left")
        self.cb_d1 = tk.Checkbutton(opt, text="提前1天", variable=self.var_d1,
                                    bg=WHITE, fg=TEXT, font=self.f_small,
                                    activebackground=WHITE, selectcolor=WHITE,
                                    disabledforeground=SUB,
                                    bd=0, highlightthickness=0, padx=0)
        self.cb_d1.pack(side="left", padx=(6, 0))

        tip = tk.Frame(p, bg=WHITE)
        tip.pack(fill="x", pady=(2, 2))
        self.lb_tip = tk.Label(tip, text="点日历选日期 → 输入即可给那天建待办",
                               bg=WHITE, fg=SUB, font=self.f_tiny)
        self.lb_tip.pack(side="left")
        tryit = tk.Label(tip, text="试一次提醒", bg=WHITE, fg=BLUE,
                         font=self.f_tiny, cursor="hand2")
        tryit.pack(side="right")
        tryit.bind("<Button-1>", lambda e: self._demo_reminder())

    # ------------------------------------------------------------ 小工具
    def _mk_btn(self, parent, text, cmd, side="left", width=3,
                bg=CARD, fg=TEXT):
        b = tk.Label(parent, text=text, width=width, bg=bg, fg=fg,
                     font=self.f_small, cursor="hand2", anchor="center")
        b._nodrag = True            # 头部按钮不要被当成「拖动胶囊」
        b.pack(side=side, padx=2)
        b.bind("<Button-1>", lambda e: cmd())
        b.bind("<Enter>", lambda e: b.configure(bg=BLUE_SOFT))
        b.bind("<Leave>", lambda e: b.configure(bg=bg))
        return b

    def _sep(self, parent, pady):
        f = tk.Frame(parent, bg=BORDER, height=1)
        f.pack(fill="x", pady=pady)

    def _bind_wheel(self, w):
        w.bind("<MouseWheel>", self._on_wheel, add="+")
        for c in w.winfo_children():
            self._bind_wheel(c)

    def _on_wheel(self, e):
        self._tip_hide()            # 列表滚动后，气泡别停在旧位置
        cv = self.recent_cv
        cv.yview_scroll(-1 if e.delta > 0 else 1, "units")

    # ------------------------------------------------------- 悬停看全文
    TIP_DELAY = 300              # 停多久弹出（毫秒）
    TIP_LEAVE = 160              # 移开后延迟判定，避免行内换控件时闪烁
    TIP_WRAP = 300               # 正文自动换行宽度

    def _bind_tip(self, widgets, title, sub=""):
        """整行共用同一个 owner：鼠标在这行的任意控件上都算「还在这行」。"""
        for w in widgets:
            w.bind("<Enter>", lambda e, o=widgets[0], t=title, s=sub:
                   self._tip_schedule(o, t, s), add="+")
            w.bind("<Motion>", lambda e, o=widgets[0], t=title, s=sub:
                   self._tip_schedule(o, t, s), add="+")
            w.bind("<Leave>", lambda e: self._tip_hide_soon(), add="+")

    def _tip_schedule(self, owner, title, sub):
        if self.tip_win is not None and self.tip_owner is owner:
            return                      # 这行的气泡已经显示，不重建
        self._cancel_tip_jobs()
        self.tip_owner, self.tip_title, self.tip_sub = owner, title, sub
        self.tip_job = self.root.after(self.TIP_DELAY, self._tip_show)

    def _cancel_tip_jobs(self):
        for attr in ("tip_job", "tip_hide_job"):
            job = getattr(self, attr, None)
            if job:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass
                setattr(self, attr, None)

    def _tip_hide_soon(self):
        if self.tip_owner is None or self.tip_hide_job:
            return
        self.tip_hide_job = self.root.after(self.TIP_LEAVE,
                                            self._tip_check_leave)

    def _tip_check_leave(self):
        self.tip_hide_job = None
        o = self.tip_owner
        if o is not None:
            try:
                if o.winfo_exists():
                    px, py = self.root.winfo_pointerxy()
                    x, y = o.winfo_rootx(), o.winfo_rooty()
                    if x <= px <= x + o.winfo_width() and \
                       y <= py <= y + o.winfo_height():
                        return          # 只是从标题挪到了右侧日期，仍在同一行
            except Exception:
                pass
        self._tip_hide()

    def _tip_hide(self):
        self._cancel_tip_jobs()
        self.tip_owner = self.tip_title = None
        self.tip_sub = ""
        if self.tip_win is not None:
            try:
                self.tip_win.destroy()
            except Exception:
                pass
            self.tip_win = None

    def _tip_show(self):
        self.tip_job = None
        o, title, sub = self.tip_owner, self.tip_title, self.tip_sub
        if not title or o is None:
            return
        try:
            if not o.winfo_exists() or not o.winfo_ismapped():
                self.tip_owner = None
                return
            px, py = self.root.winfo_pointerxy()
            x, y = o.winfo_rootx(), o.winfo_rooty()
            if not (x <= px <= x + o.winfo_width() and
                    y <= py <= y + o.winfo_height()):
                self.tip_owner = None   # 已经移开，别弹
                return
        except Exception:
            return
        if self.tip_win is not None:
            try:
                self.tip_win.destroy()
            except Exception:
                pass
            self.tip_win = None
        w = tk.Toplevel(self.root)
        w.overrideredirect(True)
        try:
            w.attributes("-topmost", True)
        except Exception:
            pass
        w.configure(bg=BORDER)
        box = tk.Frame(w, bg=CARD)
        box.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Label(box, text=title, bg=CARD, fg=TEXT, font=self.f_body,
                 wraplength=self.TIP_WRAP, justify="left", anchor="w",
                 padx=10).pack(fill="x", pady=(8, 2 if sub else 8))
        if sub:
            tk.Label(box, text=sub, bg=CARD, fg=SUB, font=self.f_small,
                     wraplength=self.TIP_WRAP, justify="left", anchor="w",
                     padx=10).pack(fill="x", pady=(0, 8))
        w.update_idletasks()
        tw, th = w.winfo_reqwidth(), w.winfo_reqheight()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        tx = max(4, min(px + 16, sw - tw - 6))
        ty = py + 18
        if ty + th > sh - 6:
            ty = py - th - 12          # 贴近屏幕底部时翻到鼠标上方
        w.geometry("%dx%d+%d+%d" % (tw, th, tx, max(4, ty)))
        self.tip_win = w

    # ------------------------------------------------------------ 折叠 / 展开
    def _expand(self):
        self.collapsed = False
        self.cap.pack_forget()
        self.panel.pack(fill="both", expand=True)
        self.render()
        self._place()

    def _collapse(self):
        if self.collapsed:
            return
        self._tip_hide()
        self.collapsed = True
        self.panel.pack_forget()
        self.cap.pack(fill="x")
        self.render()
        self._place()

    def _toggle(self):
        self._expand() if self.collapsed else self._collapse()

    def _drag_start(self, e):
        if getattr(e.widget, "_nodrag", False):
            self._drag = None
            return
        self._drag = (e.x_root, e.y_root, self.root.winfo_x(),
                      self.root.winfo_y(), False)

    def _drag_move(self, e):
        if not self._drag:
            return
        x0, y0, wx, wy, _ = self._drag
        dx, dy = e.x_root - x0, e.y_root - y0
        if abs(dx) + abs(dy) > 4:
            self._drag = (x0, y0, wx, wy, True)
        if self._drag[4]:
            self.root.geometry("+%d+%d" % (wx + dx, wy + dy))

    def _drag_end(self, e):
        if not self._drag:
            return
        moved = self._drag[4]
        if moved:
            r = self.root
            sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
            w, h = r.winfo_width(), r.winfo_height()
            self.ui["dock_rx"] = max(4, sw - (r.winfo_x() + w))
            self.ui["dock_ry"] = max(4, sh - (r.winfo_y() + h))
            save_data(self.data)
        self._drag = None
        if not moved:
            self._toggle()          # 单击胶囊 = 展开 / 单击头部 = 收起

    def _maybe_autohide(self):
        """展开后点窗口外面自动收回（Windows 全局左键检测）。"""
        if sys.platform != "win32":
            return
        try:
            if not (ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000):
                return

            class P(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

            p = P()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
            x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
            w, h = self.root.winfo_width(), self.root.winfo_height()
            inside = (x <= p.x <= x + w and y <= p.y <= y + h)
            if self.rem_win is not None:
                rx, ry = self.rem_win.winfo_rootx(), self.rem_win.winfo_rooty()
                rw, rh = self.rem_win.winfo_width(), self.rem_win.winfo_height()
                inside = inside or (rx <= p.x <= rx + rw and ry <= p.y <= ry + rh)
            if not inside:
                self._collapse()
        except Exception:
            pass

    # ------------------------------------------------------------ 渲染
    def render(self):
        self._tip_hide()            # 列表要重建，旧气泡的 owner 会失效
        today = dt.date.today()
        act = [t for t in self.data.get("active", []) if isinstance(t, dict)]
        today_n = sum(1 for t in act if parse_date(t.get("due")) == today)
        recent_n = sum(1 for t in act if not parse_date(t.get("due")))
        self._render_gold()
        if self.collapsed:
            self.cap_text.configure(text="待办 %d" % len(act))
            info = ("· 今日 %d" % today_n) if today_n else "· 今日无安排"
            if recent_n:
                info += " · 近期 %d" % recent_n
            self.cap_today.configure(text=info)
            self.cap_date.configure(text="%d/%d 周%s" %
                                    (today.month, today.day,
                                     WEEK_CN[today.weekday()]))
            return

        self.lb_head_date.configure(text="%d年%d月%d日" %
                                    (today.year, today.month, today.day))
        self.lb_recent_count.configure(text="共 %d 条" % len(act))
        self._render_recent(act)
        self._render_calendar(act)
        self._render_day(act)

    def _task_row(self, parent, t, *, compact=False):
        bgc = CARD if not compact else WHITE
        row = tk.Frame(parent, bg=bgc)
        row.pack(fill="x", pady=1)
        full = " ".join(str(t.get("title", "")).split())
        lab, color = due_label(t)
        if t.get("time") and t.get("due"):
            lab = "%s %s" % (lab, t["time"])

        # 先造好右侧控件并先 pack：长标题时它们优先拿空间，
        # 「近期」/日期标签才不会被挤到窗口外面。
        dele = tk.Label(row, text="×", bg=bgc, fg=SUB,
                        font=self.f_body, cursor="hand2", width=2)
        due = tk.Label(row, text=lab, bg=bgc, fg=color, font=self.f_small)
        done = tk.Label(row, text="○", bg=bgc, fg=BLUE,
                        font=self.f_body, cursor="hand2", width=2)
        fixed = (done.winfo_reqwidth() + due.winfo_reqwidth() +
                 dele.winfo_reqwidth() + 16)
        avail = max(90, OPEN_W - 2 * EDGE - 8 - fixed)
        shown = clip_px(full, avail, self.f_body)
        title = tk.Label(row, text=shown, bg=bgc, fg=TEXT,
                         font=self.f_body, anchor="w")

        dele.pack(side="right")
        due.pack(side="right", padx=(2, 2))
        done.pack(side="left", padx=(3, 0))
        title.pack(side="left", fill="x", expand=True)
        done.bind("<Button-1>", lambda e: self._done(t["id"]))
        dele.bind("<Button-1>", lambda e: self._remove(t["id"]))
        for w in (row, done, title, due, dele):
            self._bind_wheel(w)
        if shown != full:            # 只有被截断的标题才挂气泡，短标题不打扰
            self._bind_tip((row, done, title, due, dele), full, lab)

    def _render_recent(self, act):
        for w in self.recent_in.winfo_children():
            w.destroy()
        items = sorted(act, key=sort_key)
        if not items:
            tk.Label(self.recent_in, text="还没有待办，点日历选个日子加一条吧",
                     bg=WHITE, fg=SUB, font=self.f_small,
                     anchor="w").pack(fill="x", pady=8)
        else:
            for t in items:
                self._task_row(self.recent_in, t)
        self.recent_in.update_idletasks()
        self.recent_cv.configure(scrollregion=self.recent_cv.bbox("all"))

    def _shift_month(self, delta):
        m = self.view_m + delta
        y = self.view_y
        while m < 1:
            m += 12
            y -= 1
        while m > 12:
            m -= 12
            y += 1
        self.view_y, self.view_m = y, m
        self._render_calendar(self.data.get("active", []))

    def _goto_today(self):
        d = dt.date.today()
        self.view_y, self.view_m, self.sel = d.year, d.month, d
        self.render()
        self._place()

    def _goto_recent(self):
        """切到「不设日期」模式：输入框回车直接产出近期待办。"""
        self.sel = None
        self.render()
        self._place()
        self.ent.focus_set()

    def _render_calendar(self, act):
        self.lb_month.configure(text="%d年%d月" % (self.view_y, self.view_m))
        for w in self.cal.winfo_children():
            w.destroy()
        marked = {}
        for t in act:
            if not isinstance(t, dict):
                continue
            d = parse_date(t.get("due"))
            if d:
                marked[d] = marked.get(d, 0) + 1
        today = dt.date.today()
        weeks = calendar.monthcalendar(self.view_y, self.view_m)
        while len(weeks) < 6:
            weeks.append([0] * 7)
        for r, week in enumerate(weeks[:6]):
            for cidx, day in enumerate(week):
                if day == 0:
                    cell = tk.Frame(self.cal, bg=WHITE, height=CELL_H)
                    cell.grid(row=r, column=cidx, sticky="nsew", padx=1, pady=1)
                    cell.grid_propagate(False)
                    continue
                d = dt.date(self.view_y, self.view_m, day)
                is_sel = (d == self.sel)
                is_today = (d == today)
                has = d in marked
                bgc = BLUE if is_sel else (BLUE_SOFT if is_today else
                                           (CARD if has else WHITE))
                fgc = WHITE if is_sel else (RED if cidx >= 5 else TEXT)
                cell = tk.Frame(self.cal, bg=bgc, height=CELL_H, cursor="hand2")
                cell.grid(row=r, column=cidx, sticky="nsew", padx=1, pady=1)
                cell.grid_propagate(False)
                num = tk.Label(cell, text=str(day), bg=bgc, fg=fgc,
                               font=(self.f_cal_bold if is_today else self.f_cal))
                num.pack(expand=True, fill="both")
                dot = tk.Canvas(cell, width=8, height=4, bg=bgc,
                                highlightthickness=0, bd=0)
                col = WHITE if is_sel else (BLUE if has else bgc)
                dot.create_oval(2, 0, 6, 4, fill=col, outline="")
                dot.pack()
                for w in (cell, num, dot):
                    w.bind("<Button-1>",
                           lambda e, dd=d: self._select_day(dd))

    def _select_day(self, d):
        self.sel = d
        self.view_y, self.view_m = d.year, d.month
        self.render()
        self.ent.focus_set()

    def _render_day(self, act):
        no_date = self.sel is None
        if no_date:
            self.lb_day.configure(text="近期待办 · 不设日期")
            items = [t for t in act if not parse_date(t.get("due"))]
            self.lb_tip.configure(text="直接输入回车 → 加入近期待办（不提醒）")
            state = "disabled"
        else:
            self.lb_day.configure(text=cn_date(self.sel))
            items = [t for t in act if parse_date(t.get("due")) == self.sel]
            self.lb_tip.configure(text="点日历选日期；点「近期」建不设日期的待办")
            state = "normal"
        self.lb_day_count.configure(text="%d 项" % len(items))
        # 没日期就谈不上「当天/提前一天」，把提醒控件灰掉避免误解
        self.ent_time.configure(state=state)
        self.cb_d0.configure(state=state)
        self.cb_d1.configure(state=state)
        for w in self.day_in.winfo_children():
            w.destroy()
        if not items:
            msg = ("还没有近期待办，在下面输入回车即可" if no_date
                   else "这天还没有安排")
            tk.Label(self.day_in, text=msg, bg=WHITE, fg=SUB,
                     font=self.f_small, anchor="w").pack(fill="x", pady=6)
        else:
            ordered = sorted(items, key=lambda x: parse_time(x.get("time")))
            # 最多占两行：要么两条，要么一条 + 「另有 N 项」，避免被裁切
            shown_items = ordered[:1] if len(items) > 2 else ordered[:2]
            for t in shown_items:
                self._task_row(self.day_in, t, compact=True)
            if len(items) > len(shown_items):
                tk.Label(self.day_in,
                         text="…另有 %d 项，见上方列表" %
                              (len(items) - len(shown_items)),
                         bg=WHITE, fg=SUB, font=self.f_tiny,
                         anchor="w").pack(fill="x")

    # ------------------------------------------------------------ 增删改
    def _remind_value(self):
        d0, d1 = bool(self.var_d0.get()), bool(self.var_d1.get())
        if d0 and d1:
            return "both"
        if d0:
            return "day"
        if d1:
            return "eve"
        return "none"

    def _add_from_entry(self):
        title = " ".join(self.ent.get().split())
        if not title:
            self.ent.focus_set()
            return
        # sel is None = 近期待办：不写 due/time/remind/notified，也就不提醒
        item = {"id": new_id(), "title": title, "progress": 0,
                "createdAt": int(time.time() * 1000), "doneAt": None}
        if self.sel is not None:
            tm = self.var_time.get().strip() or DEFAULT_TIME
            # 刚建完就弹「今天到期」很打扰：把已经过去的触发点标记为已提醒，
            # 未来时刻（比如下午 3 点）到点仍会正常提醒。
            now = dt.datetime.now()
            notified = {}
            if self.sel == now.date() and parse_time(tm) <= now.time():
                notified["d0"] = iso(self.sel)
            prev = self.sel - dt.timedelta(days=1)
            if prev == now.date() and parse_time(tm) <= now.time():
                notified["d1"] = iso(prev)
            item.update({"due": iso(self.sel), "time": tm,
                         "remind": self._remind_value(),
                         "notified": notified})
        self.data.setdefault("active", []).insert(0, item)
        save_data(self.data)
        self.ent.delete(0, "end")
        self.render()
        self._place()

    def _find(self, tid):
        for lst in ("active", "done"):
            for t in self.data.get(lst, []):
                if t.get("id") == tid:
                    return t, lst
        return None, None

    def _done(self, tid):
        t, lst = self._find(tid)
        if not t or lst != "active":
            return
        t["progress"] = 100
        t["doneAt"] = int(time.time() * 1000)
        self.data["active"].remove(t)
        self.data.setdefault("done", []).insert(0, t)
        save_data(self.data)
        self.render()
        self._place()

    def _remove(self, tid):
        t, lst = self._find(tid)
        if not t:
            return
        # 界面上的删除也留一份后悔药（命令行 remove 本来就有 backup-cli）
        try:
            with open(DATA_FILE + ".backup-gui", "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1)
        except Exception as e:
            log("写删除备份失败：%r" % e)
        self.data[lst].remove(t)
        save_data(self.data)
        log("删除待办：%s（备份 data.json.backup-gui）" % clip(t.get("title", ""), 20))
        self.render()
        self._place()

    # ------------------------------------------------------------ 提醒
    def _demo_reminder(self):
        """界面上的「试一次提醒」，用来当场确认弹窗和提示音是否正常。"""
        self.rem_queue.append(("演示：到点会弹窗并响一声，可点「标记完成」",
                               "今天", dt.date.today(), "", "演示"))
        self._pump_reminder()

    def _check_reminders(self):
        now = dt.datetime.now()
        changed = False
        for t in self.data.get("active", []):
            if not isinstance(t, dict):
                continue
            due = parse_date(t.get("due"))
            if not due:
                continue
            rule = t.get("remind", "both")
            if rule == "none":
                continue
            tm = parse_time(t.get("time"))
            if not isinstance(t.get("notified"), dict):
                t["notified"] = {}
            n = t["notified"]
            # 文案按「截止日相对今天」算，不能写死「明天」：
            # 开机晚了补发「提前一天」的那条时，截止日可能已经是今天了。
            gap = (due - now.date()).days
            label = ("已逾期" if gap < 0 else "今天" if gap == 0 else
                     "明天" if gap == 1 else cn_date(due))
            for key, delta, kind in (("d1", 1, "提前1天"), ("d0", 0, "当天")):
                if (rule == "day" and key == "d1") or \
                   (rule == "eve" and key == "d0"):
                    continue
                day = due - dt.timedelta(days=delta)
                fire = dt.datetime.combine(day, tm)
                age = (now - fire).total_seconds()
                # 到点即提醒；开机晚了 20 小时内补发一次，更久就当过期不打扰
                if age < 0 or age > 20 * 3600:
                    continue
                stamp = iso(day)
                if n.get(key) == stamp:
                    continue
                n[key] = stamp
                changed = True
                self.rem_queue.append((t.get("title", "待办"), label,
                                       due, t.get("id"), kind))
        if changed:
            save_data(self.data)
            log("排入 %d 条提醒" % len(self.rem_queue))
        self._pump_reminder()

    def _pump_reminder(self):
        if self.rem_win is not None or not self.rem_queue:
            return
        title, label, due, tid, kind = self.rem_queue.pop(0)
        r = self.root
        w = tk.Toplevel(r)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=BORDER)
        box = tk.Frame(w, bg=WHITE)
        box.pack(fill="both", expand=True, padx=1, pady=1)
        bar = tk.Frame(box, bg=BLUE, height=4)
        bar.pack(fill="x")
        tk.Label(box, text="●  %s提醒 · %s到期" % (kind, label),
                 bg=WHITE, fg=BLUE, font=self.f_body,
                 anchor="w").pack(fill="x", padx=12, pady=(8, 2))
        # 长标题不截断，自动换行展示全文（窗口高度随内容自适应）
        tk.Label(box, text=" ".join(str(title).split()), bg=WHITE, fg=TEXT,
                 font=self.f_title, anchor="w", wraplength=270,
                 justify="left").pack(fill="x", padx=12)
        tk.Label(box, text=cn_date(due), bg=WHITE, fg=SUB,
                 font=self.f_small, anchor="w").pack(fill="x", padx=12, pady=(2, 6))
        btns = tk.Frame(box, bg=WHITE)
        btns.pack(fill="x", padx=12, pady=(0, 10))
        self._mk_btn(btns, "  知道了  ", self._close_reminder,
                     side="right", width=10)
        self._mk_btn(btns, "  标记完成  ",
                     lambda: (self._done(tid), self._close_reminder()),
                     side="right", width=11, bg=BLUE, fg=WHITE)
        w.update_idletasks()
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        rx, ry = self._anchor()
        ww, wh = 304, w.winfo_reqheight() + 2
        # 面板展开时把提醒放到面板上方，别压住日历和输入框
        if self.collapsed:
            wy = max(4, sh - wh - ry - CAP_H - 12)
        else:
            wy = max(4, r.winfo_rooty() - wh - 10)
        w.geometry("%dx%d+%d+%d" % (ww, wh, max(4, sw - ww - rx), wy))
        self.rem_win = w
        try:
            import winsound
            winsound.PlaySound("SystemAsterisk",
                               winsound.SND_ALIAS | winsound.SND_ASYNC)
        except Exception:
            pass
        log("弹出提醒：%s / %s" % (label, title))
        w.after(60000, self._close_reminder)

    def _close_reminder(self):
        if self.rem_win is not None:
            try:
                self.rem_win.destroy()
            except Exception:
                pass
            self.rem_win = None
        self.root.after(500, self._pump_reminder)

    # ------------------------------------------------------------ 金价展示
    def _render_gold(self):
        g = self.gold
        if not GOLD_ON:
            self.cap_gold_price.configure(text="--", fg=SUB)
            self.cap_gold_chg.configure(text="已关闭", fg=SUB)
            self.cap_gold_time.configure(text="")
            self.lb_gold_head.configure(text="%s  行情已关闭" % GOLD_NAME,
                                        fg=SUB)
            self.lb_gold_head_chg.configure(text="")
            self.lb_gold_head_tip.configure(text="--nogold")
            return
        if not g:
            self.cap_gold_price.configure(text="--", fg=SUB)
            self.cap_gold_chg.configure(text="连接中…", fg=SUB)
            self.cap_gold_time.configure(text="")
            self.lb_gold_head.configure(text="%s  行情连接中…" % GOLD_NAME,
                                        fg=SUB)
            self.lb_gold_head_chg.configure(text="")
            self.lb_gold_head_tip.configure(text="点此刷新")
            return
        arrow, color = gold_dir(g)
        price = "%.2f" % g["price"]
        rate = g.get("rate") or ""
        chg = ("%s %s" % (arrow, rate)).strip() if rate else arrow
        stale = (bool(g.get("cached")) or
                 time.time() - (g.get("fetched") or 0) > 180)
        tm = time.strftime("%H:%M", time.localtime(
            g.get("ts") or g.get("fetched") or time.time()))
        shown = color if not stale else SUB
        self.cap_gold_price.configure(text=price, fg=shown)
        self.cap_gold_chg.configure(text=chg, fg=shown)
        self.cap_gold_time.configure(text=tm + ("*" if stale else ""))
        self.lb_gold_head.configure(text="浙商金 %s 元/克" % price, fg=TEXT)
        self.lb_gold_head_chg.configure(text=chg, fg=shown)
        self.lb_gold_head_tip.configure(
            text="%s%s · 点此刷新" % (tm, " 缓存" if stale else ""))

    def _gold_refresh(self):
        """后台线程取数，不阻塞 Tk 主循环；点行情条可手动触发。"""
        if not GOLD_ON or self._gold_busy:
            return
        self._cancel_gold_timer()
        self._gold_busy = True
        threading.Thread(target=lambda: self.gold_q.put(fetch_gold()),
                         daemon=True).start()
        self.root.after(300, lambda: self._drain_gold(0))

    def _drain_gold(self, tries):
        """把线程结果送回主线程再改 UI（tkinter 不允许跨线程操作控件）。"""
        try:
            g = self.gold_q.get_nowait()
        except queue.Empty:
            if tries < 30:                       # 最多等约 6 秒（配合 4s 超时 / 5s 间隔）
                self.root.after(200, lambda: self._drain_gold(tries + 1))
            else:
                self._gold_busy = False
                self._schedule_gold(False)
            return
        self._gold_busy = False
        if g:
            g.pop("cached", None)
            self.gold = g
            write_gold_cache(g)
            if not self._gold_ok:
                log("金价已接入：%.2f 元/克" % g["price"])
            self._gold_ok = True
        else:
            if self.gold:
                self.gold["cached"] = True       # 断网时保留上次报价并标注
            if self._gold_ok:
                log("金价刷新失败，继续显示上次报价")
        self._render_gold()
        self._schedule_gold(bool(g))

    def _schedule_gold(self, ok):
        if not GOLD_ON:
            return
        self._cancel_gold_timer()
        self._gold_timer = self.root.after(
            (GOLD_INTERVAL if ok else GOLD_RETRY) * 1000, self._gold_refresh)

    def _cancel_gold_timer(self):
        if self._gold_timer:
            try:
                self.root.after_cancel(self._gold_timer)
            except Exception:
                pass
            self._gold_timer = None

    # ------------------------------------------------------------ 轮询
    def _poll(self):
        self._tick += 1
        m = _stat()
        if m is not None and _MTIME[0] is not None and m != _MTIME[0]:
            fresh = load_data()
            if fresh is None:
                pass        # 读失败：保留内存数据，基线不刷新，下一拍再试
            else:
                self.data = fresh
                self.ui = self.data.setdefault("ui", {})
                _MTIME[0] = _stat()
                try:
                    self.render()
                except Exception as e:
                    log("热加载重绘失败：%r" % e)
                log("外部改动，自动重载")
        if self._tick % 67 == 0:            # 提醒检查约每 20 秒（与金价 5 秒刷新彼此独立）
            self._check_reminders()
        if not self.collapsed and self._tick % 2 == 0:
            self._maybe_autohide()
        self.root.after(300, self._poll)

    def quit(self):
        self._cancel_gold_timer()
        self._tip_hide()
        try:
            if os.path.exists(RUNNING_FILE):
                os.remove(RUNNING_FILE)
        except Exception:
            pass
        log("退出")
        self.root.destroy()


def main():
    if "--version" in sys.argv or "-V" in sys.argv:
        print("%s %s (build %s)%s" % (APP_NAME, VERSION, BUILD,
                                      " [exe]" if FROZEN else ""))
        print("data dir: %s" % APP_DIR)
        return

    # Windows 高 DPI：不声明的话 winfo_screenwidth 会拿到缩放后的值，位置会偏
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    measure = "--measure" in sys.argv
    if not measure:
        # 读不出数据就别启动：否则内存里是空的，之后任何一次保存都会清空待办
        for _ in range(3):
            if load_data() is not None:
                break
            time.sleep(0.4)
        else:
            log("data.json 持续读失败，拒绝启动以保护待办数据")
            return
        if not acquire_lock():
            return
        log("启动 todo_dock %s（pid %d）" % (BUILD, os.getpid()))
    root = tk.Tk()
    app = Dock(root)
    if measure:
        app.collapsed = False
        app.panel.pack(fill="both", expand=True)
        app.render()
        app._place()
        root.update_idletasks()
        print("展开态 content 需求高度 =", app.content.winfo_reqheight())
        print("展开态窗口 geometry    =", root.geometry())
        root.destroy()
        return
    app.cap.pack(fill="x")                 # 首次启动 = 右下角小胶囊
    app._place()
    try:
        root.mainloop()
    finally:
        try:
            if os.path.exists(RUNNING_FILE):
                os.remove(RUNNING_FILE)
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        _err = traceback.format_exc()
        log("未捕获异常，启动失败：\n%s" % _err)
        try:
            print(_err, file=sys.stderr)
        except Exception:
            pass
        # 打包成 --windowed 的 exe 后没有控制台，静默退出会让人以为程序坏了，
        # 所以冻结环境下把最后一行错误弹出来，并指明日志位置。
        if FROZEN:
            try:
                import tkinter.messagebox as _mb
                _r = tk.Tk()
                _r.withdraw()
                _mb.showerror(
                    APP_TITLE + " 启动失败",
                    "详情已写入日志：\n%s\n\n%s" %
                    (LOG_FILE, _err.strip().splitlines()[-1]),
                    parent=_r)
                _r.destroy()
            except Exception:
                pass
        sys.exit(1)
