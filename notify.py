"""Run on a schedule (every 15 minutes is plenty): queues due-soon and overdue reminders, then sends anything waiting."""
from app.db import db_cursor
from app import notifications

if __name__ == "__main__":
    with db_cursor() as cur:
        notifications.scan_due(cur)
    sent, failed = notifications.deliver_pending(200)
    print(f"sent {sent}, failed {failed}")
