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

def _extract_variables_from_prompt(prompt: str, action: dict) -> dict:
    """Extract variable names and values from prompt based on action parameters."""
    variables = {}
    
    # Extract dates
    date_matches = re.findall(r"\b(?:today|tomorrow|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\b", prompt.lower())
    if date_matches:
        variables["date"] = "DATE"
    
    # Extract periods
    period_matches = re.findall(r"\b(?:period|class)\s*(?:number\s*)?(\d+)\b", prompt.lower())
    if period_matches:
        variables["period"] = "PERIOD"
    
    # Extract date ranges
    range_matches = re.findall(r"\b(?:from|to|-)\s*(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\b", prompt.lower())
    if len(range_matches) >= 2:
        variables["start_date"] = "START_DATE"
        variables["end_date"] = "END_DATE"
    
    # Extract days
    day_matches = re.findall(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", prompt.lower())
    if day_matches:
        variables["day"] = "DAY"
    
    # Extract subjects
    subject_keywords = ["minor", "eco", "mdc", "aec", "math", "physics", "chemistry"]
    for subject in subject_keywords:
        if subject in prompt.lower():
            variables["subject"] = "SUBJECT"
            break
    
    return variables

def _create_pattern_template(prompt: str, variables: dict) -> str:
    """Create a pattern template by replacing variable values with placeholders."""
    template = prompt.lower()
    
    # Create a map of original values to placeholders
    replacements = {}
    
    # Replace dates with placeholder
    date_matches = re.findall(r"\b(?:today|tomorrow|\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\b", template)
    for i, match in enumerate(date_matches):
        if "date" in variables:
            placeholder = "{date}" if i == 0 else "{date2}"
            replacements[match] = placeholder
    
    # Replace periods with placeholder
    period_matches = re.findall(r"\b(?:period|class)\s*(?:number\s*)?(\d+)\b", template)
    for match in period_matches:
        if "period" in variables:
            replacements[f"period {match}"] = "{period}"
            replacements[f"class {match}"] = "{period}"
            replacements[match] = "{period}"
    
    # Replace days with placeholder
    day_matches = re.findall(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", template)
    for match in day_matches:
        if "day" in variables:
            replacements[match] = "{day}"
    
    # Replace subjects with placeholder
    subject_keywords = ["minor", "eco", "mdc", "aec"]
    for subject in subject_keywords:
        if subject in template and "subject" in variables:
            replacements[subject] = "{subject}"
    
    # Apply replacements
    for original, placeholder in replacements.items():
        template = template.replace(original, placeholder)
    
    return " ".join(template.split())


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
        """Remember action with variable extraction for flexible pattern matching."""
        variables = _extract_variables_from_prompt(prompt, action)
        template = _create_pattern_template(prompt, variables)
        
        # Create action with variable placeholders
        action_with_placeholders = {
            "name": action["name"],
            "args": {}
        }
        
        # Replace variable values in action args with placeholders
        for key, value in action["args"].items():
            if key in variables:
                action_with_placeholders["args"][key] = f"{{{key}}}"
            else:
                action_with_placeholders["args"][key] = value
        
        self.sheets.save_prompt_pattern(template, action_with_placeholders, variables)

    def _pattern_action(self, prompt: str) -> dict | None:
        normalized = re.sub(r"\s+", " ", prompt.lower()).strip()
        action = self.sheets.get_prompt_pattern(normalized)
        if action:
            # Check if this is a pattern match (not exact match)
            if isinstance(action, dict) and "name" in action:
                # Add the original prompt for context
                action["prompt"] = prompt
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
            types.FunctionDeclaration(name="attendance_trends", description="Analyze attendance trends over time, optionally for a specific subject or time period.", parameters=types.Schema(type="OBJECT", properties={"subject": types.Schema(type="STRING"), "days": types.Schema(type="INTEGER")})),
            types.FunctionDeclaration(name="attendance_prediction", description="Predict future attendance based on historical patterns.", parameters=types.Schema(type="OBJECT", properties={"subject": types.Schema(type="STRING")})),
            types.FunctionDeclaration(name="get_next_period", description="Get the next class period based on current time and day. Returns the subject and time.", parameters=types.Schema(type="OBJECT", properties={"current_time": types.Schema(type="STRING", description="Current time in HH:MM format (e.g., 13:02)")})),
            types.FunctionDeclaration(name="get_timetable", description="Get the full timetable or timetable for a specific day.", parameters=types.Schema(type="OBJECT", properties={"day": types.Schema(type="STRING", description="Day of the week (e.g., Monday, Tuesday)")})),
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
        
        if name == "attendance_trends":
            subject = args.get("subject", "").strip().upper()
            days = args.get("days", 30)
            from datetime import timedelta
            end_date = local_today()
            start_date = end_date - timedelta(days=days)
            stats = self.sheets.get_attendance_stats(start_date, end_date)
            
            if subject:
                subject_stats = stats["by_subject"].get(subject, {"present": 0, "absent": 0, "no_class": 0})
                percentage = self.sheets.calculate_subject_percentage(subject_stats)
                return f"{subject} {days}-day trend: {percentage:.1f}%\nPresent: {subject_stats['present']}\nAbsent: {subject_stats['absent']}\nNo class: {subject_stats.get('no_class', 0)}"
            
            overall_percentage = self.sheets.calculate_percentage(stats)
            return f"Overall {days}-day trend: {overall_percentage:.1f}%\nPresent: {stats['present']}\nAbsent: {stats['absent']}\nNo class: {stats['no_class']}"
        
        if name == "attendance_prediction":
            subject = args.get("subject", "").strip().upper()
            stats = self.sheets.get_attendance_stats()
            
            if subject:
                subject_stats = stats["by_subject"].get(subject, {"present": 0, "absent": 0, "no_class": 0})
                total = subject_stats['present'] + subject_stats['absent']
                if total == 0:
                    return f"No attendance data available for {subject} to make predictions."
                
                current_rate = subject_stats['present'] / total
                prediction = "Likely to maintain good attendance" if current_rate > 0.8 else "May need attention" if current_rate > 0.6 else "At risk of low attendance"
                return f"{subject} prediction: {prediction}\nCurrent rate: {current_rate:.1%}"
            
            total = stats['present'] + stats['absent']
            if total == 0:
                return "No attendance data available to make predictions."
            
            current_rate = stats['present'] / total
            prediction = "Likely to maintain good attendance" if current_rate > 0.8 else "May need attention" if current_rate > 0.6 else "At risk of low attendance"
            return f"Overall prediction: {prediction}\nCurrent rate: {current_rate:.1%}"
        
        if name == "get_next_period":
            current_time = args.get("current_time", "")
            timetable = self.sheets.get_timetable()
            if not timetable:
                return "No timetable available."
            
            from datetime import datetime
            current_day = local_today().strftime("%A")
            
            if current_day not in timetable:
                return f"No classes scheduled for {current_day}."
            
            # Parse current time
            try:
                current_hour, current_minute = map(int, current_time.split(":"))
                current_minutes = current_hour * 60 + current_minute
            except:
                return "Invalid time format. Please use HH:MM format (e.g., 13:02)."
            
            # Get period times from timetable with start/end times
            day_schedule = timetable[current_day]
            periods = self.sheets.get_all_period_times()
            
            for period_num in sorted(day_schedule.keys(), key=int):
                subject = day_schedule[period_num]
                period_time = periods.get(period_num)
                if period_time:
                    try:
                        start_hour, start_minute = map(int, period_time[0].split(":"))
                        start_minutes = start_hour * 60 + start_minute
                        
                        if start_minutes > current_minutes:
                            return f"Next period: Period {period_num} - {subject} at {period_time[0]}"
                    except:
                        continue
            
            return "No more classes scheduled for today."
        
        if name == "get_timetable":
            day = args.get("day", "").capitalize()
            timetable = self.sheets.get_timetable()
            
            if not timetable:
                return "No timetable available."
            
            if day:
                if day in timetable:
                    day_schedule = timetable[day]
                    response = f"Timetable for {day}:\n"
                    for period_num in sorted(day_schedule.keys(), key=int):
                        subject = day_schedule[period_num]
                        response += f"Period {period_num}: {subject}\n"
                    return response.strip()
                else:
                    return f"No timetable found for {day}. Available days: {', '.join(timetable.keys())}"
            else:
                # Return full timetable summary
                response = "Full timetable:\n\n"
                for day in sorted(timetable.keys()):
                    day_schedule = timetable[day]
                    response += f"{day}:\n"
                    for period_num in sorted(day_schedule.keys(), key=int):
                        subject = day_schedule[period_num]
                        response += f"  Period {period_num}: {subject}\n"
                    response += "\n"
                return response.strip()
        
        if name == "sheet_read":
            sheet_name = args.get("sheet", "").lower()
            if sheet_name == "timetable":
                timetable = self.sheets.get_timetable()
                if not timetable:
                    return "No timetable available."
                response = "Timetable:\n\n"
                for day in sorted(timetable.keys()):
                    day_schedule = timetable[day]
                    response += f"{day}:\n"
                    for period_num in sorted(day_schedule.keys(), key=int):
                        subject = day_schedule[period_num]
                        response += f"  Period {period_num}: {subject}\n"
                    response += "\n"
                return response.strip()
            elif sheet_name == "attendance_log":
                stats = self.sheets.get_attendance_stats()
                return f"Overall: {self.sheets.calculate_percentage(stats):.1f}%\nPresent: {stats['present']}\nAbsent: {stats['absent']}\nNo class: {stats['no_class']}"
            elif sheet_name == "exceptions":
                exceptions = self.sheets.get_exceptions_for_date(local_today())
                if not exceptions:
                    return "No exceptions for today."
                return f"Exceptions for today: {len(exceptions)}"
            elif sheet_name == "settings":
                settings = {key: self.sheets.get_setting(key) for key in ("threshold", "reminders", "reminder_delay")}
                return f"Settings: {settings}"
            else:
                return f"Sheet '{sheet_name}' is not available through this assistant."
        
        return json.dumps({"error": "This action changes data and must be confirmed."})

    async def ask(self, prompt: str, history: list[dict] | None = None, _attempt: int = 0) -> dict:
        direct = self._pattern_action(prompt) or self._holiday_action(prompt)
        if direct:
            direct["prompt"] = prompt
            action_desc = self._describe_action(direct)
            return {"text": f"I found a saved pattern for: {action_desc}\n\nPlease confirm to execute.", "pending": direct}
        client = self._client()
        if not client:
            return {"text": "AI suggestions are not configured yet. Add Gemini keys to ai.env."}
        try:
            config = types.GenerateContentConfig(
                system_instruction=f"""You are Attendance Mate, a friendly and helpful attendance assistant. {date_context()} 

IMPORTANT INSTRUCTIONS:
1. Be conversational and helpful - talk like a human assistant, not a robot
2. When users ask about timetable, use the get_next_period or get_timetable tools
3. For "next period" questions, ask for current time if not provided, then use get_next_period
4. For attendance questions, use attendance_summary tool
5. Never return raw JSON data - always format responses as natural text
6. Keep responses concise but friendly
7. If you need to read Google Sheet data, use the available tools
8. For any write, edit, or delete actions, return the tool call for confirmation
9. Ask clarifying questions if date, period, or target is ambiguous

Example responses:
- "Your next class is Period 2 - ECO at 10:30"
- "You have MDC next at 11:30"
- "Your overall attendance is 85.5%"

Never show raw JSON or technical details to the user.""",
                tools=[types.Tool(function_declarations=self._declaration())],
                temperature=0.7,
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
                # Write operations that require confirmation
                write_operations = {"add_day_holidays", "add_period_exception", "edit_timetable", "remove_exception", "update_setting", "add_timetable_entry", "delete_timetable_entry"}
                if call.name in write_operations:
                    action = {"name": call.name, "args": args}
                    action["prompt"] = prompt
                    action_desc = self._describe_action(action)
                    return {"text": f"I can do this in Google Sheets: {action_desc}\n\nPlease confirm to execute.", "pending": action}
                # Read-only operations - execute directly
                return {"text": self._read_tool(call.name, args)}
            return {"text": "\n".join(text_parts) or "I could not understand that request. Could you rephrase it?"}
        except Exception:
            logger.exception("Gemini request failed")
            if self.keys and _attempt + 1 < len(self.keys):
                return await self.ask(prompt, history, _attempt + 1)
            return {"text": "AI is temporarily unavailable. Please try again or use the menu."}
    
    def _describe_action(self, action: dict) -> str:
        """Generate a human-readable description of the action."""
        name = action.get("name", "unknown")
        args = action.get("args", {})
        
        descriptions = {
            "add_day_holidays": f"Mark holidays from {args.get('start', '?')} to {args.get('end', '?')}",
            "add_period_exception": f"Mark period {args.get('period', '?')} on {args.get('date', '?')} as no class",
            "edit_timetable": f"Edit timetable for {args.get('day', '?')} period {args.get('period', '?')}",
            "remove_exception": f"Remove exception for {args.get('date', '?')}",
            "update_setting": f"Update setting {args.get('key', '?')} to {args.get('value', '?')}",
            "add_timetable_entry": f"Add timetable entry for {args.get('day', '?')} period {args.get('period', '?')}",
            "delete_timetable_entry": f"Delete timetable entry for {args.get('day', '?')} period {args.get('period', '?')}"
        }
        
        return descriptions.get(name, f"Execute {name}")

    def execute(self, action: dict, chat_id: int = None) -> str:
        """Execute action with undo support and audit logging."""
        name, args = action["name"], action["args"]
        undo_data = None
        
        try:
            if name == "add_day_holidays":
                start, end = _action_date(args["start"]), _action_date(args["end"])
                current = start
                count = 0
                added_dates = []
                while current <= end:
                    if not self.sheets.is_exception(current):
                        self.sheets.add_exception(current, "day", reason=args.get("reason", "Holiday"))
                        added_dates.append(current.isoformat())
                        count += 1
                    current += timedelta(days=1)
                undo_data = {"name": "remove_day_holidays", "args": {"dates": added_dates}}
                result = f"Added {count} holiday date{'s' if count != 1 else ''}."
                
            elif name == "add_period_exception":
                target = _action_date(args["date"])
                undo_data = {"name": "remove_period_exception", "args": {"date": target.isoformat(), "period": str(args["period"])}}
                self.sheets.add_exception(target, "period", period=str(args["period"]), reason=args.get("reason", "No class"))
                result = f"Period {args['period']} on {target.isoformat()} marked as no class."
                
            elif name == "edit_timetable":
                timetable = self.sheets._get_worksheet("Timetable")
                for index, row in enumerate(self.sheets._read_values("Timetable", timetable)[1:], start=2):
                    if len(row) >= 3 and row[0].lower() == args["day"].lower() and row[1] == str(args["period"]):
                        old_values = row[:5] if len(row) >= 5 else row
                        values = [[args.get("day", row[0]), str(args["period"]), args.get("subject", row[2]), args.get("start", row[3] if len(row) > 3 else ""), args.get("end", row[4] if len(row) > 4 else "")]]
                        timetable.update(f"A{index}:E{index}", values)
                        undo_data = {"name": "edit_timetable", "args": {"day": row[0], "period": row[1], "subject": row[2], "start": row[3] if len(row) > 3 else "", "end": row[4] if len(row) > 4 else ""}}
                        self.sheets.clear_cache()
                        result = "Timetable entry updated."
                        break
                else:
                    return "I could not find that timetable entry."
                    
            elif name == "remove_exception":
                target = _action_date(args["date"])
                scope = "period" if args.get("period") else "day"
                removed = self.sheets.deactivate_exception(target, scope, args.get("period"))
                result = "Exception removed." if removed else "No matching active exception found."
                
            elif name == "update_setting":
                allowed = {"threshold", "reminders", "reminder_delay", "timezone"}
                if args.get("key") not in allowed:
                    return "That setting cannot be changed by the assistant."
                old_value = self.sheets.get_setting(args["key"])
                undo_data = {"name": "update_setting", "args": {"key": args["key"], "value": old_value}}
                self.sheets.set_setting(args["key"], str(args["value"]))
                result = f"Setting {args['key']} updated."
                
            elif name == "add_timetable_entry":
                timetable = self.sheets._get_worksheet("Timetable")
                timetable.append_row([args["day"], str(args["period"]), args["subject"], args.get("start", ""), args.get("end", "")])
                undo_data = {"name": "delete_timetable_entry", "args": {"day": args["day"], "period": str(args["period"])}}
                self.sheets.clear_cache()
                result = "Timetable entry added."
                
            elif name == "delete_timetable_entry":
                timetable = self.sheets._get_worksheet("Timetable")
                for index, row in enumerate(self.sheets._read_values("Timetable", timetable)[1:], start=2):
                    if len(row) >= 2 and row[0].lower() == args["day"].lower() and row[1] == str(args["period"]):
                        old_values = row[:5] if len(row) >= 5 else row
                        undo_data = {"name": "add_timetable_entry", "args": {"day": row[0], "period": row[1], "subject": row[2], "start": row[3] if len(row) > 3 else "", "end": row[4] if len(row) > 4 else ""}}
                        timetable.delete_rows(index)
                        self.sheets.clear_cache()
                        result = "Timetable entry deleted."
                        break
                else:
                    return "I could not find that timetable entry."
            else:
                return "Unsupported action."
            
            # Log action for audit
            if chat_id:
                self.sheets.log_ai_action(chat_id, name, args, result, "success")
            
            # Store undo data for last action
            if undo_data and chat_id:
                self._store_undo_data(chat_id, undo_data)
                result += " You can undo this within 5 minutes."
            
            return result
            
        except Exception as e:
            logger.exception(f"AI action execution failed: {name}")
            if chat_id:
                self.sheets.log_ai_action(chat_id, name, args, str(e), "failed")
            return f"Failed to execute action: {str(e)}"
    
    def _store_undo_data(self, chat_id: int, undo_data: dict):
        """Store undo data with timestamp for time-limited undo."""
        undo_sheet = self.sheets._get_worksheet("AI_Undo_Stack")
        if undo_sheet:
            import json
            from datetime import datetime, timedelta
            expiry = (datetime.now() + timedelta(minutes=5)).isoformat()
            undo_sheet.append_row([str(chat_id), json.dumps(undo_data), expiry])
            self.sheets.clear_cache()
    
    def get_undo_action(self, chat_id: int) -> dict | None:
        """Get and remove the most recent undo action for a user."""
        undo_sheet = self.sheets._get_worksheet("AI_Undo_Stack")
        if not undo_sheet:
            return None
        
        from datetime import datetime
        rows = self.sheets._read_values("AI_Undo_Stack", undo_sheet)
        
        for index in range(len(rows), 1, -1):
            row = rows[index - 1]
            if len(row) >= 3 and row[0] == str(chat_id):
                try:
                    expiry = datetime.fromisoformat(row[2])
                    if datetime.now() < expiry:
                        import json
                        undo_data = json.loads(row[1])
                        undo_sheet.delete_rows(index)
                        self.sheets.clear_cache()
                        return undo_data
                    else:
                        # Expired, remove it
                        undo_sheet.delete_rows(index)
                        self.sheets.clear_cache()
                except:
                    continue
        
        return None
