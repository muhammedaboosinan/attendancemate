"""
Google Sheets integration for attendance tracking.
Handles reading and writing data to/from Google Sheets.
"""
import json
from datetime import datetime, date
from typing import Optional
import logging
import gspread
from google.oauth2.service_account import Credentials
from bot.config import Config

logger = logging.getLogger(__name__)

class SheetsManager:
    """Manages all Google Sheets operations."""
    
    SCOPES = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    
    def __init__(self):
        self.client = None
        self.spreadsheet = None
        self._connect()
    
    def _connect(self):
        """Initialize connection to Google Sheets."""
        try:
            creds = Credentials.from_service_account_file(
                Config.GOOGLE_CREDENTIALS_PATH,
                scopes=self.SCOPES
            )
            self.client = gspread.authorize(creds)
            
            if Config.GOOGLE_SHEET_ID:
                # Use existing spreadsheet
                try:
                    self.spreadsheet = self.client.open_by_key(Config.GOOGLE_SHEET_ID)
                    logger.info(f"Connected to existing spreadsheet: {self.spreadsheet.title}")
                except gspread.SpreadsheetNotFound:
                    raise ConnectionError(
                        f"Spreadsheet with ID {Config.GOOGLE_SHEET_ID} not found. "
                        "Check the ID or share the sheet with your service account."
                    )
                except Exception as e:
                    raise ConnectionError(f"Failed to open spreadsheet: {e}")
            else:
                # Try to create new spreadsheet
                try:
                    self.spreadsheet = self.client.create("Attendance Bot")
                    Config.GOOGLE_SHEET_ID = self.spreadsheet.id
                    logger.info(f"Created new spreadsheet: {self.spreadsheet.url}")
                    logger.warning("Set GOOGLE_SHEET_ID in token.env to reuse this sheet")
                except gspread.APIError as e:
                    if "quota" in str(e).lower() or "403" in str(e):
                        raise ConnectionError(
                            "Google Drive storage quota exceeded. "
                            "Please either:\n"
                            "1. Set GOOGLE_SHEET_ID in token.env to use an existing sheet\n"
                            "2. Free up space in the service account's Drive\n"
                            "3. Use a different Google account"
                        )
                    raise
                    
        except ConnectionError:
            raise
        except Exception as e:
            raise ConnectionError(f"Failed to connect to Google Sheets: {e}")
    
    def _get_worksheet(self, sheet_name: str, create_if_missing: bool = True) -> Optional[gspread.Worksheet]:
        """Get a worksheet by name, creating it if needed."""
        try:
            return self.spreadsheet.worksheet(sheet_name)
        except gspread.WorksheetNotFound:
            if create_if_missing:
                return self.spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=20)
            return None
    
    def initialize_sheets(self):
        """Initialize all required sheets with headers."""
        # Timetable sheet
        timetable = self._get_worksheet("Timetable")
        if timetable:
            rows = timetable.get_all_values()
            has_data = any(any(cell.strip() for cell in row) for row in rows)
            if not has_data:
                timetable.append_row(["Day", "Period", "Subject", "Start", "End"])
                timetable_data = [
                    ["Monday", "1", "MINOR 1", "09:30", "10:30"],
                    ["Monday", "2", "ECO (VNV)", "10:30", "11:30"],
                    ["Monday", "3", "MDC", "11:30", "12:30"],
                    ["Monday", "4", "AEC-LE", "13:30", "14:30"],
                    ["Monday", "5", "MINOR 2", "14:30", "15:30"],
                    ["Tuesday", "1", "ECO (BKU)", "09:30", "10:30"],
                    ["Tuesday", "2", "MINOR 2", "10:30", "11:30"],
                    ["Tuesday", "3", "AEC-EL", "11:30", "12:30"],
                    ["Tuesday", "4", "MINOR 1", "13:30", "14:30"],
                    ["Tuesday", "5", "MINOR 1", "14:30", "15:30"],
                    ["Wednesday", "1", "MINOR 2", "09:30", "10:30"],
                    ["Wednesday", "2", "MDC", "10:30", "11:30"],
                    ["Wednesday", "3", "AEC-LE", "11:30", "12:30"],
                    ["Wednesday", "4", "AEC-EL", "13:30", "14:30"],
                    ["Wednesday", "5", "ECO (BKU)", "14:30", "15:30"],
                    ["Thursday", "1", "MINOR 1", "09:30", "10:30"],
                    ["Thursday", "2", "ECO", "10:30", "11:30"],
                    ["Thursday", "3", "ECO (VNV)", "11:30", "12:30"],
                    ["Thursday", "4", "AEC-EL", "13:30", "14:30"],
                    ["Thursday", "5", "AEC-LE", "14:30", "15:30"],
                    ["Friday", "1", "MDC", "09:30", "10:30"],
                    ["Friday", "2", "MINOR 1", "10:30", "11:30"],
                    ["Friday", "3", "AEC-EL", "11:30", "12:30"],
                    ["Friday", "4", "AEC-LE", "13:30", "14:30"],
                    ["Friday", "5", "MINOR 2", "14:30", "15:30"],
                ]
                for row in timetable_data:
                    timetable.append_row(row)
        
        # Attendance_Log sheet
        attendance_log = self._get_worksheet("Attendance_Log")
        if attendance_log:
            rows = attendance_log.get_all_values()
            if not any(any(cell.strip() for cell in row) for row in rows):
                attendance_log.append_row([
                    "Date", "Day", "Period", "Raw Subject", "Normalized Subject",
                    "Status", "Timestamp", "Note"
                ])
        
        # Subject_Map sheet
        subject_map = self._get_worksheet("Subject_Map")
        if subject_map:
            rows = subject_map.get_all_values()
            if not any(any(cell.strip() for cell in row) for row in rows):
                subject_map.append_row(["Raw Subject", "Normalized Subject"])
                subject_data = [
                    ["ECO", "ECO"],
                    ["ECO (VNV)", "ECO"],
                    ["ECO (BKU)", "ECO"],
                    ["MINOR 1", "MINOR 1"],
                    ["MINOR 2", "MINOR 2"],
                    ["MDC", "MDC"],
                    ["AEC-LE", "AEC-LE"],
                    ["AEC-EL", "AEC-EL"],
                ]
                for row in subject_data:
                    subject_map.append_row(row)
        
        # Exceptions sheet
        exceptions = self._get_worksheet("Exceptions")
        if exceptions:
            rows = exceptions.get_all_values()
            if not any(any(cell.strip() for cell in row) for row in rows):
                exceptions.append_row(["Date", "Scope", "Period", "Reason", "Active"])
        
        # Settings sheet
        settings = self._get_worksheet("Settings")
        if settings:
            rows = settings.get_all_values()
            if not any(any(cell.strip() for cell in row) for row in rows):
                settings.append_row(["Key", "Value"])
    
    def get_subject_map(self) -> dict:
        """Get subject normalization mapping."""
        subject_map = self._get_worksheet("Subject_Map")
        if not subject_map:
            return {}
        
        rows = subject_map.get_all_values()
        if len(rows) < 2:
            return {}
        
        mapping = {}
        for row in rows[1:]:  # Skip header
            if len(row) >= 2:
                mapping[row[0]] = row[1]
        return mapping
    
    def normalize_subject(self, raw_subject: str) -> str:
        """Normalize subject name using the mapping."""
        mapping = self.get_subject_map()
        normalized = mapping.get(raw_subject, raw_subject).strip()
        if normalized.upper() in {"ECO (VNV)", "ECO (BKU)"}:
            return "ECO"
        return normalized.upper()
    
    def get_timetable(self) -> dict:
        """Get the complete timetable."""
        timetable = self._get_worksheet("Timetable")
        if not timetable:
            return {}
        
        rows = timetable.get_all_values()
        if len(rows) < 2:
            return {}
        
        schedule = {}
        for row in rows[1:]:  # Skip header
            if len(row) >= 3:
                day = row[0]
                period = row[1]
                subject = row[2]
                
                if day not in schedule:
                    schedule[day] = {}
                schedule[day][period] = subject
        
        return schedule
    
    def get_class_for_period(self, day: str, period: str) -> Optional[str]:
        """Get the subject for a specific day and period."""
        timetable = self.get_timetable()
        if day in timetable and period in timetable[day]:
            return timetable[day][period]
        return None
    
    def is_exception(self, check_date: date, period: Optional[str] = None) -> bool:
        """Check if a date or date+period is marked as an exception."""
        exceptions = self._get_worksheet("Exceptions")
        if not exceptions:
            return False
        
        rows = exceptions.get_all_values()
        if len(rows) < 2:
            return False
        
        date_str = check_date.strftime("%Y-%m-%d")
        
        for row in rows[1:]:  # Skip header
            if len(row) >= 5:
                row_date = row[0]
                scope = row[1]
                row_period = row[2]
                active = row[4].lower() == "true"
                
                if not active:
                    continue
                
                if row_date == date_str:
                    if scope == "day":
                        return True
                    elif scope == "period" and period and row_period == period:
                        return True
        
        return False
    
    def add_exception(self, check_date: date, scope: str, period: Optional[str] = None, reason: str = "") -> bool:
        """Add an exception row."""
        exceptions = self._get_worksheet("Exceptions")
        if not exceptions:
            return False
        date_str = check_date.strftime("%Y-%m-%d")
        exceptions.append_row([date_str, scope, period or "", reason, "TRUE"])
        return True
    
    def get_exceptions_for_date(self, check_date: date):
        """Return active exception rows for a date."""
        exceptions = self._get_worksheet("Exceptions")
        if not exceptions:
            return []
        rows = exceptions.get_all_values()
        if len(rows) < 2:
            return []
        date_str = check_date.strftime("%Y-%m-%d")
        result = []
        for row in rows[1:]:
            if len(row) >= 5 and row[0] == date_str and row[4].lower() == "true":
                result.append(row)
        return result
    
    def get_days_with_exceptions(self):
        """Return dates that have active day-scope exceptions (holidays)."""
        exceptions = self._get_worksheet("Exceptions")
        if not exceptions:
            return []
        rows = exceptions.get_all_values()
        if len(rows) < 2:
            return []
        days = set()
        for row in rows[1:]:
            if len(row) >= 5 and row[1] == "day" and row[4].lower() == "true":
                days.add(row[0])
        return sorted(days)
    
    def get_total_scheduled_classes(self) -> int:
        """Count all scheduled timetable rows (excluding header)."""
        timetable = self._get_worksheet("Timetable")
        if not timetable:
            return 0
        rows = timetable.get_all_values()
        return max(len(rows) - 1, 0)
    
    def get_classes_taken(self) -> int:
        """Count timetable rows minus active exception periods."""
        timetable = self._get_worksheet("Timetable")
        if not timetable:
            return 0
        rows = timetable.get_all_values()
        if len(rows) < 2:
            return 0
        scheduled = 0
        for row in rows[1:]:
            if len(row) >= 3:
                scheduled += 1
        exceptions = self._get_worksheet("Exceptions")
        if not exceptions:
            return scheduled
        exc_rows = exceptions.get_all_values()
        cancelled = 0
        for row in exc_rows[1:]:
            if len(row) >= 5 and row[1] == "period" and row[4].lower() == "true":
                cancelled += 1
        return max(scheduled - cancelled, 0)
    
    def log_attendance(self, check_date: date, day: str, period: str, 
                       raw_subject: str, status: str, note: str = "") -> bool:
        """Log attendance entry. Returns True if successful."""
        attendance_log = self._get_worksheet("Attendance_Log")
        if not attendance_log:
            return False
        
        # Check for duplicate entries
        date_str = check_date.strftime("%Y-%m-%d")
        rows = attendance_log.get_all_values()
        
        for i, row in enumerate(rows[1:], start=2):  # Skip header
            if len(row) >= 5:
                if (row[0] == date_str and row[2] == period and 
                    row[3] == raw_subject):
                    # Update existing entry
                    normalized = self.normalize_subject(raw_subject)
                    timestamp = datetime.now().isoformat()
                    attendance_log.update(f'F{i}:H{i}', [[status, timestamp, note]])
                    return True
        
        # Add new entry
        normalized = self.normalize_subject(raw_subject)
        timestamp = datetime.now().isoformat()
        attendance_log.append_row([
            date_str, day, period, raw_subject, normalized, status, timestamp, note
        ])
        return True

    def get_attendance_entry(self, check_date: date, period: str) -> Optional[dict]:
        """Return the latest log entry for a date and period."""
        attendance_log = self._get_worksheet("Attendance_Log")
        if not attendance_log:
            return None
        date_str = check_date.strftime("%Y-%m-%d")
        matches = []
        for row in attendance_log.get_all_values()[1:]:
            if len(row) >= 8 and row[0] == date_str and row[2] == str(period):
                matches.append(row)
        if not matches:
            return None
        row = matches[-1]
        return {
            "date": row[0], "day": row[1], "period": row[2],
            "raw_subject": row[3], "subject": self.normalize_subject(row[4]),
            "status": row[5].lower(), "timestamp": row[6], "note": row[7]
        }

    def get_setting(self, key: str, default: str = "") -> str:
        settings = self._get_worksheet("Settings")
        if not settings:
            return default
        for row in settings.get_all_values()[1:]:
            if len(row) >= 2 and row[0].strip().lower() == key.lower():
                return row[1]
        return default

    def set_setting(self, key: str, value: str) -> bool:
        settings = self._get_worksheet("Settings")
        if not settings:
            return False
        for index, row in enumerate(settings.get_all_values()[1:], start=2):
            if len(row) >= 1 and row[0].strip().lower() == key.lower():
                settings.update(f"B{index}", [[str(value)]])
                return True
        settings.append_row([key, str(value)])
        return True

    def deactivate_exception(self, check_date: date, scope: str, period: Optional[str] = None) -> bool:
        exceptions = self._get_worksheet("Exceptions")
        if not exceptions:
            return False
        date_str = check_date.strftime("%Y-%m-%d")
        for index, row in enumerate(exceptions.get_all_values()[1:], start=2):
            if (len(row) >= 5 and row[0] == date_str and row[1] == scope and
                    (scope != "period" or row[2] == str(period)) and
                    row[4].lower() == "true"):
                exceptions.update(f"E{index}", [["FALSE"]])
                return True
        return False
    
    def get_attendance_stats(self, start_date: Optional[date] = None,
                             end_date: Optional[date] = None) -> dict:
        """Calculate attendance statistics."""
        attendance_log = self._get_worksheet("Attendance_Log")
        if not attendance_log:
            return self._empty_stats()
        
        rows = attendance_log.get_all_values()
        if len(rows) < 2:
            return self._empty_stats()
        
        stats = {
            "total": 0,
            "present": 0,
            "absent": 0,
            "no_class": 0,
            "by_subject": {},
            "by_period": {}
        }
        
        for row in rows[1:]:  # Skip header
            if len(row) >= 6:
                try:
                    entry_date = date.fromisoformat(row[0])
                except ValueError:
                    continue
                if start_date and entry_date < start_date:
                    continue
                if end_date and entry_date > end_date:
                    continue
                status = row[5].lower()
                subject = self.normalize_subject(row[4])
                period = row[2]

                stats[status] = stats.get(status, 0) + 1
                if status in {"present", "absent"}:
                    stats["total"] += 1
                
                # By subject
                if subject not in stats["by_subject"]:
                    stats["by_subject"][subject] = {"present": 0, "absent": 0, "no_class": 0, "total": 0}
                if status in {"present", "absent"}:
                    stats["by_subject"][subject]["total"] += 1
                stats["by_subject"][subject][status] = stats["by_subject"][subject].get(status, 0) + 1
                
                # By period
                if period not in stats["by_period"]:
                    stats["by_period"][period] = {"present": 0, "absent": 0, "no_class": 0, "total": 0}
                if status in {"present", "absent"}:
                    stats["by_period"][period]["total"] += 1
                stats["by_period"][period][status] = stats["by_period"][period].get(status, 0) + 1
        
        return stats
    
    def _empty_stats(self) -> dict:
        """Return empty statistics structure."""
        return {
            "total": 0,
            "present": 0,
            "absent": 0,
            "no_class": 0,
            "by_subject": {},
            "by_period": {}
        }
    
    def calculate_percentage(self, stats: dict) -> float:
        """Calculate overall attendance percentage."""
        actual_classes = stats["present"] + stats["absent"]
        if actual_classes == 0:
            return 0.0
        
        return (stats["present"] / actual_classes) * 100
    
    def calculate_subject_percentage(self, subject_stats: dict) -> float:
        """Calculate attendance percentage for a subject."""
        actual_classes = subject_stats["present"] + subject_stats["absent"]
        if actual_classes == 0:
            return 0.0
        
        return (subject_stats["present"] / actual_classes) * 100
    
    def can_miss_before_threshold(self, stats: dict, threshold: float = 75.0) -> int:
        """Calculate how many more classes can be missed before dropping below threshold."""
        current_present = stats["present"]
        current_absent = stats["absent"]
        actual_classes = current_present + current_absent
        
        if actual_classes == 0:
            return -1  # No data
        
        current_percentage = (current_present / actual_classes) * 100
        
        if current_percentage < threshold:
            return 0  # Already below threshold
        
        # Find max additional absences
        for can_miss in range(1, 100):
            new_total = actual_classes + can_miss
            new_present = current_present
            new_percentage = (new_present / new_total) * 100
            if new_percentage < threshold:
                return can_miss - 1
        
        return -1  # Can miss many classes