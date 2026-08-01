"""End-to-end Telegram QA runner for Attendance Mate.

Run locally with tester.env and qa_user.session present. This file is intentionally
kept out of production startup; it sends real test messages and cleans up its QA row.
"""
import asyncio
import json
import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

from dotenv import dotenv_values
from telethon import TelegramClient
from gspread.exceptions import APIError

from bot.sheets import SheetsManager

ROOT = Path(__file__).parent
ENV = dotenv_values(ROOT / "tester.env")
BOT_USERNAME = ENV.get("BOT_USERNAME", "@AttendMatebot")
SESSION = ROOT / "qa_user.session"
LOG_FILE = ROOT / "logs" / "bot.log"
QA_PERIOD = "99"
QA_DAY = "Saturday"
QA_SUBJECT = "ECO"


class QARunner:
    def __init__(self):
        self.client = TelegramClient(
            str(SESSION), int(ENV["TG_API_ID"]), ENV["TG_API_HASH"]
        )
        self.bot = None
        self.sheets = None
        self.results = []
        self.log_offset = LOG_FILE.stat().st_size if LOG_FILE.exists() else 0

    @staticmethod
    def sheets_call(operation, attempts=6):
        for attempt in range(attempts):
            try:
                return operation()
            except APIError as exc:
                if "429" not in str(exc) or attempt == attempts - 1:
                    raise
                time.sleep(2 ** min(attempt + 1, 5))

    def sheet_snapshot(self):
        ranges = self.sheets_call(lambda: self.sheets.spreadsheet.values_batch_get(
            ["Attendance_Log", "Exceptions", "Settings"]
        )).get("valueRanges", [])
        values = {
            item.get("range", "").split("!")[0].replace("'", ""): item.get("values", [])
            for item in ranges
        }
        attendance = values.get("Attendance_Log", [])
        exceptions = values.get("Exceptions", [])
        settings = values.get("Settings", [])
        return {
            "attendance_rows": len(attendance),
            "attendance_last": attendance[-1] if len(attendance) > 1 else None,
            "exceptions_rows": len(exceptions),
            "exceptions_last": exceptions[-1] if len(exceptions) > 1 else None,
            "settings": {row[0]: row[1] for row in settings[1:] if len(row) >= 2},
        }

    def log_snapshot(self):
        if not LOG_FILE.exists():
            return ""
        with LOG_FILE.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(self.log_offset)
            return re.sub(r"bot\d+:[A-Za-z0-9_-]+", "bot<TOKEN>", handle.read())

    async def wait_for_bot_message(self, minimum_id, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            messages = await self.client.get_messages(self.bot, limit=20)
            for message in messages:
                if message.id > minimum_id and message.sender_id == self.bot.id:
                    return message
            await asyncio.sleep(0.4)
        raise TimeoutError("No bot response received")

    async def wait_for_edit(self, message, previous_text, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = await self.client.get_messages(self.bot, ids=message.id)
            if current and current.text != previous_text:
                return current
            await asyncio.sleep(0.4)
        return await self.client.get_messages(self.bot, ids=message.id)

    async def send_text(self, text):
        sent = await self.client.send_message(self.bot, text)
        return await self.wait_for_bot_message(sent.id)

    async def click(self, message, label):
        fresh = await self.client.get_messages(self.bot, ids=message.id)
        before_text = fresh.text or ""
        await fresh.click(text=label)
        return await self.wait_for_edit(fresh, before_text)

    async def action(self, name, operation, expected, sheet_change=None, log_hint=None):
        before_sheet = self.sheet_snapshot()
        before_log_offset = self.log_offset
        try:
            response = await operation()
            self._last_response = response
            response_text = response.text or ""
            passed = all(token.lower() in response_text.lower() for token in expected)
            changed_sheet = self.sheet_snapshot()
            log_text = self.log_snapshot() if LOG_FILE.exists() else ""
            self.log_offset = LOG_FILE.stat().st_size if LOG_FILE.exists() else self.log_offset
            if sheet_change:
                passed = passed and sheet_change(before_sheet, changed_sheet)
            if log_hint:
                passed = passed and log_hint in log_text
            result = {
                "test": name,
                "passed": passed,
                "response": response_text,
                "log": log_text[-2000:],
                "sheet_before": before_sheet,
                "sheet_after": changed_sheet,
                "root_cause": None if passed else "Response, log, or Google Sheet assertion failed",
            }
        except Exception as exc:
            result = {
                "test": name,
                "passed": False,
                "response": "",
                "log": self.log_snapshot()[-2000:],
                "sheet_before": before_sheet,
                "sheet_after": self.sheet_snapshot(),
                "root_cause": f"{type(exc).__name__}: {exc}",
            }
            self.log_offset = LOG_FILE.stat().st_size if LOG_FILE.exists() else before_log_offset
        self.results.append(result)
        status = "PASS" if result["passed"] else "FAIL"
        print(f"[{status}] {name}")
        return result

    @staticmethod
    def row_count_changed(before, after):
        return after["attendance_rows"] > before["attendance_rows"] or after["attendance_last"] != before["attendance_last"]

    @staticmethod
    def exception_changed(before, after):
        return after["exceptions_rows"] != before["exceptions_rows"] or after["exceptions_last"] != before["exceptions_last"]

    @staticmethod
    def setting_changed(before, after):
        return before["settings"] != after["settings"]

    async def add_qa_row(self):
        sheet = self.sheets.spreadsheet.worksheet("Timetable")
        self.sheets_call(lambda: sheet.append_row([QA_DAY, QA_PERIOD, QA_SUBJECT, "00:00", "23:59"]))

    async def cleanup(self):
        timetable = self.sheets.spreadsheet.worksheet("Timetable")
        rows = self.sheets_call(timetable.get_all_values)
        for index in range(len(rows), 1, -1):
            row = rows[index - 1]
            if len(row) >= 3 and row[0] == QA_DAY and row[1] == QA_PERIOD and row[2] == QA_SUBJECT:
                self.sheets_call(lambda i=index: timetable.delete_rows(i))
        attendance = self.sheets.spreadsheet.worksheet("Attendance_Log")
        rows = self.sheets_call(attendance.get_all_values)
        for index in range(len(rows), 1, -1):
            row = rows[index - 1]
            if len(row) >= 3 and row[0] == date.today().isoformat() and row[2] == QA_PERIOD:
                self.sheets_call(lambda i=index: attendance.delete_rows(i))

    async def run(self):
        await self.client.start()
        self.bot = await self.client.get_entity(BOT_USERNAME)
        self.sheets = SheetsManager()
        await self.add_qa_row()
        try:
            start = await self.action("/start command and persistent menu", lambda: self.send_text("/start"), ["Attendance Mate"])
            assert start["passed"]
            await self.action("Dashboard main-menu button", lambda: self.send_text("Dashboard"), ["Dashboard", "Overall"])
            await self.action("Today main-menu button", lambda: self.send_text("Today"), ["Today"])
            await self.action("Subjects main-menu button", lambda: self.send_text("Subjects"), ["Subjects"])
            await self.action("Reports main-menu button", lambda: self.send_text("Reports"), ["Reports"])
            await self.action("Manage main-menu button", lambda: self.send_text("Manage"), ["Manage"])
            await self.action("Refresh main-menu button", lambda: self.send_text("Refresh"), ["Dashboard"])
            await self.action("/help command", lambda: self.send_text("/help"), ["How to use"])
            await self.action("/about command", lambda: self.send_text("/about"), ["Attendance Mate", "Version"])

            subjects = await self.send_text("Subjects")
            for subject in ["ECO", "MINOR 1", "MINOR 2", "MDC", "AEC-LE", "AEC-EL"]:
                await self.action(f"Subject {subject}", lambda s=subject: self.click(subjects, s), [subject])
                subjects = await self.click(subjects, "Back")
            subjects = await self.send_text("Subjects")
            eco = await self.click(subjects, "ECO")
            await self.action("Subject History", lambda: self.click(eco, "History"), ["history"])
            eco = await self.click(eco, "Back to subject")
            await self.action("Subject Edit entries", lambda: self.click(eco, "Edit entries"), ["Edit ECO"])

            today = await self.send_text("Today")
            await self.action("Today QA period mark screen", lambda: self.click(today, f"Mark {QA_PERIOD}"), ["Period 99", "What happened"])
            today = await self.send_text("Today")
            mark = await self.click(today, f"Mark {QA_PERIOD}")
            await self.action("Present attendance write", lambda: self.click(mark, "Present"), ["Attendance saved"], self.row_count_changed, "Attendance saved")
            today = await self.send_text("Today")
            mark = await self.click(today, f"Mark {QA_PERIOD}")
            await self.action("Absent attendance edit", lambda: self.click(mark, "Absent"), ["Absent saved"], self.row_count_changed)
            today = await self.send_text("Today")
            mark = await self.click(today, f"Mark {QA_PERIOD}")
            await self.action("No Class attendance edit", lambda: self.click(mark, "No Class"), ["No class marked"], self.row_count_changed)

            reports = await self.send_text("Reports")
            for label, expected in [("Weekly", "This week"), ("Monthly", "This month"), ("Overall", "Overall")]:
                report_message = reports
                await self.action(f"Reports {label}", lambda l=label: self.click(report_message, l), [expected])
                reports = await self.send_text("Reports")

            manage = await self.send_text("Manage")
            timetable = await self.action("Timetable screen", lambda: self.click(manage, "Timetable"), ["Timetable"])
            manage = await self.send_text("Manage")
            exceptions = await self.click(manage, "Exceptions")
            await self.action("Add day holiday", lambda: self.click(exceptions, "Add Day Holiday"), ["Holiday added"], self.exception_changed)
            exceptions = await self.send_text("Manage")
            exceptions = await self.click(exceptions, "Exceptions")
            await self.action("Remove day holiday", lambda: self.click(exceptions, "Remove Today"), ["Exception removed"], self.exception_changed)
            exceptions = await self.send_text("Manage")
            exceptions = await self.click(exceptions, "Exceptions")
            await self.action("Add period no class menu", lambda: self.click(exceptions, "Add Period No Class"), ["Choose a period"])
            period_menu = self._last_response
            await self.action("Add period no class", lambda: self.click(period_menu, f"Period {QA_PERIOD}"), ["No class marked"], self.exception_changed)
            exceptions = await self.send_text("Manage")
            exceptions = await self.click(exceptions, "Exceptions")
            await self.action("Remove period exception menu", lambda: self.click(exceptions, "Remove Period"), ["Choose a period"])
            exceptions = await self.send_text("Manage")
            exceptions = await self.click(exceptions, "Exceptions")
            remove_menu = await self.click(exceptions, "Remove Period")
            await self.action("Remove period exception", lambda: self.click(remove_menu, f"Remove period {QA_PERIOD}"), ["Exception removed"], self.exception_changed)

            manage = await self.send_text("Manage")
            settings = await self.click(manage, "Settings")
            await self.action("Toggle reminders", lambda: self.click(settings, "Toggle reminders"), ["Reminders"], self.setting_changed)
            settings = await self.send_text("Manage")
            settings = await self.click(settings, "Settings")
            await self.action("Change reminder delay", lambda: self.click(settings, "Change delay"), ["Delay"], self.setting_changed)
            settings = await self.send_text("Manage")
            settings = await self.click(settings, "Settings")
            await self.action("Change warning threshold", lambda: self.click(settings, "Change threshold"), ["threshold"], self.setting_changed)
            settings = await self.send_text("Manage")
            settings = await self.click(settings, "Settings")
            manage = await self.send_text("Manage")
            await self.action("Export screen", lambda: self.click(manage, "Export"), ["Export", "Attendance_Log"])
        finally:
            await self.cleanup()
            await self.client.disconnect()

    def report(self):
        passed = sum(result["passed"] for result in self.results)
        report = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "passed": passed,
            "failed": len(self.results) - passed,
            "tests": self.results,
        }
        path = ROOT / "qa_report.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"\nQA report: {path}")
        print(f"Passed: {report['passed']}  Failed: {report['failed']}")
        return report["failed"] == 0


async def main():
    runner = QARunner()
    try:
        await runner.run()
    finally:
        passed = runner.report()
    return passed


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main()) else 1)
