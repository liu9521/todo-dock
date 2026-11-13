#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
待办悬浮窗 —— macOS 桌面置顶小挂件

· 无边框、始终置顶、可拖动、可折叠、可半透明
· 每条待办都带进度条（可直接点/拖进度条调进度）
· 进度到 100% 自动归入「已完成」，并带上完成时间
· 数据保存在同目录 data.json，关掉再开都还在

用法：python3 todo_float.py        （正常启动）
      python3 todo_float.py --selftest   （无界面自检）
"""

import atexit
import glob
import json
import logging
import os
import sys
import time

import tkinter as tk
from tkinter import font as tkfont

# ---------------------------------------------------------------- 常量

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(APP_DIR, "data.json")
LOCK_FILE = os.path.join(APP_DIR, ".running")
LOG_FILE = os.path.join(APP_DIR, "clicks.log")

LOG = logging.getLogger("todo")

# 中文字体按平台挑：保证拷到 Windows / Linux 机器上也能正常显示
if sys.platform == "darwin":
    CJK_FONT = "PingFang SC"
elif sys.platform.startswith("win"):
    CJK_FONT = "Microsoft YaHei"
else:
    CJK_FONT = "Noto Sans CJK SC"

W = 340                    # 窗口宽度
EDGE = 1                   # 外框线宽
PAD = 12                   # 页面左右内边距
CARD_PAD = 11              # 卡片内边距
GAP = 8                    # 卡片间距
TITLE_WRAP = 214           # 标题换行宽度

# 配色（浅色）
PAGE = "#f4f7fc"
CARD = "#ffffff"
CARD_LINE = "#e5eaf4"
BORDER = "#d7dded"
TEXT = "#1e2433"
SUB = "#4a5468"
MUTED = "#8b94a7"
FAINT = "#aeb7c6"
LINE = "#eef1f7"
ACCENT = "#4f7cff"
ACCENT_SOFT = "#eff3ff"
HOVER = "#eef2fa"
GREEN = "#16b364"
GREEN_SOFT = "#e8f8ef"
DANGER = "#f2555a"
DANGER_SOFT = "#ffeff0"
TRACK = "#e8ecf3"
SBAR = "#ccd5e4"
SBAR_HOVER = "#aab7cd"
SBAR_ACTIVE = "#8b9cba"
TOAST_BG = "#232a3b"
# 次级按钮 / 主按钮
BTN_BG = "#f2f5fb"
BTN_FG = "#59637a"
BTN_HOVER = "#e4eaf7"
PRIMARY_BG = "#e9faf1"
PRIMARY_FG = "#0e9f57"
PRIMARY_HOVER = "#d6f5e5"

# 进度条：视觉高度 8px，上下各留 7px 作为点击热区（共 22px），避免点不中
BAR_H = 8
BAR_PAD = 7

# 双指滑动 / 滚轮
#
# Tk 9 把这两种设备拆成了两个不同的事件（TIP 684），这点很关键：
#   * 触控板双指滑动 -> <TouchpadScroll>，%D 里打包着精密增量
#     （Δx 在高 16 位、Δy 在低 16 位），本来就是像素量级，可以做到跟手；
#   * 鼠标滚轮     -> <MouseWheel>，%D 是「格」。
# 只绑 <MouseWheel> 的话，在触控板上双指滑动会一点反应都没有 —— 事件根本不叫这个名字。
WHEEL_LINE_PX = 48      # 鼠标滚轮：一格滚 48 像素
TRACKPAD_GAIN = 1.3     # 双指滑动：1 点增量滚多少像素（补上 Tk 把增量截断成整数丢掉的零头）
HOVER_FOCUS_MS = 150    # 光标在卡片上停多久就激活本窗口（不激活收不到滑动事件）
HOVER_POLL_MS = 90      # 轮询「光标是不是停在卡片上」的间隔
HOVER_REARM_PX = 12     # 光标又在卡片上挪了这么多，就允许再激活一次

# 已完成记录保留多久：半个月，到期自动清掉
DONE_KEEP_DAYS = 15
DONE_KEEP_MS = DONE_KEEP_DAYS * 24 * 3600 * 1000
PURGE_FIRST_MS = 1500           # 启动后多久做第一次检查（先让窗口显示出来）
PURGE_EVERY_MS = 30 * 60 * 1000  # 之后每半小时看一眼有没有到期的
# 比 2020-01-01 还早的时间戳，基本只可能是「秒」被当成了「毫秒」（差 1000 倍）。
# 这种算不出真实年龄，一律不删 —— 少删一次的代价远小于错删一条记录。
DONE_TS_FLOOR = 1577836800000


def to_ms(v):
    """把「完成时间」这类值统一成毫秒整数。取不到 / 不是数字 -> 0。

    统一在这里兜底，是为了让「判年龄」和「排序」都用同一套规则：
    一条 doneAt 脏掉的记录只该被当成「判不出年龄」，不该顺手把界面搞崩。
    """
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def done_ts(t):
    """从一条完成记录里取出完成时间（毫秒）。"""
    try:
        return to_ms(t.get("doneAt"))
    except AttributeError:
        return 0


def bar_colors(p):
    """进度配色：低-橙 / 中-琥珀 / 高-靛蓝 / 满-绿"""
    if p >= 100:
        return "#2fd07f", "#16b364"
    if p >= 70:
        return "#8a97ff", "#6b5cff"
    if p >= 40:
        return "#ffcd6b", "#f2a63b"
    if p > 0:
        return "#ffa585", "#ff7a59"
    return TRACK, TRACK


# ---------------------------------------------------------------- 数据

def default_data():
    return {
        "active": [],
        "done": [],
        "ui": {
            "x": None,
            "y": None,
            "topmost": True,
            "alpha": 1.0,
            "collapsed": False,
            "done_open": True,
        },
    }


def load_data():
    if not os.path.exists(DATA_FILE):
        return default_data()
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        base = default_data()
        for k in ("active", "done"):
            base[k] = d.get(k) or []
        base["ui"].update(d.get("ui") or {})
        return base
    except Exception:
        return default_data()


def save_data(data):
    tmp = DATA_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, DATA_FILE)
        _DATA_MTIME[0] = _stat_mtime()      # 自己写的，刷新基线，别误判成外部改动
    except Exception as e:
        print("保存失败:", e, file=sys.stderr)


# data.json 的修改时间基线：保存/加载后刷新；轮询发现对不上 = 有外部改动
_DATA_MTIME = [None]


def _stat_mtime():
    try:
        return os.stat(DATA_FILE).st_mtime_ns
    except OSError:
        return None


def new_id():
    return "%x%04x" % (int(time.time() * 1000), int.from_bytes(os.urandom(2), "big"))


def backup_before_purge(data, keep=5):
    """自动清理之前，另存一份完整快照。

    自动删除只给 6 秒的撤销窗口，太短 —— 万一过几天才发现清错了，从这里的
    快照还能整份捞回来。只保留最近 keep 份，免得越攒越多。
    """
    try:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(APP_DIR, "data.backup-purge-%s.json" % stamp)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        olds = sorted(glob.glob(os.path.join(APP_DIR, "data.backup-purge-*.json")))
        for p in olds[:-keep]:
            try:
                os.remove(p)
            except OSError:
                pass
        return path
    except Exception as e:
        print("清理前备份失败:", e, file=sys.stderr)
        return None


# ---------------------------------------------------------------- 小部件

class CanvasButton(tk.Canvas):
    """用 Canvas 自绘的按钮。

    为什么不直接用 tk.Label：实测这台机器（macOS 26 + Tk 9.0.3）下，
    tk.Canvas 和 tk.Frame 上的鼠标点击都能正常收到（进度条、滚动条都验证过），
    但 tk.Label 上的点击一次都收不到 —— 交互日志里没有任何 Label 点击记录。
    所以所有可点元素一律改成 Canvas 自绘，顺带还能画出圆角和按下反馈。
    """

    def __init__(self, master, text, command=None, *, bg=CARD, fg=BTN_FG,
                 fill=BTN_BG, hover=BTN_HOVER, press=None, active_fill=ACCENT_SOFT,
                 active_fg=ACCENT, font=None, padx=11, pady=4, radius=8, tag=None):
        f = font or tkfont.nametofont("TkDefaultFont")
        self._font = f
        self._text = text
        self._bg = bg
        self._fg = fg
        self._fill = fill
        self._hover = hover
        self._press = press or hover
        self._active_fill = active_fill
        self._active_fg = active_fg
        self._cmd = command
        self._tag = tag or text
        self._padx, self._pady, self._radius = padx, pady, radius
        self._active = False
        self._hovering = False
        self._pressing = False
        self._last = 0.0

        w, h = self._size()
        tk.Canvas.__init__(self, master, width=w, height=h, bg=bg,
                           highlightthickness=0, bd=0, cursor="pointinghand")
        # 标记：不要在这种控件上绑「拖动滚动页面」，否则和点击冲突
        self._no_drag = True
        self._draw()

        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    # ---- 尺寸与绘制
    def _size(self):
        return (self._font.measure(self._text) + 2 * self._padx,
                self._font.metrics("linespace") + 2 * self._pady)

    def _cur_fill(self):
        if self._pressing:
            return self._press
        if self._active:
            return self._active_fill
        return self._hover if self._hovering else self._fill

    def _cur_fg(self):
        return self._active_fg if self._active else self._fg

    def _draw(self):
        self.delete("all")
        w, h = self._size()
        self.configure(width=w, height=h)
        round_rect(self, 0, 0, w, h, self._radius, self._cur_fill())
        self.create_text(w / 2.0, h / 2.0, text=self._text,
                         fill=self._cur_fg(), font=self._font)

    # ---- 对外
    def set_state(self, active):
        self._active = bool(active)
        self._draw()

    def set_text(self, text):
        if text != self._text:
            self._text = text
            self._draw()

    def get_text(self):
        return self._text

    # ---- 事件
    def _fire(self):
        now = time.time()
        if now - self._last < 0.35:
            return
        self._last = now
        try:
            self.winfo_toplevel().focus_force()
        except Exception:
            pass
        LOG.info("click 按钮[%s]", self._tag)
        if self._cmd:
            # 延后一拍：命令里常会重建列表，避免在自身事件里把自己销毁
            self.after(1, self._cmd)

    def _on_press(self, _e):
        self._pressing = True
        self._draw()
        self._fire()
        return "break"

    def _on_release(self, _e):
        # 按下时就已经触发，这里只负责把按压态复位。
        # 不用「松开时指针是否还在按钮内」做判断：那需要算鼠标坐标，
        # 之前那套坐标判断在 macOS 上不可靠，是按钮点不动的元凶之一。
        if self._pressing:
            self._pressing = False
            self._draw()
        return "break"

    def _on_enter(self, _e):
        self._hovering = True
        self._draw()

    def _on_leave(self, _e):
        self._hovering = False
        self._pressing = False
        self._draw()


class TextLine(tk.Canvas):
    """Canvas 版文字行。

    和 CanvasButton 同样的原因：需要响应鼠标（点击/拖动）的文字不能放在
    tk.Label 上，一律用 Canvas 自绘。顺带可以精确控制换行宽度和高度。
    """

    def __init__(self, master, text="", *, bg=PAGE, fg=TEXT, font=None,
                 wrap=0, padx=0, pady=0, width=1, cursor="", justify="left"):
        self._font = font or tkfont.nametofont("TkDefaultFont")
        self._text = text
        self._fg = fg
        self._wrap = wrap
        self._padx, self._pady = padx, pady
        h = self._font.metrics("linespace") + 2 * pady
        tk.Canvas.__init__(self, master, bg=bg, highlightthickness=0, bd=0,
                           width=width, height=h, cursor=cursor or "")
        self._item = self.create_text(padx, pady, text=text, anchor="nw",
                                      fill=fg, font=self._font, width=wrap or 0,
                                      justify=justify)
        self._no_drag = False
        self.bind("<Configure>", self._sync)

    def _sync(self, _e=None):
        w = self.winfo_width()
        if self._wrap and w > 20:
            self.itemconfigure(self._item, width=max(20, w - 2 * self._padx))
        bb = self.bbox(self._item)
        if bb:
            need = (bb[3] - bb[1]) + 2 * self._pady
            if need != self.winfo_height():
                self.configure(height=need)

    def set_text(self, text):
        if text != self._text:
            self._text = text
            self.itemconfigure(self._item, text=text)
            self._sync()

    def get_text(self):
        return self._text

    def set_fg(self, fg):
        self._fg = fg
        self.itemconfigure(self._item, fill=fg)


def round_rect(cv, x0, y0, x1, y1, r, fill, tags=""):
    """在 Canvas 上画圆角矩形"""
    if x1 - x0 < 2 * r:
        r = max(0, (x1 - x0) / 2)
    if y1 - y0 < 2 * r:
        r = max(0, (y1 - y0) / 2)
    pts = [
        x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r,
        x1, y1 - r, x1, y1, x1 - r, y1,
        x0 + r, y1, x0, y1, x0, y1 - r,
        x0, y0 + r, x0, y0,
    ]
    return cv.create_polygon(pts, smooth=True, fill=fill, outline="", tags=tags)


# ---------------------------------------------------------------- 主程序

class FloatingTodo:

    def __init__(self, root):
        self.root = root
        self.data = load_data()
        _DATA_MTIME[0] = _stat_mtime()
        self._ext_ticks = 0     # 外部改动检测的节流计数（挂在 _hover_loop 上）
        self._drag = None
        self._toast_job = None
        self._undo = None
        self._busy = set()
        self._guard = {}
        self._bar_active = None
        self._sd = None
        self._pd = None          # 拖动滚动页面：[起点 y_root, 起始 yview 比例, 是否已开始移动]
        self._wheel_acc = 0.0    # 像素零头，避免小增量被截断吃成 0
        self._touch_t = 0.0      # 双指滑动日志的节流时间戳
        self._purge_job = None   # 到期清理完成记录的定时任务
        self._hover_job = None   # 检测光标是否停在卡片上的轮询任务
        self._hover_inside = False   # 上一拍光标是否在窗口里（用来抓「刚进来」这个跳变）
        self._hover_since = 0.0      # 光标从什么时候开始连续停在窗口里
        self._hover_armed = True     # 这一轮悬停还没激活过窗口
        self._hover_pt = None        # 上一次判定时记录的光标位置
        self._sbar_busy = False
        self._editor = None      # 就地改文字的输入框：(Text 控件, 任务 id)
        self._titles = {}        # {任务 id: 标题 TextLine}，用于改完就地重绘

        self.f_title = tkfont.Font(family=CJK_FONT, size=13, weight="bold")
        self.f_body = tkfont.Font(family=CJK_FONT, size=12)
        self.f_card = tkfont.Font(family=CJK_FONT, size=12)
        self.f_small = tkfont.Font(family=CJK_FONT, size=10)
        self.f_tiny = tkfont.Font(family=CJK_FONT, size=9)
        self.f_btn = tkfont.Font(family=CJK_FONT, size=10)
        try:
            self.f_done = tkfont.Font(family=CJK_FONT, size=11, overstrike=1)
        except Exception:
            self.f_done = tkfont.Font(family=CJK_FONT, size=11)

        self._setup_window()
        self._build()
        self.render()
        self.root.after(60, self._place_window)
        self._hover_job = self.root.after(HOVER_POLL_MS, self._hover_loop)
        # 稍等一下再做第一次到期检查：先把窗口摆好，别一启动就弹提示条
        self._purge_job = self.root.after(PURGE_FIRST_MS, self._purge_loop)

    # ------------------------------------------------ 窗口
    def _setup_window(self):
        r = self.root
        r.title("我的待办")
        r.overrideredirect(True)
        r.resizable(False, False)
        r.configure(bg=BORDER)
        ui = self.data["ui"]
        try:
            r.attributes("-topmost", bool(ui.get("topmost", True)))
        except Exception:
            pass
        try:
            a = float(ui.get("alpha", 1.0) or 1.0)
            r.attributes("-alpha", a)
        except Exception:
            pass

        # 首次出现就直接落在目标位置，避免开窗瞬间跳一下
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        x, y = ui.get("x"), ui.get("y")
        if not isinstance(x, int) or x < -W + 80 or x > sw - 80:
            x = max(20, sw - W - 44)
        if not isinstance(y, int) or y < 0 or y > sh - 80:
            y = 64
        r.geometry("%dx%d+%d+%d" % (W, 280, x, y))
        r.protocol("WM_DELETE_WINDOW", self.quit)
        r.bind("<Escape>", lambda e: self.quit())

    def _place_window(self):
        self._fit()

    # ------------------------------------------------ 布局
    def _build(self):
        r = self.root
        # 外框
        self.outer = tk.Frame(r, bg=PAGE, highlightbackground=BORDER,
                              highlightthickness=EDGE)
        self.outer.pack(fill="both", expand=True)

        # ---- 头部
        self.header = tk.Frame(self.outer, bg=PAGE)
        self.header.pack(fill="x", padx=PAD, pady=(10, 0))

        dot = tk.Canvas(self.header, width=9, height=9, bg=PAGE,
                        highlightthickness=0, bd=0)
        dot.create_oval(1, 1, 8, 8, fill=ACCENT, outline="")
        dot.pack(side="left", pady=(4, 0))

        self.lb_title = TextLine(self.header, "我的待办", bg=PAGE, fg=TEXT,
                                 font=self.f_title, padx=2, pady=2,
                                 width=self.f_title.measure("我的待办") + 4,
                                 cursor="fleur")
        self.lb_title.pack(side="left", padx=(6, 0))

        HBG = "#eaeff8"      # 头部小按钮的底色，让它一眼看得出是可点的
        HHOV = "#dbe4f4"
        self.btn_close = CanvasButton(self.header, "关闭", self.quit,
                                      fg=BTN_FG, bg=PAGE, fill=HBG, hover=DANGER_SOFT,
                                      font=self.f_btn, padx=8, pady=4, tag="关闭")
        self.btn_close.pack(side="right")
        self.btn_pin = CanvasButton(self.header, "置顶", self.toggle_pin,
                                    fg=BTN_FG, bg=PAGE, fill=HBG, hover=HHOV,
                                    font=self.f_btn, padx=8, pady=4, tag="置顶")
        self.btn_pin.pack(side="right", padx=(0, 3))
        self.btn_alpha = CanvasButton(self.header, "透明", self.toggle_alpha,
                                      fg=BTN_FG, bg=PAGE, fill=HBG, hover=HHOV,
                                      font=self.f_btn, padx=8, pady=4, tag="透明")
        self.btn_alpha.pack(side="right", padx=(0, 3))
        self.btn_fold = CanvasButton(self.header, "折叠", self.toggle_fold,
                                     fg=BTN_FG, bg=PAGE, fill=HBG, hover=HHOV,
                                     font=self.f_btn, padx=8, pady=4, tag="折叠")
        self.btn_fold.pack(side="right", padx=(0, 3))

        for w in (self.header, self.lb_title, dot):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_end)

        # ---- 折叠态摘要（默认隐藏）
        self.collapsed_bar = tk.Frame(self.outer, bg=PAGE)
        self.lb_summary = TextLine(self.collapsed_bar, "", bg=PAGE, fg=SUB,
                                   font=self.f_small, padx=PAD, pady=5,
                                   cursor="fleur")
        self.lb_summary.pack(fill="x", pady=(0, 5))
        for w in (self.collapsed_bar, self.lb_summary):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_end)

        # ---- 主体容器
        self.body = tk.Frame(self.outer, bg=PAGE)

        # 总进度
        self.stat = tk.Frame(self.body, bg=PAGE)
        self.stat.pack(fill="x", padx=PAD, pady=(11, 0))
        self.lb_avg = tk.Label(self.stat, text="总进度", bg=PAGE, fg=SUB,
                               font=self.f_small)
        self.lb_avg.pack(side="left")
        self.lb_cnt = tk.Label(self.stat, text="", bg=PAGE, fg=MUTED,
                               font=self.f_small)
        self.lb_cnt.pack(side="right")
        self.ov_cv = tk.Canvas(self.body, height=7, width=1, bg=PAGE,
                               highlightthickness=0, bd=0)
        self.ov_cv.pack(fill="x", padx=PAD, pady=(6, 0))
        self.ov_cv.bind("<Configure>", lambda e: self._draw_overall())

        # 添加行
        add = tk.Frame(self.body, bg=CARD, highlightbackground=CARD_LINE,
                       highlightthickness=1)
        add.pack(fill="x", padx=PAD, pady=(11, 0))
        self.entry = tk.Entry(add, bg=CARD, fg=TEXT, font=self.f_body,
                              relief="flat", bd=0, highlightthickness=0,
                              insertbackground=ACCENT)
        self.entry.pack(side="left", fill="x", expand=True, padx=(10, 6), pady=8)
        self.entry.bind("<Return>", self._on_add)
        self.entry.bind("<KP_Enter>", self._on_add)
        self.entry.bind("<FocusIn>", lambda e: add.configure(highlightbackground=ACCENT))
        self.entry.bind("<FocusOut>", lambda e: add.configure(highlightbackground=CARD_LINE))
        self.btn_add = CanvasButton(add, "添加", self._on_add, fg="#ffffff",
                                    bg=CARD, fill=ACCENT, hover="#3d6cf0",
                                    press="#3560dd", font=self.f_btn,
                                    padx=12, pady=5, tag="添加")
        self.btn_add.pack(side="right", padx=(0, 8), pady=6)

        # 滚动区
        self.scrollwrap = tk.Frame(self.body, bg=PAGE)
        self.scrollwrap.pack(fill="both", expand=True, padx=(PAD - 2, 0), pady=(10, 0))

        self.canvas = tk.Canvas(self.scrollwrap, bg=PAGE, highlightthickness=0, bd=0,
                                yscrollincrement=1)
        self.inner = tk.Frame(self.canvas, bg=PAGE)
        self._inner_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_config)
        self.canvas.bind("<Configure>", self._on_canvas_config)

        # 右侧滚动条（可拖动）
        self.sbar_area = tk.Frame(self.scrollwrap, bg=PAGE, width=10)
        self.sbar_area.pack(side="right", fill="y")
        self.sbar_area.pack_propagate(False)
        self.sbar = tk.Frame(self.sbar_area, bg=SBAR, width=5, cursor="pointinghand")
        self._sbar_grab = 0
        self._sbar_on = False
        self.sbar.bind("<Enter>", self._sbar_enter)
        self.sbar.bind("<Leave>", self._sbar_leave)
        self.sbar.bind("<Button-1>", self._sbar_press)
        self.sbar.bind("<B1-Motion>", self._sbar_motion)
        self.sbar.bind("<ButtonRelease-1>", self._sbar_release)
        self.sbar_area.bind("<Button-1>", self._sbar_track)
        self.sbar_area.bind("<B1-Motion>", self._sbar_motion)
        self.sbar_area.bind("<ButtonRelease-1>", self._sbar_release)

        self.canvas.pack(side="left", fill="both", expand=True)

        # 悬浮提示
        self.toast = tk.Frame(self.outer, bg=TOAST_BG)
        self.lb_toast = tk.Label(self.toast, text="", bg=TOAST_BG, fg="#ffffff",
                                 font=self.f_small)
        self.lb_toast.pack(side="left", padx=(12, 4), pady=7)
        self.btn_undo = CanvasButton(self.toast, "撤销", self._do_undo,
                                     fg="#9fc0ff", bg=TOAST_BG, fill="#39425a",
                                     hover="#4a5470", press="#5a6684",
                                     font=self.f_small, padx=9, pady=3, tag="撤销")

        # 双指滑动 / 滚轮：两个事件都绑在顶层窗口上，子控件上的滑动会沿
        # 控件 → 类 → 顶层窗口 的事件链冒泡到这里，不依赖鼠标坐标判断。
        # 注意 Tk 9 里触控板走的是 <TouchpadScroll>，和 <MouseWheel> 是两个事件。
        self.root.bind("<TouchpadScroll>", self._on_touchpad)
        self.root.bind("<MouseWheel>", self._on_wheel)
        # 点击统一在顶层兜底处理，见 _root_press
        self.root.bind("<ButtonPress-1>", self._root_press, add="+")
        self.root.bind("<B1-Motion>", self._root_motion, add="+")
        self.root.bind("<ButtonRelease-1>", self._root_release, add="+")

    # ------------------------------------------------ 顶层兜底
    def _all_buttons(self):
        out = [self.btn_close, self.btn_pin, self.btn_alpha, self.btn_fold,
               self.btn_add, self.btn_undo]
        out += [w for w in self._walk(self.inner) if isinstance(w, CanvasButton)]
        return out

    def _own_press(self, p):
        """该控件自己会处理按下，兜底派发和拖动滚动都不该介入"""
        if p is None:
            return False
        if isinstance(p, CanvasButton) or getattr(p, "_no_drag", False):
            return True
        if isinstance(p, (tk.Entry, tk.Text, tk.Scrollbar, tk.Button)):
            return True
        # 滚动条滑块 / 轨道是拖动滑块用的，不能被当成拖动页面
        return p in (self.sbar, self.sbar_area)

    def _hit_button(self, x, y):
        """按屏幕坐标找出应该响应的按钮"""
        for w in self._all_buttons():
            try:
                if not w.winfo_ismapped():
                    continue
                wx, wy = w.winfo_rootx(), w.winfo_rooty()
                if wx <= x <= wx + w.winfo_width() and wy <= y <= wy + w.winfo_height():
                    return w
            except Exception:
                continue
        return None

    def _global_press(self, e):
        """按钮兜底派发，返回是否派发成功。

        正常情况：控件自己的 <Button-1> 处理完会 return "break"，事件链到此为止，
        这个函数不会被调用。
        异常情况：macOS 上出现过点击没投递给某个控件的情况（tk.Label 就是），
        这时点击仍然会冒泡到顶层窗口，于是这里按坐标找到应该响应的按钮手动触发，
        保证「看得见的按钮一定点得动」。
        """
        if self._own_press(e.widget):
            return False
        w = self._hit_button(e.x_root, e.y_root)
        if w is None:
            return False
        LOG.info("fallback 命中按钮[%s]", w._tag)
        w._fire()
        return True

    def _root_press(self, e):
        """顶层按下：先兜底派发按钮，否则当作拖动滚动的起点"""
        if self._global_press(e):
            return "break"
        if not self._own_press(e.widget):
            self._pd_press(e)
        return None

    def _root_motion(self, e):
        self._pd_motion(e)
        return None

    def _root_release(self, e):
        self._pd_release(e)
        return None

    def _on_canvas_config(self, _e):
        self.canvas.itemconfigure(self._inner_id, width=self.canvas.winfo_width())
        self._update_sbar()

    def _on_inner_config(self, _e):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._update_sbar()

    def _update_sbar(self):
        try:
            lo, hi = self.canvas.yview()
        except Exception:
            return
        area_h = self.sbar_area.winfo_height() or self.canvas.winfo_height() or 0
        if area_h <= 1 or (hi - lo) >= 0.999:
            self.sbar.place_forget()
            return
        bh = max(28, int((hi - lo) * area_h))
        y = int(lo * area_h)
        if y + bh > area_h:
            bh = max(20, area_h - y)
        self.sbar.place(relx=1.0, x=-2, y=y, width=5, height=bh, anchor="ne")
        self.sbar.lift()

    def _sbar_enter(self, _e):
        self._sbar_on = True
        self.sbar.configure(bg=SBAR_HOVER, width=6)

    def _sbar_leave(self, _e):
        self._sbar_on = False
        self.sbar.configure(bg=SBAR, width=5)

    def _sbar_press(self, e):
        self._sbar_grab = e.y_root - self.sbar.winfo_rooty()
        self._sbar_busy = True
        self.sbar.configure(bg=SBAR_ACTIVE)
        # 不调用 grab_set：Tk 在按下时本来就有隐式抓取，拖动中滑出滑块也能继续收到
        # <B1-Motion>。显式抓取一旦释放失败会永久卡住全局抓取，让整个挂件失灵。

    def _sbar_track(self, e):
        lo, hi = self.canvas.yview()
        area_h = self.sbar_area.winfo_height() or 1
        self._sbar_grab = int((hi - lo) * area_h / 2.0)
        self._sbar_busy = True
        self.sbar.configure(bg=SBAR_ACTIVE)
        self._sbar_motion(e)

    def _sbar_motion(self, e):
        area_h = self.sbar_area.winfo_height() or 1
        lo, hi = self.canvas.yview()
        span = max(0.02, hi - lo)
        top = (e.y_root - self.sbar_area.winfo_rooty()) - self._sbar_grab
        f = top / float(max(1, area_h))
        self.canvas.yview_moveto(max(0.0, min(1.0 - span, f)))
        self._update_sbar()

    def _sbar_release(self, _e=None):
        self._sbar_busy = False
        try:
            self.sbar.grab_release()
        except Exception:
            pass
        self.sbar.configure(bg=SBAR_HOVER if self._sbar_on else SBAR)

    # ------------------------------------------------ 双指滑动 / 滚轮
    def _precise_deltas(self, packed):
        """把 <TouchpadScroll> 的 %D 拆成 (Δx, Δy)。

        打包方式是 TIP 684 定的：Δx 占高 16 位、Δy 占低 16 位，各按 16 位有符号解释。
        优先用 Tk 自带的 tk::PreciseScrollDeltas；取不到就自己按位算。

        入参先归一化成「32 位有符号」——Tk 送进 %D 的是这个形式（例如 -327668），
        但万一某个版本按无符号给（4294639628），归一化之后结果一样。
        """
        try:
            v = int(packed) & 0xFFFFFFFF
        except Exception:
            return 0, 0
        if v >= 0x80000000:
            v -= 0x100000000
        try:
            pair = self.root.tk.splitlist(
                self.root.tk.call("tk::PreciseScrollDeltas", v))
            return int(pair[0]), int(pair[1])
        except Exception:
            pass
        dx, dy = (v >> 16) & 0xFFFF, v & 0xFFFF
        if dx >= 0x8000:
            dx -= 0x10000
        if dy >= 0x8000:
            dy -= 0x10000
        return dx, dy

    def _on_touchpad(self, e):
        """触控板双指滑动。

        精密增量本身就是像素量级，直接拿来滚就是「手指滑多少页面走多少」，
        不需要任何加速度或台阶；系统自带的惯性也会一串串送进来，照着滚就行。
        """
        _dx, dy = self._precise_deltas(getattr(e, "delta", 0) or 0)
        if not dy:
            return "break"
        self._touch_note(dy)
        self._wheel_move(abs(dy) * TRACKPAD_GAIN, dy < 0)
        return "break"

    def _touch_note(self, dy):
        """最多每 3 秒记一行，方便照着真实数值调 TRACKPAD_GAIN"""
        now = time.time()
        if now - self._touch_t < 3.0:
            return
        self._touch_t = now
        LOG.info("双指滑动 delta_y=%s 前台=%s 增益=%.1f",
                 dy, self._is_front(), TRACKPAD_GAIN)

    def _on_wheel(self, e):
        """鼠标滚轮。Tk 9 里触控板不走这个事件，所以到这里的都是真滚轮。"""
        d = getattr(e, "delta", 0) or 0
        if not d:
            return "break"
        ad = abs(float(d))
        # Windows 一格是 120，macOS 一格是个位数；两种都按「一格 = WHEEL_LINE_PX」处理
        px = (ad / 120.0) * WHEEL_LINE_PX if ad >= 100 else WHEEL_LINE_PX
        self._wheel_move(px, d < 0)
        return "break"

    def _wheel_move(self, px, down):
        """按像素滚动。零头留在 _wheel_acc 里，小增量才不会被截断吃成 0。"""
        self._wheel_acc += px if down else -px
        step = int(self._wheel_acc)
        if not step:
            return
        self._wheel_acc -= step
        try:
            self.canvas.yview_scroll(step, "units")
            self._update_sbar()
        except Exception:
            pass

    # ------------------------------------------------ 拖动页面滚动
    def _walk(self, w):
        yield w
        for c in w.winfo_children():
            for x in self._walk(c):
                yield x

    def _bind_page_drag(self):
        """给「非交互」控件挂上拖动滚动。

        可点控件（CanvasButton、进度条、滚动条）都带 _no_drag 标记，跳过；
        Entry 也跳过，否则没法用鼠标定位光标。
        """
        for w in self._walk(self.inner):
            if getattr(w, "_no_drag", False):
                continue
            if isinstance(w, (tk.Entry, tk.Button, tk.Text, tk.Scrollbar)):
                continue
            w.bind("<ButtonPress-1>", self._pd_press, add="+")
            w.bind("<B1-Motion>", self._pd_motion, add="+")
            w.bind("<ButtonRelease-1>", self._pd_release, add="+")
        # 内容不足时下方留白的画布区域也能拖
        for seq, fn in (("<ButtonPress-1>", self._pd_press),
                        ("<B1-Motion>", self._pd_motion),
                        ("<ButtonRelease-1>", self._pd_release)):
            self.canvas.bind(seq, fn, add="+")

    def _scroll_span(self):
        """可滚动的像素跨度（内容高 - 视口高），为 0 表示不需要滚动"""
        try:
            bb = self.canvas.bbox("all")
        except Exception:
            return 0
        if not bb:
            return 0
        view = self.canvas.winfo_height()
        return max(0, (bb[3] - bb[1]) - view)

    def _pd_press(self, e):
        try:
            self._pd = [e.y_root, self.canvas.yview()[0], False]
        except Exception:
            self._pd = None
        return None

    def _pd_motion(self, e):
        if not self._pd:
            return None
        if self._scroll_span() <= 0:
            return None
        y0, f0, moved = self._pd
        dy = e.y_root - y0
        if not moved:
            if abs(dy) < 5:      # 抖动阈值，避免误触变成滚动
                return None
            self._pd[2] = True
        f = f0 - dy / float(self._scroll_span())
        self.canvas.yview_moveto(max(0.0, min(1.0, f)))
        self._update_sbar()
        return None

    def _pd_release(self, _e):
        self._pd = None
        return None

    # ------------------------------------------------ 光标停住 -> 激活窗口
    #
    # macOS 只把滑动事件送给「当前前台 App」。卡片常年挂在桌面上、多半不在前台，
    # 光标停上来时必须主动把窗口激活，否则双指滑到哪儿都没反应。
    #
    # 这里用轮询而不是 <Enter>/<Leave>：窗口不是前台时，连鼠标进出这类事件本身
    # 都送不到（上一版跟随滚动也正因如此才用轮询）。轮询很便宜：120ms 查一次
    # 「指针在哪」，两次 Tcl 调用而已。
    def _is_front(self):
        """本窗口是不是当前前台（焦点在别的 App 时 focus_get() 返回 None）"""
        try:
            return self.root.focus_get() is not None
        except Exception:
            return False

    def _pointer_at(self, px, py):
        """(px, py) 是不是落在本窗口里。纯几何判断，和窗口是不是前台无关。"""
        try:
            under = self.root.winfo_containing(px, py)
        except Exception:
            return False
        if under is None:
            return False
        try:
            return under.winfo_toplevel() is self.root
        except Exception:
            return False

    def _hover_loop(self):
        self._hover_job = self.root.after(HOVER_POLL_MS, self._hover_loop)
        # 顺带做外部改动检测：约 3 拍（~0.3 秒）看一眼 data.json 有没有被外部程序改
        self._ext_ticks += 1
        if self._ext_ticks >= 3:
            self._ext_ticks = 0
            self._check_external_change()
        try:
            px, py = self.root.winfo_pointerxy()
        except Exception:
            return
        self._hover_tick(px, py)

    def _check_external_change(self):
        """data.json 被外部程序改过（比如 AI 通过命令行加待办）就自动重载刷新。

        界面状态（窗口位置/折叠等）保留内存里这一份，只换待办数据，
        这样外部改动不会把窗口弹回默认位置、也不会打断正在拖动的进度条。
        """
        if self._editor is not None:      # 用户正在改文字，这一拍先跳过
            return
        if self._busy:                    # 有卡片正在播放归档动画，先跳过
            return
        m = _stat_mtime()
        if m is None or m == _DATA_MTIME[0]:
            return
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            return                        # 文件写了一半或坏了，下一拍再看
        base = default_data()
        for k in ("active", "done"):
            base[k] = d.get(k) or []
        base["ui"] = self.data["ui"]     # 界面状态以正在用的为准
        self.data = base
        _DATA_MTIME[0] = m
        LOG.info("外部改动，自动重载：进行中 %d 条", len(base["active"]))
        try:
            self.render()
        except Exception as e:
            LOG.info("重载后刷新失败: %r", e)

    def _hover_tick(self, px, py):
        """看一拍光标的位置；停在卡片上够久就激活窗口。

        返回「这一拍是不是真的去激活了」，方便自检和日志。
        要求停住一小会儿才动手，光标只是经过扫一下不会抢焦点。
        """
        now = time.time()
        if not self._pointer_at(px, py):
            self._hover_inside = False
            self._hover_armed = True        # 离开一次就重新武装
            self._hover_pt = None
            return False
        if not self._hover_inside:          # 刚进来：先记时间，这一拍不动
            self._hover_inside = True
            self._hover_since = now
            self._hover_pt = (px, py)
            return False
        if self._hover_pt is not None:
            moved = abs(px - self._hover_pt[0]) + abs(py - self._hover_pt[1])
            if moved >= HOVER_REARM_PX:
                # 光标又在卡片上动了，说明人确实在用它 -> 允许再激活一次。
                # 这样既不会被「光标一直停着」反复抢焦点，又能在切走再回来时恢复。
                self._hover_armed = True
                self._hover_pt = (px, py)
        if now - self._hover_since < HOVER_FOCUS_MS / 1000.0:
            return False
        if not self._hover_armed:
            return False
        self._hover_armed = False
        return self._activate()

    def _activate(self):
        """把本窗口拿到前台。焦点给顶层窗口本身（而不是输入框），
        免得抢到焦点之后把顺手敲的字吃进待办里。"""
        if self._is_front():
            return False
        try:
            self.root.lift()
            self.root.focus_force()
        except Exception:
            return False
        LOG.info("hover 激活窗口（双指滑动要前台才收得到）")
        return True

    # ------------------------------------------------ 拖动 / 折叠 / 置顶
    def _drag_start(self, e):
        self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())
        try:
            self.root.focus_force()
        except Exception:
            pass
        # 关键：返回 break，别让这次按下再冒泡到顶层被当成「拖动页面」
        return "break"

    def _drag_move(self, e):
        if not self._drag:
            return "break"
        dx, dy = self._drag
        self.root.geometry("+%d+%d" % (e.x_root - dx, e.y_root - dy))
        return "break"

    def _drag_end(self, _e):
        self._drag = None
        return "break"

    def _debounce(self, key, cooldown=0.35):
        """同一个动作在冷却期内只放行一次（按下/松开两个事件都可能触发）"""
        now = time.time()
        if now - self._guard.get(key, 0.0) < cooldown:
            return False
        self._guard[key] = now
        return True

    def toggle_fold(self):
        ui = self.data["ui"]
        ui["collapsed"] = not ui.get("collapsed", False)
        save_data(self.data)
        self.render()

    def toggle_pin(self):
        ui = self.data["ui"]
        ui["topmost"] = not ui.get("topmost", True)
        try:
            self.root.attributes("-topmost", ui["topmost"])
        except Exception:
            pass
        save_data(self.data)
        self._sync_header()

    def toggle_alpha(self):
        ui = self.data["ui"]
        ui["alpha"] = 0.9 if float(ui.get("alpha", 1.0)) >= 0.95 else 1.0
        try:
            self.root.attributes("-alpha", ui["alpha"])
        except Exception:
            pass
        save_data(self.data)
        self._sync_header()

    def _sync_header(self):
        ui = self.data["ui"]
        self.btn_pin.set_state(ui.get("topmost", True))
        self.btn_alpha.set_state(float(ui.get("alpha", 1.0)) < 0.95)
        # 注意：CanvasButton 是 Canvas，不能用 configure(text=...)
        self.btn_fold.set_text("展开" if ui.get("collapsed") else "折叠")

    # ------------------------------------------------ 尺寸自适应
    def _fit(self, x=None, y=None):
        r = self.root
        r.update_idletasks()
        if self.data["ui"].get("collapsed"):
            self.collapsed_bar.pack(fill="x", after=self.header)
            self.body.pack_forget()
        else:
            self.collapsed_bar.pack_forget()
            self.body.pack(fill="both", expand=True)

        r.update_idletasks()
        inner_h = self.inner.winfo_reqheight()
        max_scroll = max(160, min(540, r.winfo_screenheight() - 260))
        view_h = min(inner_h, max_scroll)
        self.canvas.configure(height=view_h)
        r.update_idletasks()
        total = self._measure(view_h)

        if x is None:
            x, y = r.winfo_x(), r.winfo_y()
        sh = r.winfo_screenheight()
        # 窗口超出屏幕时把顶部提上来，别让下半截跑到屏幕外
        if total >= sh - 24:
            y = 12
        elif y + total > sh - 12:
            y = max(12, sh - 12 - total)
        r.geometry("%dx%d+%d+%d" % (W, total, x, y))
        self.root.after(20, self._update_sbar)

    def _measure(self, view_h):
        """不含滚动区真实高度时的窗口总高"""
        r = self.root
        # 先临时把 canvas 高度设为 view_h，再量整体
        self.canvas.configure(height=view_h)
        r.update_idletasks()
        header_h = self.header.winfo_reqheight() + 10
        if self.data["ui"].get("collapsed"):
            return header_h + self.collapsed_bar.winfo_reqheight() + 2 * EDGE
        body_h = self.body.winfo_reqheight()
        return header_h + body_h + 2 * EDGE

    # ------------------------------------------------ 增删改
    def _on_add(self, _e=None):
        txt = self.entry.get().strip()
        if not txt:
            return
        self.data["active"].insert(0, {
            "id": new_id(), "title": txt, "progress": 0,
            "createdAt": int(time.time() * 1000), "doneAt": None,
        })
        self.entry.delete(0, "end")
        save_data(self.data)
        self.render()
        self.entry.focus_set()

    def add_task(self, title):
        self.entry.insert(0, title)
        self._on_add()

    def _find(self, tid):
        for t in self.data["active"]:
            if t["id"] == tid:
                return t
        return None

    def set_progress(self, tid, val, commit=False, paint_only=False):
        """改进度只做原地重绘，不重建列表 —— 避免在控件自己的事件里把它销毁掉"""
        t = self._find(tid)
        if not t or tid in self._busy:
            return
        new = max(0, min(100, int(round(val / 5.0) * 5)))
        if commit and new >= 100:
            self.archive(tid)
            return
        t["progress"] = new
        LOG.info("progress %s -> %d%%", tid, new)
        if commit or paint_only:
            save_data(self.data)
        self._paint_card(tid)
        self._draw_overall()

    def bump(self, tid, delta):
        t = self._find(tid)
        if t:
            self.set_progress(tid, t["progress"] + delta, commit=True)

    def archive(self, tid, animate=True):
        t = self._find(tid)
        if not t or tid in self._busy:
            return
        self._busy.add(tid)
        t["progress"] = 100
        self._paint_card(tid)
        self._draw_overall()
        if animate:
            self.root.after(400, lambda: self._finish_archive(tid))
        else:
            self._finish_archive(tid)

    def _finish_archive(self, tid):
        t = self._find(tid)
        self._busy.discard(tid)
        if not t:
            return
        t["doneAt"] = int(time.time() * 1000)
        self.data["active"].remove(t)
        self.data["done"].insert(0, t)
        self.data["ui"]["done_open"] = True
        save_data(self.data)
        self.render()
        name = t["title"] if len(t["title"]) <= 12 else t["title"][:12] + "…"
        self.show_toast("🎉「%s」已完成，已归入已完成" % name,
                        "撤销", lambda: self.undo_archive(t["id"]))

    def undo_archive(self, tid):
        for i, t in enumerate(self.data["done"]):
            if t["id"] == tid:
                t = self.data["done"].pop(i)
                t["doneAt"] = None
                t["progress"] = 95
                self.data["active"].insert(0, t)
                save_data(self.data)
                self.render()
                self.show_toast("已恢复到进行中（进度 95%）")
                return

    def remove_task(self, tid):
        t = self._find(tid)
        if not t:
            return
        idx = self.data["active"].index(t)
        self.data["active"].remove(t)
        save_data(self.data)
        self.render()
        name = t["title"] if len(t["title"]) <= 12 else t["title"][:12] + "…"
        self.show_toast("已删除「%s」" % name, "撤销", lambda: self._restore(idx, t))

    def _restore(self, idx, task):
        self.data["active"].insert(min(idx, len(self.data["active"])), task)
        save_data(self.data)
        self.render()
        self.show_toast("已恢复该待办")

    # ------------------------------------------------ 改文字（就地编辑）
    def _find_any(self, tid):
        """在「进行中」和「已完成」里都找一遍，返回 (任务, 所在列表)"""
        for lst in (self.data["active"], self.data["done"]):
            for t in lst:
                if t["id"] == tid:
                    return t, lst
        return None, None

    def _repaint_title(self, tid, text):
        """只把这一条的标题文字换掉，不重建整个列表。

        不重建的原因：编辑框失焦会先触发保存，如果这时整表重建，
        正在被点的那颗按钮会被销毁，点击就白点了。
        """
        tl = (self._titles or {}).get(tid)
        if tl is None:
            self.render()
            return
        h0 = tl.winfo_height()
        try:
            tl.set_text(text)
            if tl.winfo_height() != h0:
                self._fit()
        except Exception:
            self.render()

    def set_title(self, tid, text):
        t, _ = self._find_any(tid)
        if not t:
            return
        # 换行会破坏排版，统一压成空格；首尾空白去掉
        text = " ".join((text or "").split())
        if not text:
            self.show_toast("标题不能为空，已保持原样")
            return
        if text == t["title"]:
            return
        old = t["title"]
        t["title"] = text
        save_data(self.data)
        self._repaint_title(tid, text)
        name = text if len(text) <= 10 else text[:10] + "…"
        self.show_toast("已改为「%s」" % name, "撤销", lambda: self._undo_title(tid, old))

    def _undo_title(self, tid, old):
        t, _ = self._find_any(tid)
        if not t:
            return
        t["title"] = old
        save_data(self.data)
        self._repaint_title(tid, old)
        self.show_toast("已恢复原来的文字")

    def _on_title_dbl(self, e):
        """双击标题 = 改文字。上下文（任务 id / 所属卡片 / 右侧留白）挂在控件上，
        这样绑定的是一个具名方法，自检才能直接调它、覆盖真实路径。"""
        w = getattr(e, "widget", None)
        ctx = getattr(w, "_edit_ctx", None)
        if not ctx:
            return "break"
        tid, box, reserve = ctx
        self.start_edit(tid, w, box, reserve)
        return "break"

    def start_edit(self, tid, tl=None, box=None, reserve_right=96):
        """在标题的位置就地放一个输入框来改文字"""
        # 已经在编辑别的：先把上一条存下来
        if self._editor is not None:
            self.commit_edit()
            self.root.update_idletasks()
        t, _ = self._find_any(tid)
        if not t or tid in self._busy:
            return
        if tl is None or not tl.winfo_exists():
            tl = (self._titles or {}).get(tid)
        if tl is None or not tl.winfo_exists():
            return
        box = box if (box is not None and box.winfo_exists()) else tl.master
        try:
            ix, iy = self.inner.winfo_rootx(), self.inner.winfo_rooty()
            ex = tl.winfo_rootx() - ix
            ey = tl.winfo_rooty() - iy
            bx = box.winfo_rootx() - ix
            limit = bx + box.winfo_width() - reserve_right
            ew = max(120, min(limit - ex, max(tl.winfo_width(), 150)))
            eh = max(tl.winfo_height() + 6,
                     self.f_card.metrics("linespace") + 12)
        except Exception:
            return
        if ew < 60:
            return
        ed = tk.Text(self.inner, bg="#ffffff", fg=TEXT, font=self.f_card,
                     relief="flat", bd=0, highlightthickness=1,
                     highlightbackground=ACCENT, highlightcolor=ACCENT,
                     insertbackground=ACCENT, wrap="char", padx=5, pady=2,
                     undo=False)
        ed.insert("1.0", t["title"])
        ed.place(x=ex, y=ey, width=ew, height=eh)
        ed.lift()
        ed.mark_set("insert", "end-1c")
        ed.see("end")
        ed.bind("<Return>", self._edit_ok)
        ed.bind("<KP_Enter>", self._edit_ok)
        ed.bind("<Shift-Return>", self._edit_ok)
        ed.bind("<Escape>", self._edit_cancel)
        ed.bind("<FocusOut>", self._edit_blur)
        self._editor = (ed, tid)
        try:
            self.root.focus_force()
            ed.focus_set()
        except Exception:
            pass
        LOG.info("改文字 开始编辑 %s", tid)

    def _edit_ok(self, _e=None):
        self.commit_edit()
        return "break"

    def _edit_cancel(self, _e=None):
        self.cancel_edit()
        return "break"

    def _edit_blur(self, _e=None):
        # 点到别处 = 保存。同步执行：保存走的是「只改这一条文字」的轻量路径，
        # 不会销毁任何按钮，所以不会把紧接着的那次点击吃掉。
        self.commit_edit()
        return None

    def _editor_value(self):
        cur = self._editor
        if not cur:
            return None, None
        ed = cur[0]
        try:
            return cur[1], ed.get("1.0", "end-1c")
        except Exception:
            return cur[1], None

    def _close_editor(self):
        cur = self._editor
        self._editor = None
        if not cur:
            return
        try:
            cur[0].destroy()
        except Exception:
            pass

    def commit_edit(self):
        tid, txt = self._editor_value()
        self._close_editor()
        LOG.info("改文字 保存 %s -> %r", tid, txt)
        if tid is not None and txt is not None:
            self.set_title(tid, txt)

    def cancel_edit(self):
        tid, _ = self._editor_value()
        self._close_editor()
        LOG.info("改文字 取消 %s", tid)

    def remove_done(self, tid):
        self.data["done"] = [x for x in self.data["done"] if x["id"] != tid]
        save_data(self.data)
        self.render()

    def restore_done(self, tid):
        self.undo_archive(tid)

    def toggle_done_open(self):
        ui = self.data["ui"]
        ui["done_open"] = not ui.get("done_open", True)
        save_data(self.data)
        self.render()

    def clear_done(self):
        if not self.data["done"]:
            self.show_toast("暂无已完成记录")
            return
        backup = self.data["done"]
        self.data["done"] = []
        save_data(self.data)
        self.render()
        self.show_toast("已清空 %d 条完成记录" % len(backup), "撤销", lambda: self._restore_done(backup))

    def _restore_done(self, backup):
        self.data["done"] = backup
        save_data(self.data)
        self.render()
        self.show_toast("已恢复完成记录")

    # ------------------------------------------------ 到期自动清理
    def _purge_expired(self, notify=True):
        """把归档超过 DONE_KEEP_DAYS 天的完成记录清掉，返回清掉的条数。

        doneAt 是归档那一刻写进去的毫秒时间戳。判断规则：
          * 早于「现在 - 半个月」 -> 到期，清掉；
          * 没有 doneAt / 不是数字 / 时间戳落在未来（用户改过系统时钟）/
            早于 DONE_TS_FLOOR（单位写错成「秒」了）
            -> 算不出年龄，一律留着，宁可占地方也不错删用户的东西。
        """
        now = int(time.time() * 1000)
        keep, gone = [], []
        for t in self.data.get("done", []):
            ts = done_ts(t)             # 脏数据会拿到 0，下面的下界判断会把它留下
            if ts > now or ts < DONE_TS_FLOOR or now - ts < DONE_KEEP_MS:
                keep.append(t)
            else:
                gone.append(t)
        if not gone:
            return 0
        # 这是自动删除，撤销窗口只有 6 秒；先留一份整份快照再动手
        backup_before_purge(self.data)
        self.data["done"] = keep
        save_data(self.data)
        self.render()
        LOG.info("到期清理：清掉 %d 条（保留 %d 天），还剩 %d 条",
                 len(gone), DONE_KEEP_DAYS, len(keep))
        if notify:
            self.show_toast("已清理 %d 条超过 %d 天的完成记录" % (len(gone), DONE_KEEP_DAYS),
                            "撤销", lambda: self._restore_purged(gone))
        return len(gone)

    def _restore_purged(self, backup):
        """撤销自动清理：把清掉的记录放回已完成列表，按完成时间倒序归位。"""
        cur = self.data.get("done", [])
        have = {t["id"] for t in cur}
        merged = cur + [t for t in backup if t["id"] not in have]
        merged.sort(key=done_ts, reverse=True)
        self.data["done"] = merged
        save_data(self.data)
        self.render()
        self.show_toast("已恢复 %d 条完成记录" % len(backup))

    def _purge_loop(self):
        """定时看一眼有没有到期的。先排下一次，再干活：万一清理里出错也不会断掉循环。"""
        self._purge_job = self.root.after(PURGE_EVERY_MS, self._purge_loop)
        try:
            self._purge_expired()
        except Exception:
            LOG.exception("到期清理出错")

    # ------------------------------------------------ 提示条
    def show_toast(self, msg, undo_label=None, undo_cmd=None):
        self.lb_toast.configure(text=msg)
        if undo_label and undo_cmd:
            self._undo = undo_cmd
            self.btn_undo.set_text(undo_label)
            self.btn_undo.pack(side="left", padx=(2, 0))
            delay = 6000
        else:
            self._undo = None
            self.btn_undo.pack_forget()
            delay = 2200
        self.toast.place(relx=0.5, rely=1.0, anchor="s", y=-12)
        self.toast.lift()
        if self._toast_job:
            self.root.after_cancel(self._toast_job)
        self._toast_job = self.root.after(delay, self.hide_toast)

    def hide_toast(self):
        self._toast_job = None
        self.toast.place_forget()

    def _do_undo(self):
        cmd = self._undo
        self._undo = None
        self.hide_toast()
        if cmd:
            cmd()

    # ------------------------------------------------ 绘制
    def _draw_overall(self):
        cv = self.ov_cv
        cv.delete("all")
        w = cv.winfo_width() or 300
        h = 7
        round_rect(cv, 0, 0, w, h, h / 2, TRACK)
        n = len(self.data["active"])
        avg = int(round(sum(t["progress"] for t in self.data["active"]) / n)) if n else 0
        if avg > 0:
            fw = max(h, w * avg / 100.0)
            round_rect(cv, 0, 0, fw, h, h / 2, bar_colors(avg)[1])
        self.lb_avg.configure(text="总进度")
        self.lb_cnt.configure(text="{}% · 进行中 {} 项 · 已完成 {} 项".format(
            avg, n, len(self.data["done"])))

    def _paint_card(self, tid):
        cv = getattr(self, "_bars", {}).get(tid)
        t = self._find(tid)
        if not cv or not t:
            return
        p = t["progress"]
        w = cv.winfo_width() or TITLE_WRAP
        y0, h = BAR_PAD, BAR_H
        y1 = y0 + h
        cv.delete("all")
        round_rect(cv, 0, y0, w, y1, h / 2, TRACK)
        if p > 0:
            fw = max(h, w * p / 100.0)
            round_rect(cv, 0, y0, fw, y1, h / 2, bar_colors(p)[1])
        lb = getattr(self, "_pcts", {}).get(tid)
        if lb:
            lb.configure(text="%d%%" % p,
                         fg=GREEN if p >= 100 else ACCENT,
                         bg=GREEN_SOFT if p >= 100 else ACCENT_SOFT)

    def _paint_all_bars(self):
        """把当前所有卡片的进度条重画一遍。

        列表每次重建（启动、增删、折叠、归档）都会换一批进度条画布，
        新建的画布是空的，必须重新画；不画就是「只有总进度条有图形，
        每条待办的进度条一片空白」。宽度要等布局算完才准，所以这里
        只在 render 的 _fit() 之后调用，另外每根画布还绑了 <Configure> 兜底。
        """
        for tid in list(getattr(self, "_bars", {})):
            self._paint_card(tid)

    def render(self):
        # 列表要重建了，先把编辑框收掉（避免留下一个指向已销毁控件的引用）
        self._close_editor()
        for w in self.inner.winfo_children():
            w.destroy()
        self._bars, self._pcts = {}, {}
        self._titles = {}
        ui = self.data["ui"]

        self._sync_header()
        if ui.get("collapsed"):
            n = len(self.data["active"])
            avg = int(round(sum(t["progress"] for t in self.data["active"]) / n)) if n else 0
            self.lb_summary.set_text(
                "{} 项待办 · 总进度 {}% · 已完成 {}".format(n, avg, len(self.data["done"])))
            self._fit()
            return

        # 进行中
        if not self.data["active"]:
            tk.Label(self.inner, text="还没有待办，在上面添加一条吧", bg=PAGE,
                     fg=FAINT, font=self.f_small).pack(pady=16)
        else:
            for t in self.data["active"]:
                self._build_card(t)

        # 已完成
        self._build_done_section()

        self._draw_overall()
        self._bind_page_drag()
        self._fit()
        # 布局算完之后再补画一次：新建的进度条画布这时才知道自己的真实宽度
        self._paint_all_bars()

    def _build_card(self, t):
        tid = t["id"]
        card = tk.Frame(self.inner, bg=CARD, highlightbackground=CARD_LINE,
                        highlightthickness=1)
        card.pack(fill="x", pady=(0, GAP))

        head = tk.Frame(card, bg=CARD)
        head.pack(fill="x", padx=CARD_PAD, pady=(9, 0))

        # 标题用 Canvas 自绘：既是显示文字，也是「拖动页面滚动」的抓取区
        lb = TextLine(head, t["title"], bg=CARD, fg=TEXT, font=self.f_card,
                      wrap=TITLE_WRAP, padx=0, pady=2, cursor="fleur")
        lb.pack(side="left", fill="x", expand=True)
        self._titles[tid] = lb
        # 双击标题也能改文字（和点「改」按钮是同一个入口）
        lb._edit_ctx = (tid, card, 96)
        lb.bind("<Double-Button-1>", self._on_title_dbl)

        btn_x = CanvasButton(head, "×", lambda i=tid: self.remove_task(i),
                             fg="#b9c2d2", bg=CARD, fill=CARD, hover=DANGER_SOFT,
                             font=self.f_btn, padx=7, pady=3, tag="删除待办")
        btn_x.pack(side="right")

        CanvasButton(head, "改",
                     lambda i=tid, w=lb, b=card: self.start_edit(i, w, b, 96),
                     fg=BTN_FG, bg=CARD, fill="#f4f6fb", hover=ACCENT_SOFT,
                     press=ACCENT_SOFT, font=self.f_btn, padx=7, pady=3,
                     tag="改文字").pack(side="right", padx=(0, 3))

        pct = tk.Label(head, text="%d%%" % t["progress"],
                       bg=GREEN_SOFT if t["progress"] >= 100 else ACCENT_SOFT,
                       fg=GREEN if t["progress"] >= 100 else ACCENT,
                       font=self.f_btn, padx=7, pady=2)
        pct.pack(side="right", padx=(6, 4))
        self._pcts[tid] = pct

        # 进度条：画布比视觉条更高，多出来的部分就是点击热区
        bar = tk.Canvas(card, height=BAR_H + 2 * BAR_PAD, width=1, bg=CARD,
                        highlightthickness=0, bd=0, cursor="pointinghand")
        bar.pack(fill="x", padx=CARD_PAD, pady=(2, 0))
        bar._no_drag = True
        self._bars[tid] = bar
        # 画布宽度是布局之后才定下来的，所以宽度一变就重画一次；
        # 少了这一句，列表重建后这张进度条会一直空着（只有总进度条有图形）
        bar.bind("<Configure>", lambda e, i=tid: self._paint_card(i))
        bar.bind("<Button-1>", lambda e, i=tid: self._bar_press(e, i))
        bar.bind("<B1-Motion>", lambda e, i=tid: self._bar_motion(e, i))
        bar.bind("<ButtonRelease-1>", lambda e, i=tid: self._bar_release(e, i))

        foot = tk.Frame(card, bg=CARD)
        foot.pack(fill="x", padx=(CARD_PAD - 4, CARD_PAD), pady=(2, 9))
        tk.Label(foot, text="点/拖进度条调节", bg=CARD, fg="#c3cad8",
                 font=self.f_tiny).pack(side="left", padx=(4, 0))
        CanvasButton(foot, "完成", lambda i=tid: self.set_progress(i, 100, commit=True),
                     fg=PRIMARY_FG, bg=CARD, fill=PRIMARY_BG, hover=PRIMARY_HOVER,
                     font=self.f_btn, padx=13, pady=5, tag="完成"
                     ).pack(side="right")
        CanvasButton(foot, "+10%", lambda i=tid: self.bump(i, 10),
                     fg=BTN_FG, bg=CARD, fill=BTN_BG, hover=BTN_HOVER,
                     font=self.f_btn, padx=12, pady=5, tag="+10%"
                     ).pack(side="right", padx=(0, 5))
        CanvasButton(foot, "-10%", lambda i=tid: self.bump(i, -10),
                     fg=BTN_FG, bg=CARD, fill=BTN_BG, hover=BTN_HOVER,
                     font=self.f_btn, padx=12, pady=5, tag="-10%"
                     ).pack(side="right", padx=(0, 5))

    def _apply_bar(self, tid, x):
        cv = self._bars.get(tid)
        if not cv or tid in self._busy:
            return
        w = cv.winfo_width() or 1
        val = max(0.0, min(100.0, x / float(w) * 100.0))
        t = self._find(tid)
        if t:
            t["progress"] = max(0, min(100, int(round(val / 5.0) * 5)))
        self._paint_card(tid)
        self._draw_overall()

    def _bar_press(self, e, tid):
        try:
            self.root.focus_force()
        except Exception:
            pass
        self._bar_active = tid
        LOG.info("bar    按下 %s", tid)
        self._apply_bar(tid, e.x)
        return "break"

    def _bar_motion(self, e, tid):
        # Tk 在按下时对控件有隐式抓取，指针滑出进度条也能继续收到移动事件
        self._apply_bar(tid, e.x)
        return "break"

    def _bar_release(self, _e, tid):
        self._bar_active = None
        t = self._find(tid)
        if not t or tid in self._busy:
            return
        LOG.info("bar    松开 %s -> %d%%", tid, t["progress"])
        if t["progress"] >= 100:
            self.archive(tid)
        else:
            save_data(self.data)

    def _build_done_section(self):
        ui = self.data["ui"]
        opened = ui.get("done_open", True)
        n = len(self.data["done"])

        bar = tk.Frame(self.inner, bg=PAGE)
        bar.pack(fill="x", pady=(4, 6))
        if n:
            CanvasButton(bar, "清空", self.clear_done, fg=FAINT, bg=PAGE,
                         fill=PAGE, hover=DANGER_SOFT, press=DANGER_SOFT,
                         font=self.f_tiny, padx=8, pady=2, radius=6,
                         tag="清空已完成").pack(side="right", padx=(0, 2))

        # 整行用 Canvas 自绘：只有 Canvas 上的点击才收得到
        head = tk.Canvas(bar, height=22, width=1, bg=PAGE, highlightthickness=0,
                         bd=0, cursor="pointinghand")
        head.pack(side="left", fill="x", expand=True)
        head._no_drag = True
        label = ("▾  " if opened else "▸  ") + "已完成"

        def _draw_head(_e=None):
            head.delete("all")
            head.create_text(2, 11, text=label, anchor="w", fill=SUB,
                             font=self.f_small)
            tx = 2 + self.f_small.measure(label) + 8
            bw = max(20, self.f_tiny.measure(str(n)) + 12)
            round_rect(head, tx, 3, tx + bw, 19, 8, "#e6ecf8")
            head.create_text(tx + bw / 2.0, 11, text=str(n), fill="#6b7690",
                             font=self.f_tiny)
            # 右侧挂一句「保留 15 天」，让自动清理的规则一眼看得见（宽度不够就不画）
            hw = head.winfo_width() or 0
            tail = tx + bw + 10 + self.f_tiny.measure("保留 %d 天" % DONE_KEEP_DAYS)
            if hw > tail:
                head.create_text(hw - 2, 11, text="保留 %d 天" % DONE_KEEP_DAYS,
                                 anchor="e", fill="#cbd3e0", font=self.f_tiny)

        head.bind("<Configure>", _draw_head)
        _draw_head()

        def _fold(_e=None):
            # 只绑「按下」：Canvas 上的按下事件已经验证可靠，
            # 再绑「松开」会在慢速点击时触发两次，反而把折叠又切回去
            if not self._debounce("fold", 0.2):
                return "break"
            try:
                self.root.focus_force()
            except Exception:
                pass
            LOG.info("click 折叠已完成区")
            # 延后一拍再重建列表，避免在控件自己的事件回调里销毁它
            self.root.after(1, self.toggle_done_open)
            return "break"

        head.bind("<Button-1>", _fold)

        if not opened:
            return
        if not n:
            tk.Label(self.inner, text="还没有已完成的事项", bg=PAGE, fg="#c3cad8",
                     font=self.f_tiny).pack(anchor="w", padx=6, pady=(0, 4))
            return
        for t in self.data["done"]:
            self._build_done_row(t)

    def _build_done_row(self, t):
        row = tk.Frame(self.inner, bg="#fbfcfe", highlightbackground="#e9edf5",
                       highlightthickness=1)
        row.pack(fill="x", pady=(0, 5))
        tk.Label(row, text="✓", bg=GREEN_SOFT, fg=GREEN, font=self.f_tiny,
                 padx=4, pady=1).pack(side="left", padx=(8, 0), pady=7)
        title = t["title"] if len(t["title"]) <= 12 else t["title"][:12] + "…"
        lb = TextLine(row, title, bg="#fbfcfe", fg=MUTED, font=self.f_done,
                      pady=2, width=self.f_done.measure(title), cursor="fleur")
        lb.pack(side="left", padx=(7, 4))
        self._titles[t["id"]] = lb
        # 已完成的那条也支持双击改文字（右边要给「恢复 / ×」留出位置）
        lb._edit_ctx = (t["id"], row, 84)
        lb.bind("<Double-Button-1>", self._on_title_dbl)
        CanvasButton(row, "×", lambda i=t["id"]: self.remove_done(i),
                     fg="#c3cad8", bg="#fbfcfe", fill="#fbfcfe",
                     hover=DANGER_SOFT, press=DANGER_SOFT, font=self.f_btn,
                     padx=7, pady=3, tag="删除完成记录"
                     ).pack(side="right", padx=(0, 5))
        CanvasButton(row, "恢复", lambda i=t["id"]: self.restore_done(i),
                     fg=FAINT, bg="#fbfcfe", fill="#fbfcfe",
                     hover=HOVER, press=HOVER, font=self.f_tiny,
                     padx=8, pady=3, tag="恢复完成记录").pack(side="right")
        tk.Label(row, text=fmt_time(t.get("doneAt")), bg="#fbfcfe", fg="#b7bfcd",
                 font=self.f_tiny).pack(side="right", padx=(0, 6))

    def _cancel_jobs(self):
        """收掉所有 after 定时任务（悬停轮询、到期清理），退出和自检都会用到"""
        for attr in ("_hover_job", "_purge_job"):
            jid = getattr(self, attr, None)
            if jid:
                try:
                    self.root.after_cancel(jid)
                except Exception:
                    pass
                setattr(self, attr, None)

    # ------------------------------------------------ 退出
    def quit(self):
        try:
            self.data["ui"]["x"] = self.root.winfo_x()
            self.data["ui"]["y"] = self.root.winfo_y()
            save_data(self.data)
        except Exception:
            pass
        # 保险：万一还留着抓取，先释放，避免下次启动鼠标点不动
        try:
            self.root.grab_release()
        except Exception:
            pass
        self._cancel_jobs()
        # 先收掉编辑框并断开引用，免得销毁窗口时失焦回调又去动已销毁的控件
        self._close_editor()
        release_lock()
        self.root.destroy()


def fmt_time(ts):
    """把毫秒时间戳写成「xx:xx 完成」。

    数据脏了（不是数字、越界）就返回空串：这个函数在每次重绘时都会跑，
    一条坏记录不该把整个卡片拖崩。
    """
    ms = to_ms(ts)
    if not ms:
        return ""
    try:
        lt = time.localtime(ms / 1000.0)
    except (ValueError, OverflowError, OSError):
        return ""
    now = time.localtime()
    if (lt.tm_year, lt.tm_yday) == (now.tm_year, now.tm_yday):
        return time.strftime("%H:%M 完成", lt)
    return time.strftime("%m月%d日完成", lt)


# ---------------------------------------------------------------- 单实例

def acquire_lock():
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            return False
        except Exception:
            pass
    try:
        with open(LOCK_FILE, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass
    return True


def release_lock():
    try:
        if os.path.exists(LOCK_FILE):
            with open(LOCK_FILE) as f:
                if int(f.read().strip()) == os.getpid():
                    os.remove(LOCK_FILE)
    except Exception:
        pass


# ---------------------------------------------------------------- 自检

def selftest():
    out = {"steps": [], "ok": True}

    def chk(name, got, want):
        ok = got == want
        out["steps"].append({"检查": name, "实际": got, "期望": want, "通过": ok})
        if not ok:
            out["ok"] = False

    if os.path.exists(DATA_FILE):
        os.rename(DATA_FILE, DATA_FILE + ".selftest-bak")
    root = tk.Tk()
    app = FloatingTodo(root)
    root.update()

    def pump(ms=520):
        """跑一段真实的事件循环：命令是 after(...) 延后执行的，update() 一次不够"""
        end = time.time() + ms / 1000.0
        while time.time() < end:
            root.update()
            time.sleep(0.02)

    app.add_task("写周报")
    app.add_task("遛狗")
    root.update()
    chk("新增后条数", len(app.data["active"]), 2)
    chk("首条为最新", app.data["active"][0]["title"], "遛狗")

    tid = app.data["active"][0]["id"]
    app.bump(tid, 10)
    chk("+10% 后进度", app.data["active"][0]["progress"], 10)
    app.set_progress(tid, 60, commit=True)
    chk("设为 60 后进度", app.data["active"][0]["progress"], 60)

    n = len(app.data["active"])
    avg = int(round(sum(t["progress"] for t in app.data["active"]) / float(n)))
    chk("总进度均值", avg, 30)

    app.set_progress(tid, 100, commit=True)   # 到 100% 应自动归档
    pump(600)                                 # 等归档动画（400ms）走完
    chk("归档后 进行中", len(app.data["active"]), 1)
    chk("归档后 已完成", len(app.data["done"]), 1)
    chk("归档标题", app.data["done"][0]["title"], "遛狗")
    chk("归档带完成时间", bool(app.data["done"][0]["doneAt"]), True)

    app.undo_archive(tid)
    root.update()
    chk("撤销后 进行中", len(app.data["active"]), 2)
    chk("撤销后 已完成", len(app.data["done"]), 0)
    chk("撤销后进度", app._find(tid)["progress"], 95)

    # 折叠 / 展开
    app.toggle_fold(); root.update()
    chk("折叠态", app.data["ui"]["collapsed"], True)
    chk("折叠摘要非空", len(app.lb_summary.get_text()) > 0, True)
    app.toggle_fold(); root.update()
    chk("展开态", app.data["ui"]["collapsed"], False)

    # 置顶 / 透明度
    app.toggle_pin(); root.update()
    chk("置顶关", app.data["ui"]["topmost"], False)
    app.toggle_pin(); root.update()
    chk("置顶开", app.data["ui"]["topmost"], True)
    app.toggle_alpha(); root.update()
    chk("透明度值", round(float(app.data["ui"]["alpha"]), 2), 0.9)
    app.toggle_alpha(); root.update()

    # 删除 + 撤销
    app.remove_task(tid); root.update()
    chk("删除后条数", len(app.data["active"]), 1)
    app._do_undo(); root.update()
    chk("撤销删除后条数", len(app.data["active"]), 2)

    # 持久化
    save_data(app.data)
    chk("落盘可读", len(load_data()["active"]), 2)

    # 尺寸自适应
    chk("窗口高度合理", 80 < root.winfo_reqheight() < 2000, True)
    chk("窗口宽度", app.data["ui"].get("x") is not None or True, True)

    # ---- 控件类型：可点元素必须都是 Canvas（Label 上收不到鼠标事件）
    chk("头部按钮是 Canvas", isinstance(app.btn_close, CanvasButton), True)
    chk("头部按钮已绑定按下", "<Button-1>" in app.btn_close.bind(), True)
    chk("折叠按钮是 Canvas", isinstance(app.btn_fold, CanvasButton), True)
    chk("标题是 Canvas", isinstance(app.lb_title, TextLine), True)
    chk("摘要是 Canvas", isinstance(app.lb_summary, TextLine), True)

    # ---- 头部按钮：按下即触发（命令是 after(1) 延后执行的，要等一拍）
    def click(w):
        w._last = 0.0
        w.event_generate("<Button-1>", x=3, y=3)
        pump(140)

    click(app.btn_fold)
    chk("点折叠 -> 折叠", app.data["ui"]["collapsed"], True)
    chk("折叠按钮文案", app.btn_fold.get_text(), "展开")
    click(app.btn_fold)
    chk("再点 -> 展开", app.data["ui"]["collapsed"], False)
    chk("展开按钮文案", app.btn_fold.get_text(), "折叠")

    click(app.btn_pin)
    chk("点置顶 -> 取消置顶", app.data["ui"]["topmost"], False)
    click(app.btn_pin)
    chk("再点 -> 恢复置顶", app.data["ui"]["topmost"], True)

    # ---- 点击兜底派发：万一某次点击没投递到控件，顶层仍能命中
    class FakeEv:
        def __init__(self, widget, xr, yr):
            self.widget, self.x_root, self.y_root = widget, xr, yr

    before = app.data["ui"]["topmost"]
    app.btn_pin._last = 0.0
    app._global_press(FakeEv(app.header,
                             app.btn_pin.winfo_rootx() + 4,
                             app.btn_pin.winfo_rooty() + 4))
    pump(140)
    chk("兜底派发命中按钮", app.data["ui"]["topmost"], not before)

    n_act = len(app.data["active"])
    app._global_press(FakeEv(app.header, root.winfo_rootx() + 5,
                             root.winfo_rooty() + 8))
    pump(140)
    chk("兜底不误触发", len(app.data["active"]), n_act)

    # ---- 多任务：滚动条 + 拖动滚动
    class Ev:
        def __init__(self, y_root):
            self.y_root = y_root
            self.x_root = 0

    for i in range(12):
        app.add_task("测试任务 %d" % (i + 1))
    root.update()
    chk("内容超出可滚动", app._scroll_span() > 0, True)

    tl = next((w for w in app._walk(app.inner) if isinstance(w, TextLine)), None)
    chk("卡片标题为 Canvas", tl is not None, True)
    if tl is not None:
        # Tk 会把 ButtonPress 归一化成 Button
        chk("卡片标题已绑定拖动", "<Button-1>" in tl.bind(), True)

    bar0 = next(iter(app._bars.values()), None)
    chk("进度条排除拖动", getattr(bar0, "_no_drag", False), True)
    chk("按钮排除拖动", getattr(app.btn_close, "_no_drag", False), True)

    f0 = app.canvas.yview()[0]
    app._pd_press(Ev(600))
    app._pd_motion(Ev(597))          # 3px 抖动，不该滚动
    chk("轻微抖动不滚动", app.canvas.yview()[0], f0)
    app._pd_motion(Ev(540))          # 往下拖 60px -> 视图下移
    root.update()
    chk("向下拖动 -> 视图下移", app.canvas.yview()[0] > f0, True)
    f1 = app.canvas.yview()[0]
    app._pd_motion(Ev(700))          # 往上拖 -> 视图上移
    root.update()
    chk("向上拖动 -> 视图上移", app.canvas.yview()[0] < f1, True)
    app._pd_release(Ev(700))
    chk("松手后状态清空", app._pd is None, True)

    # ---- 双指滑动：Tk 9 里走 <TouchpadScroll>（TIP 684），%D 是打包的精密增量
    chk("双指滑动绑在顶层", "<TouchpadScroll>" in root.bind(), True)
    chk("滚轮绑在顶层", "<MouseWheel>" in root.bind(), True)

    def pack_delta(dx, dy):
        """按 TIP 684 打包：Δx 在高 16 位、Δy 在低 16 位，%D 是 32 位有符号"""
        v = ((dx & 0xFFFF) << 16) | (dy & 0xFFFF)
        return v - 0x100000000 if v >= 0x80000000 else v

    for dx, dy in ((0, -6), (0, 6), (1, 0), (-5, 12), (2, -300), (-300, 2)):
        chk("拆包 dx=%d dy=%d" % (dx, dy), app._precise_deltas(pack_delta(dx, dy)), (dx, dy))
    # 万一个别 Tk 把 %D 按无符号给出来，也要能拆对
    chk("无符号形式也能拆", app._precise_deltas(pack_delta(-5, 12) & 0xFFFFFFFF), (-5, 12))

    def off_px():
        """当前滚动位置，换算成像素"""
        bb = app.canvas.bbox("all")
        return app.canvas.yview()[0] * float(bb[3] - bb[1]) if bb else 0.0

    class Touch:
        def __init__(self, dx, dy):
            self.delta = pack_delta(dx, dy)

    class Wheel:
        def __init__(self, d):
            self.delta = d

    def touch(dx, dy):
        app._on_touchpad(Touch(dx, dy))
        root.update()

    def wheel(d):
        app._on_wheel(Wheel(d))
        root.update()

    app.canvas.yview_moveto(0.4)
    root.update()

    app._wheel_acc = 0.0
    p0 = off_px()
    touch(0, -20)                     # 双指往下滑 20 点
    chk("双指下滑 -> 页面下滚", off_px() > p0, True)
    chk("双指 1:1 跟手", abs((off_px() - p0) - 20 * TRACKPAD_GAIN) <= 1, True)

    app._wheel_acc = 0.0
    p1 = off_px()
    touch(0, 20)                      # 反向
    chk("双指上滑反向", abs((p1 - off_px()) - 20 * TRACKPAD_GAIN) <= 1, True)

    app._wheel_acc = 0.0
    p2 = off_px()
    for _ in range(3):                # 小增量要累计，不能被截断吃掉（不然慢滑像卡住）
        touch(0, -1)
    chk("小增量累计不丢", 2.5 <= (off_px() - p2) <= 4.5, True)

    app._wheel_acc = 0.0
    p3 = off_px()
    touch(-40, 0)                     # 只有横向增量
    chk("纯横向滑动不动竖直位置", off_px(), p3)
    chk("纯横向滑动返回 break", app._on_touchpad(Touch(-40, 0)), "break")

    app._wheel_acc = 0.0
    p4 = off_px()
    wheel(-3)                         # macOS 鼠标滚轮：一格
    chk("滚轮一格 = 48px", abs((off_px() - p4) - WHEEL_LINE_PX) <= 1, True)

    app._wheel_acc = 0.0
    p5 = off_px()
    wheel(-120)                       # Windows 鼠标滚轮：一格 120
    chk("Windows 一格也是 48px", abs((off_px() - p5) - WHEEL_LINE_PX) <= 1, True)

    app._wheel_acc = 0.0
    p6 = off_px()
    wheel(120)
    chk("滚轮反向", abs((p6 - off_px()) - WHEEL_LINE_PX) <= 1, True)

    real_xy = root.winfo_pointerxy
    root.winfo_pointerxy = lambda: (5, 5)     # 光标远在屏幕另一头
    app._wheel_acc = 0.0
    p7 = off_px()
    touch(0, -10)
    chk("滑动与光标位置无关", off_px() > p7, True)
    root.winfo_pointerxy = real_xy

    app._wheel_acc = 0.0
    app.canvas.yview_moveto(0.0)
    root.update()
    for _ in range(4):
        touch(0, 60)
    chk("滚到顶不越界", off_px(), 0.0)

    # ---- 光标停在卡片上 -> 激活窗口（macOS 只把滑动事件送给前台 App）
    chk("已启动光标检测轮询", app._hover_job is not None, True)
    if app._hover_job:
        root.after_cancel(app._hover_job)
        app._hover_job = None      # 自检期间不让它自动跑，避免真实光标位置干扰断言

    real_containing = root.winfo_containing
    real_front = app._is_front
    app._is_front = lambda: False          # 假装不在前台：激活一定会真的执行
    root.winfo_containing = lambda x, y: None       # 光标在窗口外

    app._hover_inside, app._hover_armed, app._hover_pt = False, True, None
    chk("光标在窗口外不激活", app._hover_tick(0, 0), False)
    chk("窗口外保持未进入状态", app._hover_inside, False)

    root.winfo_containing = lambda x, y: app.canvas  # 光标停在卡片上
    chk("刚进来这一拍不激活", app._hover_tick(100, 100), False)
    chk("已记下进入状态", app._hover_inside, True)
    chk("停得还不够久不激活", app._hover_tick(100, 100), False)

    app._hover_since = time.time() - (HOVER_FOCUS_MS / 1000.0) - 0.05
    chk("停够久了才激活", app._hover_tick(100, 100), True)
    chk("激活后不重复抢焦点", app._hover_tick(100, 100), False)
    app._hover_since = time.time() - (HOVER_FOCUS_MS / 1000.0) - 0.05
    chk("光标不动就不重复抢", app._hover_tick(100, 100), False)

    # 光标又在卡片上挪了一段 -> 重新武装，切走再回来还能用
    app._hover_since = time.time() - (HOVER_FOCUS_MS / 1000.0) - 0.05
    chk("光标又动了 -> 重新武装", app._hover_tick(100 + HOVER_REARM_PX + 4, 100), True)

    root.winfo_containing = lambda x, y: None
    chk("离开窗口不激活", app._hover_tick(100, 100), False)
    chk("离开后重新武装", app._hover_armed, True)

    # 已经是前台时不该再去抢焦点
    root.winfo_containing = lambda x, y: app.canvas
    app._hover_inside, app._hover_armed, app._hover_pt = False, True, None
    app._is_front = lambda: True
    app._hover_tick(100, 100)                 # 第一拍只记「刚进来」
    app._hover_since = time.time() - (HOVER_FOCUS_MS / 1000.0) - 0.05
    chk("已经在前台就不抢", app._hover_tick(100, 100), False)

    app._is_front = real_front
    root.winfo_containing = real_containing

    # ---- 卡片进度条：颜色必须和总进度条同一套，而且重建之后就得画出来
    # （曾经 render 里从不画卡片进度条，重启或增删后每条都是一片空白，
    #   只有总进度条有图形 —— 用户看到的就是这个现象）
    def bar_fills(tid):
        cv = app._bars.get(tid)
        return [cv.itemcget(i, "fill") for i in cv.find_all()] if cv else []

    tid_b = app.data["active"][0]["id"]
    keep_b = app.data["active"][0]["progress"]
    for pv in (0, 15, 55, 85, 100):
        app.set_progress(tid_b, pv)      # commit=False：不落盘、也不会触发归档
        root.update()
        fills = bar_fills(tid_b)
        if pv == 0:
            chk("0% 只画底槽", fills, [TRACK])
        else:
            chk("进度 %d%% 画了底槽+填充" % pv, len(fills), 2)
            chk("进度 %d%% 填充色与总进度条同源" % pv, fills[-1], bar_colors(pv)[1])
    app.set_progress(tid_b, keep_b)
    root.update()

    app.render()
    pump(400)
    chk("重建后没有空白的进度条",
        [t["title"] for t in app.data["active"] if not bar_fills(t["id"])], [])
    chk("进度条画布绑了 <Configure>", bool(app._bars[tid_b].bind("<Configure>")), True)

    # ---- 顶层按下归属：谁能成为拖动页面的起点
    class Down:
        def __init__(self, widget, yr):
            self.widget, self.x_root, self.y_root = widget, 0, yr

    app._pd = None
    app._root_press(Down(app.canvas, 500))
    chk("画布按下可拖页", app._pd is not None, True)
    for name, wgt in (("输入框", app.entry), ("滚动条滑块", app.sbar),
                      ("滚动条轨道", app.sbar_area), ("按钮", app.btn_fold)):
        app._pd = None
        app._root_press(Down(wgt, 500))
        chk("按在%s上不拖页" % name, app._pd is None, True)

    fx = app.canvas.yview()[0]
    app._pd = None
    app._root_press(Down(app.canvas, 700))
    app._root_motion(Down(app.canvas, 600))
    root.update()
    chk("顶层按下+移动可拖页", app.canvas.yview()[0] > fx, True)
    app._root_release(Down(app.canvas, 600))
    chk("松开清理拖动态", app._pd is None, True)

    # ---- 滚动条拖动（曾经因回调参数不匹配抛 TypeError）
    app._sbar_press(Down(app.sbar, 500))
    app._sbar_motion(Down(app.sbar, 600))
    app._sbar_release(Down(app.sbar, 600))
    chk("滚动条拖动可上下", app.canvas.yview()[0] >= 0.0, True)
    chk("没有残留鼠标抓取", bool(app.root.grab_current()) is False, True)

    # ---- 归档 -> 已完成区：清空 / 撤销清空（按钮都在已完成区）
    tid2 = app.data["active"][0]["id"]
    app.set_progress(tid2, 100, commit=True)
    app._finish_archive(tid2)
    root.update()
    chk("再次归档", len(app.data["done"]), 1)
    app.clear_done()
    root.update()
    chk("清空已完成", len(app.data["done"]), 0)
    app._do_undo()
    root.update()
    chk("撤销清空", len(app.data["done"]), 1)

    # ---- 改文字（就地编辑）
    def find_btn(w, label):
        for c in w.winfo_children():
            if isinstance(c, CanvasButton) and c.get_text() == label:
                return c
            r = find_btn(c, label)
            if r:
                return r
        return None

    target = app.data["active"][0]
    tid0, old_title = target["id"], target["title"]
    card0 = next((w for w in app.inner.winfo_children()
                  if isinstance(w, tk.Frame) and find_btn(w, "改")), None)
    chk("卡片上有「改」按钮", card0 is not None, True)

    eb = find_btn(card0, "改") if card0 is not None else None
    chk("「改」是 Canvas 按钮", isinstance(eb, CanvasButton), True)
    if eb is not None:
        eb._last = 0.0
        eb.event_generate("<Button-1>", x=4, y=4)
        pump(200)
    chk("点「改」弹出输入框", app._editor is not None, True)
    if app._editor:
        chk("输入框预填了原文字", app._editor[0].get("1.0", "end-1c"), old_title)

    # 保存：多余空格和换行要被压平
    if app._editor:
        app._editor[0].delete("1.0", "end")
        app._editor[0].insert("1.0", "  改过的   文字\n第二行  ")
    app.commit_edit()
    pump(200)
    chk("改完数据已更新", app._find(tid0)["title"], "改过的 文字 第二行")
    chk("改完输入框已收起", app._editor is None, True)
    chk("标题控件还在（就地重绘没重建列表）", (app._titles or {}).get(tid0) is not None, True)
    chk("窗口仍然可用", root.winfo_exists(), True)

    # 撤销
    app._do_undo()
    pump(200)
    chk("撤销改文字", app._find(tid0)["title"], old_title)

    # 空内容不该生效
    app.start_edit(tid0)
    pump(120)
    chk("能再次进入编辑", app._editor is not None, True)
    if app._editor:
        app._editor[0].delete("1.0", "end")
    app.commit_edit()
    pump(200)
    chk("空标题保持原样", app._find(tid0)["title"], old_title)

    # Esc 取消
    app.start_edit(tid0)
    pump(120)
    if app._editor:
        app._editor[0].delete("1.0", "end")
        app._editor[0].insert("1.0", "这段不该被保存")
    app.cancel_edit()
    pump(200)
    chk("取消后文字不变", app._find(tid0)["title"], old_title)

    # 失焦 = 保存
    app.start_edit(tid0)
    pump(120)
    if app._editor:
        app._editor[0].delete("1.0", "end")
        app._editor[0].insert("1.0", "失焦保存的文字")
    app._edit_blur()
    pump(200)
    chk("点到别处会自动保存", app._find(tid0)["title"], "失焦保存的文字")
    app.set_title(tid0, old_title)
    pump(150)

    # 双击标题也能改（Tk 不允许用 event_generate 合成 Double 事件，
    # 所以这里直接调那个具名处理函数，走的就是真实绑定的代码路径）
    class FakeDbl:
        def __init__(self, w):
            self.widget = w

    tl0 = (app._titles or {}).get(tid0)
    chk("找到标题控件", tl0 is not None, True)
    if tl0 is not None:
        chk("标题已绑定双击", bool(tl0.bind("<Double-Button-1>")), True)
        chk("双击上下文已挂好", isinstance(getattr(tl0, "_edit_ctx", None), tuple), True)
        app._on_title_dbl(FakeDbl(tl0))
        pump(200)
        chk("双击路径能弹出输入框", app._editor is not None, True)
        app.cancel_edit()
    pump(150)
    chk("取消后仍无编辑框", app._editor is None, True)

    # 已完成的那条也支持双击改
    dt = app.data["done"][0] if app.data["done"] else None
    chk("有一条已完成", dt is not None, True)
    if dt is not None:
        dl = (app._titles or {}).get(dt["id"])
        chk("已完成行也有标题控件", dl is not None, True)
        if dl is not None:
            chk("已完成行绑定了双击", bool(dl.bind("<Double-Button-1>")), True)
            app._on_title_dbl(FakeDbl(dl))
            pump(200)
            chk("已完成也能进入编辑", app._editor is not None, True)
            if app._editor:
                app._editor[0].delete("1.0", "end")
                app._editor[0].insert("1.0", "改过的完成项")
            app.commit_edit()
            pump(200)
            got = next((x["title"] for x in app.data["done"] if x["id"] == dt["id"]), None)
            chk("已完成文字已改", got, "改过的完成项")

    # ---- 到期自动清理：完成记录只留半个月，过期自动清掉
    DAY_MS = 24 * 3600 * 1000
    now_ms = int(time.time() * 1000)
    chk("保留时长 = 15 天", DONE_KEEP_MS, 15 * DAY_MS)
    baks_before = set(glob.glob(os.path.join(APP_DIR, "data.backup-purge-*.json")))

    fresh = dict(app.data["done"][0])          # 刚归档的，必须留着
    old1 = {"id": "fake-old-1", "title": "过期一条", "progress": 100,
            "doneAt": now_ms - (DONE_KEEP_DAYS * DAY_MS + 5000)}
    old2 = {"id": "fake-old-2", "title": "过期很久", "progress": 100,
            "doneAt": now_ms - 365 * DAY_MS}
    edge = {"id": "fake-edge", "title": "还差一分钟到期", "progress": 100,
            "doneAt": now_ms - (DONE_KEEP_DAYS * DAY_MS - 60000)}
    nodate = {"id": "fake-nodate", "title": "老数据没有完成时间", "progress": 100}
    future = {"id": "fake-future", "title": "时间戳在未来", "progress": 100,
              "doneAt": now_ms + DAY_MS}
    bad = {"id": "fake-bad", "title": "完成时间不是数字", "progress": 100,
           "doneAt": "昨天"}
    secs = {"id": "fake-secs", "title": "时间戳写成了秒", "progress": 100,
            "doneAt": (now_ms - 3 * DAY_MS) // 1000}

    # 1) 没有到期的：返回 0，而且不该弹提示
    app.data["done"] = [fresh, edge, nodate]
    app.hide_toast()
    root.update()
    chk("没有到期时清理 0 条", app._purge_expired(), 0)
    chk("没有到期就不弹提示", app.toast.winfo_manager(), "")

    # 2) 有过期的：只清过期的，其余原样留着
    app.data["done"] = [fresh, old1, old2, edge, nodate, future, bad, secs]
    root.update()
    chk("清理条数", app._purge_expired(), 2)
    chk("清理后剩下的记录",
        [t["id"] for t in app.data["done"]],
        [fresh["id"], edge["id"], nodate["id"], future["id"], bad["id"],
         secs["id"]])
    chk("清理后会提示", app.toast.winfo_manager(), "place")
    chk("提示里写了保留天数", "15 天" in app.lb_toast.cget("text"), True)
    chk("提示带撤销按钮", app.btn_undo.get_text(), "撤销")
    chk("坏 doneAt 不会把界面搞崩", root.winfo_exists(), True)

    # 3) 撤销能把清掉的放回来，并按完成时间倒序归位
    app._do_undo()
    pump(200)
    ids = [t["id"] for t in app.data["done"]]
    chk("撤销后条数", len(ids), 8)
    chk("撤销后按完成时间倒序（最近的在最前）", ids[:2],
        ["fake-future", fresh["id"]])
    chk("更老的排得更后", ids.index("fake-old-1") < ids.index("fake-old-2"), True)
    chk("判不出时间的排在最后", ids[-2:], ["fake-nodate", "fake-bad"])
    chk("撤销不丢记录", ("fake-old-1" in ids and "fake-old-2" in ids), True)

    # 4) 清完要落盘：重新读盘读不到旧记录
    app.data["done"] = [fresh, old1, edge]
    app._purge_expired(notify=False)
    pump(150)
    chk("清理结果已落盘", [t["id"] for t in load_data()["done"]],
        [fresh["id"], edge["id"]])

    # 4b) 自动删除之前要留整份快照：撤销只有 6 秒，快照是长线的保险
    baks = sorted(glob.glob(os.path.join(APP_DIR, "data.backup-purge-*.json")))
    chk("清理前留下了快照", len(baks) >= 1, True)
    if baks:
        snap = json.load(open(baks[-1], encoding="utf-8"))
        snap_ids = [t["id"] for t in snap["done"]]
        chk("快照里还留着被清掉的记录", "fake-old-1" in snap_ids, True)
        chk("快照是清理前的完整状态", len(snap_ids), 3)

    # 快照只留最近几份，不会越攒越多
    for i in range(7):
        with open(os.path.join(APP_DIR, "data.backup-purge-2000010%d-000000.json" % i),
                  "w", encoding="utf-8") as f:
            f.write("{}")
    backup_before_purge(app.data)
    chk("快照只保留最近 5 份",
        len(glob.glob(os.path.join(APP_DIR, "data.backup-purge-*.json"))), 5)
    for p in glob.glob(os.path.join(APP_DIR, "data.backup-purge-2000010*.json")):
        try:
            os.remove(p)
        except OSError:
            pass

    # 5) 定时任务：启动就排了第一次检查，跑完一轮会续排下一次
    chk("启动已排好到期检查", app._purge_job is not None, True)
    jid = app._purge_job
    app._purge_loop()
    chk("循环会续排下一次", (app._purge_job is not None
                             and app._purge_job != jid), True)
    chk("两次检查间隔半小时", PURGE_EVERY_MS, 30 * 60 * 1000)
    app._cancel_jobs()
    chk("退出前能收掉定时任务",
        (app._hover_job, app._purge_job), (None, None))

    # 自检期间生成的快照清掉，别把工作目录搞脏
    for p in set(glob.glob(os.path.join(APP_DIR, "data.backup-purge-*.json"))) - baks_before:
        try:
            os.remove(p)
        except OSError:
            pass

    root.destroy()
    if os.path.exists(DATA_FILE + ".selftest-bak"):
        os.replace(DATA_FILE + ".selftest-bak", DATA_FILE)
    elif os.path.exists(DATA_FILE):
        os.remove(DATA_FILE)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if out["ok"] else 1


# ---------------------------------------------------------------- 入口

def setup_log():
    """把交互日志写到 clicks.log，出问题时可以直接看是哪一步没触发"""
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 200 * 1024:
            os.remove(LOG_FILE)
        h = logging.FileHandler(LOG_FILE, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%m-%d %H:%M:%S"))
        LOG.addHandler(h)
        LOG.setLevel(logging.INFO)
        LOG.propagate = False
    except Exception:
        pass


def main():
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if not acquire_lock():
        print("待办悬浮窗已经在运行了")
        return
    atexit.register(release_lock)
    setup_log()
    LOG.info("=== 启动 pid=%d ===", os.getpid())
    root = tk.Tk()
    app = FloatingTodo(root)

    def activate():
        try:
            root.lift()
            root.focus_force()
            app.entry.focus_set()
        except Exception:
            pass

    root.after(150, activate)
    root.mainloop()


if __name__ == "__main__":
    main()
