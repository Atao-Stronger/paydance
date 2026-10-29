import copy
import datetime as dt
import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QDate, QTime
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QTimeEdit

import settings_bridge
from settings_ui import CalendarDialog, SettingsDialog, configure_application


loader = importlib.machinery.SourceFileLoader("paydance_qt_test", "paydance.pyw")
spec = importlib.util.spec_from_loader(loader.name, loader)
core = importlib.util.module_from_spec(spec)
sys.modules[loader.name] = core
loader.exec_module(core)


class QtSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        configure_application(cls.qt)

    def setUp(self):
        self.live = copy.deepcopy(core.DEFAULT_CONFIG)
        core.roll_calendar(self.live, dt.date.today())
        self.saved = []

        def submit(edited):
            candidate = settings_bridge.merge_settings(self.live, edited)
            core.validate_config(candidate)
            self.live = candidate
            self.saved.append(copy.deepcopy(candidate))
            self.window.saved(candidate)

        self.window = SettingsDialog(core, self.live, submit)
        self.window.show()
        self.qt.processEvents()

    def tearDown(self):
        if self.window.calendar_window is not None:
            self.window.calendar_window.reject()
        self.window.reject()
        self.qt.processEvents()

    def combo(self, key, value):
        widget = self.window.controls[key]
        widget.setCurrentIndex(widget.findData(value))

    def test_navigation_has_three_separate_pages_and_fixed_footer(self):
        self.assertEqual(self.window.pages.count(), 3)
        for index in range(3):
            self.window.navigation.button(index).click()
            self.assertEqual(self.window.pages.currentIndex(), index)
            self.assertTrue(self.window.month_summary.isVisible())
        self.window.resize(760, 540)
        self.qt.processEvents()
        self.assertTrue(self.window.footer.isVisible())

    def test_application_and_settings_share_packaged_logo(self):
        icon = self.qt.windowIcon()
        self.assertFalse(icon.isNull())
        self.assertEqual(icon.cacheKey(), self.window.windowIcon().cacheKey())
        image = icon.pixmap(32, 32).toImage()
        self.assertFalse(image.isNull())
        self.assertEqual(image.pixelColor(16, 16).getRgb(), (255, 198, 61, 255))
        self.assertTrue(self.window.apply_button.isVisible())
        self.assertTrue(self.window.month_summary.isVisible())

    def test_month_total_updates_without_calendar_and_only_uses_saved_values(self):
        self.window.update_month_total(12345.67, "2026年10月")
        self.assertEqual(self.window.month_amount.text(), "¥ 12,345.67")
        self.assertIn("2026年10月", self.window.month_detail.text())
        self.assertIsNone(self.window.calendar_window)
        self.window.controls["daily_salary"].setText("123.00")
        self.assertEqual(self.window.month_amount.text(), "¥ 12,345.67")
        self.window.save_button.click()
        expected = core.month_earned_now(dt.datetime.now(), self.live)
        self.assertEqual(self.window.month_amount.text(), "¥ %s" % format(expected, ",.2f"))

    def test_salary_mode_shows_only_relevant_fields(self):
        self.assertTrue(self.window.fields["daily_salary"].isVisible())
        self.assertFalse(self.window.fields["monthly_salary"].isVisible())
        self.combo("salary_mode", "monthly")
        self.assertTrue(self.window.fields["monthly_salary"].isVisible())
        self.assertFalse(self.window.fields["daily_salary"].isVisible())
        self.window.controls["monthly_subsidy"].setText("440.00")
        self.assertIn("个工作日", self.window.preview.text())

    def test_native_time_controls_and_evening_break_toggle(self):
        for key in ("work_start", "work_end", "lunch_start", "lunch_end", "overtime_end"):
            self.assertIsInstance(self.window.controls[key], QTimeEdit)
            self.assertEqual(self.window.controls[key].displayFormat(), "HH:mm")
        self.assertFalse(self.window.controls["dinner_start"].isEnabled())
        self.window.controls["dinner_break_enabled"].setChecked(True)
        self.assertTrue(self.window.controls["dinner_start"].isEnabled())
        self.assertTrue(self.window.save())
        self.assertTrue(self.saved[-1]["dinner_break_enabled"])

    def test_insurance_options_update_preview_save_and_reload(self):
        combo = self.window.controls["insurance_mode"]
        self.assertIsInstance(combo, QComboBox)
        self.assertEqual([combo.itemText(i) for i in range(combo.count())],
                         ["不扣除", "五险一金", "六险二金"])
        amount = self.window.controls["monthly_insurance"]
        self.assertFalse(amount.isEnabled())
        for mode in ("five_one", "six_two"):
            self.combo("insurance_mode", mode)
            amount.setText("1100.25")
            self.assertTrue(amount.isEnabled())
            self.assertIn(core.INSURANCE_OPTIONS[mode], self.window.preview.text())
            self.assertTrue(self.window.save())
            with tempfile.TemporaryDirectory() as folder:
                with patch.object(core, "CFG_PATH", os.path.join(folder, "config.json")):
                    core.save_config(self.saved[-1])
                    restored = core.load_config()
            self.assertEqual(restored["insurance_mode"], mode)
            self.assertEqual(restored["monthly_insurance"], 1100.25)
        self.combo("insurance_mode", "none")
        self.assertFalse(amount.isEnabled())
        self.assertNotIn("每月扣除", self.window.preview.text())
        self.assertEqual(amount.text(), "1100.25")

    def test_salary_stat_mode_preserves_internal_keys(self):
        self.combo("salary_stat_mode", "overtime")
        self.assertTrue(self.window.save())
        self.assertEqual(self.saved[-1]["salary_stat_mode"], "overtime")

    def test_save_without_closing_does_not_apply_later_edits(self):
        self.window.controls["daily_salary"].setText("321.00")
        self.window.save_button.click()
        self.assertTrue(self.window.isVisible())
        self.assertEqual(self.saved[-1]["daily_salary"], 321)
        self.window.controls["daily_salary"].setText("322.00")
        self.assertEqual(self.live["daily_salary"], 321)
        self.window.apply_button.click()
        self.assertEqual(self.live["daily_salary"], 322)
        self.assertFalse(self.window.isVisible())

    def test_invalid_amount_and_time_order_do_not_save(self):
        self.window.controls["monthly_subsidy"].setText("NaN")
        self.assertFalse(self.window.save())
        self.assertEqual(self.saved, [])
        self.window.controls["monthly_subsidy"].setText("0.00")
        self.window.controls["work_start"].setTime(QTime(23, 0))
        self.assertFalse(self.window.save())
        self.assertEqual(self.saved, [])
        self.assertTrue(self.window.status.text())

    def test_no_selected_workday_is_rejected(self):
        for button in self.window.day_buttons:
            button.setChecked(False)
        self.assertFalse(self.window.save())
        self.assertIn("工作日", self.window.status.text())

    def test_saving_keeps_live_calendar_position_and_overtime_state(self):
        self.live.update(calendar_month="2000-02", calendar_overrides={},
                         overtime_today=True, overtime_date="2000-02-01",
                         position=[300, 400], always_on_top=False)
        self.window.controls["daily_salary"].setText("123.00")
        self.assertTrue(self.window.save())
        self.assertEqual(self.live["calendar_month"], "2000-02")
        self.assertTrue(self.live["overtime_today"])
        self.assertEqual(self.live["position"], [300, 400])
        self.assertFalse(self.live["always_on_top"])

    def test_calendar_is_reused_and_reopened_after_closing(self):
        first = self.window.open_calendar()
        self.assertIs(self.window.open_calendar(), first)
        first.reject()
        self.assertIsNone(self.window.calendar_window)
        self.assertIsNot(self.window.open_calendar(), first)

    def test_calendar_uses_current_deductions_and_saves_custom_income_immediately(self):
        self.combo("insurance_mode", "six_two")
        self.window.controls["monthly_insurance"].setText("1100.00")
        calendar = self.window.open_calendar()
        self.assertIsInstance(calendar, CalendarDialog)
        selected = calendar.selected()
        calendar.kind_combo.setCurrentIndex(calendar.kind_combo.findData("statutory"))
        calendar.attendance_combo.setCurrentIndex(calendar.attendance_combo.findData("overtime"))
        calendar.amount.setText("888.50")
        calendar.save_day()
        self.assertEqual(self.live["calendar_overrides"][selected.isoformat()], {
            "kind": "statutory", "attendance": "overtime", "amount": 888.5})
        self.assertEqual(self.live["monthly_insurance"], 1100)
        calendar.reset_day()
        self.assertNotIn(selected.isoformat(), self.live["calendar_overrides"])

    def test_calendar_selection_preserves_widget_and_rejects_invalid_money(self):
        calendar = self.window.open_calendar()
        native = calendar.calendar
        day = dt.date.today().replace(day=1)
        native.setSelectedDate(QDate(day.year, day.month, day.day))
        self.assertIs(calendar.calendar, native)
        for value in ("", "1.234", "-1", "NaN"):
            calendar.amount.setText(value)
            calendar.save_day()
            self.assertEqual(self.saved, [])
        self.assertTrue(calendar.status.text())

    def test_theme_and_sliders_are_saved_without_changing_widget_state(self):
        self.combo("theme", "forest")
        self.window.controls["ui_scale"].setValue(50)
        self.window.controls["opacity"].setValue(80)
        self.window.controls["font_scale"].setValue(110)
        self.window.controls["animations"].setChecked(False)
        self.assertTrue(self.window.save())
        expected = dict(core.DEFAULT_CONFIG["colors"], **core.THEMES["forest"]["colors"])
        self.assertEqual(self.live["colors"], expected)
        self.assertEqual(self.live["ui_scale"], .5)
        self.assertEqual(self.live["opacity"], .8)
        self.assertEqual(self.live["font_scale"], 1.1)
        self.assertFalse(self.live["animations"])


class SettingsBridgeTests(unittest.TestCase):
    def test_month_total_uses_live_config_and_is_throttled_to_once_per_second(self):
        bridge = settings_bridge.SettingsWindow.__new__(settings_bridge.SettingsWindow)
        bridge.core = core
        bridge.app = MagicMock(cfg=copy.deepcopy(core.DEFAULT_CONFIG))
        bridge.connection, bridge.process = MagicMock(), MagicMock()
        bridge.connection.poll.return_value = False
        bridge.ready, bridge.summary_at = True, None
        fixed = dt.datetime(2026, 10, 22, 12, 0)
        with (patch.object(settings_bridge.dt, "datetime", wraps=dt.datetime) as clock,
              patch.object(core, "month_earned_now", return_value=1234.56) as earned):
            clock.now.return_value = fixed
            bridge._poll()
            bridge._poll()
        self.assertEqual(bridge.connection.send.call_count, 1)
        self.assertEqual(bridge.connection.send.call_args.args[0][0], "month_total")
        self.assertEqual(bridge.connection.send.call_args.args[0][1]["earned"], 1234.56)
        self.assertIs(earned.call_args.args[1], bridge.app.cfg)

    def test_open_settings_reuses_running_process(self):
        app = core.PayDanceApp.__new__(core.PayDanceApp)
        app.settings_window = MagicMock()
        with patch.object(core, "SettingsWindow") as constructor:
            app.open_settings()
        constructor.assert_not_called()
        app.settings_window.focus.assert_called_once()

    def test_process_start_failure_is_reported(self):
        app = core.PayDanceApp.__new__(core.PayDanceApp)
        app.root, app.settings_window = MagicMock(), None
        with (patch.object(core, "SettingsWindow", side_effect=RuntimeError("spawn failed")),
              patch.object(core, "log_exc") as logged,
              patch.object(core.messagebox, "showerror") as error):
            app.open_settings()
        logged.assert_called_once()
        error.assert_called_once()
        self.assertIn("spawn failed", error.call_args.args[1])
        self.assertIsNone(app.settings_window)

    def test_worker_reply_applies_validated_settings_on_main_process(self):
        bridge = settings_bridge.SettingsWindow.__new__(settings_bridge.SettingsWindow)
        bridge.core = core
        bridge.app = MagicMock(cfg=copy.deepcopy(core.DEFAULT_CONFIG))
        bridge.connection, bridge.process = MagicMock(), MagicMock()
        bridge.connection.poll.side_effect = [True, False]
        bridge.connection.recv.return_value = ("save", dict(bridge.app.cfg, daily_salary=123))
        bridge._poll()
        self.assertEqual(bridge.app.apply_config.call_args.args[0]["daily_salary"], 123)
        self.assertEqual(bridge.connection.send.call_args.args[0][0], "saved")

    def test_invalid_worker_message_is_rejected_without_applying(self):
        bridge = settings_bridge.SettingsWindow.__new__(settings_bridge.SettingsWindow)
        bridge.core = core
        bridge.app = MagicMock(cfg=copy.deepcopy(core.DEFAULT_CONFIG))
        bridge.connection, bridge.process = MagicMock(), MagicMock()
        bridge.connection.poll.side_effect = [True, False]
        bridge.connection.recv.return_value = ("save", dict(bridge.app.cfg, daily_salary=-1))
        bridge._poll()
        bridge.app.apply_config.assert_not_called()
        self.assertEqual(bridge.connection.send.call_args.args[0][0], "error")

    def test_closing_worker_releases_polling_and_pipe(self):
        bridge = settings_bridge.SettingsWindow.__new__(settings_bridge.SettingsWindow)
        bridge.app = MagicMock()
        bridge.connection, bridge.process = MagicMock(), MagicMock()
        bridge.poll_id = "after#1"
        bridge.close()
        bridge.app.root.after_cancel.assert_called_once_with("after#1")
        bridge.connection.close.assert_called_once()
        self.assertIsNone(bridge.app.settings_window)
