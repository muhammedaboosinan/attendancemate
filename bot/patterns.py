"""
Pattern matching system without AI dependency.
Handles natural language queries and executes corresponding actions.
"""
import re
import logging
from datetime import date, timedelta
from bot.time_utils import now, today

logger = logging.getLogger(__name__)


class PatternMatcher:
    """Pattern matching system for natural language commands."""
    
    def __init__(self, sheets):
        self.sheets = sheets
        self._init_built_in_patterns()
    
    def _init_built_in_patterns(self):
        """Initialize built-in pattern rules."""
        self.patterns = {
            # Next period queries
            r"next\s+period": self._handle_next_period,
            r"what'?s\s+next\s+period": self._handle_next_period,
            r"what'?s\s+next\s+class": self._handle_next_period,
            r"coming\s+class": self._handle_next_period,
            r"upcoming\s+class": self._handle_next_period,
            
            # No class - single day
            r"no\s+class\s+(?:for\s+)?(?:today|tomorrow)": self._handle_no_class_single,
            r"no\s+classes?\s+(?:for\s+)?(?:today|tomorrow)": self._handle_no_class_single,
            r"no\s+class\s+(?:for\s+)?(\d{4}-\d{1,2}-\d{1,2})": self._handle_no_class_date,
            r"no\s+class\s+(?:for\s+)?(\d{1,2}-\d{1,2}-\d{2,4})": self._handle_no_class_date,
            
            # No class - date range
            r"no\s+class\s+(?:for\s+)?(\d{4}-\d{1,2}-\d{1,2})\s*(?:to|-|until)\s*(\d{4}-\d{1,2}-\d{1,2})": self._handle_no_class_range,
            r"no\s+class\s+(?:for\s+)?(\d{1,2}-\d{1,2}-\d{2,4})\s*(?:to|-|until)\s*(\d{1,2}-\d{1,2}-\d{2,4})": self._handle_no_class_range,
            
            # Attendance queries
            r"what'?s\s+my\s+attendance": self._handle_attendance_summary,
            r"attendance\s+summary": self._handle_attendance_summary,
            r"show\s+attendance": self._handle_attendance_summary,
            r"my\s+attendance": self._handle_attendance_summary,
            
            # Timetable queries
            r"show\s+timetable": self._handle_show_timetable,
            r"what'?s\s+my\s+timetable": self._handle_show_timetable,
            r"class\s+schedule": self._handle_show_timetable,
            r"when\s+do\s+classes?\s+start": self._handle_class_start_times,
            r"what\s+time\s+do\s+classes?\s+start": self._handle_class_start_times,
        }
    
    def match(self, prompt: str) -> dict | None:
        """Match prompt against patterns and return action."""
        prompt_lower = prompt.lower().strip()
        
        for pattern, handler in self.patterns.items():
            match = re.search(pattern, prompt_lower)
            if match:
                action = handler(prompt, match)
                if action:
                    action["prompt"] = prompt
                    return action
        
        # Check custom patterns from Google Sheets
        return self._check_custom_patterns(prompt_lower)
    
    def _check_custom_patterns(self, prompt: str) -> dict | None:
        """Check custom patterns from Prompts_Actions sheet."""
        try:
            patterns_sheet = self.sheets._get_worksheet("Prompts_Actions")
            if not patterns_sheet:
                return None
            
            rows = self.sheets._read_values("Prompts_Actions", patterns_sheet)
            if len(rows) <= 1:
                return None
            
            import json
            for row in rows[1:]:
                if len(row) >= 3 and row[2].lower() == "true":
                    pattern = row[0].lower()
                    if self._match_pattern(pattern, prompt):
                        try:
                            action = json.loads(row[1])
                            action["prompt"] = prompt
                            return action
                        except json.JSONDecodeError:
                            logger.warning(f"Invalid action in pattern: {row[0]}")
        except Exception as e:
            logger.warning(f"Error checking custom patterns: {e}")
        
        return None
    
    def _match_pattern(self, pattern: str, prompt: str) -> bool:
        """Check if prompt matches pattern template."""
        # Simple pattern matching with wildcards
        pattern_regex = pattern.replace("*", ".*")
        return re.match(pattern_regex, prompt) is not None
    
    def _handle_next_period(self, prompt: str, match) -> dict:
        """Handle next period queries."""
        current_time = now().strftime("%H:%M")
        timetable = self.sheets.get_timetable()
        period_times = self.sheets.get_all_period_times()
        
        if not timetable:
            return {"type": "message", "text": "No timetable available."}
        
        current_day = now().strftime("%A")
        if current_day not in timetable:
            return {"type": "message", "text": f"No classes scheduled for {current_day}."}
        
        try:
            current_hour, current_minute = map(int, current_time.split(":"))
            current_minutes = current_hour * 60 + current_minute
        except:
            return {"type": "message", "text": "Invalid time format."}
        
        day_schedule = timetable[current_day]
        for period_num in sorted(day_schedule.keys(), key=int):
            subject = day_schedule[period_num]
            period_time = period_times.get(period_num)
            if period_time:
                try:
                    start_hour, start_minute = map(int, period_time[0].split(":"))
                    start_minutes = start_hour * 60 + start_minute
                    
                    if start_minutes > current_minutes:
                        return {
                            "type": "message",
                            "text": f"Your next class is Period {period_num} - {subject} at {period_time[0]}"
                        }
                except:
                    continue
        
        return {"type": "message", "text": "No more classes scheduled for today."}
    
    def _handle_no_class_single(self, prompt: str, match) -> dict:
        """Handle no class for today/tomorrow."""
        date_str = match.group(0)
        target_date = self._parse_relative_date(date_str)
        
        if not target_date:
            return {"type": "message", "text": f"Could not parse date: {date_str}"}
        
        return {
            "type": "action",
            "action": "add_day_holidays",
            "args": {"start": target_date.isoformat(), "end": target_date.isoformat(), "reason": "No class"},
            "description": f"Mark {date_str} as no class"
        }
    
    def _handle_no_class_date(self, prompt: str, match) -> dict:
        """Handle no class for specific date."""
        date_str = match.group(1)
        target_date = self._parse_date(date_str)
        
        if not target_date:
            return {"type": "message", "text": f"Could not parse date: {date_str}"}
        
        return {
            "type": "action",
            "action": "add_day_holidays",
            "args": {"start": target_date.isoformat(), "end": target_date.isoformat(), "reason": "No class"},
            "description": f"Mark {date_str} as no class"
        }
    
    def _handle_no_class_range(self, prompt: str, match) -> dict:
        """Handle no class for date range."""
        start_str = match.group(1)
        end_str = match.group(2)
        
        start_date = self._parse_date(start_str)
        end_date = self._parse_date(end_str)
        
        if not start_date or not end_date:
            return {"type": "message", "text": f"Could not parse date range: {start_str} to {end_str}"}
        
        return {
            "type": "action",
            "action": "add_day_holidays",
            "args": {"start": start_date.isoformat(), "end": end_date.isoformat(), "reason": "No class"},
            "description": f"Mark {start_str} to {end_str} as no class"
        }
    
    def _handle_attendance_summary(self, prompt: str, match) -> dict:
        """Handle attendance summary queries."""
        stats = self.sheets.get_attendance_stats()
        percentage = self.sheets.calculate_percentage(stats)
        
        return {
            "type": "message",
            "text": f"Your overall attendance: {percentage:.1f}%\nPresent: {stats['present']}\nAbsent: {stats['absent']}\nNo class: {stats['no_class']}"
        }
    
    def _handle_show_timetable(self, prompt: str, match) -> dict:
        """Handle timetable queries."""
        timetable = self.sheets.get_timetable()
        period_times = self.sheets.get_all_period_times()
        
        if not timetable:
            return {"type": "message", "text": "No timetable available."}
        
        response = "📚 Your weekly schedule:\n\n"
        for day in sorted(timetable.keys()):
            day_schedule = timetable[day]
            response += f"📅 {day}:\n"
            for period_num in sorted(day_schedule.keys(), key=int):
                subject = day_schedule[period_num]
                times = period_times.get(period_num)
                if times:
                    response += f"  ⏰ Period {period_num}: {times[0]} - {times[1]} → {subject}\n"
                else:
                    response += f"  📚 Period {period_num}: {subject}\n"
            response += "\n"
        
        return {"type": "message", "text": response.strip()}
    
    def _handle_class_start_times(self, prompt: str, match) -> dict:
        """Handle class start time queries."""
        timetable = self.sheets.get_timetable()
        period_times = self.sheets.get_all_period_times()
        
        if not timetable:
            return {"type": "message", "text": "No timetable available."}
        
        response = "📅 First class start times:\n\n"
        for day in sorted(timetable.keys()):
            day_schedule = timetable[day]
            if "1" in day_schedule:
                subject = day_schedule["1"]
                times = period_times.get("1")
                if times:
                    response += f"📅 {day}: {times[0]} - {subject}\n"
                else:
                    response += f"📅 {day}: {subject}\n"
        
        return {"type": "message", "text": response.strip()}
    
    def _parse_relative_date(self, date_str: str) -> date | None:
        """Parse relative dates like today, tomorrow."""
        date_str = date_str.lower().strip()
        if date_str == "today":
            return today()
        elif date_str == "tomorrow":
            return today() + timedelta(days=1)
        return None
    
    def _parse_date(self, date_str: str) -> date | None:
        """Parse date string in various formats."""
        date_str = date_str.strip()
        
        # Try relative dates first
        relative = self._parse_relative_date(date_str)
        if relative:
            return relative
        
        # Try various date formats
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d/%m/%y"):
            try:
                from datetime import datetime
                return datetime.strptime(date_str, fmt).date()
            except ValueError:
                continue
        
        return None