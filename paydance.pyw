# -*- coding: utf-8 -*-
"""
下班倒计时 · 今日已赚 桌面小窗（Windows 11）
========================================================
无边框半透明小窗，常驻桌面：
  · 距下班实时倒计时（含午休分段，午休不算钱）
  · 按日薪实时折算「今日已赚」金额（每秒跳动）
  · 工作日进度条 / 下班前 1 小时变橙、10 分钟变红
  · 置顶 / 贴桌面底层，可拖动
  · 系统托盘图标 + 右键菜单（隐藏/显示、开机自启开关等）
  · 老板键 Ctrl+Alt+D 隐藏/呼出
  · 配置文件 config.json，改完在托盘菜单「重新加载配置」

纯 Python 标准库（tkinter + ctypes）实现逻辑，界面绘制依赖 Pillow。
运行：双击 启动下班倒计时.bat 或直接 pythonw paydance.pyw
"""

import ctypes
import ctypes.wintypes as wt
import datetime as dt
import json
import math
import multiprocessing
import os
import queue
import re
import shutil
import struct
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
import urllib.parse
import winreg
import tkinter as tk
from tkinter import messagebox
from settings_bridge import SettingsWindow

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageTk
except ImportError:
    Image = ImageDraw = ImageFont = ImageFilter = ImageTk = None

FONT_DIR = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")

APP_NAME = "下班倒计时"
APP_ID = "PayDance"         # 沿用原配置和缓存目录，避免改名后丢失设置
APP_VERSION = "1.0.0"       # 唯一来源：打包时的 EXE 版本资源也从这里读
APP_TITLE = "下班倒计时"
MUTEX_NAME = "PayDanceWidget_SingleInstance_v1"
ERROR_ALREADY_EXISTS = 183
LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()), APP_ID)
LOG_PATH = os.path.join(LOG_DIR, "paydance.log")
LOG_MAX_BYTES = 512 * 1024
LOG_RETENTION_DAYS = 14
LOG_BACKUP_COUNT = 2

# ---------------------------------------------------------------- 工具函数

def log_exc():
    """只记录当前异常，并通过轮转和过期清理限制磁盘占用。"""
    if sys.exc_info()[0] is None:
        return
    try:
        cleanup_exception_logs()
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("\n[%s]\n%s\n" % (dt.datetime.now(), traceback.format_exc()))
        if os.path.getsize(LOG_PATH) > LOG_MAX_BYTES:
            cleanup_exception_logs(force_rotate=True)
    except Exception:
        pass


def cleanup_exception_logs(force_rotate=False):
    """清理过期异常日志，并最多保留当前文件和两个轮转备份。"""
    paths = [LOG_PATH] + [LOG_PATH + ".%d" % i
                          for i in range(1, LOG_BACKUP_COUNT + 1)]
    cutoff = time.time() - LOG_RETENTION_DAYS * 86400
    for path in paths:
        try:
            if os.path.exists(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            pass
    try:
        if not os.path.exists(LOG_PATH) or (
                not force_rotate and os.path.getsize(LOG_PATH) <= LOG_MAX_BYTES):
            return
        oldest = LOG_PATH + ".%d" % LOG_BACKUP_COUNT
        if os.path.exists(oldest):
            os.remove(oldest)
        for index in range(LOG_BACKUP_COUNT - 1, 0, -1):
            source = LOG_PATH + ".%d" % index
            if os.path.exists(source):
                os.replace(source, LOG_PATH + ".%d" % (index + 1))
        os.replace(LOG_PATH, LOG_PATH + ".1")
        backup = LOG_PATH + ".1"
        if os.path.getsize(backup) > LOG_MAX_BYTES:
            with open(backup, "rb") as f:
                tail = f.read()[-LOG_MAX_BYTES:]
            with open(backup, "wb") as f:
                f.write(tail)
    except OSError:
        pass


def schedule_exception_log_cleanup(root):
    cleanup_exception_logs()
    root.after(24 * 60 * 60 * 1000,
               lambda: schedule_exception_log_cleanup(root))


def emit(text):
    """打包成 windowed 程序后 sys.stdout 是 None，直接 print 会抛 AttributeError。"""
    if sys.stdout is not None:
        print(text)


def fatal_message(text):
    """pythonw / windowed 打包没有控制台，致命错误只能用系统弹窗告诉用户。"""
    try:
        ctypes.windll.user32.MessageBoxW(
            None, ctypes.c_wchar_p(text),
            ctypes.c_wchar_p(APP_NAME), 0x10)
    except Exception:
        pass


def here(name):
    base = (os.path.dirname(os.path.abspath(sys.executable))
            if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


CFG_PATH = (os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                         APP_ID, "config.json")
            if getattr(sys, "frozen", False) else here("config.json"))
CONFIG_WARNING = None

INSURANCE_OPTIONS = {
    "none": "不扣除", "five_one": "五险一金", "six_two": "六险二金"}

DEFAULT_CONFIG = {
    "work_start": "09:00",
    "lunch_start": "12:15",
    "lunch_end": "13:45",
    "work_end": "18:30",
    "daily_salary": 320,
    "salary_mode": "daily",       # daily 日薪 / monthly 月薪
    "salary_stat_mode": "actual", # actual 按记录 / normal 全正常 / overtime 全加班
    "calendar_month": "",         # 当前月逐日设置所属月份
    "calendar_overrides": {},     # 只保存手动修改的日期
    "last_month_summary": None,   # 跨月后仅保留上月合计
    "monthly_salary": 7000,
    "monthly_subsidy": 0,         # 每月固定补贴
    "insurance_mode": "none",    # 不扣除 / 五险一金 / 六险二金
    "monthly_insurance": 0,      # 每月个人缴纳总额
    "monthly_overtime_extra": 20, # 月薪模式：加班当天额外收入
    "workdays": [0, 1, 2, 3, 4],   # 0=周一 ... 6=周日
    "overtime_today": False,        # 今天是否加班
    "overtime_date": "",            # 加班标记所属日期（自动按天重置）
    "overtime_dates": [],          # 已确认加班的日期，用于本月累计
    "overtime_end": "20:30",        # 加班下班时间
    "overtime_daily_salary": 340,   # 加班日目标金额
    "dinner_break_enabled": False,  # 是否在加班日启用晚休
    "dinner_start": "18:30",       # 晚休开始时间
    "dinner_end": "19:00",         # 晚休结束时间
    "position": None,              # [x, y]，null 表示自动放右上角
    "opacity": 1.0,
    "font_scale": 1.0,
    "ui_scale": 0.75,             # 面板整体缩放；实际倍率＝系统DPI × 本项
    "theme": "midnight",
    "animations": True,            # 动效总开关
    "always_on_top": True,
    "desktop_layer": False,        # 贴在桌面底层（壁纸之上、窗口之下）
    "colors": {
        "bg": "#0E1218",
        "bg_grad": "#1A2430",
        "border": "#2A3644",
        "title": "#8B98A5",
        "countdown": "#5BC8F7",
        "warn1": "#FFA94D",
        "warn2": "#FF6B6B",
        "money": "#FFC53D",
        "moneylbl": "#8B98A5",
        "bar_bg": "#1E2834",
        "bar_fg": "#FFC53D",
        "bar_fg2": "#FF9F43",
        "footer": "#6E7B88",
        "dot_ot": "#A78BFA",
    },
}


THEMES = {
    "midnight": {
        "name": "午夜蓝",
        "colors": {
            "bg": "#0B1018", "bg_grad": "#162334",
            "border": "#26384C",
            "title": "#9FB0C3", "countdown": "#6BD4FF",
            "money": "#FFD166", "moneylbl": "#91A4B8",
            "bar_bg": "#1B2A3B",
            "bar_fg": "#FFD166", "bar_fg2": "#FF9F5A",
            "footer": "#778BA1",
        },
    },
    "sakura": {
        "name": "樱花夜",
        "colors": {
            "bg": "#171018", "bg_grad": "#2A1828",
            "border": "#4A2C43",
            "title": "#C5A6BB", "countdown": "#FF91BE",
            "money": "#FFD0A8", "moneylbl": "#BA97AE",
            "bar_bg": "#382237",
            "bar_fg": "#FF91BE", "bar_fg2": "#FFD0A8",
            "footer": "#9C7E92",
        },
    },
    "forest": {
        "name": "松林绿",
        "colors": {
            "bg": "#0B1513", "bg_grad": "#17302A",
            "border": "#29473F",
            "title": "#A2BBB4", "countdown": "#64E0B2",
            "money": "#F5D67B", "moneylbl": "#91ACA4",
            "bar_bg": "#1B3931",
            "bar_fg": "#64E0B2", "bar_fg2": "#F5D67B",
            "footer": "#78978E",
        },
    },
}


# ---------------------------------------------------------------- 颜色工具

def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def rgb2hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c))))
                                   for c in rgb)


def mix(a, b, t):
    """a、b 为十六进制色，t 为 b 的权重"""
    ra, rb = hex2rgb(a), hex2rgb(b)
    return rgb2hex(ra[i] + (rb[i] - ra[i]) * t for i in range(3))


def lerp_rgb(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def _finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(float(value))


def money_value(value):
    if isinstance(value, str):
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", value.strip()):
            raise ValueError("金额必填，须大于等于 0，最多保留两位小数")
        value = float(value.strip())
    if not _finite_number(value) or value < 0 or round(value, 2) != value:
        raise ValueError("金额必填，须大于等于 0，最多保留两位小数")
    return float(value)


def _valid_hex_color(value):
    if not isinstance(value, str) or len(value) != 7 or value[0] != "#":
        return False
    try:
        int(value[1:], 16)
        return True
    except ValueError:
        return False


def validate_config(cfg):
    if cfg.get("salary_mode") not in ("daily", "monthly"):
        raise ValueError("工资模式无效")
    if cfg.get("salary_stat_mode", "actual") not in ("actual", "normal", "overtime"):
        raise ValueError("薪资统计方式无效")
    if cfg.get("insurance_mode", "none") not in INSURANCE_OPTIONS:
        raise ValueError("五险一金 / 六险二金扣除方式无效")
    money_value(cfg.get("monthly_insurance", 0))
    calendar_month = cfg.get("calendar_month", "")
    if calendar_month:
        if not isinstance(calendar_month, str) or len(calendar_month) != 7 or \
                dt.date.fromisoformat(calendar_month + "-01").strftime("%Y-%m") != calendar_month:
            raise ValueError("日历月份无效")
    overrides = cfg.get("calendar_overrides", {})
    if not isinstance(overrides, dict):
        raise ValueError("日历记录无效")
    for date, item in overrides.items():
        if not isinstance(date, str) or not calendar_month or \
                dt.date.fromisoformat(date).strftime("%Y-%m") != calendar_month or \
                not isinstance(item, dict) or item.get("kind") not in (
                    "workday", "makeup", "rest", "statutory") or \
                item.get("attendance") not in ("normal", "overtime", "rest"):
            raise ValueError("日历记录无效")
        amount = item.get("amount")
        if amount is not None:
            money_value(amount)
        if item["kind"] in ("rest", "statutory"):
            if item["attendance"] == "normal":
                raise ValueError("休息日请选择休息或加班")
            if item["attendance"] == "overtime" and amount is None:
                raise ValueError("休息日加班请填写当天收入")
    summary = cfg.get("last_month_summary")
    if summary is not None and (not isinstance(summary, dict) or
            not isinstance(summary.get("month"), str) or
            not _finite_number(summary.get("earned")) or summary["earned"] < 0):
        raise ValueError("上月汇总无效")
    for key in ("daily_salary", "monthly_salary", "overtime_daily_salary",
                "monthly_overtime_extra", "monthly_subsidy"):
        money_value(cfg[key])
    days = cfg["workdays"]
    if not isinstance(days, list) or not days or any(
            not isinstance(day, int) or day not in range(7) for day in days):
        raise ValueError("至少选择一个有效工作日")
    times = {key: parse_t(cfg[key]) for key in (
        "work_start", "lunch_start", "lunch_end", "work_end", "overtime_end",
        "dinner_start", "dinner_end")}
    if any(not 0 <= value < 1440 for value in times.values()) or not (
            times["work_start"] < times["lunch_start"] < times["lunch_end"] <
            times["work_end"] <= times["overtime_end"]):
        raise ValueError("工作时间顺序无效")
    if cfg.get("dinner_break_enabled") and not (
            times["work_end"] <= times["dinner_start"] < times["dinner_end"] <
            times["overtime_end"]):
        raise ValueError("晚休时间顺序无效")
    opacity = cfg.get("opacity")
    font_scale = cfg.get("font_scale")
    if not _finite_number(opacity) or not 0 < float(opacity) <= 1:
        raise ValueError("透明度必须在 0 到 1 之间")
    if not _finite_number(font_scale) or not 0.5 <= float(font_scale) <= 2:
        raise ValueError("字体大小必须在 50% 到 200% 之间")
    ui_scale = cfg.get("ui_scale", 1.0)
    if not _finite_number(ui_scale) or not 0.25 <= float(ui_scale) <= 1.0:
        raise ValueError("面板大小必须在 25% 到 100% 之间")
    colors = cfg.get("colors")
    if not isinstance(colors, dict) or any(
            not _valid_hex_color(colors.get(key)) for key in DEFAULT_CONFIG["colors"]):
        raise ValueError("颜色配置无效")
    position = cfg.get("position")
    if position is not None and (not isinstance(position, (list, tuple)) or
                                 len(position) != 2 or any(
                                     not _finite_number(value) for value in position)):
        raise ValueError("窗口位置配置无效")
    overtime_date = cfg.get("overtime_date", "")
    if overtime_date:
        dt.date.fromisoformat(overtime_date)
    overtime_dates = cfg.get("overtime_dates", [])
    if not isinstance(overtime_dates, list):
        raise ValueError("加班日期配置无效")
    for value in overtime_dates:
        dt.date.fromisoformat(value)
    if (cfg["salary_mode"] == "monthly" or
            float(cfg["monthly_subsidy"]) > 0 or
            insurance_deduction(cfg) > 0) and month_workdays(
                dt.date.today(), cfg) == 0:
        raise ValueError("本月至少需要一个工作日")
    return cfg


def _read_config(path):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("配置格式无效")
    for k, v in data.items():
        if k not in DEFAULT_CONFIG:
            continue
        if k == "colors" and isinstance(v, dict):
            # 只认当前版本还存在的颜色键。旧版本遗留的无用键直接忽略，
            # 否则它们会被一路带回配置文件，永远清不掉。
            cfg["colors"].update({name: value for name, value in v.items()
                                  if name in DEFAULT_CONFIG["colors"]})
        else:
            cfg[k] = v
    if "monthly_overtime_extra" not in data:
        cfg["monthly_overtime_extra"] = max(
            0, float(cfg["overtime_daily_salary"]) - float(cfg["daily_salary"]))
    return validate_config(cfg)


def load_config():
    global CONFIG_WARNING
    CONFIG_WARNING = None
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        return _read_config(CFG_PATH)
    except FileNotFoundError:
        if getattr(sys, "frozen", False) and os.path.exists(here("config.json")):
            try:
                cfg = _read_config(here("config.json"))
            except Exception:
                CONFIG_WARNING = "程序旁的旧配置无效，已使用默认设置。"
                log_exc()
        save_config(cfg)
    except Exception:
        try:
            cfg = _read_config(CFG_PATH + ".bak")
            CONFIG_WARNING = "配置文件损坏，已恢复上次有效备份。"
            save_config(cfg, backup=False)
        except Exception:
            # 配置和备份都读不了：先留一份现场副本，然后照常按默认值运行。
            # 不能阻止保存——那会让用户改什么都存不下来，比覆盖原文件更糟。
            preserve_corrupt_config()
            CONFIG_WARNING = ("配置文件与备份都已损坏，已使用默认设置；"
                              "损坏的原文件已另存为 config.json.corrupt。")
        log_exc()
    return cfg


def preserve_corrupt_config():
    """配置损坏时留一份现场副本，免得第一次保存把线索覆盖掉。"""
    try:
        shutil.copy2(CFG_PATH, CFG_PATH + ".corrupt")
    except OSError:
        pass


def save_config(cfg, backup=True):
    try:
        validate_config(cfg)
        folder = os.path.dirname(CFG_PATH)
        os.makedirs(folder, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".config-", suffix=".tmp",
                                         dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            if backup and os.path.exists(CFG_PATH):
                try:
                    _read_config(CFG_PATH)
                    shutil.copy2(CFG_PATH, CFG_PATH + ".bak")
                except Exception:
                    pass
            os.replace(temp_path, CFG_PATH)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
    except Exception:
        log_exc()


def parse_t(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def fmt_t(minutes):
    return "%02d:%02d" % (minutes // 60, minutes % 60)


def fmt_cd(seconds):
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return "%02d:%02d:%02d" % (h, m, s)


# 国务院办公厅 2026 年部分节假日安排：国办发明电〔2025〕7号。
# https://www.gov.cn/gongbao/2025/issue_12406/material/gwygb202532.pdf
HOLIDAYS_2026 = (
    ("2026-01-01", "2026-01-03"),
    ("2026-02-15", "2026-02-23"),
    ("2026-04-04", "2026-04-06"),
    ("2026-05-01", "2026-05-05"),
    ("2026-06-19", "2026-06-21"),
    ("2026-09-25", "2026-09-27"),
    ("2026-10-01", "2026-10-07"),
)
WORKING_WEEKENDS_2026 = {
    "2026-01-04", "2026-02-14", "2026-02-28", "2026-05-09",
    "2026-09-20", "2026-10-10",
}
STATUTORY_HOLIDAYS_2026 = {
    "2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18",
    "2026-02-19", "2026-04-05", "2026-05-01", "2026-05-02",
    "2026-06-19", "2026-09-25", "2026-10-01", "2026-10-02",
    "2026-10-03",
}
CALENDAR_DIR = os.path.join(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()),
                            APP_ID, "calendar")
CALENDAR_REFRESH_DAYS = 7        # 缓存超过这么久才重新联网（节假日一年才公布一次）
CALENDAR_RETRY_SECONDS = 60      # 本地缓存读取失败后的重试间隔
_calendar_lock = threading.RLock()
_calendar_days = {}
_calendar_years = {2026}
_calendar_loaded = set()
_calendar_tried_at = {}


def _valid_calendar(data, year):
    if not isinstance(data, dict) or data.get("year") != year:
        raise ValueError("节假日数据年份不符")
    papers, days = data.get("papers"), data.get("days")
    if not isinstance(papers, list) or not papers or not isinstance(days, list) or not days:
        raise ValueError("尚未发布节假日安排")
    if any(not isinstance(p, str) or urllib.parse.urlparse(p).scheme != "https" or
           urllib.parse.urlparse(p).hostname not in ("www.gov.cn", "gov.cn")
           for p in papers):
        raise ValueError("节假日数据没有有效的国务院文件来源")
    result = {}
    for item in days:
        date = dt.date.fromisoformat(item["date"])
        if date.year not in (year - 1, year, year + 1) or type(
                item.get("isOffDay")) is not bool or date.isoformat() in result:
            raise ValueError("节假日日期无效")
        result[date.isoformat()] = not item["isOffDay"]
    return result


def _load_calendar_cache(year):
    """读本地节假日缓存。

    读失败（文件缺失或被占用）不永久放弃，但也不能每帧都去碰磁盘——
    is_workday 会在一帧里被调用几十次，所以失败后限流重试。
    """
    now = time.time()
    with _calendar_lock:
        if year in _calendar_loaded:
            return
        if now - _calendar_tried_at.get(year, 0.0) < CALENDAR_RETRY_SECONDS:
            return
        _calendar_tried_at[year] = now
    try:
        with open(os.path.join(CALENDAR_DIR, "%d.json" % year),
                  encoding="utf-8") as f:
            days = _valid_calendar(json.load(f), year)
    except (OSError, ValueError, KeyError, TypeError):
        return
    with _calendar_lock:
        _calendar_days.update(days)
        _calendar_years.add(year)
        _calendar_loaded.add(year)


def refresh_calendar(year):
    """后台调用；未发布或联网失败时保留已验证缓存。"""
    url = "https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/%d.json" % year
    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            data = json.load(response)
        days = _valid_calendar(data, year)
        os.makedirs(CALENDAR_DIR, exist_ok=True)
        path = os.path.join(CALENDAR_DIR, "%d.json" % year)
        fd, temp_path = tempfile.mkstemp(dir=CALENDAR_DIR)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        with _calendar_lock:
            _calendar_days.update(days)
            _calendar_years.add(year)
            _calendar_loaded.add(year)
        return True
    except Exception:
        return False
    finally:
        # 无论成功与否都记下尝试时间：被墙/离线时不必每次启动都白等一遍
        _touch_calendar_stamp(year)


def refresh_nearby_calendars():
    """后台调用：只在缓存过期时才联网。

    节假日一年才公布一次，每次启动都重下既没意义，网络不通时还会
    白等 3×8 秒（raw.githubusercontent 在国内基本连不上）。
    """
    year = dt.date.today().year
    for candidate in (year - 1, year, year + 1):
        if _calendar_refresh_due(candidate):
            refresh_calendar(candidate)


def _calendar_refresh_due(year, days=CALENDAR_REFRESH_DAYS):
    """距上次联网尝试（成功与否都算）超过 days 天才再试一次。"""
    try:
        age = time.time() - os.path.getmtime(_calendar_stamp_path(year))
    except OSError:
        return True
    return age >= days * 86400


def _calendar_stamp_path(year):
    return os.path.join(CALENDAR_DIR, "%d.stamp" % year)


def _touch_calendar_stamp(year):
    try:
        os.makedirs(CALENDAR_DIR, exist_ok=True)
        with open(_calendar_stamp_path(year), "w") as f:
            f.write(str(int(time.time())))
    except OSError:
        pass


def calendar_status(year):
    _load_calendar_cache(year)
    with _calendar_lock:
        known = year in _calendar_years
    return "按国务院放假与调休" if known else "按星期估算（假期未公布）"


def is_workday(day, cfg):
    date = day.isoformat()
    for year in (day.year - 1, day.year, day.year + 1):
        _load_calendar_cache(year)
    with _calendar_lock:
        calendar_value = _calendar_days.get(date)
    if calendar_value is not None:
        return calendar_value
    if day.year == 2026:
        if date in WORKING_WEEKENDS_2026:
            return True
        if any(start <= date <= end for start, end in HOLIDAYS_2026):
            return False
    return day.weekday() in cfg["workdays"]


def calendar_day(day, cfg):
    date = day.isoformat()
    if date in STATUTORY_HOLIDAYS_2026:
        kind = "statutory"
    elif day.year == 2026 and date in WORKING_WEEKENDS_2026:
        kind = "makeup"
    elif is_workday(day, cfg):
        with _calendar_lock:
            makeup = _calendar_days.get(date) is True and day.weekday() >= 5
        kind = "makeup" if makeup else "workday"
    else:
        kind = "rest"
    recorded = date in cfg.get("overtime_dates", []) or (
        cfg.get("overtime_today") and cfg.get("overtime_date") == date)
    mode = cfg.get("salary_stat_mode", "actual")
    attendance = ("overtime" if kind in ("workday", "makeup") and (
        mode == "overtime" or mode == "actual" and recorded) else
        "normal" if kind in ("workday", "makeup") else "rest")
    item = cfg.get("calendar_overrides", {}).get(date) if \
        cfg.get("calendar_month") == date[:7] else None
    if item:
        return dict(item)
    return {"kind": kind, "attendance": attendance, "amount": None}


def roll_calendar(cfg, today):
    month = today.strftime("%Y-%m")
    previous = cfg.get("calendar_month", "")
    if previous == month:
        return False
    if previous and previous < month:
        first = dt.date.fromisoformat(previous + "-01")
        next_month = (first.replace(year=first.year + 1, month=1)
                      if first.month == 12 else first.replace(month=first.month + 1))
        last = next_month - dt.timedelta(days=1)
        cfg["last_month_summary"] = {
            "month": previous, "earned": round(month_earned(last, cfg, True), 2)}
    recorded = (cfg.get("overtime_dates", []) if not previous else [])
    cfg["calendar_month"] = month
    cfg["calendar_overrides"] = {}
    cfg["overtime_dates"] = []
    for date in recorded:
        if date.startswith(month):
            day = dt.date.fromisoformat(date)
            if is_workday(day, cfg):
                cfg["calendar_overrides"][date] = {
                    "kind": calendar_day(day, cfg)["kind"],
                    "attendance": "overtime", "amount": None}
    return True


def month_workdays(day, cfg):
    first = day.replace(day=1)
    next_month = (first.replace(year=first.year + 1, month=1)
                  if first.month == 12 else first.replace(month=first.month + 1))
    count = (next_month - first).days
    return sum(calendar_day(first + dt.timedelta(days=i), cfg)["kind"] in (
               "workday", "makeup")
               for i in range(count))


def insurance_deduction(cfg):
    return (float(cfg.get("monthly_insurance", 0))
            if cfg.get("insurance_mode", "none") != "none" else 0.0)


def base_daily_salary(day, cfg, overtime=False):
    adjustment = float(cfg.get("monthly_subsidy", 0)) - insurance_deduction(cfg)
    if cfg.get("salary_mode") == "monthly":
        income = (float(cfg["monthly_salary"]) + adjustment) / month_workdays(day, cfg)
        if overtime:
            income += float(cfg.get("monthly_overtime_extra", 0))
    else:
        income = float(cfg["overtime_daily_salary" if overtime else "daily_salary"])
        if adjustment:
            income += adjustment / month_workdays(day, cfg)
    return max(0.0, income)


def overtime_target(day, cfg):
    return base_daily_salary(day, cfg, overtime=True)


def suggested_day_income(day, cfg, kind, attendance):
    if attendance == "rest":
        return 0.0
    base = base_daily_salary(day, cfg)
    if kind == "statutory":
        return round(base * 3, 2)
    if kind == "rest":
        return round(base * 2, 2)
    return round(overtime_target(day, cfg) if attendance == "overtime"
                 else base, 2)


def salary_preview_text(day, cfg):
    normal = base_daily_salary(day, cfg)
    overtime = overtime_target(day, cfg)
    if cfg.get("salary_mode") == "monthly" or float(
            cfg.get("monthly_subsidy", 0)) > 0 or insurance_deduction(cfg) > 0:
        caption = ("%d年%d月 · %d个工作日\n"
                "正常 ¥%.2f/天 · 加班当天 ¥%.2f"
                % (day.year, day.month, month_workdays(day, cfg),
                   normal, overtime))
    else:
        caption = "正常 ¥%.2f/天 · 加班当天 ¥%.2f" % (normal, overtime)
    if cfg.get("insurance_mode", "none") != "none":
        caption += "\n%s每月扣除 ¥%.2f" % (
            INSURANCE_OPTIONS[cfg["insurance_mode"]], insurance_deduction(cfg))
    return caption


def month_earned(day, cfg, include_today):
    first = day.replace(day=1)
    last = day.day if include_today else day.day - 1
    normal_income = base_daily_salary(day, cfg)
    overtime_income = overtime_target(day, cfg)
    total = 0.0
    for i in range(last):
        current = first + dt.timedelta(days=i)
        item = calendar_day(current, cfg)
        if item["attendance"] == "rest":
            continue
        if item["amount"] is not None:
            total += item["amount"]
        elif item["kind"] in ("workday", "makeup"):
            total += (overtime_income if item["attendance"] == "overtime"
                      else normal_income)
    return total


# ---------------------------------------------------------------- 核心计算

def compute(now, cfg):
    """根据当前时间和配置算出：阶段、倒计时、已赚金额、进度
    进度 = 已工作分钟 / 应工作分钟（午休不计入），按真实时间推进。"""
    ws = parse_t(cfg["work_start"])
    ls = parse_t(cfg["lunch_start"])
    le = parse_t(cfg["lunch_end"])
    we = parse_t(cfg["work_end"])
    total_min = (ls - ws) + (we - le)
    day = now.date()
    base_salary = base_daily_salary(day, cfg)
    day_info = calendar_day(day, cfg)

    # 加班：下班时间顺延，按配置的加班日总收入均匀分摊
    override = cfg.get("calendar_overrides", {}).get(day.isoformat()) if \
        cfg.get("calendar_month") == day.strftime("%Y-%m") else None
    ot = (override["attendance"] == "overtime" if override else
          bool(cfg.get("overtime_today", False) and
               cfg.get("overtime_date") == day.isoformat()))
    ot_end = parse_t(cfg.get("overtime_end", "20:30"))
    overtime_min = ot_end - we
    dinner_enabled = bool(ot and cfg.get("dinner_break_enabled", False))
    dinner_start = parse_t(cfg.get("dinner_start", "18:30"))
    dinner_end = parse_t(cfg.get("dinner_end", "19:00"))
    dinner_min = dinner_end - dinner_start if dinner_enabled else 0
    off_s = (ot_end if ot else we) * 60
    total_work_min = total_min + (overtime_min if ot else 0) - dinner_min
    if ot:
        # 加班日总目标按全部工时分摊
        target = (day_info["amount"] if day_info["amount"] is not None else
                  overtime_target(day, cfg))
        hourly = target / (total_work_min / 60.0)
    else:
        target = base_salary
        hourly = target / (total_min / 60.0)
    full_day = target

    r = {"hourly": hourly, "total_min": total_min, "overtime": ot,
         "dinner_break": dinner_enabled,
         "full_day": full_day}

    # 休息日显示本月已累计的正常工作日和已记录加班增量
    if day_info["attendance"] == "rest":
        earned = month_earned(day, cfg, False)
        r.update(phase="rest", earned=0.0, progress=0.0,
                 title="今日休息",
                 countdown="¥%.2f" % earned,
                 money_prefix="本月已赚")
        return r

    sec = now.hour * 3600 + now.minute * 60 + now.second
    ws_s, ls_s, le_s, we_s = ws * 60, ls * 60, le * 60, we * 60
    dinner_start_s, dinner_end_s = dinner_start * 60, dinner_end * 60
    hourly_s = hourly / 3600.0  # 每秒钟赚多少

    if sec < ws_s:
        r.update(phase="pre", earned=0.0, progress=0.0,
                 title="距上班", countdown=fmt_cd(ws_s - sec),
                 money_prefix="今日已赚")
    elif sec < ls_s:
        earned = (sec - ws_s) * hourly_s
        # 进度：上午已过分钟 / 上午总分钟
        r.update(phase="am", earned=earned,
                 progress=(sec - ws_s) / 60.0 / total_work_min,
                 title="距下班", countdown=fmt_cd(off_s - sec),
                 money_prefix="今日已赚")
    elif sec < le_s:
        earned = (ls_s - ws_s) * hourly_s
        # 午休不工作，进度停在午休开始那一刻
        r.update(phase="lunch", earned=earned,
                 progress=(ls_s - ws_s) / 60.0 / total_work_min,
                 title="午休中 · 距开工", countdown=fmt_cd(le_s - sec),
                 money_prefix="今日已赚")
    elif sec < we_s:
        earned = ((ls_s - ws_s) + (sec - le_s)) * hourly_s
        # 下午：已工作分钟 = 上午总分钟 + (现在 - 下午开始)
        worked_min = (ls_s - ws_s) / 60.0 + (sec - le_s) / 60.0
        r.update(phase="pm", earned=earned,
                 progress=worked_min / total_work_min,
                 title="距下班", countdown=fmt_cd(off_s - sec),
                 money_prefix="今日已赚")
    elif dinner_enabled and dinner_start_s <= sec < dinner_end_s:
        worked_ot_s = max(0, dinner_start_s - we_s)
        earned = ((total_min * 60) + worked_ot_s) * hourly_s
        worked_min = total_min + worked_ot_s / 60.0
        r.update(phase="dinner", earned=earned,
                 progress=worked_min / total_work_min,
                 title="晚休中 · 距开工", countdown=fmt_cd(dinner_end_s - sec),
                 money_prefix="今日已赚")
    elif ot and sec < off_s:
        # 加班中：按总目标/总工时 均匀累计（已在 hourly 中体现）
        worked_ot_s = sec - we_s
        if dinner_enabled and sec >= dinner_end_s:
            worked_ot_s -= dinner_end_s - dinner_start_s
        earned = ((total_min * 60) + worked_ot_s) * hourly_s
        worked_min = total_min + worked_ot_s / 60.0
        r.update(phase="ot", earned=earned,
                 progress=worked_min / total_work_min,
                 title="加班中 · 距下班", countdown=fmt_cd(off_s - sec),
                 money_prefix="今日已赚")
    else:
        r.update(phase="done", earned=month_earned(day, cfg, True), progress=1.0,
                 title="已下班", countdown="本月已赚",
                 money_prefix="本月已赚")
    return r


def month_earned_now(now, cfg):
    state = compute(now, cfg)
    if state["phase"] == "done":
        return state["earned"]
    return month_earned(now.date(), cfg, False) + state["earned"]


# ---------------------------------------------------------------- 图标（金¥硬币）

# ¥ 字样的 9x12 点阵
Y_GLYPH = [
    "x.......x",
    ".x.....x.",
    "..x...x..",
    "...x.x...",
    "....x....",
    ".xxxxxxx.",
    "....x....",
    "...xxx...",
    "....x....",
    "....x....",
    "....x....",
    "..xxxxx..",
]


def coin_bitmap(size=32):
    """生成金色硬币图标的像素：返回 (BGRA 字节, AND 掩码字节)"""
    w = h = size
    cx = cy = (size - 1) / 2.0
    R = size / 2.0 - 1.0
    GOLD = (255, 198, 61)
    GOLD_L = (255, 224, 120)
    GOLD_D = (212, 160, 44)
    EDGE = (166, 124, 32)
    INK = (110, 74, 18)
    gy0 = (h - len(Y_GLYPH)) // 2
    gx0 = (w - 9) // 2
    bgra = bytearray(w * h * 4)
    for y in range(h):
        for x in range(w):
            dx = x - cx
            dy = y - cy
            d = (dx * dx + dy * dy) ** 0.5
            i = (y * w + x) * 4
            if d > R:
                continue  # 透明
            gx, gy = x - gx0, y - gy0
            if 0 <= gx < 9 and 0 <= gy < len(Y_GLYPH) \
                    and Y_GLYPH[gy][gx] == "x":
                col = INK
            elif d > R - 1.6:
                col = EDGE
            elif d > R - 3.4:
                col = GOLD_D
            elif dx <= -3 and dy <= -3:
                col = GOLD_L
            else:
                col = GOLD
            bgra[i], bgra[i + 1], bgra[i + 2], bgra[i + 3] = \
                col[2], col[1], col[0], 255
    rowbytes = ((w + 31) // 32) * 4
    mask = bytearray([0xFF]) * (rowbytes * h)
    for y in range(h):
        for x in range(w):
            if bgra[(y * w + x) * 4 + 3]:
                mask[y * rowbytes + x // 8] &= ~(0x80 >> (x % 8))
    return bytes(bgra), bytes(mask)


def write_ico(path, size=32):
    bgra, mask = coin_bitmap(size)
    rows = [bgra[y * size * 4:(y + 1) * size * 4] for y in range(size)]
    xor = b"".join(reversed(rows))  # ICO 的 XOR 位图是自底向上
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0,
                        1, 32, 40 + len(xor) + len(mask), 22)
    bih = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32,
                      0, 0, 0, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(header + entry + bih + xor + mask)


def make_gdi_icon():
    """用 GDI 把硬币位图变成 HICON（托盘用）。
    返回 (hicon, 需要在图标存续期间保活、并在最后释放的位图句柄)。"""
    gdi32 = ctypes.windll.gdi32
    user32 = ctypes.windll.user32
    size = 32
    bgra, mask = coin_bitmap(size)

    bih = BITMAPINFOHEADER()
    bih.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bih.biWidth = size
    bih.biHeight = -size  # 自上而下
    bih.biPlanes = 1
    bih.biBitCount = 32
    ppv = ctypes.c_void_p()
    hbm = gdi32.CreateDIBSection(None, ctypes.byref(bih), 0,
                                 ctypes.byref(ppv), None, 0)
    ctypes.memmove(ppv, bgra, len(bgra))
    hbm_mask = gdi32.CreateBitmap(size, size, 1, 1, mask)
    info = ICONINFO(True, 0, 0, hbm_mask, hbm)
    hicon = user32.CreateIconIndirect(ctypes.byref(info))
    return hicon, (hbm, hbm_mask)


# ---------------------------------------------------------------- 托盘线程

WM_APP = 0x8000
CB_MSG = WM_APP + 1          # 托盘回调消息
WM_HOTKEY = 0x0312
WM_TIMER = 0x0113
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
MOD_ALT = 0x1
MOD_CONTROL = 0x2
NIF_MESSAGE = 0x1
NIF_ICON = 0x2
NIF_TIP = 0x4
NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
HOTKEY_ID = 1
HOTKEY_D = 0x44  # 'D'  显示/隐藏


def overtime_menu_text(enabled, end_time):
    return "取消加班" if enabled else "今天加班 (至%s)" % end_time


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD),
                ("Data3", wt.WORD), ("Data4", wt.BYTE * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT),
        ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT),
        ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128),
        ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256), ("uTimeout", wt.UINT),
        ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
        ("guidItem", GUID), ("hBalloonIcon", wt.HICON),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wt.HWND), ("message", wt.UINT),
                ("wParam", wt.WPARAM), ("lParam", wt.LPARAM),
                ("time", wt.DWORD), ("pt", POINT)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long), ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wt.BOOL), ("xHotspot", wt.DWORD),
        ("yHotspot", wt.DWORD), ("hbmMask", wt.HBITMAP),
        ("hbmColor", wt.HBITMAP),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT), ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR),
    ]



def add_tray_icon(shell32, nid, attempts=5):
    """挂上托盘图标。登录时 Explorer 可能尚未就绪，失败要重试而不是放弃。"""
    nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
    for attempt in range(attempts):
        if shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            return True
        if attempt + 1 < attempts:
            time.sleep(0.5 * (attempt + 1))
    return False


def tray_thread(to_tk, to_tray):
    """托盘图标线程：独立消息循环，与 tk 主线程用队列通信"""
    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32
    kernel32 = ctypes.windll.kernel32

    hwnd = None
    nid = None
    hicon = None
    keepalive = None
    icon_added = False
    retry_ticks = 0
    cls_name = None
    hinst = None
    class_registered = False
    try:
        kernel32.GetModuleHandleW.restype = wt.HMODULE
        user32.CreateWindowExW.restype = wt.HWND
        user32.DefWindowProcW.restype = wt.LPARAM

        def icon_event(event):
            if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                send_ui_message(to_tk, ("toggle_visible",))
            elif event == WM_RBUTTONUP:
                pt = POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                send_ui_message(to_tk, ("popup_menu", (pt.x, pt.y)))

        wndproc_type = ctypes.WINFUNCTYPE(wt.LPARAM, wt.HWND, wt.UINT,
                                         wt.WPARAM, wt.LPARAM)

        @wndproc_type
        def wndproc(window, message, wparam, lparam):
            if message == CB_MSG:
                icon_event(lparam)
                return 0
            return user32.DefWindowProcW(window, message, wparam, lparam)

        cls_name = "PayDanceTrayMsgWnd"
        hinst = kernel32.GetModuleHandleW(None)

        wc = WNDCLASSW()
        wc.lpfnWndProc = ctypes.cast(wndproc, ctypes.c_void_p)
        wc.hInstance = hinst
        wc.lpszClassName = cls_name
        if not user32.RegisterClassW(ctypes.byref(wc)):
            raise OSError("RegisterClassW failed")
        class_registered = True

        hwnd = user32.CreateWindowExW(0, cls_name, None, 0, 0, 0, 0, 0,
                                      0, 0, hinst, 0)  # 隐藏顶层窗口，接收通知区回调
        if not hwnd:
            raise OSError("CreateWindowExW failed")

        # 老板键必须先注册：托盘图标挂不上时（例如登录时 Explorer 还没就绪），
        # 老板键仍要可用，否则小窗被别的窗口盖住就再也叫不出来了。
        try:
            hotkey_ok = bool(user32.RegisterHotKey(
                hwnd, HOTKEY_ID, MOD_CONTROL | MOD_ALT, HOTKEY_D))
        except Exception:
            log_exc()
            hotkey_ok = False
        if not hotkey_ok:
            # 多数是被别的程序占用了。不能一声不吭：用户按了没反应，
            # 又不知道小窗被藏到哪儿去。
            send_ui_message(to_tk, ("hotkey_failed",))

        hicon, keepalive = make_gdi_icon()

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = 1
        nid.uCallbackMessage = CB_MSG
        nid.hIcon = hicon
        nid.szTip = APP_TITLE
        # 挂不上不算致命：继续跑消息循环，等 Explorer 就绪后自动补挂
        icon_added = add_tray_icon(shell32, nid)
        if not icon_added:
            send_ui_message(to_tk, ("tray_failed",))
        taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")

        user32.SetTimer(hwnd, 1, 200, None)

        msg = MSG()
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret <= 0:
                break
            if msg.message == CB_MSG:
                icon_event(msg.lParam)
            elif msg.message == WM_HOTKEY:
                if msg.wParam == HOTKEY_ID:
                    send_ui_message(to_tk, ("toggle_visible",))
            elif taskbar_created and msg.message == taskbar_created:
                # Explorer 重启会清空整个通知区，必须重新挂上图标。
                # 先删一次旧登记：若 Explorer 并未真正重启（图标还在），
                # 直接 NIM_ADD 会因"已存在"而失败。NIM_DELETE 对不存在是空操作。
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
                icon_added = add_tray_icon(shell32, nid, attempts=2)
            elif msg.message == WM_TIMER:
                if not icon_added:
                    # 之前没挂上（例如登录时 Explorer 未就绪）：每约 10 秒补试一次
                    retry_ticks += 1
                    if retry_ticks >= 50:
                        retry_ticks = 0
                        icon_added = add_tray_icon(shell32, nid, attempts=1)
                try:
                    while True:
                        message = to_tray.get_nowait()
                        kind = message[0]
                        if kind == "tooltip":
                            nid.uFlags = NIF_TIP
                            nid.szTip = message[1][:127]
                            shell32.Shell_NotifyIconW(NIM_MODIFY,
                                                      ctypes.byref(nid))
                        elif kind == "exit":
                            raise SystemExit
                except queue.Empty:
                    pass
    except SystemExit:
        pass
    except Exception:
        log_exc()
        send_ui_message(to_tk, ("tray_failed",))
    finally:
        if nid is not None and icon_added:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        if hwnd:
            user32.KillTimer(hwnd, 1)
            user32.UnregisterHotKey(hwnd, HOTKEY_ID)
            user32.DestroyWindow(hwnd)
        if hicon:
            user32.DestroyIcon(hicon)
        for handle in keepalive or ():
            ctypes.windll.gdi32.DeleteObject(handle)
        # 注销窗口类，否则同一进程内无法再次注册同名类（托盘线程将无法重启）
        if class_registered:
            user32.UnregisterClassW(cls_name, hinst)


# ---------------------------------------------------------------- 开机自启

AUTOSTART_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"


def _registered_autostart():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY) as key:
            return winreg.QueryValueEx(key, APP_NAME)[0]
    except FileNotFoundError:
        return None


def startup_vbs_path():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Microsoft", "Windows", "Start Menu",
                        "Programs", "Startup", APP_NAME + ".vbs")


def legacy_startup_vbs_path():
    return os.path.join(os.path.dirname(startup_vbs_path()), APP_ID + ".vbs")


def _pythonw_path():
    exe = sys.executable
    if os.path.basename(exe).lower() == "pythonw.exe":
        return exe
    cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(cand):
        return cand
    return exe


# WSH 按系统 ANSI 代码页解析 .vbs 源码：用 UTF-8 写盘时，含中文等非 ASCII 的
# 安装路径会被曲解成乱码，WScript.Shell.Run 静默失败（脚本仍以 0 退出）。
VBS_ENCODING = "utf-16"   # UTF-16LE + BOM，与代码页无关，WSH 可正确读取
VBS_BOM = b"\xff\xfe"


def install_autostart():
    if getattr(sys, "frozen", False):
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY) as key:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ,
                              '"%s"' % os.path.abspath(sys.executable))
        # 迁移 Windows 启动设置中的开关，避免把用户禁用的旧启动项重新启用。
        for path in (startup_vbs_path(), legacy_startup_vbs_path()):
            if not os.path.exists(path):
                continue
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                    AUTOSTART_APPROVED_KEY + r"\StartupFolder") as key:
                    status, kind = winreg.QueryValueEx(key, os.path.basename(path))
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                                      AUTOSTART_APPROVED_KEY + r"\Run") as key:
                    winreg.SetValueEx(key, APP_NAME, 0, kind, status)
            except FileNotFoundError:
                pass
            os.remove(path)
        return 'HKCU\\%s\\%s' % (AUTOSTART_RUN_KEY, APP_NAME)
    # VBS 字符串里每个内嵌双引号必须写成 ""（三连引号包裹路径才是合法的）
    command = '"""{py}"" ""{script}"""'.format(
        py=_pythonw_path(), script=os.path.abspath(__file__))
    vbs = 'CreateObject("WScript.Shell").Run %s, 0, False\n' % command
    path = startup_vbs_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding=VBS_ENCODING) as f:
        f.write(vbs)
    legacy = legacy_startup_vbs_path()
    if legacy != path and os.path.exists(legacy):
        os.remove(legacy)
    return path


def _read_startup_vbs(path=None):
    """读取启动项 VBS，返回 (内容, 是否已是当前编码)。
    旧版本按 UTF-8 写盘，读取时必须一并兼容。"""
    with open(path or startup_vbs_path(), "rb") as f:
        data = f.read()
    if data.startswith(VBS_BOM):
        return data.decode(VBS_ENCODING), True
    try:
        return data.decode("utf-8"), False
    except UnicodeDecodeError:
        return data.decode("mbcs"), False


def remove_autostart():
    if getattr(sys, "frozen", False):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY,
                                0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, APP_NAME)
        except FileNotFoundError:
            pass
    path = startup_vbs_path()
    for entry in (path, legacy_startup_vbs_path()):
        if os.path.exists(entry):
            os.remove(entry)
    return path


def autostart_enabled():
    if getattr(sys, "frozen", False) and _registered_autostart() is not None:
        return _registered_autostart() == '"%s"' % os.path.abspath(sys.executable)
    path = startup_vbs_path()
    if not os.path.exists(path):
        path = legacy_startup_vbs_path()
    if not os.path.exists(path):
        return False
    try:
        target = (os.path.abspath(sys.executable)
                  if getattr(sys, "frozen", False)
                  else os.path.abspath(__file__))
        contents, _ = _read_startup_vbs(path)
        return target in contents
    except Exception:
        return False


def repair_autostart():
    """只在打包版运行时修复已有启动项；不替用户启用自启。"""
    if not getattr(sys, "frozen", False):
        return False
    if _registered_autostart() is not None:
        if autostart_enabled() and not any(os.path.exists(path) for path in
                                          (startup_vbs_path(), legacy_startup_vbs_path())):
            return False
        install_autostart()
        return True
    path = startup_vbs_path()
    legacy = legacy_startup_vbs_path()
    if not os.path.exists(path):
        path = legacy
    if not os.path.exists(path):
        return False
    contents, _ = _read_startup_vbs(path)
    if not contents.startswith('CreateObject("WScript.Shell").Run ') or \
            not any(name in contents for name in (APP_ID, APP_NAME)):
        return False
    install_autostart()
    return True


def send_tray_message(messages, message):
    """单槽队列只保留最新提示，退出指令覆盖提示。"""
    try:
        messages.put_nowait(message)
    except queue.Full:
        try:
            pending = messages.get_nowait()
        except queue.Empty:
            pending = None
        if pending and pending[0] == "exit" and message[0] != "exit":
            message = pending
        messages.put_nowait(message)


def send_ui_message(messages, message):
    """界面事件队列有界；繁忙时丢弃最旧事件，避免无限积压。"""
    try:
        messages.put_nowait(message)
    except queue.Full:
        try:
            messages.get_nowait()
        except queue.Empty:
            return
        try:
            messages.put_nowait(message)
        except queue.Full:
            pass
# ---------------------------------------------------------------- 窗口辅助（Win32）

SWP_NOSIZE = 0x1
SWP_NOMOVE = 0x2
SWP_NOACTIVATE = 0x10
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_DONOTROUND = 1
SM_CXSCREEN = 0
SM_CYSCREEN = 1
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
LOGPIXELSX = 88
HWND_BOTTOM = 1

user32 = ctypes.windll.user32


def setup_win32():
    """为所有用到的 Win32 调用声明参数/返回类型。
    没有 argtypes 时负值句柄（HWND_MESSAGE=-3、TOPMOST=-1）会被零扩展成
    32 位导致错误 1400，指针返回值也会被截断，必须显式声明。"""
    u = user32
    g = ctypes.windll.gdi32
    k = ctypes.windll.kernel32
    s = ctypes.windll.shell32
    d = ctypes.windll.dwmapi
    HWND = wt.HWND
    LRESULT = ctypes.c_ssize_t
    LONG_PTR = ctypes.c_ssize_t
    HDC = wt.HDC
    HBITMAP = wt.HBITMAP
    BOOL = wt.BOOL
    UINT = wt.UINT
    DWORD = wt.DWORD

    u.GetParent.argtypes = [HWND]
    u.GetParent.restype = HWND
    u.GetWindowLongPtrW.argtypes = [HWND, ctypes.c_int]
    u.GetWindowLongPtrW.restype = LONG_PTR
    u.SetWindowLongPtrW.argtypes = [HWND, ctypes.c_int, LONG_PTR]
    u.SetWindowLongPtrW.restype = LONG_PTR
    u.SetWindowPos.argtypes = [HWND, HWND, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, UINT]
    u.SetWindowPos.restype = BOOL
    u.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    u.RegisterClassW.restype = wt.WORD
    u.UnregisterClassW.argtypes = [wt.LPCWSTR, wt.HINSTANCE]
    u.UnregisterClassW.restype = BOOL
    u.CreateWindowExW.argtypes = [DWORD, wt.LPCWSTR, wt.LPCWSTR, DWORD,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, HWND, wt.HMENU,
                                  wt.HINSTANCE, wt.LPVOID]
    u.CreateWindowExW.restype = HWND
    u.DefWindowProcW.argtypes = [HWND, UINT, wt.WPARAM, wt.LPARAM]
    u.DefWindowProcW.restype = LRESULT
    u.DestroyWindow.argtypes = [HWND]
    u.DestroyWindow.restype = BOOL
    u.GetMessageW.argtypes = [ctypes.POINTER(MSG), HWND, UINT, UINT]
    u.GetMessageW.restype = ctypes.c_int
    u.SetTimer.argtypes = [HWND, ctypes.c_size_t, UINT, wt.LPVOID]
    u.SetTimer.restype = ctypes.c_size_t
    u.KillTimer.argtypes = [HWND, ctypes.c_size_t]
    u.KillTimer.restype = BOOL
    u.RegisterHotKey.argtypes = [HWND, ctypes.c_int, UINT, UINT]
    u.RegisterHotKey.restype = BOOL
    u.UnregisterHotKey.argtypes = [HWND, ctypes.c_int]
    u.UnregisterHotKey.restype = BOOL
    u.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    u.GetCursorPos.restype = BOOL
    u.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
    u.CreateIconIndirect.restype = wt.HICON
    u.DestroyIcon.argtypes = [wt.HICON]
    u.DestroyIcon.restype = BOOL
    u.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
    u.RegisterWindowMessageW.restype = UINT
    u.MessageBoxW.argtypes = [HWND, wt.LPCWSTR, wt.LPCWSTR, UINT]
    u.MessageBoxW.restype = ctypes.c_int
    u.GetSystemMetrics.argtypes = [ctypes.c_int]
    u.GetSystemMetrics.restype = ctypes.c_int
    u.GetDpiForSystem.argtypes = []
    u.GetDpiForSystem.restype = UINT
    u.GetDC.argtypes = [HWND]
    u.GetDC.restype = HDC
    u.ReleaseDC.argtypes = [HWND, HDC]
    u.ReleaseDC.restype = ctypes.c_int
    g.GetDeviceCaps.argtypes = [HDC, ctypes.c_int]
    g.GetDeviceCaps.restype = ctypes.c_int
    u.SetProcessDPIAware.argtypes = []
    u.SetProcessDPIAware.restype = BOOL

    g.CreateCompatibleDC.argtypes = [HDC]
    g.CreateCompatibleDC.restype = HDC
    g.CreateDIBSection.argtypes = [HDC, ctypes.c_void_p, UINT,
                                   ctypes.POINTER(ctypes.c_void_p),
                                   wt.HANDLE, DWORD]
    g.CreateDIBSection.restype = HBITMAP
    g.SelectObject.argtypes = [HDC, wt.HANDLE]
    g.SelectObject.restype = wt.HANDLE
    g.DeleteObject.argtypes = [wt.HANDLE]
    g.DeleteObject.restype = BOOL
    g.DeleteDC.argtypes = [HDC]
    g.DeleteDC.restype = BOOL
    g.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, UINT, UINT,
                               wt.LPVOID]
    g.CreateBitmap.restype = HBITMAP

    s.Shell_NotifyIconW.argtypes = [DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    s.Shell_NotifyIconW.restype = BOOL

    d.DwmSetWindowAttribute.argtypes = [HWND, DWORD, wt.LPVOID, DWORD]
    d.DwmSetWindowAttribute.restype = ctypes.c_long

    k.GetModuleHandleW.argtypes = [wt.LPCWSTR]
    k.GetModuleHandleW.restype = wt.HINSTANCE

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
        ctypes.windll.shcore.SetProcessDpiAwareness.restype = ctypes.c_long
    except Exception:
        pass


def get_hwnd(root):
    return user32.GetParent(root.winfo_id())


def set_square_corners(root):
    try:
        hwnd = get_hwnd(root)
        pref = ctypes.c_int(DWMWCP_DONOTROUND)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(pref),
            ctypes.sizeof(pref))
    except Exception:
        pass


def move_window_absolute(root, x, y):
    user32.SetWindowPos(get_hwnd(root), 0, x, y, 0, 0,
                        SWP_NOSIZE | SWP_NOACTIVATE)


def clamp_to_desktop(x, y, w, h):
    """把窗口夹回整个虚拟桌面范围。只按主屏夹取会把副屏上的小窗硬拉回主屏。"""
    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    return (max(vx + 12, min(x, vx + vw - w - 12)),
            max(vy + 12, min(y, vy + vh - h - 12)))


def system_ui_scale():
    """系统 DPI 相对 96 的倍率。

    进程是 SYSTEM_DPI_AWARE，画面按物理像素绘制，所以高分屏上必须自己放大，
    否则 150% 缩放的屏幕上小窗会明显偏小。低于 100% 时按 100% 处理，
    要更小由配置里的 ui_scale 决定。
    """
    dpi = 0
    try:
        dpi = user32.GetDpiForSystem()
    except Exception:
        try:
            hdc = user32.GetDC(None)
            dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, LOGPIXELSX)
            user32.ReleaseDC(None, hdc)
        except Exception:
            return 1.0
    if not dpi or dpi < 96:
        return 1.0
    return dpi / 96.0


# ---------------------------------------------------------------- 主界面

class PayDanceApp:
    def __init__(self):
        self.cfg = load_config()
        if roll_calendar(self.cfg, dt.date.today()):
            save_config(self.cfg)
        # 加班标记按天自动重置：新的一天默认"不加班"
        today = dt.date.today().isoformat()
        if self.cfg.get("overtime_date") != today:
            self.cfg["overtime_today"] = False
            self.cfg["overtime_date"] = today
            save_config(self.cfg)
        self.pinned = False
        self.cur_x = 0
        self.cur_y = 0
        self.visible = True
        self.last_tooltip = None
        self.to_tk = queue.Queue(maxsize=32)
        self.to_tray = queue.Queue(maxsize=1)
        self._tray_warning_shown = False
        self._hotkey_warning_shown = False
        self._pin_warning_shown = False

        # 动效状态
        self.disp_money = 0.0          # 缓动显示金额
        self.disp_bar = 0.0            # 缓动进度
        self.fade = 0.0 if self.cfg["animations"] else 1.0
        self.ambient_phase = 0.0
        self.prev_phase = None
        self.confetti = []
        self.dragging = False
        self._photo = None
        self.settings_window = None
        self._last_frame_at = 0.0

        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.attributes(
            "-alpha", 0.0 if self.cfg["animations"] else self.cfg["opacity"])
        self.root.configure(bg=self.cfg["colors"]["bg"])

        self.build_base()             # 布局 + 字体 + 静态底图（抗锯齿）
        self._label = tk.Label(self.root, bd=0, highlightthickness=0,
                               bg=self.cfg["colors"]["bg"])
        self._label.pack()
        self.render_frame()
        set_square_corners(self.root)

        # 初始位置（启动时夹取回屏幕内，避免窗口跑到屏幕外找不到）
        pos = self.cfg.get("position")
        self.root.update_idletasks()
        h = self.root.winfo_height()
        if pos and len(pos) == 2:
            x, y = int(pos[0]), int(pos[1])
        else:
            x, y = user32.GetSystemMetrics(SM_CXSCREEN) - self.px_w - 24, 28
        self.cur_x, self.cur_y = clamp_to_desktop(x, y, self.px_w, h)
        self.root.geometry("+%d+%d" % (self.cur_x, self.cur_y))

        if "--uicheck" in sys.argv:
            self._print_layout()
            if sys.stdout is not None:
                sys.stdout.flush()
            os._exit(0)

        self.apply_modes()
        self.root.deiconify()
        self.root.iconbitmap(default=(sys.executable if getattr(sys, "frozen", False)
                                      else here("paydance.ico")))

        self.bind_events()
        self.root.after(200, self.poll_tray)
        self.root.after(80, self.tick)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

    # ---------------- UI 构建 ----------------

    def _font(self, name, size, index=0):
        try:
            return ImageFont.truetype(
                os.path.join(FONT_DIR, name),
                int(round(size * self.ui * self.SS)), index=index)
        except Exception:
            return ImageFont.load_default()

    def _S(self, v):
        """布局单位 -> 超采样画布坐标。"""
        return int(round(v * self.ui * self.SS))

    def _U(self, v):
        """布局单位 -> 最终 1x 像素坐标。"""
        return int(round(v * self.ui))

    def build_base(self):
        s = self.cfg["font_scale"]
        c = self.cfg["colors"]
        # 面板整体倍率 = 系统 DPI × 用户设置。w/h 保持设计稿尺寸（布局单位），
        # 实际像素尺寸是 ui 倍，所有绘制坐标经 _S()/_U() 换算。
        self.ui = system_ui_scale() * float(self.cfg.get("ui_scale", 1.0))
        # 超采样倍率跟着 ui 反向调整，让画布总像素基本恒定：
        # 否则 150% 屏上画布会变成 2.25 倍，把渲染开销整个吃掉。
        self.SS = max(2, min(4, int(round(3.0 / self.ui))))
        self.w = 270
        self.h = 318
        self.px_w = max(1, int(round(self.w * self.ui)))
        self.px_h = max(1, int(round(self.h * self.ui)))
        self.cx = self.w / 2.0
        self.pad = 22
        self.chip_y = 18
        self.chip_h = 22
        self.ring_r = 91
        self.ring_w = 9
        self.ring_cy = self.chip_y + self.chip_h + 8 + self.ring_r
        self.ring_bbox = (self.cx - self.ring_r, self.ring_cy - self.ring_r,
                          self.cx + self.ring_r, self.ring_cy + self.ring_r)
        self.cd_y = self.ring_cy + self.ring_r + 38
        self.f_cn = self._font("msyh.ttc", 14 * s)
        self.f_cn_s = self._font("msyh.ttc", 12 * s)
        self.f_digit = self._font("bahnschrift.ttf", 30 * s)
        self.f_cn_big = self._font("msyh.ttc", 22 * s)
        self.f_money = self._font("segoeuib.ttf", 34 * s)
        self.cd_color = hex2rgb(c["countdown"])
        self._state = {}
        # 静态底图只在配置变化时构建并降采样一次，可以用质量更好的 LANCZOS。
        # 每帧渲染只用到 1x 版本，3x 原图不必常驻内存。
        self.base_small = self._render_base().resize(
            (self.px_w, self.px_h), Image.LANCZOS)
        self.base_small_rest = self._render_base(with_ring=False).resize(
            (self.px_w, self.px_h), Image.LANCZOS)
        self._make_glow_layers()

    def _make_glow_layers(self):
        c = self.cfg["colors"]
        side = self._S(276)
        layer = Image.new("RGBA", (side, side))
        ad = ImageDraw.Draw(layer)
        mid = side // 2
        radius = self._S(78)
        ad.ellipse((mid - radius, mid - radius, mid + radius, mid + radius),
                   fill=hex2rgb(c["bar_fg"]) + (25,))
        # 柔光在 1x 域合成，面积只有 3x 的 1/9，视觉上无差别
        self._ambient_layer = layer.filter(
            ImageFilter.GaussianBlur(self._S(30))).reduce(self.SS)
        self._halo_layers = {}

    def _render_base(self, with_ring=True):
        W, H, SS = self.w, self.h, self.SS
        c = self.cfg["colors"]
        # 画布按实际像素尺寸（px_w/px_h），绘制坐标仍走 _S()（布局单位）
        cw, ch = self.px_w * SS, self.px_h * SS
        img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        top, bot = hex2rgb(c["bg"]), hex2rgb(c["bg_grad"])
        gd = ImageDraw.Draw(img)
        for y in range(ch):
            t = (y / float(ch)) ** 0.82
            col = tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3))
            gd.line([(0, y), (cw, y)], fill=col + (255,))
        glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        gg = ImageDraw.Draw(glow)
        accent = hex2rgb(c["countdown"])
        gg.ellipse([self._S(-35), self._S(-75), self._S(W + 35), self._S(105)],
                   fill=accent + (30,))
        glow = glow.filter(ImageFilter.GaussianBlur(self._S(34)))
        img.alpha_composite(glow)
        d = ImageDraw.Draw(img)
        # 内衬用直角：这圈原本也是圆角，和外圈叠在角上会多出一道"内圆弧"
        d.rectangle(
            [self._S(5), self._S(5), self._S(W - 5), self._S(H - 5)],
            outline=hex2rgb(mix(c["border"], "#FFFFFF", 0.08)) + (120,),
            width=self._S(1))
        # 主描边：小圆角
        d.rounded_rectangle(
            [self._S(1), self._S(1), self._S(W - 1), self._S(H - 1)],
            radius=self._S(8), outline=hex2rgb(c["border"]) + (255,),
            width=self._S(1))
        x0, y0, x1, y1 = self.ring_bbox
        if with_ring:
            inset = 17
            d.ellipse([self._S(x0 + inset), self._S(y0 + inset),
                       self._S(x1 - inset), self._S(y1 - inset)],
                      fill=hex2rgb(mix(c["bg"], c["bg_grad"], 0.35)) + (150,),
                      outline=hex2rgb(mix(c["border"], c["bg"], 0.45)) + (120,),
                      width=self._S(1))
            d.arc([self._S(x0), self._S(y0), self._S(x1), self._S(y1)],
                  start=0, end=359.9,
                  fill=hex2rgb(mix(c["bar_bg"], "#FFFFFF", 0.18)) + (255,),
                  width=self._S(self.ring_w))
        return img

    def _text(self, img, d, xy, text, font, fill, anchor="mm", glow=None,
              glow_alpha=70):
        x, y = xy
        if glow is not None:
            bbox = d.textbbox((self._S(x), self._S(y)), text, font=font,
                              anchor=anchor)
            margin = self._S(8)
            left, top = bbox[0] - margin, bbox[1] - margin
            width = bbox[2] - bbox[0] + margin * 2
            height = bbox[3] - bbox[1] + margin * 2
            layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            ImageDraw.Draw(layer).text((self._S(x) - left, self._S(y) - top), text,
                                       font=font, fill=glow + (glow_alpha,),
                                       anchor=anchor)
            layer = layer.filter(ImageFilter.GaussianBlur(self._S(2)))
            img.alpha_composite(layer, (left, top))
        d.text((self._S(x), self._S(y)), text, font=font,
               fill=fill + (255,), anchor=anchor)

    def _draw_ring(self, img, d, prog, fg1, fg2):
        if prog <= 0.003:
            return
        x0, y0, x1, y1 = self.ring_bbox
        sweep = 360.0 * min(prog, 1.0)
        b = [self._S(x0), self._S(y0), self._S(x1), self._S(y1)]
        if sweep < 360 and (sweep / 360.0) * 2 * math.pi * self._S(self.ring_r) < 0.5:
            return
        bg = hex2rgb(self.cfg["colors"]["bg"])
        glow = tuple(int(round(v)) for v in lerp_rgb(hex2rgb(fg1), bg, 0.5)) + (255,)
        bglow = [b[0] - self._S(4), b[1] - self._S(4),
                 b[2] + self._S(4), b[3] + self._S(4)]
        glow_width, ring_width = self._S(self.ring_w + 8), self._S(self.ring_w)
        # 分档数按渐变总色差自适应：每档色阶不超过 6，肉眼不可分，
        # 而 d.arc 每次都要栅格化整个包围盒，档数直接决定耗时。
        delta = max(abs(p - q) for p, q in zip(hex2rgb(fg1), hex2rgb(fg2)))
        N = max(8, min(40, int(math.ceil(delta / 6.0))))
        # PIL arc 是顺时针画，start/end 沿 0°（3 点钟）起算
        # 我们要进度弧从 12 点钟（=270°）起顺时针走 sweep
        # 若 sweep 跨 360°，拆成两段：270°→360° 和 0°→(sweep-90)°
        segments = []
        if sweep <= 90:
            # 终点没跨过 360，直接画
            segments.append((270.0, 270.0 + sweep))
        elif sweep >= 360:
            # 全周：PIL arc 不支持 0→360，画成三段（270→360，0→90，90→270）
            segments.append((270.0, 360.0))
            segments.append((0.0, 90.0))
            segments.append((90.0, 270.0))
        else:
            # 拆两段：先画到 360°，再从 0° 起画剩余
            segments.append((270.0, 360.0))
            segments.append((0.0, sweep - 90.0))
        # 衬底光晕是单一颜色，每段画一次即可（原先每档都重复画一遍）
        for seg_a0, seg_a1 in segments:
            d.arc(bglow, start=seg_a0, end=seg_a1, fill=glow, width=glow_width)
        # 用 N 段均匀渐变（按 sweep 总长分配）
        total_seg = sum(p1 - p0 for p0, p1 in segments)
        drawn = 0.0
        for seg_a0, seg_a1 in segments:
            seg_len = seg_a1 - seg_a0
            seg_n = max(1, int(round(N * seg_len / total_seg)))
            for i in range(seg_n):
                sub_p0 = seg_a0 + seg_len * (i / seg_n)
                sub_p1 = seg_a0 + seg_len * ((i + 1) / seg_n)
                if sub_p1 - sub_p0 < 0.1:
                    continue
                col_ratio = (drawn + (sub_p1 - sub_p0) / 2.0) / total_seg
                rgb = tuple(int(round(v)) for v in lerp_rgb(
                    hex2rgb(fg1), hex2rgb(fg2), col_ratio)) + (255,)
                d.arc(b, start=sub_p0, end=sub_p1, fill=rgb, width=ring_width)
                drawn += (sub_p1 - sub_p0)
        # 进度头部指示器：发光"小金球"效果
        # 中心稍微内移到 ring_r - 2（视觉上更居中于弧线）
        end_angle_deg = (270.0 + sweep) % 360.0
        th = math.radians(end_angle_deg)
        ball_r = self.ring_r - 4
        hx = self.cx + ball_r * math.cos(th)
        hy = self.ring_cy + ball_r * math.sin(th)
        S = self._S
        r = 5   # 金球半径（像素，在 SS=1 缩放前）

        # ① 外层浅金柔光（最大光晕）
        outer_col = lerp_rgb(hex2rgb(fg2), (255, 255, 255), 0.6)
        d.ellipse([S(hx - r * 2.5), S(hy - r * 2.5),
                   S(hx + r * 2.5), S(hy + r * 2.5)],
                  fill=tuple(int(round(v * 0.25)) for v in outer_col) + (255,))
        # ② 中层亮金球（比弧线亮一点，用纯金色 fg2）
        d.ellipse([S(hx - r), S(hy - r), S(hx + r), S(hy + r)],
                  fill=hex2rgb(fg2) + (255,))
        # ③ 顶部高光小白点（制造立体感）
        hi_r = max(1, r // 2)
        hi_x = hx - r * 0.3
        hi_y = hy - r * 0.3
        d.ellipse([S(hi_x - hi_r), S(hi_y - hi_r),
                   S(hi_x + hi_r), S(hi_y + hi_r)],
                  fill=(255, 255, 255, 200))

    def render_frame(self):
        st = self._state
        rest = st.get("phase") == "rest"
        c = self.cfg["colors"]
        # 背景 = 静态底图 + 柔光，全部在 1x 域合成
        bg = (self.base_small_rest if rest else self.base_small).copy()
        if self.cfg["animations"]:
            drift = math.sin(self.ambient_phase) * 26
            bg.alpha_composite(self._ambient_layer, (
                self._U(self.cx + drift - 138), self._U(self.ring_cy - 138)))
        prog = st.get("bar", 0.0)
        phase = st.get("phase", "pm")
        fg1 = c["dot_ot"] if phase == "ot" else c["bar_fg"]
        fg2 = c["bar_fg2"] if phase != "ot" else c["dot_ot"]
        if not rest and self.cfg["animations"]:
            pulse = (math.sin(self.ambient_phase * 2.0) + 1.0) / 2.0
            level = min(11, int(pulse * 12))
            key = (fg1, level)
            if key not in self._halo_layers:
                side = self._S(224)
                halo = Image.new("RGBA", (side, side))
                hd = ImageDraw.Draw(halo)
                grow = 5 + level / 11 * 3
                middle = side // 2
                radius = self._S(self.ring_r + grow)
                hd.ellipse((middle - radius, middle - radius,
                            middle + radius, middle + radius),
                           outline=hex2rgb(fg1) + (35 + int(level / 11 * 35),),
                           width=self._S(5 + level / 11 * 2))
                self._halo_layers[key] = halo.filter(
                    ImageFilter.GaussianBlur(self._S(7))).reduce(self.SS)
            bg.alpha_composite(self._halo_layers[key], (
                self._U(self.cx - 112), self._U(self.ring_cy - 112)))
        # 前景（进度环、文字、胶囊、彩带）仍按 3x 超采样绘制。
        # 把 1x 背景用 NEAREST 放大 3 倍后画在其上，末尾再 reduce(3) 缩回，
        # 背景逐像素原样还原，绘制语义与单画布完全一致。
        img = bg.resize((self.px_w * self.SS, self.px_h * self.SS),
                        Image.NEAREST)
        d = ImageDraw.Draw(img)
        if not rest:
            self._draw_ring(img, d, prog, fg1, fg2)
        mcol = st.get("money_color", hex2rgb(c["money"]))
        money_glow = 70
        if self.cfg["animations"]:
            money_glow += int(30 * ((math.sin(self.ambient_phase * 1.7) + 1) / 2))
        if rest:
            # 休息日：圆环位置直接显示本月已赚
            self._text(img, d, (self.cx, self.ring_cy - 4),
                       st.get("cd", ""), self.f_money, mcol, glow=mcol,
                       glow_alpha=money_glow)
            self._text(img, d, (self.cx, self.ring_cy + 29),
                       st.get("money_label", "本月已赚"), self.f_cn,
                       hex2rgb(c["moneylbl"]))
        else:
            self._text(img, d, (self.cx, self.ring_cy - 4),
                       "¥%.2f" % st.get("money", 0.0), self.f_money, mcol,
                       glow=mcol, glow_alpha=money_glow)
            self._text(img, d, (self.cx, self.ring_cy + 29),
                       st.get("money_label", "今日已赚"),
                       self.f_cn, hex2rgb(c["moneylbl"]))
        # 顶部：左侧阶段文字，右侧加班徽标
        dot_cy = self.chip_y + self.chip_h / 2
        self._text(img, d, (self.pad, dot_cy),
                   st.get("chip", "距下班"), self.f_cn, hex2rgb(c["title"]),
                   anchor="lm")
        if st.get("overtime"):
            self._text(img, d,
                       (self.w - self.pad, dot_cy),
                       "加班 %s" % fmt_t(parse_t(
                           self.cfg.get("overtime_end", "20:30"))),
                       self.f_cn_s, hex2rgb(c["warn1"]), anchor="rm")
        if not rest and phase != "done":
            cd_txt = st.get("cd", "--:--:--")
            f_cd = self.f_digit if cd_txt.isascii() else self.f_cn_big
            pill_col = mix(c["bg_grad"], c["countdown"], 0.08)
            d.rounded_rectangle(
                [self._S(self.cx - 94), self._S(self.cd_y - 22),
                 self._S(self.cx + 94), self._S(self.cd_y + 22)],
                radius=self._S(15), fill=hex2rgb(pill_col) + (225,),
                outline=hex2rgb(mix(c["border"], c["countdown"], 0.16)) + (220,),
                width=self._S(1))
            icon_x = self.cx - 72
            icon_col = hex2rgb(mix(c["footer"], c["countdown"], 0.3))
            d.ellipse([self._S(icon_x - 6), self._S(self.cd_y - 6),
                       self._S(icon_x + 6), self._S(self.cd_y + 6)],
                      outline=icon_col + (255,), width=self._S(1))
            d.line([self._S(icon_x), self._S(self.cd_y),
                    self._S(icon_x), self._S(self.cd_y - 4)],
                   fill=icon_col + (255,), width=self._S(1))
            d.line([self._S(icon_x), self._S(self.cd_y),
                    self._S(icon_x + 3), self._S(self.cd_y + 2)],
                   fill=icon_col + (255,), width=self._S(1))
            self._text(img, d, (self.cx + 10, self.cd_y), cd_txt, f_cd,
                       st.get("cd_color", hex2rgb(c["countdown"])),
                       glow=st.get("cd_color", hex2rgb(c["countdown"])))
        for p in self.confetti:
            col = hex2rgb(p["col"])
            alpha = int(255 * p["life"] / 120.0)
            d.rectangle(
                [self._S(p["x"]), self._S(p["y"]),
                 self._S(p["x"] + p["size"]), self._S(p["y"] + p["size"] * 0.6)],
                fill=col + (alpha,))
        # 3x 超采样降到 1x：SS 整除时 reduce 就是精确的盒式降采样，比 LANCZOS 快约 4 倍
        small = img.reduce(self.SS)
        # 尺寸变了必须重建 PhotoImage：它的尺寸不可变，paste 只会把新帧写进
        # 旧画布，结果窗口不跟着变大、还留一圈残影（改「面板大小」时会走到）。
        if self._photo is None or (self._photo.width(),
                                   self._photo.height()) != small.size:
            self._photo = ImageTk.PhotoImage(small)
            self._label.config(image=self._photo)
        else:
            self._photo.paste(small)

    def _print_layout(self):
        emit("window: %dx%d (ui=%.2f SS=%d) ring_cy=%s ring_r=%s cd_y=%s"
             % (self.px_w, self.px_h, self.ui, self.SS, self.ring_cy,
                self.ring_r, self.cd_y))

    # ---------------- 彩带 ----------------

    def spawn_confetti(self):
        import random
        colors = ["#FFC53D", "#4FC3F7", "#FF6B9D", "#3DDC84", "#A78BFA",
                  "#FF9F43"]
        for _ in range(46):
            self.confetti.append({
                "x": random.uniform(0, self.w),
                "y": random.uniform(0, self.h * 0.35),
                "vx": random.uniform(-1.3, 1.3),
                "vy": random.uniform(0.4, 2.2),
                "size": random.uniform(2.0, 4.6),
                "life": random.randint(55, 120),
                "col": random.choice(colors),
            })

    def update_confetti(self):
        if not self.confetti:
            return
        alive = []
        for p in self.confetti:
            p["vy"] += 0.07
            p["x"] += p["vx"]
            p["y"] += p["vy"]
            p["life"] -= 1
            if p["life"] > 0 and p["y"] < self.h + 12:
                alive.append(p)
        self.confetti = alive

    # ---------------- 模式切换 ----------------

    def apply_modes(self):
        try:
            if self.cfg["desktop_layer"] and not self.pin_to_desktop():
                # 贴不上时本次先置顶，免得窗口既不置顶也不在桌面层、被别的窗口盖住。
                # 但不去改配置：偏好是用户的，下次启动仍会再试一次。
                self.root.attributes("-topmost", True)
                if not self._pin_warning_shown:
                    self._pin_warning_shown = True
                    self.root.after(0, lambda: messagebox.showwarning(
                        APP_NAME,
                        "未能贴到桌面底层，本次改为置顶显示。\n"
                        "你的设置没有被改动，下次启动会再试一次。",
                        parent=self.root))
                return
            if not self.cfg["desktop_layer"]:
                self.unpin_from_desktop()
            self.root.attributes("-topmost", bool(self.cfg["always_on_top"]))
        except Exception:
            log_exc()
            # 出意外时至少把窗口顶到最上层：否则它可能既不置顶、也不在桌面层，
            # 被别的窗口整个盖住，用户会以为小窗丢了。
            try:
                self.root.attributes("-topmost", True)
            except Exception:
                pass

    def pin_to_desktop(self):
        """贴到桌面底层：壁纸之上、所有普通窗口之下。

        这里**不用**给 Progman/WorkerW 发 0x052C 再 SetParent 的经典做法：
        实测在 Windows 11 build 26200 上，Tk 窗口一旦被 SetParent 过去就
        完全不再绘制——窗口"存在"但屏幕上什么都没有（加不加 WS_CHILD 都一样），
        而且这台机器上根本找不到可用的 WorkerW。把窗口留在原层级、
        只压到 z 序最底，效果完全一样，窗口还能正常显示。
        """
        if self.pinned:
            return True
        user32.SetWindowPos(get_hwnd(self.root), HWND_BOTTOM,
                            self.cur_x, self.cur_y, 0, 0,
                            SWP_NOSIZE | SWP_NOACTIVATE)
        self.pinned = True
        return True

    def unpin_from_desktop(self):
        if not self.pinned:
            return
        self.pinned = False
        user32.SetWindowPos(get_hwnd(self.root),
                            HWND_TOPMOST if self.cfg["always_on_top"]
                            else HWND_NOTOPMOST, self.cur_x, self.cur_y,
                            0, 0, SWP_NOSIZE | SWP_NOACTIVATE)

    def keep_at_bottom(self):
        """贴桌面底层时，窗口被点过就会被抬到最上层，需要周期性压回去。"""
        if self.pinned and not self.dragging:
            user32.SetWindowPos(get_hwnd(self.root), HWND_BOTTOM,
                                0, 0, 0, 0,
                                SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)

    # ---------------- 事件 ----------------

    def bind_events(self):
        self.root.bind("<ButtonPress-1>", self.on_press)
        self.root.bind("<B1-Motion>", self.on_drag)
        self.root.bind("<ButtonRelease-1>", self.on_release)
        self.root.bind("<Double-Button-1>", self.on_double)
        self.root.bind("<Button-3>", self.on_right)

    def on_press(self, e):
        self._off = (e.x_root - self.cur_x, e.y_root - self.cur_y)
        self.dragging = True

    def on_drag(self, e):
        x = e.x_root - self._off[0]
        y = e.y_root - self._off[1]
        self.cur_x, self.cur_y = x, y
        move_window_absolute(self.root, x, y)

    def on_release(self, e):
        self.dragging = False
        self.render_frame()
        self.cfg["position"] = [self.cur_x, self.cur_y]
        save_config(self.cfg)

    def on_double(self, e):
        self.reset_position()

    def on_right(self, e):
        self.popup_widget_menu(e)

    def popup_widget_menu(self, e):
        self.popup_menu(e.x_root, e.y_root)

    def popup_menu(self, x, y, from_tray=False):
        host = None
        if from_tray:
            # 贴桌面或隐藏小窗时，菜单仍需有一个可见的顶层窗口作宿主。
            host = tk.Toplevel(self.root)
            host.overrideredirect(True)
            host.attributes("-alpha", 0.01)
            host.geometry("1x1+%d+%d" % (x, y))
            host.update_idletasks()
            user32.SetForegroundWindow(get_hwnd(host))
            host.focus_force()
        m = getattr(self, "_context_menu", None)
        if m is None or not m.winfo_exists():
            m = tk.Menu(self.root, tearoff=0)
            self._context_menu = m
        else:
            m.delete(0, "end")
        self._fill_menu(m)
        try:
            m.tk_popup(x, y)
        finally:
            if m.winfo_exists():
                m.grab_release()
            if host and host.winfo_exists():
                host.destroy()

    def _fill_menu(self, m):
        if not hasattr(self, "_menu_vars"):
            self._menu_vars = {
                "always_on_top": tk.BooleanVar(master=self.root),
                "desktop_layer": tk.BooleanVar(master=self.root),
            }
        for key, variable in self._menu_vars.items():
            variable.set(bool(self.cfg[key]))
        m.add_checkbutton(label="置顶显示", onvalue=1, offvalue=0,
                          variable=self._menu_vars["always_on_top"],
                          command=self.toggle_topmost)
        m.add_checkbutton(label="贴在桌面底层", onvalue=1, offvalue=0,
                          variable=self._menu_vars["desktop_layer"],
                          command=self.toggle_desktop)
        m.add_separator()
        today = dt.date.today()
        day_info = calendar_day(today, self.cfg)
        saved_today = self.cfg.get("calendar_overrides", {}).get(today.isoformat())
        live_overtime = (saved_today["attendance"] == "overtime" if saved_today
                         else self.cfg.get("overtime_today", False) and
                         self.cfg.get("overtime_date") == today.isoformat())
        label = ("休息日加班：打开日历填写收入..." if day_info["kind"] in
                 ("rest", "statutory") else overtime_menu_text(
                     live_overtime, self.cfg.get("overtime_end", "20:30")))
        m.add_command(label=label,
                      command=self.toggle_overtime)
        m.add_separator()
        m.add_command(label="取消自启" if autostart_enabled() else "开机自启",
                      command=self.toggle_autostart)
        m.add_command(label="个性化与工作设置...", command=self.open_settings)
        m.add_command(label="重新加载配置", command=self.reload_config)
        m.add_command(label="重置位置", command=self.reset_position)
        m.add_separator()
        m.add_command(label="退出", command=self.quit)

    def toggle_visible(self):
        if self.visible:
            self.root.withdraw()
            self.visible = False
        else:
            self.root.deiconify()
            self.visible = True
            self.fade = 0.0  # 重新淡入
            self.apply_modes()

    def toggle_topmost(self):
        self.cfg["always_on_top"] = not self.cfg["always_on_top"]
        if self.cfg["always_on_top"]:
            self.cfg["desktop_layer"] = False
        save_config(self.cfg)
        self.apply_modes()

    def toggle_desktop(self):
        self.cfg["desktop_layer"] = not self.cfg["desktop_layer"]
        if self.cfg["desktop_layer"]:
            self.cfg["always_on_top"] = False
        save_config(self.cfg)
        self.apply_modes()

    def toggle_overtime(self):
        today_date = dt.date.today()
        today = today_date.isoformat()
        current = calendar_day(today_date, self.cfg)
        if current["kind"] in ("rest", "statutory"):
            self.open_settings()
            self.settings_window.open_calendar()
            return
        if roll_calendar(self.cfg, today_date):
            save_config(self.cfg)
        saved_today = self.cfg["calendar_overrides"].get(today)
        live_overtime = (saved_today["attendance"] == "overtime" if saved_today
                         else self.cfg.get("overtime_today", False) and
                         self.cfg.get("overtime_date") == today)
        self.cfg["overtime_today"] = not live_overtime
        self.cfg["overtime_date"] = today
        self.cfg["calendar_overrides"][today] = {
            "kind": current["kind"], "attendance": "overtime" if self.cfg["overtime_today"]
            else "normal", "amount": None}
        save_config(self.cfg)

    def toggle_autostart(self):
        try:
            if autostart_enabled():
                remove_autostart()
                text = "已取消开机自启"
            else:
                install_autostart()
                text = "已开启开机自启，下次登录 Windows 时会自动启动%s" % APP_NAME
            self.root.after(0, lambda: messagebox.showinfo(
                APP_NAME, text, parent=self.root))
        except Exception:
            log_exc()
            self.root.after(0, lambda: messagebox.showerror(
                APP_NAME, "设置开机自启失败，请检查权限或启动文件夹设置。",
                parent=self.root))

    def open_settings(self):
        try:
            if self.settings_window is not None and self.settings_window.focus():
                return
            self.settings_window = SettingsWindow(self, sys.modules[__name__])
        except Exception as exc:
            log_exc()
            self.settings_window = None
            messagebox.showerror("设置窗口无法打开", str(exc), parent=self.root)

    def apply_config(self, cfg, persist=True):
        self.cfg = cfg
        if persist:
            save_config(self.cfg)
        settings_open = self.settings_window is not None
        fade_in = self.cfg["animations"] and not settings_open
        self.root.attributes("-alpha", 0.0 if fade_in else self.cfg["opacity"])
        self.root.configure(bg=self.cfg["colors"]["bg"])
        self.build_base()
        self._label.config(bg=self.cfg["colors"]["bg"])
        self.render_frame()
        set_square_corners(self.root)
        # 面板大小可能变了，重新夹回桌面范围，免得放大后跑出屏幕
        self.cur_x, self.cur_y = clamp_to_desktop(
            self.cur_x, self.cur_y, self.px_w, self.px_h)
        self.root.geometry("+%d+%d" % (self.cur_x, self.cur_y))
        self.fade = 0.0 if fade_in else 1.0
        if not settings_open:
            self.disp_money = 0.0
            self.disp_bar = 0.0
        self.cd_color = hex2rgb(self.cfg["colors"]["countdown"])
        self.apply_modes()

    def reload_config(self):
        cfg = load_config()
        if roll_calendar(cfg, dt.date.today()):
            save_config(cfg)
        self.apply_config(cfg, persist=False)
        if CONFIG_WARNING:
            warning = CONFIG_WARNING
            self.root.after(0, lambda: messagebox.showwarning(
                "%s 配置" % APP_NAME, warning, parent=self.root))

    def reset_position(self):
        self.root.update_idletasks()
        x = user32.GetSystemMetrics(SM_CXSCREEN) - self.px_w - 24
        y = 28
        self.cur_x, self.cur_y = clamp_to_desktop(
            x, y, self.px_w, self.root.winfo_height())
        if self.pinned:
            move_window_absolute(self.root, x, y)
        else:
            self.root.geometry("+%d+%d" % (x, y))
        self.cfg["position"] = None
        save_config(self.cfg)

    # ---------------- 定时刷新 ----------------

    def poll_tray(self):
        try:
            while True:
                message = self.to_tk.get_nowait()
                kind = message[0]
                if kind == "popup_menu":
                    self.popup_menu(*message[1], from_tray=True)
                elif kind == "toggle_visible":
                    self.toggle_visible()
                elif kind == "hotkey_failed" and not self._hotkey_warning_shown:
                    self._hotkey_warning_shown = True
                    messagebox.showwarning(
                        APP_NAME,
                        "老板键 Ctrl+Alt+D 注册失败，多半已被其它程序占用。\n"
                        "可以改用托盘图标或小窗右键菜单。",
                        parent=self.root)
                elif kind == "tray_failed" and not self._tray_warning_shown:
                    self._tray_warning_shown = True
                    messagebox.showwarning(
                        APP_NAME,
                        "托盘图标启动失败：托盘菜单暂不可用，"
                        "小窗右键菜单和 Ctrl+Alt+D 仍然可用。",
                        parent=self.root)
        except queue.Empty:
            pass
        except Exception:
            log_exc()
        self.root.after(200, self.poll_tray)

    def tick(self):
        try:
            today_date = dt.date.today()
            today = today_date.isoformat()
            if roll_calendar(self.cfg, today_date):
                self.cfg["overtime_today"] = False
                self.cfg["overtime_date"] = today
                save_config(self.cfg)
            if self.cfg.get("overtime_date") != today:
                self.cfg["overtime_today"] = False
                self.cfg["overtime_date"] = today
                save_config(self.cfg)
            r = compute(dt.datetime.now(), self.cfg)
            c = self.cfg["colors"]
            anim = self.cfg["animations"]
            now = dt.datetime.now()

            # 启动/显示淡入
            if self.fade < 1.0:
                self.fade = min(1.0, self.fade + 0.045)
                eased = 1 - (1 - self.fade) * (1 - self.fade)
                self.root.attributes("-alpha",
                                     self.cfg["opacity"] * eased)

            if anim:
                self.ambient_phase += 0.035
            # 倒计时颜色
            if r["phase"] in ("am", "pm", "ot"):
                off_s = parse_t(self.cfg["overtime_end"]) * 60 \
                    if r["overtime"] else parse_t(self.cfg["work_end"]) * 60
                rem = off_s - (now.hour * 3600 + now.minute * 60 +
                               now.second)
                target = (c["warn2"] if rem < 600 else
                          c["warn1"] if rem < 3600 else c["countdown"])
            else:
                target = c["countdown"]
            if anim:
                self.cd_color = lerp_rgb(self.cd_color, hex2rgb(target), 0.1)
                col = rgb2hex(self.cd_color)
                if now.second % 2 == 1:
                    col = mix(col, c["bg"], 0.10)
            else:
                col = target

            # 金额
            target_money = r["earned"]
            if anim:
                self.disp_money += (target_money - self.disp_money) * 0.55
                if abs(target_money - self.disp_money) < 0.0004:
                    self.disp_money = target_money
            else:
                self.disp_money = target_money
            mcol = c["money"]

            # 进度
            prog = max(0.0, min(1.0, r["progress"]))
            if anim:
                self.disp_bar += (prog - self.disp_bar) * 0.12
                if abs(prog - self.disp_bar) < 0.001:
                    self.disp_bar = prog
            else:
                self.disp_bar = prog
            # 组装渲染状态
            self._state = {
                "bar": self.disp_bar,
                "phase": r["phase"],
                "money": self.disp_money,
                "money_color": hex2rgb(mcol),
                "chip": {"am": "上班中", "pm": "上班中", "lunch": "午休中",
                         "ot": "加班中", "dinner": "晚休中",
                         "done": "已下班", "rest": "休息",
                         "pre": "未上班"}.get(r["phase"], "距下班"),
                "cd": r["countdown"],
                "cd_color": hex2rgb(col),
                "money_label": r.get("money_prefix", "今日已赚"),
                "overtime": r["overtime"],
            }

            # 下班彩带
            if self.prev_phase is not None and self.prev_phase != r["phase"] \
                    and r["phase"] == "done" and anim:
                self.spawn_confetti()
            self.prev_phase = r["phase"]
            self.update_confetti()

            # 设置窗口打开时降低后台重绘频率，进度仍持续更新。
            frame_time = time.monotonic()
            if not self.dragging and (self.settings_window is None or
                    frame_time - self._last_frame_at >= 0.2):
                self.render_frame()
                self._last_frame_at = frame_time

            # 贴桌面底层时窗口被点过就会被抬起来，每秒压回去一次
            if self.pinned and now.second != getattr(self, "_bottom_tick", -1):
                self._bottom_tick = now.second
                self.keep_at_bottom()

            # 托盘提示（每秒最多一次）
            # done 阶段显示的金额是本月累计，标签必须跟着 money_prefix 走
            earned_label = r.get("money_prefix", "今日已赚")
            if r["phase"] == "rest":
                tooltip = "%s · 今日休息 · 本月已赚 %s" % (APP_NAME, r["countdown"])
            elif r["phase"] == "done":
                tooltip = "%s · %s · %s ¥%.2f" % (
                    APP_NAME, r["title"], earned_label, target_money)
            elif r["countdown"]:
                tooltip = "%s · %s %s · %s ¥%.2f" % (
                    APP_NAME, r["title"], r["countdown"], earned_label, target_money)
            else:
                tooltip = "%s · %s · %s ¥%.2f" % (
                    APP_NAME, r["title"], earned_label, target_money)
            if tooltip != self.last_tooltip:
                self.last_tooltip = tooltip
                send_tray_message(self.to_tray, ("tooltip", tooltip))
        except Exception:
            log_exc()
        self.root.after(80, self.tick)

    def quit(self):
        try:
            if self.settings_window is not None:
                self.settings_window.close()
            save_config(self.cfg)
            if self.pinned:
                self.unpin_from_desktop()
            send_tray_message(self.to_tray, ("exit",))
            if self.cfg["animations"]:
                for i in range(1, 5):
                    self.root.after(
                        i * 50, lambda a=i: self.root.attributes(
                            "-alpha", max(0.05, self.cfg["opacity"] *
                                          (1 - a / 5.0))))
            self.root.after(280, self.root.destroy)
        except Exception:
            self.root.destroy()


# ---------------------------------------------------------------- 命令行 / 入口

def selftest(times):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    cfg = load_config()
    emit("配置: 上班 %s 午休 %s-%s 下班 %s 日薪 ¥%s 计薪日 %s" % (
        cfg["work_start"], cfg["lunch_start"], cfg["lunch_end"],
        cfg["work_end"], cfg["daily_salary"],
        ",".join(str(d) for d in cfg["workdays"])))
    for ts in times:
        now = dt.datetime.fromisoformat(ts)
        r = compute(now, cfg)
        emit("%s %s | 阶段=%-5s %-12s %-16s 已赚 ¥%-9.2f 进度 %5.1f%% 时薪 ¥%.2f"
              % (ts, ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
                 [now.weekday()], r["phase"], r["title"], r["countdown"],
                  r["earned"], r["progress"] * 100, r["hourly"]))


_kernel32_lasterror = None


def _mutex_api():
    """带 use_last_error 的 kernel32。

    ctypes 只有开 use_last_error 才能可靠地取到 GetLastError：否则
    CreateMutexW 与读错误码之间可能被其它调用冲掉。
    """
    global _kernel32_lasterror
    if _kernel32_lasterror is None:
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateMutexW.argtypes = [wt.LPVOID, wt.BOOL, wt.LPCWSTR]
        api.CreateMutexW.restype = wt.HANDLE
        api.CloseHandle.argtypes = [wt.HANDLE]
        api.CloseHandle.restype = wt.BOOL
        _kernel32_lasterror = api
    return _kernel32_lasterror


def acquire_instance_lock(name=MUTEX_NAME):
    """用命名互斥体实现单实例。

    进程无论正常退出、崩溃还是被任务管理器强制结束，内核都会释放互斥体，
    所以不存在 PID 文件那种"残留锁被复用 PID 占住导致再也打不开"的问题。
    返回互斥体句柄；已有实例在运行时返回 None。
    """
    api = _mutex_api()
    handle = api.CreateMutexW(None, False, name)
    if not handle:
        return None
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        api.CloseHandle(handle)
        return None
    return handle


def release_instance_lock(handle):
    if handle:
        _mutex_api().CloseHandle(handle)


def main():
    setup_win32()
    cleanup_exception_logs()
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    if "--selftest" in sys.argv:
        i = sys.argv.index("--selftest")
        times = sys.argv[i + 1].split(",") if i + 1 < len(sys.argv) else \
            [dt.datetime.now().isoformat(timespec="seconds")]
        selftest(times)
        return
    if "--make-icon" in sys.argv:
        write_ico(here("paydance.ico"))
        emit("已生成 paydance.ico")
        return
    if "--install-autostart" in sys.argv:
        emit("开机自启已设置: %s" % install_autostart())
        return
    if "--remove-autostart" in sys.argv:
        emit("开机自启已移除: %s" % remove_autostart())
        return

    # 界面绘制依赖 Pillow；没有它时下面会直接崩在渲染里，而 pythonw 没有控制台，
    # 用户只会看到"双击了没反应"，所以必须先明确告知。
    if Image is None:
        fatal_message(
            "缺少 Pillow 图像库，下班倒计时无法启动。\n\n"
            "请打开命令行执行：\n    pip install Pillow\n\n"
            "安装完成后重新启动下班倒计时。")
        return

    if "--uicheck" not in sys.argv:
        try:
            repair_autostart()
        except Exception:
            log_exc()

    instance_lock = None
    # 单实例（命名互斥体，进程结束时由内核自动释放）
    if "--uicheck" not in sys.argv:
        instance_lock = acquire_instance_lock()
        if instance_lock is None:
            return

    try:
        app = PayDanceApp()
        schedule_exception_log_cleanup(app.root)
        if CONFIG_WARNING:
            app.root.after(500, lambda: messagebox.showwarning(
                "%s 配置" % APP_NAME, CONFIG_WARNING, parent=app.root))
        threading.Thread(target=refresh_nearby_calendars, daemon=True).start()
        threading.Thread(target=tray_thread, daemon=True,
                         args=(app.to_tk, app.to_tray)).start()
        if "--settings" in sys.argv:
            app.root.after(0, app.open_settings)
        app.root.mainloop()
    except Exception as exc:
        log_exc()
        # 打包成 windowed 程序后没有控制台，任何启动/运行异常如果只写日志，
        # 用户看到的就是"双击了没反应"，所以必须弹窗告诉他。
        fatal_message("%s 遇到错误并退出：%s: %s\n\n详细信息见日志：\n%s"
                      % (APP_NAME, type(exc).__name__, exc, LOG_PATH))
    finally:
        release_instance_lock(instance_lock)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
