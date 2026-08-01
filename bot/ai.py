"""Gemini assistant and safe, allowlisted Sheets actions."""
from __future__ import annotations

from datetime import date, timedelta
import json
import logging
import re

from google import genai
from google.genai import types

from bot.config import Config
from bot.sheets import SheetsManager
from bot.time_utils import date_context, today as local_today

logger = logging.getLogger(__name__)


def _date_from_text(value: str) -> date | None:
    value = value.lower().strip()
    today = local_today()
    if value == "today":
        return today
    if value == "tomorrow":
        return today + timedelta(days=1)
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d/%m/%y"):
        try:
            from datetime import datetime
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def _action_date(value: str) -> date:
    """Parse dates returned by Gemini in ISO or common user formats."""
    parsed = _date_from_text(str(value))
    if parsed is None:
        raise ValueError(f"Unsupported date format: {value}")
    return parsed


def _holiday_prompt(prompt: str) -> tuple[date, date] | None:
    text = prompt.lower()
    if not any(word in text for word in ("holiday", "no class", "no classes", "leave")):
        return None
    matches = re.findall(r"\b(?:today|tomorrow|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\b", text)
    if not matches:
        return None
    dates = [_date_from_text(item) for item in matches]
    dates = [item for item in dates if item]
    if not dates:
        return None
    return min(dates), max(dates)


def _period_prompt(prompt: str) -> dict | None:
    if not any(word in prompt.lower() for word in ("holiday", "no class", "no classes", "cancel")):
        return None
    match = re.search(r"(?:period|class)\s*(?:number\s*)?(\d+).*?(today|tomorrow|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})", prompt.lower())
    if not match:
        return None
    target = _date_from_text(match.group(2))
    if not target:
        return None
    return {"name": "add_period_exception", "args": {"date": target.isoformat(), "period": match.group(1), "reason": "No class"}}


class GeminiAssistant:
    def __init__(self, sheets: SheetsManager):
        self.sheets = sheets
        self.keys = Config.AI_KEYS
        self.key_index = 0
        self.clients = {}

    def _client(self):
        if not self.keys:
            return None
        key = self.keys[self.key_index % len(self.keys)]
        self.key_index += 1
        if key not in self.clients:
            self.clients[key] = genai.Client(api_key=key)
        return self.clients[key]

    def remember_action(self, prompt: str, action: dict):
        self.sheets.save_prompt_pattern(prompt, {"name": action["name"], "args": action["args"]})

    def _pattern_action(self, prompt: str) -> dict | None:
        normalized = re.sub(r"\s+", " ", prompt.lower()).strip()
        action = self.sheets.get_prompt_pattern(normalized)
        if action and not any(word in normalized for word in ("today", "tomorrow")):
            return action
        return None

    def _holiday_action(self, prompt: str) -> dict | None:
        period_action = _period_prompt(prompt)
        if period_action:
            return period_action
        dates = _holiday_prompt(prompt)
        if not dates:
            return None
        start, end = dates
        return {"name": "add_day_holidays", "args": {"start": start.isoformat(), "end": end.isoformat(), "reason": "Holiday"}}

    def _declaration(self):
        return [
            types.FunctionDeclaration(name="attendance_summary", description="Read current attendance summary, optionally for a subject.", parameters=types.Schema(type="OBJECT", properties={"subject": types.Schema(type="STRING")})),
            types.FunctionDeclaration(name="sheet_read", description="Read a safe summary of a Google Sheet tab.", parameters=types.Schema(type="OBJECT", required=["sheet"], properties={"sheet": types.Schema(type="STRING")})),
            types.FunctionDeclaration(name="add_day_holidays", description="Mark every date in an inclusive range as a holiday. Requires confirmation.", parameters=types.Schema(type="OBJECT", required=["start", "end"], properties={"start": types.Schema(type="STRING"), "end": types.Schema(type="STRING"), "reason": types.Schema(type="STRING")})),
            types.FunctionDeclaration(name="add_period_exception", description="Mark one period on one date as no class. Requires confirmation.", parameters=types.Schema(type="OBJECT", required=["date", "period"], properties={"date": types.Schema(type="STRING"), "period": types.Schema(type="STRING"), "reason": types.Schema(type="STRING")})),
            types.FunctionDeclaration(name="edit_timetable", description="Change a timetable subject or time. Requires confirmation.", parameters=types.Schema(type="OBJECT", required=["day", "period"], properties={"day": types.Schema(type="STRING"), "period": types.Schema(type="STRING"), "subject": types.Schema(type="STRING"), "start": types.Schema(type="STRING"), "end": types.Schema(type="STRING")})),
            types.FunctionDeclaration(name="remove_exception", description="Remove an active day or period exception. Requires confirmation.", parameters=types.Schema(type="OBJECT", required=["date"], properties={"date": types.Schema(type="STRING"), "period": types.Schema(type="STRING")})),
            types.FunctionDeclaration(name="update_setting", description="Change a supported bot setting. Requires confirmation.", parameters=types.Schema(type="OBJECT", required=["key", "value"], properties={"key": types.Schema(type="STRING"), "value": types.Schema(type="STRING")})),
            types.FunctionDeclaration(name="add_timetable_entry", description="Add a timetable row. Requires confirmation.", parameters=types.Schema(type="OBJECT", required=["day", "period", "subject"], properties={"day": types.Schema(type="STRING"), "period": types.Schema(type="STRING"), "subject": types.Schema(type="STRING"), "start": types.Schema(type="STRING"), "end": types.Schema(type="STRING")})),
            types.FunctionDeclaration(name="delete_timetable_entry", description="Delete a timetable row. Requires confirmation.", parameters=types.Schema(type="OBJECT", required=["day", "period"], properties={"day": types.Schema(type="STRING"), "period": types.Schema(type="STRING")})),
        ]

    def _read_tool(self, name: str, args: dict) -> str:
        if name == "attendance_summary":
            stats = self.sheets.get_attendance_stats()
            subject = args.get("subject", "").strip().upper()
            if subject:
                stats = stats["by_subject"].get(subject, {"present": 0, "absent": 0, "no_class": 0})
                percentage = self.sheets.calculate_subject_percentage(stats)
                return f"{subject}: {percentage:.1f}%\nPresent: {stats['present']}\nAbsent: {stats['absent']}\nNo class: {stats.get('no_class', 0)}"
            return f"Overall: {self.sheets.calculate_percentage(stats):.1f}%\nPresent: {stats['present']}\nAbsent: {stats['absent']}\nNo class: {stats['no_class']}"
        if name == "sheet_read":
            allowed = {"timetable": self.sheets.get_timetable, "attendance_log": lambda: self.sheets.get_attendance_stats(), "exceptions": lambda: self.sheets.get_exceptions_for_date(local_today()), "settings": lambda: {key: self.sheets.get_setting(key) for key in ("threshold", "reminders", "reminder_delay")}}
            reader = allowed.get(args.get("sheet", "").lower())
            return json.dumps(reader() if reader else {"error": "Sheet is not available through this assistant."}, default=str)
        return json.dumps({"error": "This action changes data and must be confirmed."})

    async def ask(self, prompt: str, history: list[dict] | None = None, _attempt: int = 0) -> dict:
        direct = self._pattern_action(prompt) or self._holiday_action(prompt)
        if direct:
            direct["prompt"] = prompt
            return {"text": "I found a saved attendance action. Please confirm it.", "pending": direct}
        client = self._client()
        if not client:
            return {"text": "AI suggestions are not configured yet. Add Gemini keys to ai.env."}
        try:
            config = types.GenerateContentConfig(
                system_instruction=f"You are Attendance Mate's concise attendance assistant. {date_context()} Use these dates and weekdays for today/tomorrow and date questions. Read Google Sheet data through tools. Never invent values. For any write, edit, or delete, return the tool call for confirmation. Ask a short clarifying question if date, period, or target is ambiguous.",
                tools=[types.Tool(function_declarations=self._declaration())],
                temperature=0.2,
            )
            context = ""
            if history:
                context = "Recent conversation:\n" + "\n".join(
                    f"{turn['role']}: {turn['text']}" for turn in history
                ) + "\n\n"
            response = await __import__("asyncio").to_thread(client.models.generate_content, model=Config.AI_MODEL, contents=context + "Current user message:\n" + prompt, config=config)
            calls = []
            text_parts = []
            for part in response.candidates[0].content.parts:
                if part.function_call:
                    calls.append(part.function_call)
                elif part.text:
                    text_parts.append(part.text)
            if calls:
                call = calls[0]
                args = dict(call.args or {})
                if call.name in {"add_day_holidays", "add_period_exception", "edit_timetable", "remove_exception", "update_setting", "add_timetable_entry", "delete_timetable_entry"}:
                    action = {"name": call.name, "args": args}
                    action["prompt"] = prompt
                    return {"text": "I can do this in Google Sheets. Please confirm.", "pending": action}
                return {"text": self._read_tool(call.name, args)}
            return {"text": "\n".join(text_parts) or "I could not understand that request. Could you rephrase it?"}
        except Exception:
            logger.exception("Gemini request failed")
            if self.keys and _attempt + 1 < len(self.keys):
                return await self.ask(prompt, history, _attempt + 1)
            return {"text": "AI is temporarily unavailable. Please try again or use the menu."}

    def execute(self, action: dict) -> str:
        name, args = action["name"], action["args"]
        if name == "add_day_holidays":
            start, end = _action_date(args["start"]), _action_date(args["end"])
            current = start
            count = 0
            while current <= end:
                if not self.sheets.is_exception(current):
                    self.sheets.add_exception(current, "day", reason=args.get("reason", "Holiday"))
                    count += 1
                current += timedelta(days=1)
            return f"Added {count} holiday date{'s' if count != 1 else ''}."
        if name == "add_period_exception":
            target = _action_date(args["date"])
            self.sheets.add_exception(target, "period", period=str(args["period"]), reason=args.get("reason", "No class"))
            return f"Period {args['period']} on {target.isoformat()} marked as no class."
        if name == "edit_timetable":
            timetable = self.sheets._get_worksheet("Timetable")
            for index, row in enumerate(self.sheets._read_values("Timetable", timetable)[1:], start=2):
                if len(row) >= 3 and row[0].lower() == args["day"].lower() and row[1] == str(args["period"]):
                    values = [[args.get("day", row[0]), str(args["period"]), args.get("subject", row[2]), args.get("start", row[3] if len(row) > 3 else ""), args.get("end", row[4] if len(row) > 4 else "")]]
                    timetable.update(f"A{index}:E{index}", values)
                    self.sheets.clear_cache()
                    return "Timetable entry updated."
            return "I could not find that timetable entry."
        if name == "remove_exception":
            target = _action_date(args["date"])
            scope = "period" if args.get("period") else "day"
            removed = self.sheets.deactivate_exception(target, scope, args.get("period"))
            return "Exception removed." if removed else "No matching active exception found."
        if name == "update_setting":
            allowed = {"threshold", "reminders", "reminder_delay", "timezone"}
            if args.get("key") not in allowed:
                return "That setting cannot be changed by the assistant."
            self.sheets.set_setting(args["key"], str(args["value"]))
            return f"Setting {args['key']} updated."
        if name == "add_timetable_entry":
            timetable = self.sheets._get_worksheet("Timetable")
            timetable.append_row([args["day"], str(args["period"]), args["subject"], args.get("start", ""), args.get("end", "")])
            self.sheets.clear_cache()
            return "Timetable entry added."
        if name == "delete_timetable_entry":
            timetable = self.sheets._get_worksheet("Timetable")
            for index, row in enumerate(self.sheets._read_values("Timetable", timetable)[1:], start=2):
                if len(row) >= 2 and row[0].lower() == args["day"].lower() and row[1] == str(args["period"]):
                    timetable.delete_rows(index)
                    self.sheets.clear_cache()
                    return "Timetable entry deleted."
            return "I could not find that timetable entry."
        return "Unsupported action."
