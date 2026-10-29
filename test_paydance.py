import ctypes
import ctypes.wintypes as wt
import datetime as dt
import importlib.machinery
import importlib.util
import io
import json
import os
import queue
import re
import tempfile
import tkinter as tk
import unittest
import uuid
from contextlib import contextmanager
from PIL import ImageChops
from unittest.mock import MagicMock, patch


loader = importlib.machinery.SourceFileLoader("paydance", "paydance.pyw")
spec = importlib.util.spec_from_loader(loader.name, loader)
paydance = importlib.util.module_from_spec(spec)
loader.exec_module(paydance)


@contextmanager
def isolated_autostart_registry():
    registry = paydance.winreg
    root = r"Software\PayDanceTest-" + uuid.uuid4().hex
    with (patch.object(paydance, "AUTOSTART_RUN_KEY", root + r"\Run"),
          patch.object(paydance, "AUTOSTART_APPROVED_KEY", root + r"\Approved")):
        try:
            yield
        finally:
            for suffix in (r"\Approved\StartupFolder", r"\Approved\Run",
                           r"\Approved", r"\Run", ""):
                try:
                    registry.DeleteKey(registry.HKEY_CURRENT_USER, root + suffix)
                except FileNotFoundError:
                    pass


class SalaryCalendarTests(unittest.TestCase):
    def setUp(self):
        self.cfg = dict(paydance.DEFAULT_CONFIG)
        self.cfg.update(salary_mode="monthly", monthly_salary=4400,
                        monthly_subsidy=440, monthly_overtime_extra=75,
                        daily_salary=320, overtime_daily_salary=340,
                        overtime_date="2026-09-20", overtime_today=False,
                        overtime_dates=[])

    def test_2026_holidays_and_makeup_days(self):
        for date, expected in (("2026-09-20", True), ("2026-09-25", False),
                               ("2026-10-01", False), ("2026-10-10", True),
                               ("2026-02-14", True), ("2026-02-16", False)):
            with self.subTest(date=date):
                self.assertEqual(paydance.is_workday(dt.date.fromisoformat(date),
                                                    self.cfg), expected)

    def test_monthly_salary_and_overtime(self):
        day = dt.date(2026, 9, 20)
        self.assertEqual(paydance.month_workdays(day, self.cfg), 22)
        self.assertEqual(paydance.base_daily_salary(day, self.cfg), 220)
        self.cfg["overtime_today"] = True
        at_noon = paydance.compute(dt.datetime(2026, 9, 20, 12, 15), self.cfg)
        self.assertEqual(at_noon["full_day"], 295)
        self.assertEqual(at_noon["phase"], "lunch")
        self.assertEqual(paydance.month_earned(day, self.cfg, True), 3300 + 75)

    def test_holiday_and_saved_overtime(self):
        self.cfg["overtime_dates"] = ["2026-09-20"]
        self.assertEqual(paydance.compute(dt.datetime(2026, 9, 25, 14),
                                          self.cfg)["phase"], "rest")
        self.assertEqual(paydance.month_earned(dt.date(2026, 9, 25),
                                               self.cfg, False), 4180 + 75)

    def test_salary_stat_modes(self):
        day = dt.date(2026, 9, 20)
        self.cfg["salary_stat_mode"] = "normal"
        self.assertEqual(paydance.month_earned(day, self.cfg, True), 15 * 220)
        self.cfg["salary_stat_mode"] = "overtime"
        self.assertEqual(paydance.month_earned(day, self.cfg, True), 15 * 295)
        self.cfg["salary_mode"] = "daily"
        self.assertEqual(paydance.month_earned(day, self.cfg, True), 15 * 360)

    def test_daily_salary_includes_monthly_subsidy(self):
        day = dt.date(2026, 9, 20)
        self.cfg["salary_mode"] = "daily"
        self.assertEqual(paydance.base_daily_salary(day, self.cfg), 340)
        self.assertEqual(paydance.overtime_target(day, self.cfg), 360)
        self.assertEqual(paydance.suggested_day_income(
            day, self.cfg, "makeup", "overtime"), 360)
        self.cfg["monthly_subsidy"] = 0
        self.assertEqual(paydance.base_daily_salary(day, self.cfg), 320)
        self.assertEqual(paydance.overtime_target(day, self.cfg), 340)

    def test_month_calendar_distinguishes_holidays_and_uses_manual_income(self):
        self.cfg["calendar_month"] = "2026-09"
        self.cfg["calendar_overrides"] = {
            "2026-09-25": {"kind": "statutory", "attendance": "overtime",
                           "amount": 900}}
        self.assertEqual(paydance.calendar_day(dt.date(2026, 9, 25),
                                                self.cfg)["kind"], "statutory")
        self.assertEqual(paydance.calendar_day(dt.date(2026, 9, 26),
                                                self.cfg)["kind"], "rest")
        self.assertEqual(paydance.calendar_day(dt.date(2026, 9, 20),
                                                self.cfg)["kind"], "makeup")
        self.assertEqual(paydance.month_earned(dt.date(2026, 9, 26),
                                               self.cfg, False), 19 * 220 + 900)
        holiday_state = paydance.compute(dt.datetime(2026, 9, 25, 12, 0),
                                         self.cfg)
        self.assertEqual(holiday_state["full_day"], 900)
        self.assertNotEqual(holiday_state["phase"], "rest")

    def test_month_calendar_preset_allows_per_day_override(self):
        self.cfg.update(calendar_month="2026-09", salary_stat_mode="overtime",
                        calendar_overrides={"2026-09-20": {
                            "kind": "makeup", "attendance": "normal", "amount": None}})
        self.assertEqual(paydance.month_earned(dt.date(2026, 9, 20),
                                               self.cfg, True), 14 * 295 + 220)

    def test_month_rollover_keeps_only_summary(self):
        self.cfg.update(calendar_month="2026-09", overtime_dates=["2026-09-20"],
                        calendar_overrides={"2026-09-25": {
                            "kind": "statutory", "attendance": "overtime",
                            "amount": 900}})
        self.assertTrue(paydance.roll_calendar(self.cfg, dt.date(2026, 10, 1)))
        self.assertEqual(self.cfg["last_month_summary"],
                         {"month": "2026-09", "earned": 4840 + 75 + 900})
        self.assertEqual(self.cfg["calendar_overrides"], {})
        self.assertEqual(self.cfg["overtime_dates"], [])
        self.assertFalse(paydance.roll_calendar(self.cfg, dt.date(2026, 10, 1)))

    def test_legacy_overtime_dates_migrate_into_current_calendar(self):
        self.cfg.update(overtime_dates=["2026-09-20", "2026-08-31"])
        self.assertTrue(paydance.roll_calendar(self.cfg, dt.date(2026, 9, 21)))
        self.assertEqual(list(self.cfg["calendar_overrides"]), ["2026-09-20"])
        self.assertEqual(self.cfg["calendar_overrides"]["2026-09-20"][
                         "attendance"], "overtime")
        self.assertEqual(self.cfg["overtime_dates"], [])

    def test_invalid_salary_stat_mode_rejected(self):
        with self.assertRaises(ValueError):
            paydance.validate_config(dict(self.cfg, salary_stat_mode="half"))
        self.cfg.update(calendar_month="2026-09", calendar_overrides={
            "2026-09-25": {"kind": "statutory", "attendance": "overtime",
                           "amount": None}})
        with self.assertRaises(ValueError):
            paydance.validate_config(self.cfg)

    def test_daily_mode_backward_compatibility(self):
        self.cfg.pop("salary_mode")
        self.cfg["monthly_subsidy"] = 0
        self.assertEqual(paydance.base_daily_salary(dt.date(2026, 9, 20),
                                                    self.cfg), 320)
        self.assertEqual(paydance.overtime_target(dt.date(2026, 9, 20),
                                                  self.cfg), 340)

    def test_salary_preview_uses_same_calculation(self):
        day = dt.date(2026, 9, 20)
        self.assertEqual(paydance.salary_preview_text(day, self.cfg),
                         "2026年9月 · 22个工作日\n"
                         "正常 ¥220.00/天 · 加班当天 ¥295.00")
        self.cfg["salary_mode"] = "daily"
        self.assertEqual(paydance.salary_preview_text(day, self.cfg),
                         "2026年9月 · 22个工作日\n"
                         "正常 ¥340.00/天 · 加班当天 ¥360.00")

    def test_insurance_deduction_in_monthly_and_daily_modes(self):
        day = dt.date(2026, 9, 20)
        for insurance_mode in ("five_one", "six_two"):
            for salary_mode, normal, overtime in (
                    ("monthly", 170, 245), ("daily", 290, 310)):
                with self.subTest(insurance=insurance_mode, salary=salary_mode):
                    cfg = dict(self.cfg, insurance_mode=insurance_mode,
                               monthly_insurance=1100, salary_mode=salary_mode,
                               overtime_today=True)
                    self.assertEqual(paydance.base_daily_salary(day, cfg), normal)
                    self.assertEqual(paydance.overtime_target(day, cfg), overtime)
                    state = paydance.compute(dt.datetime(2026, 9, 20, 12, 15), cfg)
                    self.assertEqual(state["full_day"], overtime)
                    self.assertAlmostEqual(state["earned"], overtime * 195 / 600)
                    self.assertEqual(paydance.month_earned(day, cfg, True),
                                     14 * normal + overtime)
                    last = dt.date(2026, 9, 30)
                    gross = (4840 if salary_mode == "monthly" else 320 * 22 + 440)
                    extra = 75 if salary_mode == "monthly" else 20
                    self.assertEqual(paydance.month_earned(last, cfg, True),
                                     gross - 1100 + extra)
                    preview = paydance.salary_preview_text(day, cfg)
                    self.assertIn("正常 ¥%.2f/天" % normal, preview)
                    self.assertIn(paydance.INSURANCE_OPTIONS[insurance_mode], preview)

    def test_disabling_insurance_preserves_salary_and_legacy_config(self):
        day = dt.date(2026, 9, 20)
        self.cfg.update(insurance_mode="none", monthly_insurance=1100)
        self.assertEqual(paydance.base_daily_salary(day, self.cfg), 220)
        self.assertNotIn("扣除", paydance.salary_preview_text(day, self.cfg))
        self.cfg.pop("insurance_mode")
        self.cfg.pop("monthly_insurance")
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(self.cfg, handle)
            cfg = paydance._read_config(path)
        self.assertEqual(cfg["insurance_mode"], "none")
        self.assertEqual(cfg["monthly_insurance"], 0)
        self.assertEqual(paydance.base_daily_salary(day, cfg), 220)

    def test_insurance_cannot_make_income_negative(self):
        day = dt.date(2026, 9, 20)
        self.cfg.update(insurance_mode="six_two", monthly_insurance=5500)
        self.assertEqual(paydance.base_daily_salary(day, self.cfg), 0)
        self.assertEqual(paydance.overtime_target(day, self.cfg), 45)
        self.cfg["monthly_insurance"] = 10000
        self.assertEqual(paydance.overtime_target(day, self.cfg), 0)

    def test_insurance_keeps_manual_calendar_income_as_final_amount(self):
        self.cfg.update(insurance_mode="five_one", monthly_insurance=1100,
                        calendar_month="2026-09", calendar_overrides={
                            "2026-09-20": {"kind": "makeup", "attendance": "overtime",
                                           "amount": 500},
                            "2026-09-25": {"kind": "statutory", "attendance": "overtime",
                                           "amount": 900}})
        self.assertEqual(paydance.month_earned(dt.date(2026, 9, 30), self.cfg, True),
                         21 * 170 + 500 + 900)
        self.assertEqual(paydance.compute(dt.datetime(2026, 9, 20, 12),
                                         self.cfg)["full_day"], 500)

    def test_invalid_insurance_settings_are_rejected(self):
        with self.assertRaises(ValueError):
            paydance.validate_config(dict(self.cfg, insurance_mode="other"))
        for amount in (-1, "NaN", "Infinity", "1.234", "abc"):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                paydance.validate_config(dict(self.cfg, insurance_mode="five_one",
                                               monthly_insurance=amount))
        with patch.object(paydance, "month_workdays", return_value=0):
            with self.assertRaises(ValueError):
                paydance.validate_config(dict(self.cfg, salary_mode="daily",
                                               monthly_subsidy=0,
                                               insurance_mode="five_one",
                                               monthly_insurance=100))

    def test_month_earned_now_handles_partial_days_done_rest_and_new_month(self):
        self.cfg.update(insurance_mode="five_one", monthly_insurance=1100)
        for stamp, expected in (
                ("2026-09-01T08:00:00", 0),
                ("2026-09-20T08:00:00", 14 * 170),
                ("2026-09-20T12:15:00", 14 * 170 + 170 * 195 / 480),
                ("2026-09-20T13:00:00", 14 * 170 + 170 * 195 / 480),
                ("2026-09-20T18:30:00", 15 * 170),
                ("2026-09-26T12:00:00", 19 * 170),
                ("2026-10-01T12:00:00", 0)):
            with self.subTest(time=stamp):
                self.assertAlmostEqual(paydance.month_earned_now(
                    dt.datetime.fromisoformat(stamp), self.cfg), expected)

    def test_legacy_click_through_setting_is_ignored(self):
        with patch("builtins.open", return_value=io.StringIO(
                '{"click_through": true}')):
            config = paydance.load_config()
        self.assertNotIn("click_through", config)

    def test_tray_right_click_and_single_value_exit(self):
        user32, shell32, kernel32 = MagicMock(), MagicMock(), MagicMock()
        user32.DefWindowProcW = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)(
                lambda *_: 0)
        wndproc = None

        def register_class(pointer):
            nonlocal wndproc
            callback = ctypes.WINFUNCTYPE(wt.LPARAM, wt.HWND, wt.UINT,
                                          wt.WPARAM, wt.LPARAM)
            wndproc = ctypes.cast(pointer._obj.lpfnWndProc, callback)
            return 1

        user32.RegisterClassW.side_effect = register_class
        user32.CreateWindowExW.return_value = 1234
        user32.CreatePopupMenu.return_value = 5678
        kernel32.GetModuleHandleW.return_value = 1

        messages = iter(((paydance.CB_MSG, paydance.WM_RBUTTONUP),
                         (paydance.WM_TIMER, 0)))

        first_message = True

        def next_message(pointer, *_):
            nonlocal first_message
            if first_message:
                first_message = False
                wndproc(1234, paydance.CB_MSG, 1, paydance.WM_RBUTTONUP)
            pointer._obj.message, pointer._obj.lParam = next(messages)
            return 1

        user32.GetMessageW.side_effect = next_message
        def cursor_position(pointer):
            pointer._obj.x, pointer._obj.y = 123, 456
            return 1

        user32.GetCursorPos.side_effect = cursor_position
        to_tk = queue.Queue()
        to_tray = queue.Queue()
        to_tray.put(("exit",))
        with (patch.object(paydance.ctypes.windll, "user32", user32),
              patch.object(paydance.ctypes.windll, "shell32", shell32),
              patch.object(paydance.ctypes.windll, "kernel32", kernel32),
              patch.object(paydance, "make_gdi_icon", return_value=(1, None))):
            paydance.tray_thread(to_tk, to_tray)

        self.assertEqual(to_tk.get_nowait(), ("popup_menu", (123, 456)))
        self.assertEqual(to_tk.get_nowait(), ("popup_menu", (123, 456)))
        self.assertEqual(user32.CreateWindowExW.call_args.args[8], 0)
        self.assertIn(paydance.NIM_DELETE, [call.args[0] for call in
                       shell32.Shell_NotifyIconW.call_args_list])
        user32.DestroyWindow.assert_called_once_with(1234)

    def test_tray_messages_use_widget_menu(self):
        app = paydance.PayDanceApp.__new__(paydance.PayDanceApp)
        app.to_tk = queue.Queue()
        app.root = MagicMock()
        app.toggle_visible = MagicMock()
        app.popup_menu = MagicMock()
        app.to_tk.put(("toggle_visible",))
        app.to_tk.put(("popup_menu", (123, 456)))
        app.poll_tray()
        app.toggle_visible.assert_called_once_with()
        app.popup_menu.assert_called_once_with(123, 456, from_tray=True)

    def test_tray_popup_uses_shared_widget_menu(self):
        app = paydance.PayDanceApp.__new__(paydance.PayDanceApp)
        app.root = MagicMock()
        app._fill_menu = MagicMock()
        with (patch.object(paydance.tk, "Toplevel") as host_class,
              patch.object(paydance.tk, "Menu") as menu_class,
              patch.object(paydance, "get_hwnd", return_value=1234)):
            app.popup_menu(123, 456, from_tray=True)
        host_class.assert_called_once_with(app.root)
        host_class.return_value.focus_force.assert_called_once_with()
        menu_class.assert_called_once_with(app.root, tearoff=0)
        app._fill_menu.assert_called_once_with(menu_class.return_value)
        menu_class.return_value.tk_popup.assert_called_once_with(123, 456)
        host_class.return_value.destroy.assert_called_once_with()

    def test_tray_menu_command_survives_temporary_host(self):
        root = tk.Tk()
        root.withdraw()
        try:
            app = paydance.PayDanceApp.__new__(paydance.PayDanceApp)
            app.root = root
            action = MagicMock()
            app._fill_menu = lambda menu: menu.add_command(
                label="取消自启", command=action)
            with (patch.object(paydance, "get_hwnd", return_value=0),
                  patch.object(tk.Menu, "tk_popup")):
                app.popup_menu(123, 456, from_tray=True)
            self.assertTrue(app._context_menu.winfo_exists())
            app._context_menu.invoke(0)
            action.assert_called_once_with()
        finally:
            root.destroy()

    def test_shared_menu_contains_widget_actions(self):
        root = tk.Tk()
        root.withdraw()
        try:
            app = paydance.PayDanceApp.__new__(paydance.PayDanceApp)
            app.root = root
            app.cfg = dict(paydance.DEFAULT_CONFIG)
            menu = tk.Menu(root, tearoff=0)
            with patch.object(paydance, "autostart_enabled", return_value=False):
                app._fill_menu(menu)
            labels = [menu.entrycget(i, "label") for i in
                      range(menu.index("end") + 1)
                      if menu.type(i) != "separator"]
            self.assertEqual(labels, [
                "置顶显示", "贴在桌面底层", "今天加班 (至20:30)",
                "开机自启", "个性化与工作设置...", "重新加载配置",
                "重置位置", "退出"])
            enabled_menu = tk.Menu(root, tearoff=0)
            with patch.object(paydance, "autostart_enabled", return_value=True):
                app._fill_menu(enabled_menu)
            self.assertEqual(enabled_menu.entrycget(5, "label"), "取消自启")
            self.assertEqual(enabled_menu.type(5), "command")
        finally:
            root.destroy()

    def test_autostart_toggle_shows_result(self):
        app = paydance.PayDanceApp.__new__(paydance.PayDanceApp)
        app.root = MagicMock()
        app.root.after.side_effect = lambda _delay, callback: callback()
        with (patch.object(paydance, "autostart_enabled", return_value=False),
              patch.object(paydance, "install_autostart") as install,
              patch.object(paydance.messagebox, "showinfo") as info):
            app.toggle_autostart()
            install.assert_called_once_with()
            self.assertIn("已开启开机自启", info.call_args.args[1])
        with (patch.object(paydance, "autostart_enabled", return_value=True),
              patch.object(paydance, "remove_autostart") as remove,
              patch.object(paydance.messagebox, "showinfo") as info):
            app.toggle_autostart()
            remove.assert_called_once_with()
            self.assertEqual(info.call_args.args[1], "已取消开机自启")


class StabilityTests(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads(json.dumps(paydance.DEFAULT_CONFIG))

    def test_icon_mask_matches_visible_coin_pixels(self):
        for size in (16, 32):
            pixels, mask = paydance.coin_bitmap(size)
            stride = ((size + 31) // 32) * 4
            self.assertEqual(len(mask), stride * size)
            for y in range(size):
                for x in range(size):
                    transparent = bool(mask[y * stride + x // 8] & (0x80 >> (x % 8)))
                    self.assertEqual(transparent, pixels[(y * size + x) * 4 + 3] == 0)

    def test_money_inputs_require_nonnegative_two_decimal_values(self):
        for raw in ("", "-1", "1.234", "NaN", "Infinity", "1e3", "  "):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                paydance.money_value(raw)
        for raw, expected in (("0", 0.0), ("0.00", 0.0),
                              ("12.34", 12.34)):
            self.assertEqual(paydance.money_value(raw), expected)
        self.cfg.update(daily_salary="0", monthly_salary="0.00",
                        overtime_daily_salary="0")
        paydance.validate_config(self.cfg)
        with self.assertRaises(ValueError):
            paydance.validate_config(dict(self.cfg, monthly_subsidy="0.001"))

    def test_non_finite_and_empty_days_rejected(self):
        for key in ("daily_salary", "monthly_salary", "monthly_subsidy",
                    "monthly_overtime_extra", "overtime_daily_salary"):
            for value in ("NaN", "Infinity", "-Infinity"):
                cfg = dict(self.cfg, **{key: value})
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    paydance.validate_config(cfg)
        with self.assertRaises(ValueError):
            paydance.validate_config(dict(self.cfg, workdays=[]))
        for key, value in (("opacity", float("nan")),
                           ("opacity", 1.5),
                           ("font_scale", float("inf")),
                           ("position", ["x", 2]),
                           ("overtime_date", "not-a-date"),
                           ("colors", {"bg": "bad"})):
            cfg = dict(self.cfg, **{key: value})
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                paydance.validate_config(cfg)

    def test_exception_log_only_records_active_errors(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(paydance, "LOG_PATH", os.path.join(folder, "paydance.log")), \
                patch.object(paydance, "LOG_MAX_BYTES", 64):
            paydance.log_exc()
            self.assertFalse(os.path.exists(paydance.LOG_PATH))
            try:
                raise RuntimeError("expected test error")
            except RuntimeError:
                paydance.log_exc()
            self.assertTrue(os.path.exists(paydance.LOG_PATH + ".1"))
            self.assertLessEqual(os.path.getsize(paydance.LOG_PATH + ".1"), 64)
            paydance.cleanup_exception_logs()

    def test_config_migration_backup_and_corruption(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "app", "config.json")
            old = os.path.join(folder, "config.json")
            with open(old, "w", encoding="utf-8") as f:
                json.dump(dict(self.cfg, daily_salary=123), f)
            with (patch.object(paydance, "CFG_PATH", path),
                  patch.object(paydance, "here", return_value=old),
                  patch.object(paydance.sys, "frozen", True, create=True)):
                self.assertEqual(paydance.load_config()["daily_salary"], 123)
                self.assertTrue(os.path.exists(path))
                new = dict(self.cfg, daily_salary=456)
                paydance.save_config(new)
                with open(path, "w", encoding="utf-8") as f:
                    f.write("broken")
                self.assertEqual(paydance.load_config()["daily_salary"], 123)
                self.assertIn("恢复", paydance.CONFIG_WARNING)

    def test_corrupt_config_is_preserved_and_saving_still_works(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "config.json")
            for target in (path, path + ".bak"):
                with open(target, "w", encoding="utf-8") as f:
                    f.write("broken")
            with patch.object(paydance, "CFG_PATH", path):
                paydance.load_config()
                self.assertIn("损坏", paydance.CONFIG_WARNING)
                # 坏文件留一份现场副本，方便事后排查
                with open(path + ".corrupt", encoding="utf-8") as f:
                    self.assertEqual(f.read(), "broken")
                # 关键：保存必须照常生效，否则用户改什么都存不下来
                paydance.save_config(dict(self.cfg, daily_salary=456))
                with open(path, encoding="utf-8") as f:
                    self.assertEqual(json.load(f)["daily_salary"], 456)

    def test_autostart_repairs_only_existing_packaged_entry(self):
        with tempfile.TemporaryDirectory() as folder, isolated_autostart_registry():
            path = os.path.join(folder, "PayDance.vbs")
            exe = os.path.join(folder, "中文 路径", "PayDance.exe")
            with (patch.object(paydance, "startup_vbs_path", return_value=path),
                  patch.object(paydance.sys, "frozen", True, create=True),
                  patch.object(paydance.sys, "executable", exe)):
                self.assertFalse(paydance.repair_autostart())
                self.assertFalse(os.path.exists(path))
                paydance.install_autostart()
                self.assertTrue(paydance.autostart_enabled())
                self.assertFalse(os.path.exists(path))
                self.assertEqual(paydance._registered_autostart(), '"%s"' % exe)
                with patch.object(paydance.sys, "executable", exe + ".moved"):
                    self.assertTrue(paydance.repair_autostart())
                self.assertFalse(paydance.autostart_enabled())
                self.assertTrue(paydance.repair_autostart())
                self.assertTrue(paydance.autostart_enabled())
                paydance.remove_autostart()
                self.assertFalse(os.path.exists(path))
                self.assertIsNone(paydance._registered_autostart())
                self.assertFalse(paydance.repair_autostart())

    def test_autostart_migrates_legacy_name_after_rename(self):
        with tempfile.TemporaryDirectory() as folder, isolated_autostart_registry():
            exe = os.path.join(folder, "下班倒计时", "下班倒计时.exe")
            with (patch.dict(os.environ, {"APPDATA": folder}),
                  patch.object(paydance.sys, "frozen", True, create=True),
                  patch.object(paydance.sys, "executable", exe)):
                self.assertFalse(paydance.repair_autostart())
                path = paydance.startup_vbs_path()
                legacy = paydance.legacy_startup_vbs_path()
                os.makedirs(os.path.dirname(legacy), exist_ok=True)
                with open(legacy, "w", encoding=paydance.VBS_ENCODING) as f:
                    f.write('CreateObject("WScript.Shell").Run '
                            '"""C:\\old\\PayDance.exe""", 0, False\n')
                self.assertTrue(paydance.repair_autostart())
                self.assertFalse(os.path.exists(path))
                self.assertFalse(os.path.exists(legacy))
                self.assertTrue(paydance.autostart_enabled())
                self.assertFalse(paydance.repair_autostart())
                paydance.remove_autostart()
                self.assertFalse(paydance.repair_autostart())
                self.assertFalse(os.path.exists(path))

    def test_autostart_vbs_uses_an_encoding_wsh_can_read(self):
        # WSH 按系统 ANSI 代码页解析 .vbs，UTF-8 写盘会让非 ASCII 路径静默失效
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "PayDance.vbs")
            exe = os.path.join(folder, "中文 路径", "PayDance.exe")
            with (patch.object(paydance, "startup_vbs_path", return_value=path),
                  patch.object(paydance.sys, "frozen", False, create=True),
                  patch.object(paydance, "_pythonw_path", return_value=exe)):
                paydance.install_autostart()
                with open(path, "rb") as f:
                    self.assertTrue(f.read().startswith(paydance.VBS_BOM))
                contents, current = paydance._read_startup_vbs()
                self.assertTrue(current)
                self.assertIn(exe, contents)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(contents)
                self.assertFalse(paydance._read_startup_vbs()[1])

    def test_autostart_cli_works_without_a_console(self):
        with tempfile.TemporaryDirectory() as folder, isolated_autostart_registry():
            path = os.path.join(folder, "PayDance.vbs")
            exe = os.path.join(folder, "中文 路径", "PayDance.exe")
            with (patch.object(paydance, "startup_vbs_path", return_value=path),
                  patch.object(paydance.sys, "frozen", True, create=True),
                  patch.object(paydance.sys, "executable", exe),
                  patch.object(paydance.sys, "stdout", None),
                  patch.object(paydance, "setup_win32"),
                  patch.object(paydance, "cleanup_exception_logs"),
                  patch.object(paydance.ctypes, "windll", MagicMock()),
                  patch.object(paydance, "PayDanceApp") as app):
                with patch.object(paydance.sys, "argv",
                                  [exe, "--install-autostart"]):
                    paydance.main()
                self.assertTrue(paydance.autostart_enabled())
                with patch.object(paydance.sys, "argv",
                                  [exe, "--remove-autostart"]):
                    paydance.main()
                self.assertFalse(os.path.exists(path))
                self.assertIsNone(paydance._registered_autostart())
                app.assert_not_called()

    def test_autostart_migration_preserves_windows_disabled_status(self):
        with tempfile.TemporaryDirectory() as folder, isolated_autostart_registry():
            with (patch.dict(os.environ, {"APPDATA": folder}),
                  patch.object(paydance.sys, "frozen", True, create=True)):
                path = paydance.startup_vbs_path()
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding=paydance.VBS_ENCODING) as f:
                    f.write('CreateObject("WScript.Shell").Run """C:\\old\\PayDance.exe""", 0, False\n')
                registry = paydance.winreg
                status = b"\x03" + b"\0" * 11
                with registry.CreateKey(registry.HKEY_CURRENT_USER,
                                        paydance.AUTOSTART_APPROVED_KEY + r"\StartupFolder") as key:
                    registry.SetValueEx(key, os.path.basename(path), 0, registry.REG_BINARY, status)
                self.assertTrue(paydance.repair_autostart())
                with registry.OpenKey(registry.HKEY_CURRENT_USER,
                                      paydance.AUTOSTART_APPROVED_KEY + r"\Run") as key:
                    self.assertEqual(registry.QueryValueEx(key, paydance.APP_NAME)[0], status)
                self.assertFalse(os.path.exists(path))

    def test_ui_check_does_not_repair_autostart(self):
        with (patch.object(paydance.sys, "argv", ["PayDance.exe", "--uicheck"]),
              patch.object(paydance, "setup_win32"),
              patch.object(paydance, "cleanup_exception_logs"),
              patch.object(paydance.ctypes, "windll", MagicMock()),
              patch.object(paydance, "repair_autostart") as repair,
              patch.object(paydance, "PayDanceApp", side_effect=RuntimeError),
              patch.object(paydance, "log_exc")):
            paydance.main()
            repair.assert_not_called()

    def test_calendar_validation_offline_and_makeup(self):
        data = {"year": 2027, "papers": ["https://www.gov.cn/test"],
                "days": [{"date": "2027-10-09", "isOffDay": False}]}
        with tempfile.TemporaryDirectory() as folder, patch.object(
                paydance, "CALENDAR_DIR", folder):
            paydance._calendar_days.clear()
            paydance._calendar_loaded.clear()
            paydance._calendar_years.clear()
            paydance._calendar_years.add(2026)
            with patch.object(paydance.urllib.request, "urlopen",
                              return_value=io.BytesIO(json.dumps(data).encode())):
                self.assertTrue(paydance.refresh_calendar(2027))
            self.assertTrue(paydance.is_workday(dt.date(2027, 10, 9), self.cfg))
            paydance._calendar_days.clear()
            paydance._calendar_loaded.clear()
            paydance._calendar_tried_at.clear()      # 模拟进程重启后的空缓存
            self.assertTrue(paydance.is_workday(dt.date(2027, 10, 9), self.cfg))
            with patch.object(paydance.urllib.request, "urlopen",
                              side_effect=OSError("offline")):
                self.assertFalse(paydance.refresh_calendar(2027))
            self.assertRaises(ValueError, paydance._valid_calendar,
                              {"year": 2028, "papers": [], "days": []}, 2028)
            self.assertIn("按星期估算", paydance.calendar_status(2028))

    def test_calendar_cache_retry_is_throttled_not_permanent(self):
        # 读缓存失败不能永久放弃，但也不能每帧都去碰磁盘
        with tempfile.TemporaryDirectory() as folder, patch.object(
                paydance, "CALENDAR_DIR", folder):
            paydance._calendar_days.clear()
            paydance._calendar_loaded.clear()
            paydance._calendar_tried_at.clear()
            paydance._load_calendar_cache(2029)          # 文件不存在
            self.assertNotIn(2029, paydance._calendar_loaded)
            paydance._load_calendar_cache(2029)          # 限流窗口内不再重试
            self.assertNotIn(2029, paydance._calendar_loaded)
            paydance._calendar_tried_at[2029] = 0.0      # 假装已过限流窗口
            with open(os.path.join(folder, "2029.json"), "w",
                      encoding="utf-8") as f:
                json.dump({"year": 2029, "papers": ["https://www.gov.cn/t"],
                           "days": [{"date": "2029-01-01",
                                     "isOffDay": False}]}, f)
            paydance._load_calendar_cache(2029)
            self.assertIn(2029, paydance._calendar_loaded)

    def test_calendar_refresh_is_throttled_by_stamp(self):
        # 节假日一年才公布一次：缓存没过期就不该联网
        year = dt.date.today().year
        with tempfile.TemporaryDirectory() as folder, patch.object(
                paydance, "CALENDAR_DIR", folder):
            for candidate in (year - 1, year, year + 1):
                self.assertTrue(paydance._calendar_refresh_due(candidate))
                paydance._touch_calendar_stamp(candidate)
                self.assertFalse(paydance._calendar_refresh_due(candidate))
            with patch.object(paydance, "refresh_calendar") as refresh:
                paydance.refresh_nearby_calendars()
                refresh.assert_not_called()

    def test_tray_queue_keeps_exit(self):
        messages = queue.Queue(maxsize=1)
        paydance.send_tray_message(messages, ("tooltip", "old"))
        paydance.send_tray_message(messages, ("tooltip", "new"))
        self.assertEqual(messages.get_nowait(), ("tooltip", "new"))
        paydance.send_tray_message(messages, ("exit",))
        paydance.send_tray_message(messages, ("tooltip", "later"))
        self.assertEqual(messages.get_nowait(), ("exit",))

    def test_ui_queue_is_bounded_and_keeps_latest(self):
        messages = queue.Queue(maxsize=2)
        paydance.send_ui_message(messages, ("toggle_visible",))
        paydance.send_ui_message(messages, ("popup_menu", (1, 2)))
        paydance.send_ui_message(messages, ("popup_menu", (3, 4)))
        self.assertEqual(messages.qsize(), 2)
        self.assertEqual(messages.get_nowait(), ("popup_menu", (1, 2)))
        self.assertEqual(messages.get_nowait(), ("popup_menu", (3, 4)))

    def test_instance_lock_is_exclusive_and_released(self):
        # 命名互斥体：同名第二次创建会拿到 ERROR_ALREADY_EXISTS
        name = "PayDanceTest_%d" % os.getpid()
        first = paydance.acquire_instance_lock(name)
        self.assertTrue(first)
        self.assertIsNone(paydance.acquire_instance_lock(name))
        paydance.release_instance_lock(first)
        second = paydance.acquire_instance_lock(name)
        self.assertTrue(second)
        paydance.release_instance_lock(second)


    def test_settings_keep_background_progress_with_slower_frame_rendering(self):
        with patch.object(paydance, "load_config", return_value=dict(self.cfg)), \
                patch.object(paydance, "save_config"), \
                patch.object(paydance, "log_exc", side_effect=AssertionError):
            app = paydance.PayDanceApp()
            try:
                app.render_frame = MagicMock()
                app.settings_window = object()
                with patch.object(paydance.time, "monotonic",
                                  side_effect=(10.0, 10.1, 10.21, 10.25)):
                    app.tick()
                    app.render_frame.assert_called_once()
                    app.tick()
                    app.render_frame.assert_called_once()
                    app.tick()
                    self.assertEqual(app.render_frame.call_count, 2)
                    app.settings_window = None
                    app.tick()
                    self.assertEqual(app.render_frame.call_count, 3)
                app.settings_window = object()
                app.disp_money = 42.0
                app.disp_bar = 0.5
                app.apply_config(dict(self.cfg))
                self.assertEqual(app.disp_money, 42.0)
                self.assertEqual(app.disp_bar, 0.5)
                self.assertEqual(app.fade, 1.0)
            finally:
                app.root.destroy()

    def test_glow_layers_are_cached_at_display_scale(self):
        # 柔光必须在 1x 域合成：按 3x 缓存会让每帧合成面积变成 9 倍
        app = paydance.PayDanceApp.__new__(paydance.PayDanceApp)
        app.cfg = json.loads(json.dumps(paydance.DEFAULT_CONFIG))
        app.ui = 1.0
        app.SS = 3
        app._make_glow_layers()
        self.assertEqual(app._ambient_layer.size, (276, 276))
        self.assertLess(app._ambient_layer.size[0], app._S(276))

    def test_panel_scale_follows_dpi_and_user_setting(self):
        app = paydance.PayDanceApp.__new__(paydance.PayDanceApp)
        app.cfg = json.loads(json.dumps(paydance.DEFAULT_CONFIG))
        self.assertEqual(app.cfg["ui_scale"], 0.75)      # 默认 75%
        with patch.object(paydance, "system_ui_scale", return_value=1.5):
            app.build_base()
            self.assertEqual((app.px_w, app.px_h), (304, 358))
            # 超采样倍率反向调整，画布总量保持恒定，否则高分屏会拖慢渲染
            self.assertEqual(app.px_w * app.SS, 912)
            app.cfg["ui_scale"] = 1.0                    # 上限 100%
            app.build_base()
            self.assertEqual((app.px_w, app.px_h), (405, 477))
            self.assertEqual(app.px_w * app.SS, 810)
            app.cfg["ui_scale"] = 0.25                   # 下限 25%
            app.build_base()
            # 系统 DPI 1.5 × 0.25 = 0.375
            self.assertEqual((app.px_w, app.px_h), (101, 119))

    def test_panel_scale_bounds_are_validated(self):
        for value in (0.2, 1.1, "NaN", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                paydance.validate_config(dict(self.cfg, ui_scale=value))
        # 边界值本身要放行
        for value in (0.25, 1.0):
            with self.subTest(value=value):
                paydance.validate_config(dict(self.cfg, ui_scale=value))

    def test_defaults_are_opaque_and_seventy_five_percent(self):
        self.assertEqual(paydance.DEFAULT_CONFIG["opacity"], 1.0)
        self.assertEqual(paydance.DEFAULT_CONFIG["ui_scale"], 0.75)


    def test_emit_survives_missing_stdout(self):
        # windowed 打包后 sys.stdout 是 None，直接 print 会抛 AttributeError
        with patch.object(paydance.sys, "stdout", None):
            paydance.emit("这行不该让程序崩掉")

    def test_panel_scale_change_resizes_the_window(self):
        # PhotoImage 尺寸不可变，paste 不会让窗口变大；改面板大小必须重建它
        cfg = json.loads(json.dumps(self.cfg))
        with patch.object(paydance, "load_config", return_value=cfg), \
                patch.object(paydance, "save_config"), \
                patch.object(paydance, "system_ui_scale", return_value=1.0):
            app = paydance.PayDanceApp()
            try:
                app.root.update_idletasks()
                # 默认 75%
                self.assertEqual((app.px_w, app.px_h), (202, 238))
                self.assertEqual((app._photo.width(), app._photo.height()),
                                 (202, 238))
                # Windows 任务栏读取窗口的原生 HICON，不能只设置 EXE 文件资源。
                api = ctypes.WinDLL("user32")
                api.GetParent.argtypes = [wt.HWND]
                api.GetParent.restype = wt.HWND
                api.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
                api.SendMessageW.restype = ctypes.c_ssize_t
                api.GetClassLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
                api.GetClassLongPtrW.restype = ctypes.c_size_t
                wrapper = api.GetParent(app.root.winfo_id())
                self.assertTrue(api.SendMessageW(wrapper, 0x007F, 0, 0) or
                                api.GetClassLongPtrW(wrapper, -34))  # GCLP_HICONSM
                api.GetIconInfo.argtypes = [wt.HICON, ctypes.POINTER(paydance.ICONINFO)]
                api.GetIconInfo.restype = wt.BOOL
                gdi = ctypes.WinDLL("gdi32")
                gdi.GetBitmapBits.argtypes = [wt.HBITMAP, wt.LONG, wt.LPVOID]
                gdi.GetBitmapBits.restype = wt.LONG
                gdi.DeleteObject.argtypes = [wt.HANDLE]
                for offset, size in ((-34, 16), (-14, 32)):
                    info = paydance.ICONINFO()
                    hicon = (api.SendMessageW(wrapper, 0x007F, int(size == 32), 0) or
                             api.GetClassLongPtrW(wrapper, offset))
                    self.assertTrue(api.GetIconInfo(hicon, ctypes.byref(info)))
                    try:
                        pixels = ctypes.create_string_buffer(size * size * 4)
                        self.assertEqual(gdi.GetBitmapBits(info.hbmColor, len(pixels), pixels),
                                         len(pixels))
                        self.assertIn(bytes((61, 198, 255, 255)), pixels.raw)
                    finally:
                        gdi.DeleteObject(info.hbmColor)
                        gdi.DeleteObject(info.hbmMask)
                bigger = json.loads(json.dumps(app.cfg))
                bigger["ui_scale"] = 1.0
                app.apply_config(bigger, persist=False)
                app.root.update_idletasks()
                self.assertEqual((app.px_w, app.px_h), (270, 318))
                self.assertEqual((app._photo.width(), app._photo.height()),
                                 (270, 318))
                self.assertEqual((app.root.winfo_width(),
                                  app.root.winfo_height()), (270, 318))
                smaller = json.loads(json.dumps(app.cfg))
                smaller["ui_scale"] = 0.25
                app.apply_config(smaller, persist=False)
                app.root.update_idletasks()
                self.assertEqual((app._photo.width(), app._photo.height()),
                                 (68, 80))
                self.assertEqual((app.root.winfo_width(),
                                  app.root.winfo_height()), (68, 80))
            finally:
                app.root.destroy()

    def test_startup_failure_is_reported_not_swallowed(self):
        # 打包成 windowed 程序后没有控制台，启动异常只写日志等于"双击没反应"
        with (patch.object(paydance, "setup_win32"),
              patch.object(paydance, "cleanup_exception_logs"),
              patch.object(paydance.ctypes, "windll", MagicMock()),
              patch.object(paydance.sys, "argv", ["PayDance.pyw"]),
              patch.object(paydance, "repair_autostart"),
              patch.object(paydance, "acquire_instance_lock",
                           return_value=object()),
              patch.object(paydance, "release_instance_lock") as release,
              patch.object(paydance, "PayDanceApp",
                           side_effect=RuntimeError("boom")),
              patch.object(paydance, "log_exc"),
              patch.object(paydance, "fatal_message") as fatal):
            paydance.main()
        fatal.assert_called_once()
        self.assertIn("boom", fatal.call_args.args[0])
        release.assert_called_once()

    def test_card_border_small_round_with_square_inner_line(self):
        # 需求：主描边小圆角；内衬改成直角——内衬原本也是圆角，
        # 和外圈叠在角上会多出一道"内圆弧"。
        app = paydance.PayDanceApp.__new__(paydance.PayDanceApp)
        app.cfg = json.loads(json.dumps(paydance.DEFAULT_CONFIG))
        app.cfg["ui_scale"] = 1.0        # 固定成 270x318，取样坐标才稳定
        app.root = MagicMock()
        app._label = MagicMock()
        app._photo = None
        app.ambient_phase = 0.0
        app.confetti = []
        app.disp_money = app.disp_bar = 0.0
        app.cd_color = paydance.hex2rgb(app.cfg["colors"]["countdown"])
        with patch.object(paydance, "system_ui_scale", return_value=1.0):
            app.build_base()
        app._state = {
            "bar": 0.5, "phase": "pm", "money": 1.0,
            "money_color": paydance.hex2rgb(app.cfg["colors"]["money"]),
            "chip": "上班中", "cd": "02:34:56",
            "cd_color": paydance.hex2rgb(app.cfg["colors"]["countdown"]),
            "money_label": "今日已赚", "overtime": False}
        shots = []

        class Recorder:
            def __init__(self, img):
                shots.append(img.copy())

            def paste(self, img):
                shots.append(img.copy())

            def width(self):
                return shots[-1].width

            def height(self):
                return shots[-1].height

        with patch.object(paydance.ImageTk, "PhotoImage", Recorder):
            app.render_frame()
        img = shots[-1]
        self.assertEqual(img.size, (270, 318))
        colors = app.cfg["colors"]
        border = paydance.hex2rgb(colors["border"])
        inner = paydance.hex2rgb(
            paydance.mix(colors["border"], "#FFFFFF", 0.08))
        bg = paydance.hex2rgb(colors["bg"])

        def dist(pixel, target):
            return sum(abs(a - b) for a, b in zip(pixel[:3], target))

        # 主描边是小圆角：正角位置不该落在描边上（直角时会正好是描边色）
        self.assertGreater(dist(img.getpixel((1, 1)), border), 20,
                           "外角还是直角")
        # 但边的中点要在描边上
        self.assertLess(dist(img.getpixel((1, img.height // 2)), border), 25,
                        "主描边没画出来")
        # 内衬是直角：角点要落在内衬线上（圆角时角点会露出背景色）
        corner = img.getpixel((5, 5))
        self.assertLess(dist(corner, inner), dist(corner, bg),
                        "内衬角上还是圆角（又出现内圆弧了）")

    def test_no_hover_state_left_in_the_renderer(self):
        # 鼠标划过不应让画面有任何变化
        app = paydance.PayDanceApp.__new__(paydance.PayDanceApp)
        self.assertFalse(hasattr(app, "hover"))
        self.assertFalse(hasattr(app, "_press_overlay"))
        app2 = paydance.PayDanceApp.__new__(paydance.PayDanceApp)
        app2.cfg = json.loads(json.dumps(paydance.DEFAULT_CONFIG))
        app2.ui, app2.SS = 1.0, 3
        app2.root = MagicMock()
        app2._label = MagicMock()
        app2._photo = None
        app2.ambient_phase = 0.0
        app2.confetti = []
        app2.disp_money = app2.disp_bar = 0.0
        app2.cd_color = paydance.hex2rgb(app2.cfg["colors"]["countdown"])
        app2.build_base()
        app2._state = {
            "bar": 0.5, "phase": "pm", "money": 1.0,
            "money_color": paydance.hex2rgb(app2.cfg["colors"]["money"]),
            "chip": "上班中", "cd": "02:34:56",
            "cd_color": paydance.hex2rgb(app2.cfg["colors"]["countdown"]),
            "money_label": "今日已赚", "overtime": False}
        shots = []

        class Recorder:
            def __init__(self, img):
                shots.append(img.copy())

            def paste(self, img):
                shots.append(img.copy())

            def width(self):
                return shots[-1].width

            def height(self):
                return shots[-1].height

        with patch.object(paydance.ImageTk, "PhotoImage", Recorder):
            app2.render_frame()
            app2.render_frame()
        diff = ImageChops.difference(shots[0].convert("RGBA"),
                                     shots[1].convert("RGBA"))
        self.assertIsNone(diff.getbbox(), "两次渲染结果不一致")

    def test_hotkey_failure_is_reported(self):
        # Ctrl+Alt+D 被别的程序占用时不能一声不吭
        user32, shell32, kernel32 = self._tray_mocks(1)
        user32.RegisterHotKey.return_value = 0
        user32.GetMessageW.return_value = 0
        to_tk, to_tray = queue.Queue(), queue.Queue()
        with (patch.object(paydance.ctypes.windll, "user32", user32),
              patch.object(paydance.ctypes.windll, "shell32", shell32),
              patch.object(paydance.ctypes.windll, "kernel32", kernel32),
              patch.object(paydance, "make_gdi_icon", return_value=(1, None))):
            paydance.tray_thread(to_tk, to_tray)
        self.assertEqual(to_tk.get_nowait(), ("hotkey_failed",))

    def test_apply_modes_exception_still_keeps_window_on_top(self):
        app = paydance.PayDanceApp.__new__(paydance.PayDanceApp)
        app.cfg = json.loads(json.dumps(paydance.DEFAULT_CONFIG))
        app.cfg["desktop_layer"] = True
        app.pinned = False
        app.workerw = None
        app.root = MagicMock()
        with patch.object(paydance.PayDanceApp, "pin_to_desktop",
                          side_effect=RuntimeError("boom")), \
                patch.object(paydance, "log_exc"):
            app.apply_modes()
        app.root.attributes.assert_any_call("-topmost", True)

    def test_unknown_color_keys_are_dropped(self):
        # 旧版本遗留的无用颜色键不该被一路带回配置文件（否则永远清不掉）
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "config.json")
            legacy = json.loads(json.dumps(paydance.DEFAULT_CONFIG))
            legacy["colors"]["money_off"] = "#123456"
            legacy["colors"]["pct"] = "#654321"
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(legacy, handle)
            with patch.object(paydance, "CFG_PATH", path):
                cfg = paydance.load_config()
            self.assertNotIn("money_off", cfg["colors"])
            self.assertNotIn("pct", cfg["colors"])
            self.assertEqual(set(cfg["colors"]),
                             set(paydance.DEFAULT_CONFIG["colors"]))

    def test_no_color_key_is_defined_but_never_used(self):
        # 历史上出现过 money_off / pct：配置里定义了、3 个主题里也定义了，
        # 但渲染代码从不引用，用户调了没反应。这里扫一遍源码防复发。
        with open(paydance.__file__, encoding="utf-8") as handle:
            src = handle.read()
        used = re.sub(r"(?s)DEFAULT_CONFIG = \{.*?\n\}", "", src)
        used = re.sub(r"(?s)THEMES = \{.*?\n\}", "", used)
        for key in paydance.DEFAULT_CONFIG["colors"]:
            self.assertTrue('"%s"' % key in used,
                            "颜色键 %s 定义了却从未被使用" % key)


    def test_menu_checkmarks_have_live_variables(self):
        root = tk.Tk()
        root.withdraw()
        try:
            app = paydance.PayDanceApp.__new__(paydance.PayDanceApp)
            app.root = root
            app.cfg = self.cfg
            app.toggle_topmost = MagicMock()
            app.toggle_desktop = MagicMock()
            for name in ("toggle_overtime", "toggle_autostart", "open_settings",
                         "reload_config", "reset_position", "quit"):
                setattr(app, name, MagicMock())
            menu = tk.Menu(root, tearoff=0)
            with patch.object(paydance, "autostart_enabled", return_value=False):
                app._fill_menu(menu)
            self.assertTrue(app._menu_vars["always_on_top"].get())
            self.assertFalse(app._menu_vars["desktop_layer"].get())
            app.cfg["always_on_top"] = False
            app.cfg["desktop_layer"] = True
            menu.delete(0, "end")
            with patch.object(paydance, "autostart_enabled", return_value=False):
                app._fill_menu(menu)
            self.assertFalse(app._menu_vars["always_on_top"].get())
            self.assertTrue(app._menu_vars["desktop_layer"].get())
        finally:
            root.destroy()

    def test_tray_failure_notifies_once(self):
        app = paydance.PayDanceApp.__new__(paydance.PayDanceApp)
        app.to_tk = queue.Queue()
        app.root = MagicMock()
        app._tray_warning_shown = False
        app.to_tk.put(("tray_failed",))
        app.to_tk.put(("tray_failed",))
        with patch.object(paydance.messagebox, "showwarning") as warning:
            app.poll_tray()
        warning.assert_called_once()

    def _tray_mocks(self, notify_result):
        user32, shell32, kernel32 = MagicMock(), MagicMock(), MagicMock()
        user32.CreateWindowExW.return_value = 1234
        user32.RegisterClassW.return_value = 1
        kernel32.GetModuleHandleW.return_value = 1
        shell32.Shell_NotifyIconW.return_value = notify_result
        return user32, shell32, kernel32

    def test_tray_hotkey_registers_even_when_icon_fails(self):
        # 托盘挂不上时老板键仍必须可用，否则小窗被盖住就再也叫不出来
        user32, shell32, kernel32 = self._tray_mocks(0)
        user32.GetMessageW.return_value = 0
        to_tk, to_tray = queue.Queue(), queue.Queue()
        with (patch.object(paydance.ctypes.windll, "user32", user32),
              patch.object(paydance.ctypes.windll, "shell32", shell32),
              patch.object(paydance.ctypes.windll, "kernel32", kernel32),
              patch.object(paydance, "make_gdi_icon", return_value=(1, None)),
              patch.object(paydance.time, "sleep")):
            paydance.tray_thread(to_tk, to_tray)
        user32.RegisterHotKey.assert_called_once()
        self.assertEqual(to_tk.get_nowait(), ("tray_failed",))
        # 从没挂上就不该发 NIM_DELETE
        self.assertNotIn(paydance.NIM_DELETE, [call.args[0] for call in
                         shell32.Shell_NotifyIconW.call_args_list])

    def test_tray_readds_icon_when_explorer_restarts(self):
        # Explorer 重启会清空通知区，必须靠 TaskbarCreated 补挂
        user32, shell32, kernel32 = self._tray_mocks(1)
        user32.RegisterWindowMessageW.return_value = 0xC123
        messages = iter([(0xC123, 0)])

        def next_message(pointer, *_):
            try:
                pointer._obj.message, pointer._obj.lParam = next(messages)
                return 1
            except StopIteration:
                return 0

        user32.GetMessageW.side_effect = next_message
        to_tk, to_tray = queue.Queue(), queue.Queue()
        with (patch.object(paydance.ctypes.windll, "user32", user32),
              patch.object(paydance.ctypes.windll, "shell32", shell32),
              patch.object(paydance.ctypes.windll, "kernel32", kernel32),
              patch.object(paydance, "make_gdi_icon", return_value=(1, None))):
            paydance.tray_thread(to_tk, to_tray)
        # 补挂前必须先 NIM_DELETE：图标还在时直接 NIM_ADD 会因"已存在"失败
        order = [call.args[0] for call in shell32.Shell_NotifyIconW.call_args_list
                 if call.args[0] in (paydance.NIM_ADD, paydance.NIM_DELETE)]
        self.assertEqual(order, [paydance.NIM_ADD, paydance.NIM_DELETE,
                                 paydance.NIM_ADD, paydance.NIM_DELETE])

    def test_tray_tooltip_calls_month_total_by_its_name(self):
        app = paydance.PayDanceApp.__new__(paydance.PayDanceApp)
        app.cfg = json.loads(json.dumps(paydance.DEFAULT_CONFIG))
        app.cfg["overtime_date"] = dt.date.today().isoformat()
        app.root = MagicMock()
        app.dragging = False
        app.pinned = False
        app.settings_window = None
        app._last_frame_at = 0.0
        app.fade = 1.0
        app.prev_phase = None
        app.confetti = []
        app.ambient_phase = 0.0
        app.disp_money = app.disp_bar = 0.0
        app.cd_color = paydance.hex2rgb(app.cfg["colors"]["countdown"])
        app.last_tooltip = None
        app.to_tray = queue.Queue()
        app.render_frame = MagicMock()
        done = {"phase": "done", "title": "已下班", "countdown": "本月已赚",
                "earned": 5120.0, "money_prefix": "本月已赚", "progress": 1.0,
                "overtime": False, "hourly": 40.0, "full_day": 320.0}
        with patch.object(paydance, "compute", return_value=done), \
                patch.object(paydance, "save_config"), \
                patch.object(paydance, "send_tray_message") as send:
            app.tick()
        self.assertEqual(send.call_args.args[1],
                         ("tooltip", "下班倒计时 · 已下班 · 本月已赚 ¥5120.00"))

    def test_position_clamps_to_virtual_desktop(self):
        # 副屏在主屏右侧时不能把窗口夹回主屏
        sizes = {0: 2560, 1: 1440, 76: 0, 77: 0, 78: 5120, 79: 1440}
        with patch.object(paydance.user32, "GetSystemMetrics",
                          side_effect=lambda index: sizes[index]):
            self.assertEqual(paydance.clamp_to_desktop(3000, 100, 270, 318),
                             (3000, 100))
            self.assertEqual(paydance.clamp_to_desktop(9999, 100, 270, 318)[0],
                             5120 - 270 - 12)
            self.assertEqual(paydance.clamp_to_desktop(-800, 100, 270, 318),
                             (12, 100))

    def test_desktop_pin_failure_falls_back_to_topmost(self):
        # 万一贴桌面失败，本次先置顶，但绝不能改写用户的偏好设置
        app = paydance.PayDanceApp.__new__(paydance.PayDanceApp)
        app.cfg = json.loads(json.dumps(paydance.DEFAULT_CONFIG))
        app.cfg["desktop_layer"] = True
        app.cfg["always_on_top"] = False
        app.pinned = False
        app._pin_warning_shown = False
        app.root = MagicMock()
        with patch.object(paydance.PayDanceApp, "pin_to_desktop",
                          return_value=False), \
                patch.object(paydance, "save_config") as save, \
                patch.object(paydance, "log_exc"), \
                patch.object(paydance.messagebox, "showwarning") as warning:
            app.apply_modes()
            self.assertTrue(app.cfg["desktop_layer"])
            self.assertFalse(app.cfg["always_on_top"])
            save.assert_not_called()
            app.root.attributes.assert_any_call("-topmost", True)
            warning.assert_not_called()
            app.root.after.call_args.args[1]()
            warning.assert_called_once()

    def test_pin_to_desktop_pushes_window_to_the_bottom(self):
        # 贴桌面底层 = 保持普通窗口、只压到 z 序最底。
        # 不用 SetParent 到 WorkerW/Progman：实测那样 Tk 窗口会彻底不再绘制。
        app = paydance.PayDanceApp.__new__(paydance.PayDanceApp)
        app.pinned = False
        app.cur_x, app.cur_y = 10, 20
        app.root = MagicMock()
        with patch.object(paydance, "get_hwnd", return_value=111), \
                patch.object(paydance.user32, "SetWindowPos") as pos:
            self.assertTrue(app.pin_to_desktop())
            self.assertTrue(app.pinned)
            self.assertEqual(pos.call_args.args[0], 111)
            self.assertEqual(pos.call_args.args[1], paydance.HWND_BOTTOM)
            pos.reset_mock()
            # 已经贴上时再调用不该重复动窗口
            app.pin_to_desktop()
            pos.assert_not_called()

    def test_unpin_restores_requested_layer(self):
        app = paydance.PayDanceApp.__new__(paydance.PayDanceApp)
        app.pinned = True
        app.cur_x, app.cur_y = 10, 20
        app.cfg = {"always_on_top": False}
        app.root = MagicMock()
        with patch.object(paydance, "get_hwnd", return_value=111), \
                patch.object(paydance.user32, "SetWindowPos") as pos:
            app.unpin_from_desktop()
            self.assertFalse(app.pinned)
            self.assertEqual(pos.call_args.args[1], paydance.HWND_NOTOPMOST)
            app.pinned = True
            app.cfg["always_on_top"] = True
            app.unpin_from_desktop()
            self.assertEqual(pos.call_args.args[1], paydance.HWND_TOPMOST)

    def test_keep_at_bottom_only_while_pinned_and_not_dragging(self):
        app = paydance.PayDanceApp.__new__(paydance.PayDanceApp)
        app.root = MagicMock()
        with patch.object(paydance, "get_hwnd", return_value=111), \
                patch.object(paydance.user32, "SetWindowPos") as pos:
            app.pinned, app.dragging = False, False
            app.keep_at_bottom()
            pos.assert_not_called()
            app.pinned, app.dragging = True, True
            app.keep_at_bottom()
            pos.assert_not_called()          # 拖动时不要跟用户抢层级
            app.pinned, app.dragging = True, False
            app.keep_at_bottom()
            self.assertEqual(pos.call_args.args[1], paydance.HWND_BOTTOM)

    def test_missing_pillow_reports_instead_of_dying_silently(self):
        with patch.object(paydance, "Image", None), \
                patch.object(paydance, "setup_win32"), \
                patch.object(paydance, "cleanup_exception_logs"), \
                patch.object(paydance.ctypes, "windll", MagicMock()), \
                patch.object(paydance.sys, "argv", ["PayDance.pyw"]), \
                patch.object(paydance, "fatal_message") as fatal, \
                patch.object(paydance, "PayDanceApp") as app_class:
            paydance.main()
        fatal.assert_called_once()
        app_class.assert_not_called()


if __name__ == "__main__":
    unittest.main()
