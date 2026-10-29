"""Keep the Qt settings event loop separate from the Tk desktop widget."""

import copy
import ctypes
import datetime as dt
import importlib.machinery
import importlib.util
import multiprocessing
import sys
from tkinter import messagebox


EDITABLE_FIELDS = (
    "salary_mode", "salary_stat_mode", "daily_salary", "monthly_salary",
    "monthly_subsidy", "overtime_daily_salary", "monthly_overtime_extra",
    "insurance_mode", "monthly_insurance", "work_start", "work_end",
    "lunch_start", "lunch_end", "overtime_end", "dinner_break_enabled",
    "dinner_start", "dinner_end", "workdays", "theme", "colors", "opacity",
    "ui_scale", "font_scale", "animations")


def merge_settings(live, edited):
    candidate = copy.deepcopy(live)
    for key in EDITABLE_FIELDS:
        candidate[key] = copy.deepcopy(edited[key])
    month = dt.date.today().strftime("%Y-%m")
    if edited.get("calendar_month") == live.get("calendar_month") == month:
        candidate["calendar_overrides"] = copy.deepcopy(edited["calendar_overrides"])
    return candidate


def run_settings(connection, cfg, source_path):
    try:
        if getattr(sys, "frozen", False):
            core = sys.modules["__main__"]
        else:
            loader = importlib.machinery.SourceFileLoader("paydance_core", source_path)
            spec = importlib.util.spec_from_loader(loader.name, loader)
            core = importlib.util.module_from_spec(spec)
            loader.exec_module(core)
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from settings_ui import SettingsDialog, configure_application

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(core.APP_ID + ".Settings")
        qt = QApplication.instance() or QApplication([])
        qt.setApplicationName(core.APP_NAME)
        qt.setApplicationDisplayName(core.APP_NAME)
        configure_application(qt)
        window = SettingsDialog(core, cfg,
                                lambda candidate: connection.send(("save", candidate)))

        def poll():
            try:
                while connection.poll():
                    action, payload = connection.recv()
                    if action == "saved":
                        window.saved(payload)
                    elif action == "month_total":
                        window.update_month_total(payload["earned"], payload["month"])
                    elif action == "error":
                        window.save_failed(payload)
                    elif action == "focus":
                        window.showNormal()
                        window.raise_()
                        window.activateWindow()
                    elif action == "close":
                        qt.quit()
            except (EOFError, OSError):
                qt.quit()

        timer = QTimer()
        timer.timeout.connect(poll)
        timer.start(30)
        window.show()
        connection.send(("ready", None))
        qt.exec()
    except Exception as exc:
        try:
            connection.send(("error", "设置窗口无法启动：%s" % exc))
        except (EOFError, OSError):
            pass
    finally:
        connection.close()


class SettingsWindow:
    def __init__(self, app, core):
        self.app, self.core = app, core
        self.ready = False
        self.summary_at = None
        context = multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe()
        self.process = context.Process(
            target=run_settings,
            args=(child, copy.deepcopy(app.cfg), core.__file__), daemon=True)
        self.process.start()
        child.close()
        self.poll_id = app.root.after(50, self._poll)

    def _poll(self):
        try:
            while self.connection.poll():
                action, payload = self.connection.recv()
                if action == "save":
                    try:
                        cfg = merge_settings(self.app.cfg, payload)
                        self.core.validate_config(cfg)
                        self.app.apply_config(cfg)
                        self.connection.send(("saved", copy.deepcopy(cfg)))
                        self.summary_at = None
                    except (ValueError, TypeError, KeyError) as exc:
                        self.connection.send(("error", str(exc)))
                elif action == "error":
                    messagebox.showerror("设置窗口", payload, parent=self.app.root)
                elif action == "ready":
                    self.ready = True
            now = dt.datetime.now().replace(microsecond=0)
            if getattr(self, "ready", False) and self.summary_at != now:
                self.connection.send(("month_total", {
                    "month": now.strftime("%Y年%m月"),
                    "earned": self.core.month_earned_now(now, self.app.cfg)}))
                self.summary_at = now
        except (EOFError, OSError):
            self.close()
            return
        if not self.process.is_alive():
            self.close()
            return
        self.poll_id = self.app.root.after(50, self._poll)

    def focus(self):
        if not self.process.is_alive():
            self.close()
            return False
        self.connection.send(("focus", None))
        return True

    def close(self):
        self.app.root.after_cancel(self.poll_id)
        try:
            if self.process.is_alive():
                self.connection.send(("close", None))
        except (EOFError, OSError):
            pass
        self.connection.close()
        self.app.settings_window = None
