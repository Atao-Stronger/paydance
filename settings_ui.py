"""Qt Widgets settings and current-month attendance editor."""

import calendar
import copy
import datetime as dt
from pathlib import Path

from PySide6.QtCore import QDate, QLocale, Qt, QTime
from PySide6.QtGui import QColor, QDoubleValidator, QFont, QIcon, QPalette, QTextCharFormat
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCalendarWidget, QCheckBox,
    QColorDialog, QComboBox, QDialog, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSlider,
    QStackedWidget, QTimeEdit, QVBoxLayout, QWidget)


STYLE = """
QWidget { font-family: 'Microsoft YaHei UI'; font-size: 13px; color: #243347; }
QDialog, QStackedWidget, QScrollArea, QWidget#page { background: #f5f7fa; }
QFrame#sidebar, QFrame#footer { background: #ffffff; }
QFrame#sidebar { border-right: 1px solid #e6ebf1; }
QFrame#footer { border-top: 1px solid #e6ebf1; }
QFrame#monthSummary { background: #ffffff; border-bottom: 1px solid #e6ebf1; }
QLabel#monthAmount { color: #137c68; font-size: 28px; font-weight: 700; }
QFrame#card { background: #ffffff; border: 1px solid #e5eaf0; border-radius: 12px; }
QFrame#preview { background: #eaf7f3; border: 1px solid #d0ebe3; border-radius: 12px; }
QLabel { background: transparent; }
QLabel#title { font-size: 23px; font-weight: 700; color: #18283b; }
QLabel#section { font-size: 15px; font-weight: 700; color: #233449; }
QLabel#brand { font-size: 18px; font-weight: 700; }
QLabel#muted, QLabel#fieldLabel { color: #728197; font-size: 12px; }
QLabel#previewValue { color: #137c68; font-size: 16px; font-weight: 600; }
QPushButton { background: #ffffff; border: 1px solid #dbe2ea; border-radius: 7px;
              padding: 8px 16px; font-weight: 500; }
QPushButton:hover { background: #f2f6f8; border-color: #b7c6d2; }
QPushButton:pressed { background: #e8eef3; }
QPushButton:disabled { color: #a7b1bf; background: #f1f4f7; }
QPushButton#primary { background: #17856f; color: #ffffff; border-color: #17856f; }
QPushButton#primary:hover { background: #106e5c; }
QPushButton#nav { text-align: left; border: none; padding: 13px 16px; color: #637389; }
QPushButton#nav:checked { background: #e7f4ef; color: #137c68; font-weight: 700; }
QPushButton#day { padding: 8px 4px; }
QPushButton#day:checked { background: #e7f4ef; color: #137c68; border-color: #89c5b5; }
QComboBox, QLineEdit, QTimeEdit {
    background: #fbfcfe; border: 1px solid #dbe2ea; border-radius: 7px;
    min-height: 20px; padding: 7px 10px; selection-background-color: #17856f; }
QComboBox:focus, QLineEdit:focus, QTimeEdit:focus { border-color: #17856f; }
QComboBox:disabled, QLineEdit:disabled, QTimeEdit:disabled {
    background: #f1f4f7; color: #a4afbd; }
QComboBox QAbstractItemView { background: #ffffff; selection-background-color: #e7f4ef;
                            selection-color: #137c68; padding: 4px; outline: none; }
QCheckBox { spacing: 9px; padding: 4px 0; }
QCheckBox::indicator { width: 17px; height: 17px; }
QScrollArea { border: none; }
QScrollBar:vertical { background: transparent; width: 7px; margin: 3px; }
QScrollBar::handle:vertical { background: #ccd6e0; border-radius: 3px; min-height: 35px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QSlider::groove:horizontal { height: 5px; background: #e0e7ee; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #17856f; border-radius: 2px; }
QSlider::handle:horizontal { background: #ffffff; border: 2px solid #17856f;
                           width: 14px; height: 14px; margin: -6px 0; border-radius: 8px; }
QCalendarWidget QWidget { background: #ffffff; }
QCalendarWidget QAbstractItemView { selection-background-color: #17856f;
                                  selection-color: #ffffff; outline: none; }
"""


def configure_application(app):
    app.setStyle("Fusion")
    palette = QPalette()
    for role, color in ((QPalette.ColorRole.Window, "#f5f7fa"),
                        (QPalette.ColorRole.WindowText, "#243347"),
                        (QPalette.ColorRole.Base, "#fbfcfe"),
                        (QPalette.ColorRole.AlternateBase, "#f1f4f7"),
                        (QPalette.ColorRole.Text, "#243347"),
                        (QPalette.ColorRole.Button, "#ffffff"),
                        (QPalette.ColorRole.ButtonText, "#243347"),
                        (QPalette.ColorRole.Highlight, "#17856f"),
                        (QPalette.ColorRole.HighlightedText, "#ffffff")):
        palette.setColor(role, QColor(color))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#a4afbd"))
    app.setPalette(palette)
    QLocale.setDefault(QLocale("zh_CN"))
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setStyleSheet(STYLE)


def label(text, kind=None):
    widget = QLabel(text)
    if kind:
        widget.setObjectName(kind)
    widget.setWordWrap(True)
    return widget


def card(title, preview=False):
    frame = QFrame()
    frame.setObjectName("preview" if preview else "card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 14, 18, 15)
    layout.setSpacing(10)
    layout.addWidget(label(title, "section"))
    return frame, layout


def money_input(value):
    widget = QLineEdit("%.2f" % value)
    validator = QDoubleValidator(0, 1_000_000_000, 2, widget)
    validator.setNotation(QDoubleValidator.Notation.StandardNotation)
    widget.setValidator(validator)
    return widget


def center_window(window, width, height):
    screen = window.screen() or QApplication.primaryScreen()
    area = screen.availableGeometry()
    window.resize(min(width, area.width() - 40), min(height, area.height() - 40))
    frame = window.frameGeometry()
    frame.moveCenter(area.center())
    window.move(frame.topLeft())


class SettingsDialog(QDialog):
    def __init__(self, core, cfg, submit):
        super().__init__()
        self.core, self.cfg, self.submit = core, copy.deepcopy(cfg), submit
        self.controls, self.fields = {}, {}
        self.pending_colors = dict(cfg["colors"])
        self.calendar_window = None
        self.pending_close = False
        self.pending_message = ""
        self.setWindowTitle("%s %s · 个性化与工作设置" % (core.APP_NAME, core.APP_VERSION))
        icon = QIcon(str(Path(__file__).with_name("paydance.ico")))
        QApplication.instance().setWindowIcon(icon)
        self.setWindowIcon(icon)
        self.setMinimumSize(760, 540)
        self._build()
        self._connect_changes()
        self._update_fields()
        self.update_month_total(core.month_earned_now(dt.datetime.now(), self.cfg),
                                dt.date.today().strftime("%Y年%m月"))
        center_window(self, 960, 740)

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QHBoxLayout()
        body.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(186)
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(18, 30, 18, 20)
        nav.setSpacing(7)
        nav.addWidget(label("下班倒计时", "brand"))
        nav.addWidget(label("个性化与工作设置", "muted"))
        nav.addSpacing(26)
        self.navigation = QButtonGroup(self)
        self.pages = QStackedWidget()
        for index, (name, builder) in enumerate((
                ("收入与扣除", self._income_page),
                ("作息与日历", self._schedule_page),
                ("小窗外观", self._appearance_page))):
            button = QPushButton(name)
            button.setObjectName("nav")
            button.setCheckable(True)
            button.setChecked(index == 0)
            self.navigation.addButton(button, index)
            nav.addWidget(button)
            self.pages.addWidget(builder())
        self.navigation.idClicked.connect(self.pages.setCurrentIndex)
        nav.addStretch()
        nav.addWidget(label("设置保存后立即生效\n重启后仍会保留", "muted"))
        body.addWidget(sidebar)
        right = QVBoxLayout()
        right.setSpacing(0)
        self.month_summary = QFrame()
        self.month_summary.setObjectName("monthSummary")
        summary = QHBoxLayout(self.month_summary)
        summary.setContentsMargins(26, 16, 26, 16)
        summary.setSpacing(16)
        summary.addWidget(label("本月已赚", "section"))
        self.month_amount = label("", "monthAmount")
        summary.addWidget(self.month_amount)
        summary.addStretch()
        self.month_detail = label("", "muted")
        self.month_detail.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        summary.addWidget(self.month_detail)
        right.addWidget(self.month_summary)
        right.addWidget(self.pages, 1)
        body.addLayout(right, 1)
        outer.addLayout(body, 1)
        footer = QFrame()
        footer.setObjectName("footer")
        row = QHBoxLayout(footer)
        row.setContentsMargins(24, 15, 24, 15)
        row.setSpacing(9)
        self.status = label("", "muted")
        row.addWidget(self.status, 1)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.reject)
        self.save_button = QPushButton("保存")
        self.save_button.clicked.connect(lambda: self.save(False))
        self.apply_button = QPushButton("保存并关闭")
        self.apply_button.setObjectName("primary")
        self.apply_button.clicked.connect(lambda: self.save(True))
        self.apply_button.setAutoDefault(False)
        for button in (self.cancel_button, self.save_button, self.apply_button):
            button.setAutoDefault(False)
            row.addWidget(button)
        self.footer = footer
        outer.addWidget(footer)

    def _page(self, title, description):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(26, 22, 26, 20)
        layout.setSpacing(13)
        layout.addWidget(label(title, "title"))
        layout.addWidget(label(description, "muted"))
        scroll.setWidget(page)
        return scroll, layout

    def _combo(self, key, options):
        combo = QComboBox()
        for value, text in options.items():
            combo.addItem(text, value)
        combo.setCurrentIndex(max(0, combo.findData(self.cfg.get(key))))
        self.controls[key] = combo
        return combo

    def _money(self, key):
        box = money_input(float(self.cfg.get(key, 0)))
        self.controls[key] = box
        return box

    def _field(self, grid, row, col, title, widget, key):
        field = QWidget()
        layout = QVBoxLayout(field)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(label(title, "fieldLabel"))
        layout.addWidget(widget)
        grid.addWidget(field, row, col)
        self.fields[key] = field

    def _income_page(self):
        page, layout = self._page("收入与扣除", "设置计薪方式，预览每个工作日的收入。")
        frame, content = card("薪资计算")
        content.addWidget(self._combo("salary_mode", {"daily": "按天发工资", "monthly": "按月发工资"}))
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(14)
        for key, title, row, col in (
                ("daily_salary", "日薪（元）", 0, 0),
                ("monthly_salary", "月薪（元）", 0, 0),
                ("overtime_daily_salary", "加班日总收入（元）", 0, 1),
                ("monthly_overtime_extra", "加班额外收入 / 天（元）", 0, 1),
                ("monthly_subsidy", "每月固定补贴（元）", 1, 0)):
            self._field(grid, row, col, title, self._money(key), key)
        self._field(grid, 1, 1, "累计统计方式", self._combo("salary_stat_mode", {
            "actual": "按实际加班记录", "normal": "全按正常", "overtime": "全按加班"}), "salary_stat_mode")
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        content.addLayout(grid)
        layout.addWidget(frame)
        frame, content = card("社保与公积金")
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        self._field(grid, 0, 0, "扣除方式", self._combo("insurance_mode", self.core.INSURANCE_OPTIONS), "insurance_mode")
        self._field(grid, 0, 1, "每月个人缴纳总额（元）", self._money("monthly_insurance"), "monthly_insurance")
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        content.addLayout(grid)
        content.addWidget(label("按当月工作日分摊。工资已填实发金额时，选择“不扣除”。", "muted"))
        layout.addWidget(frame)
        frame, content = card("当前收入预览", preview=True)
        self.preview = label("", "previewValue")
        content.addWidget(self.preview)
        layout.addWidget(frame)
        layout.addStretch()
        return page

    def _time(self, key):
        widget = QTimeEdit(QTime.fromString(self.cfg[key], "HH:mm"))
        widget.setDisplayFormat("HH:mm")
        self.controls[key] = widget
        return widget

    def _schedule_page(self):
        page, layout = self._page("作息与日历", "安排上下班、休息时段和当月出勤。")
        frame, content = card("工作时间")
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(14)
        for i, (key, title) in enumerate((
                ("work_start", "上班时间"), ("work_end", "正常下班"),
                ("lunch_start", "午休开始"), ("lunch_end", "午休结束"),
                ("overtime_end", "加班下班"))):
            self._field(grid, i // 2, i % 2, title, self._time(key), key)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        content.addLayout(grid)
        dinner = QCheckBox("加班日启用晚休")
        dinner.setChecked(self.cfg["dinner_break_enabled"])
        self.controls["dinner_break_enabled"] = dinner
        content.addWidget(dinner)
        row = QGridLayout()
        row.setHorizontalSpacing(18)
        for col, (key, title) in enumerate((("dinner_start", "晚休开始"), ("dinner_end", "晚休结束"))):
            self._field(row, 0, col, title, self._time(key), key)
            row.setColumnStretch(col, 1)
        content.addLayout(row)
        layout.addWidget(frame)
        frame, content = card("工作日与调休")
        days = QHBoxLayout()
        days.setSpacing(7)
        self.day_buttons = []
        for index, text in enumerate(("周一", "周二", "周三", "周四", "周五", "周六", "周日")):
            button = QPushButton(text)
            button.setObjectName("day")
            button.setCheckable(True)
            button.setChecked(index in self.cfg["workdays"])
            days.addWidget(button, 1)
            self.day_buttons.append(button)
        content.addLayout(days)
        content.addWidget(label("%d 年%s；未发布的年份按星期估算。" % (
            dt.date.today().year, self.core.calendar_status(dt.date.today().year)), "muted"))
        button = QPushButton("编辑本月工作日历")
        button.clicked.connect(self.open_calendar)
        content.addWidget(button)
        layout.addWidget(frame)
        layout.addStretch()
        return page

    def _appearance_page(self):
        page, layout = self._page("小窗外观", "调整桌面倒计时的配色、大小和动效。")
        frame, content = card("颜色与动效")
        content.addWidget(self._combo("theme", {key: info["name"] for key, info in self.core.THEMES.items()}))
        self.accent_button = QPushButton("选择强调色")
        self.accent_button.clicked.connect(self.choose_accent)
        content.addWidget(self.accent_button)
        animations = QCheckBox("启用呼吸、流光与数字缓动")
        animations.setChecked(self.cfg["animations"])
        self.controls["animations"] = animations
        content.addWidget(animations)
        layout.addWidget(frame)
        frame, content = card("显示比例")
        for key, title, low, high in (("opacity", "窗口透明度", 65, 100),
                                     ("ui_scale", "面板大小", 25, 100),
                                     ("font_scale", "字体大小", 85, 120)):
            row = QHBoxLayout()
            row.addWidget(label(title))
            value = label("%d%%" % round(self.cfg[key] * 100), "muted")
            row.addStretch()
            row.addWidget(value)
            content.addLayout(row)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(min(low, round(self.cfg[key] * 100)), max(high, round(self.cfg[key] * 100)))
            slider.setValue(round(self.cfg[key] * 100))
            slider.valueChanged.connect(lambda amount, target=value: target.setText("%d%%" % amount))
            self.controls[key] = slider
            content.addWidget(slider)
        layout.addWidget(frame)
        layout.addStretch()
        return page

    def _connect_changes(self):
        for key, widget in self.controls.items():
            if isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self._update_fields)
            elif isinstance(widget, QLineEdit):
                widget.textChanged.connect(self._update_fields)
            elif isinstance(widget, QCheckBox):
                widget.toggled.connect(self._update_fields)
        for button in self.day_buttons:
            button.toggled.connect(self._update_fields)
        self.controls["theme"].currentIndexChanged.connect(self.choose_theme)

    def _collect(self):
        cfg = copy.deepcopy(self.cfg)
        for key, widget in self.controls.items():
            if isinstance(widget, QComboBox):
                cfg[key] = widget.currentData()
            elif isinstance(widget, QLineEdit):
                if not widget.hasAcceptableInput():
                    raise ValueError("收入金额请填写非负数，最多两位小数")
                cfg[key] = self.core.money_value(widget.text())
            elif isinstance(widget, QTimeEdit):
                if not widget.hasAcceptableInput():
                    raise ValueError("请填写完整的工作时间")
                cfg[key] = widget.time().toString("HH:mm")
            elif isinstance(widget, QCheckBox):
                cfg[key] = widget.isChecked()
            elif isinstance(widget, QSlider):
                cfg[key] = widget.value() / 100
        cfg["workdays"] = [i for i, button in enumerate(self.day_buttons) if button.isChecked()]
        cfg["colors"] = dict(self.pending_colors)
        return cfg

    def _update_fields(self, *_):
        daily = self.controls["salary_mode"].currentData() == "daily"
        for key in ("daily_salary", "overtime_daily_salary"):
            self.fields[key].setVisible(daily)
        for key in ("monthly_salary", "monthly_overtime_extra"):
            self.fields[key].setVisible(not daily)
        self.controls["monthly_insurance"].setEnabled(self.controls["insurance_mode"].currentData() != "none")
        for key in ("dinner_start", "dinner_end"):
            self.controls[key].setEnabled(self.controls["dinner_break_enabled"].isChecked())
        try:
            cfg = self._collect()
            self.core.validate_config(cfg)
            self.preview.setText(self.core.salary_preview_text(dt.date.today(), cfg))
        except (ValueError, TypeError, ZeroDivisionError):
            self.preview.setText("填好薪资与工作日后，这里会显示每日收入。")

    def choose_theme(self, *_):
        self.pending_colors.update(self.core.THEMES[self.controls["theme"].currentData()]["colors"])
        self.accent_button.setText("强调色  %s" % self.pending_colors["bar_fg"])

    def choose_accent(self):
        chosen = QColorDialog.getColor(QColor(self.pending_colors["bar_fg"]), self, "选择小窗强调色")
        if chosen.isValid():
            color = chosen.name().upper()
            for key in ("bar_fg", "bar_fg2", "countdown"):
                self.pending_colors[key] = color
            self.accent_button.setText("强调色  %s" % color)

    def save(self, close_after=False, message=""):
        try:
            candidate = self._collect()
            self.core.validate_config(candidate)
        except (ValueError, TypeError, KeyError) as exc:
            self.save_failed(str(exc))
            return False
        self.pending_close, self.pending_message = close_after, message
        self.save_button.setEnabled(False)
        self.apply_button.setEnabled(False)
        self.status.setText("正在保存…")
        self.status.setStyleSheet("")
        self.submit(candidate)
        return True

    def saved(self, cfg):
        self.cfg = copy.deepcopy(cfg)
        self.update_month_total(self.core.month_earned_now(dt.datetime.now(), cfg),
                                dt.date.today().strftime("%Y年%m月"))
        self.save_button.setEnabled(True)
        self.apply_button.setEnabled(True)
        self.status.setStyleSheet("color: #137c68;")
        self.status.setText("已保存并应用")
        if self.calendar_window is not None:
            self.calendar_window.status.setText(self.pending_message or "已保存")
            self.calendar_window.refresh()
        if self.pending_close:
            self.accept()

    def update_month_total(self, earned, month):
        self.month_amount.setText("¥ %s" % format(earned, ",.2f"))
        self.month_detail.setText("%s\n含今日实时收入" % month)

    def save_failed(self, text):
        self.save_button.setEnabled(True)
        self.apply_button.setEnabled(True)
        self.status.setStyleSheet("color: #b34b48;")
        self.status.setText(text)
        if self.calendar_window is not None:
            self.calendar_window.status.setText(text)

    def open_calendar(self):
        try:
            candidate = self._collect()
            self.core.validate_config(candidate)
        except (ValueError, TypeError) as exc:
            self.save_failed(str(exc))
            return None
        if self.calendar_window is None:
            self.core.roll_calendar(self.cfg, dt.date.today())
            self.calendar_window = CalendarDialog(self)
            self.calendar_window.finished.connect(lambda _: setattr(self, "calendar_window", None))
        self.calendar_window.show()
        self.calendar_window.raise_()
        self.calendar_window.activateWindow()
        return self.calendar_window


class CalendarDialog(QDialog):
    KIND_LABELS = {"workday": "工作日", "makeup": "调休补班", "rest": "普通休息日", "statutory": "法定节假日"}
    ATTENDANCE_LABELS = {"normal": "正常上班", "overtime": "加班", "rest": "休息"}

    def __init__(self, settings):
        super().__init__(settings)
        self.settings, self.core = settings, settings.core
        self.setWindowTitle("%s · 当月工作日历" % self.core.APP_NAME)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumSize(600, 590)
        today = dt.date.today()
        self.first = today.replace(day=1)
        self.last = today.replace(day=calendar.monthrange(today.year, today.month)[1])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(13)
        layout.addWidget(label("%d 年 %d 月 · 工作日历" % (today.year, today.month), "title"))
        layout.addWidget(label("选择日期，调整出勤和当天最终收入。", "muted"))
        self.calendar = QCalendarWidget()
        self.calendar.setLocale(QLocale("zh_CN"))
        self.calendar.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.calendar.setNavigationBarVisible(False)
        header_format = QTextCharFormat()
        header_format.setBackground(QColor("#f1f5f8"))
        header_format.setForeground(QColor("#728197"))
        self.calendar.setHeaderTextFormat(header_format)
        self.calendar.setMinimumDate(QDate(self.first.year, self.first.month, 1))
        self.calendar.setMaximumDate(QDate(self.last.year, self.last.month, self.last.day))
        self.calendar.setSelectedDate(QDate(today.year, today.month, today.day))
        self.calendar.setMinimumHeight(245)
        self.calendar.selectionChanged.connect(self.select_day)
        layout.addWidget(self.calendar, 1)
        layout.addWidget(label("绿色：补班   红色：法定节假日   灰色：休息   粗体：加班", "muted"))
        frame, content = card("当天出勤")
        self.date_label = label("", "muted")
        content.addWidget(self.date_label)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        self.kind_combo, self.attendance_combo = QComboBox(), QComboBox()
        for key, text in self.KIND_LABELS.items():
            self.kind_combo.addItem(text, key)
        for key, text in self.ATTENDANCE_LABELS.items():
            self.attendance_combo.addItem(text, key)
        self.amount = money_input(0)
        for col, (title, widget) in enumerate((("日期类型", self.kind_combo),
                                               ("出勤状态", self.attendance_combo),
                                               ("最终收入（元）", self.amount))):
            grid.addWidget(label(title, "fieldLabel"), 0, col)
            grid.addWidget(widget, 1, col)
            grid.setColumnStretch(col, 1)
        content.addLayout(grid)
        content.addWidget(label("休息日 / 法定节假日分别按 2 / 3 倍预估，可改为实际收入。\n手填金额不再扣社保公积金，“保存当天”立即生效。", "muted"))
        buttons = QHBoxLayout()
        self.status = label("", "muted")
        buttons.addWidget(self.status, 1)
        reset = QPushButton("恢复默认")
        reset.clicked.connect(self.reset_day)
        save = QPushButton("保存当天")
        save.setObjectName("primary")
        save.clicked.connect(self.save_day)
        buttons.addWidget(reset)
        buttons.addWidget(save)
        content.addLayout(buttons)
        layout.addWidget(frame)
        self.total = label("", "previewValue")
        layout.addWidget(self.total)
        self.kind_combo.currentIndexChanged.connect(self.suggest_amount)
        self.attendance_combo.currentIndexChanged.connect(self.suggest_amount)
        self.refresh()
        self.select_day()
        center_window(self, 740, 790)

    def selected(self):
        date = self.calendar.selectedDate()
        return dt.date(date.year(), date.month(), date.day())

    def refresh(self):
        cfg = self.settings._collect()
        for number in range(1, self.last.day + 1):
            day = self.first.replace(day=number)
            item = self.core.calendar_day(day, cfg)
            fmt = QTextCharFormat()
            fmt.setForeground(QColor({"workday": "#34445a", "makeup": "#14826d",
                                      "rest": "#a1acba", "statutory": "#c15a59"}[item["kind"]]))
            fmt.setFontWeight(QFont.Weight.Bold if item["attendance"] == "overtime" else QFont.Weight.Normal)
            self.calendar.setDateTextFormat(QDate(day.year, day.month, day.day), fmt)
        past = self.core.month_earned(dt.date.today(), cfg, False)
        full = self.core.month_earned(self.last, cfg, True)
        caption = "已过日期 ¥%.2f    全月预计 ¥%.2f" % (past, full)
        summary = cfg.get("last_month_summary")
        if summary:
            caption += "\n上月 %s 汇总 ¥%.2f" % (summary["month"], summary["earned"])
        self.total.setText(caption)

    def select_day(self):
        day, cfg = self.selected(), self.settings._collect()
        item = self.core.calendar_day(day, cfg)
        self.date_label.setText(day.strftime("%Y 年 %m 月 %d 日"))
        for widget, key in ((self.kind_combo, "kind"), (self.attendance_combo, "attendance")):
            widget.blockSignals(True)
            widget.setCurrentIndex(widget.findData(item[key]))
            widget.blockSignals(False)
        amount = item["amount"] if item["amount"] is not None else self.core.suggested_day_income(
            day, cfg, item["kind"], item["attendance"])
        self.amount.setText("%.2f" % amount)

    def suggest_amount(self, *_):
        self.amount.setText("%.2f" % self.core.suggested_day_income(
            self.selected(), self.settings._collect(), self.kind_combo.currentData(),
            self.attendance_combo.currentData()))

    def save_day(self):
        try:
            if not self.amount.hasAcceptableInput():
                raise ValueError("当天收入请填写非负数，最多两位小数")
            amount = self.core.money_value(self.amount.text())
            kind, attendance = self.kind_combo.currentData(), self.attendance_combo.currentData()
            day = self.selected()
            if kind in ("workday", "makeup") and amount == self.core.suggested_day_income(
                    day, self.settings._collect(), kind, attendance):
                amount = None
            candidate = self.settings._collect()
            candidate["calendar_overrides"][day.isoformat()] = {
                "kind": kind, "attendance": attendance, "amount": amount}
            self.core.validate_config(candidate)
        except (ValueError, TypeError) as exc:
            self.status.setText(str(exc))
            return
        self.settings.cfg["calendar_overrides"] = candidate["calendar_overrides"]
        self.settings.save(message="当天设置已保存")

    def reset_day(self):
        self.settings.cfg["calendar_overrides"].pop(self.selected().isoformat(), None)
        if self.settings.save(message="已恢复默认并保存"):
            self.select_day()
