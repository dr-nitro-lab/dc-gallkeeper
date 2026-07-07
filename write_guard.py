import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote


PAUSE_REASONS = {
    "ip_block",
    "write_block",
    "anti_automation",
    "rate_limit",
}


class WritePausedError(RuntimeError):
    pass


@dataclass
class WriteFailureSignal:
    category: str
    message: str
    pause_until: str
    should_pause: bool


def normalize_message(text):
    text = str(text or "")
    alert_match = re.search(r"alert\((['\"])(.*?)\1\)", text, flags=re.S)
    if alert_match:
        text = alert_match.group(2)
    reason_match = re.search(r"reason=([\"'])(.*?)\1", text, flags=re.S)
    if reason_match:
        text = reason_match.group(2)
    response_match = re.search(r"response=([\"'])(.*?)\1", text, flags=re.S)
    if response_match and len(text) > 500:
        text = response_match.group(2)
    text = unquote(text)
    return " ".join(text.replace("\\n", " ").replace("\n", " ").split())


def classify_write_failure(message):
    message = normalize_message(message)
    lowered = message.lower()
    if ("ip" in lowered or "아이피" in message) and "차단" in message:
        return "ip_block"
    if any(word in message for word in ("비정상", "올바른 방법", "비공식 확장", "자동")):
        return "anti_automation"
    if any(word in message for word in ("도배", "너무 빠", "잠시 후", "rate", "too many")):
        return "rate_limit"
    if "차단" in message and any(word in message for word in ("글쓰기", "등록", "이용", "접근")):
        return "write_block"
    if any(word in message for word in ("적합한 단어", "금지어", "금칙어")):
        return "forbidden_word"
    if message:
        return "unknown"
    return "empty"


def parse_pause_until(message, now=None):
    message = normalize_message(message)
    now = now or datetime.now()

    patterns = [
        (r"(20\d{2})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})일?\s*(\d{1,2})[:시]\s*(\d{1,2})?", True),
        (r"(\d{1,2})[.\-/월]\s*(\d{1,2})일?\s*(\d{1,2})[:시]\s*(\d{1,2})?", False),
    ]
    for pattern, has_year in patterns:
        match = re.search(pattern, message)
        if not match:
            continue
        if has_year:
            year, month, day, hour, minute = match.groups(default="0")
        else:
            month, day, hour, minute = match.groups(default="0")
            year = str(now.year)
        try:
            parsed = datetime(
                int(year),
                int(month),
                int(day),
                int(hour),
                int(minute or 0),
            )
        except ValueError:
            continue
        if parsed < now and not has_year:
            try:
                parsed = parsed.replace(year=parsed.year + 1)
            except ValueError:
                continue
        return parsed

    relative = [
        (r"(\d+)\s*일", "days"),
        (r"(\d+)\s*시간", "hours"),
        (r"(\d+)\s*분", "minutes"),
    ]
    for pattern, unit in relative:
        match = re.search(pattern, message)
        if not match:
            continue
        amount = int(match.group(1))
        if amount <= 0:
            continue
        return now + timedelta(**{unit: amount})
    return None


def analyse_write_failure(text, default_pause_minutes=30, now=None):
    message = normalize_message(text)
    category = classify_write_failure(message)
    should_pause = category in PAUSE_REASONS
    pause_until = ""
    if should_pause:
        parsed_until = parse_pause_until(message, now=now)
        if parsed_until is None:
            parsed_until = (now or datetime.now()) + timedelta(minutes=default_pause_minutes)
        pause_until = parsed_until.isoformat(timespec="seconds")
    return WriteFailureSignal(
        category=category,
        message=message,
        pause_until=pause_until,
        should_pause=should_pause,
    )


class WriteGuard:
    def __init__(self, path, default_pause_minutes=30):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.default_pause_minutes = int(default_pause_minutes)
        self._init_db()

    def _connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def _init_db(self):
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS write_guard_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action_type TEXT NOT NULL,
                    board_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL DEFAULT '',
                    pause_until TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS write_guard_state (
                    scope TEXT PRIMARY KEY,
                    paused_until TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def current_pause(self):
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT paused_until, category, message
                FROM write_guard_state
                WHERE scope = 'global'
                """
            ).fetchone()
        if row is None or not row[0]:
            return None
        try:
            paused_until = datetime.fromisoformat(row[0])
        except ValueError:
            return {
                "paused_until": row[0],
                "category": row[1],
                "message": row[2],
            }
        if paused_until <= datetime.now():
            self.clear_pause()
            return None
        return {
            "paused_until": row[0],
            "category": row[1],
            "message": row[2],
        }

    def require_allowed(self, action_type, board_id=""):
        pause = self.current_pause()
        if pause is None:
            return
        raise WritePausedError(
            "write guard paused until {} category={} message={!r}".format(
                pause["paused_until"],
                pause["category"],
                pause["message"],
            )
        )

    def record_success(self, action_type, board_id=""):
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO write_guard_events(action_type, board_id, status)
                VALUES (?, ?, 'success')
                """,
                (action_type, board_id or ""),
            )

    def record_failure(self, action_type, board_id, exc):
        signal = analyse_write_failure(
            str(exc),
            default_pause_minutes=self.default_pause_minutes,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO write_guard_events(
                    action_type, board_id, status, category, message, pause_until
                )
                VALUES (?, ?, 'failure', ?, ?, ?)
                """,
                (
                    action_type,
                    board_id or "",
                    signal.category,
                    signal.message,
                    signal.pause_until,
                ),
            )
            if signal.should_pause:
                conn.execute(
                    """
                    INSERT INTO write_guard_state(
                        scope, paused_until, category, message, updated_at
                    )
                    VALUES ('global', ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(scope)
                    DO UPDATE SET
                        paused_until = excluded.paused_until,
                        category = excluded.category,
                        message = excluded.message,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (signal.pause_until, signal.category, signal.message),
                )
        return signal

    def clear_pause(self):
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE write_guard_state
                SET paused_until = '',
                    updated_at = CURRENT_TIMESTAMP
                WHERE scope = 'global'
                """
            )
