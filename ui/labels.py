"""Short display labels; stored customer evidence stays unchanged."""
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def customer_name(value):
    return re.sub(r"^synthetic\s+", "", str(value or "Customer"), flags=re.IGNORECASE)


def ist_time(value, missing="—"):
    """Format stored timestamps for display without changing the stored instant."""
    if value is None or value == "":
        return missing
    try:
        timestamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        # Legacy timestamps without an offset are stored in UTC too.
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    except (ValueError, TypeError, OverflowError):
        return "Unknown time"
