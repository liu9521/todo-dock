# data.json 数据格式与内部机制

## 文件位置

- 挂件主目录：`~/.workbuddy/todo-widget/`（环境变量 `TODO_WIDGET_HOME` 可覆盖）
- 数据文件：`<挂件主目录>/data.json` —— 待办数据的唯一来源
- 金价缓存：`<挂件主目录>/gold_cache.json` —— 上次成功的浙商积存金报价（离线兜底）
- 运行标记：`<挂件主目录>/.running` —— 当前挂件进程 PID
- 运行日志：`<挂件主目录>/dock.log` —— 启动、热加载、提醒、金价异常
- 程序本体：`<挂件主目录>/todo_dock.py`（默认）；旧版 `todo_float.py`；macOS 启动器 `待办悬浮窗.app`
- 使用手册：`<挂件主目录>/使用说明.html`

## 结构

```json
{
  "active": [ 待办, ... ],
  "done":   [ 待办, ... ],
  "ui": {
    "x": null, "y": null,        // 窗口位置（null = 自动贴边）
    "topmost": true,             // 置顶
    "alpha": 0.9,                // 不透明度
    "collapsed": false,          // 折叠态
    "done_open": true,           // 已完成区展开
    "follow": true
  }
}
```

每条待办：

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | `"%x%04x" % (毫秒时间戳, 2字节随机数)`，如 `1a13d37ece` |
| title | string | 标题，可含换行（就地编辑支持多行） |
| progress | int | 0-100；到 100 自动归档到 done |
| createdAt | int | 毫秒时间戳 |
| doneAt | int/null | 完成时间；进行中为 null |
| due | string/null | 可选，截止日期 `"YYYY-MM-DD"`。日历胶囊用来排期、打点、触发提醒；旧版忽略 |
| time | string | 可选，提醒时刻 `"HH:MM"`，默认 `"09:00"` |
| remind | string | 可选，`both`（默认）/ `day` / `eve` / `none`。both=当天+提前一天 |
| notified | object | 可选，`{"d0":"YYYY-MM-DD","d1":"..."}`，已提醒标记，防止重复弹窗 |
| ui.dock_rx / dock_ry | int | 可选，胶囊右下角距屏幕右下角的像素偏移，拖动后记住 |

### 两类待办

| 类型 | 判定 | 行为 |
|------|------|------|
| 近期待办 | 没有 `due` 字段，或 `due` 为 null | 列表标签「近期」；排序档位 1（紧急之后、远期之前）；不进日历、不提醒、不计入「今日 N」 |
| 日期待办 | `due` 为 `"YYYY-MM-DD"` | 日历打点、可按 `time`/`remind` 触发当天/提前一天提醒 |

挂件里点「+ 近期」或「近期」按钮后 `sel = None`，此时添加待办只写
`id/title/progress/createdAt/doneAt` 五个基础字段，并把提醒控件置为 disabled。
命令行 `todo.py add "标题"`（不带 `--due`）等价；`todo.py setdue <词> none` 可把日期待办转回近期待办
（会移除 `due`/`time`/`remind`/`notified`），`setdue <词> YYYY-MM-DD` 则补上日期并重置提醒。

> 新增字段都是**可选**的：`todo_dock.py` 的 `load_data()` 只校验 `active`/`done` 是数组，
> 其余字段原样保留，所以旧版 `todo_float.py` 写回时不会破坏这些排期字段的读取方。
> （反过来，若旧版进程与新版同时运行，旧版保存时可能丢掉这些字段——靠 `.running` 单实例锁避免。）

序列化格式：`json.dump(..., ensure_ascii=False, indent=1)`，
写入方式：先写 `.tmp` 再 `os.replace` 原子替换。

## 热加载机制（挂件侧）

- `todo_dock.py` 每 300ms 一拍 `_poll()`，比对 data.json 的 `st_mtime_ns` 与内存基线；
- 基线在挂件启动加载后、以及挂件每次自己保存后刷新——
  所以只有"外部程序改的文件"才会触发重载；
- 重载时替换整份数据并重新渲染，**ui 状态保留内存里的**，
  因此外部改动不会把窗口弹回默认位置；
- 读文件失败（写一半、被占用）时该拍跳过、保留内存数据、基线不刷新，下拍再试；
  **绝不回退成空数据**，否则下一次保存会抹掉全部待办。

**已知边界**：若外部改动落在"挂件自己正要保存"的同 0.3 秒窗口内，
且挂件先完成了保存，外部那次改动可能被覆盖（挂件会把内存数据写回）。
日常使用概率极低；重要操作后可用 `todo.py list` 核对一次。

## 金价缓存（gold_cache.json）

```json
{"price": 840.05, "rate": "+0.85%", "amt": "+7.04",
 "ts": 1794536409.0, "fetched": 1794536410.2}
```

- 数据源：京东金融 H5 `stdLatestPrice?productSku=1961543816`（浙商银行积存金实时报价）。
- 后台线程取数 → `queue.Queue` → 主线程 `after` 消费后刷新 Label，不阻塞 Tk 主循环。
- **每 5 秒刷新**（`GOLD_INTERVAL`），请求 4 秒超时，失败也 5 秒重试
  （`GOLD_RETRY`）；失败时保留内存里上次报价并标 `cached`，
  界面上时间后带 `*`、展开条写「缓存」。
- 涨跌方向：优先 `upAndDownAmt` / `upAndDownRate` 的符号或数值，
  接口偶尔不带符号时用 `price - yesterdayPrice` 兜底；涨红跌绿，平盘灰。
- **必须单独存文件**：写进 data.json 会让 `st_mtime_ns` 变化，
  触发挂件自己的热加载，每 5 秒无谓重绘一次，还可能与用户编辑撞车。

## 备份文件

- `data.backup-HHMM.json`：挂件到期清理 done 记录前的快照（保留最近 5 份）
- `data.json.backup-cli`：todo.py remove 前的单份滚动备份

## 恢复数据

把任一备份复制为 `data.json`（挂件开着的话 0.3 秒自动刷新）。
