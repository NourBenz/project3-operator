import os
import smtplib
from email.message import EmailMessage


class NotificationError(Exception):
    pass


class OwnerNotifier:
    def __init__(self, *, recipients=None):
        raw = recipients if recipients is not None else os.environ.get("OWNER_EMAILS", "")
        self.recipients = tuple(value.strip() for value in raw.split(",") if value.strip())

    def notify(self, *, repository, issue_number, action, summary):
        if not self.recipients:
            raise NotificationError("OWNER_EMAILS is not configured")
        host = os.environ.get("SMTP_HOST")
        port = int(os.environ.get("SMTP_PORT", "587"))
        username = os.environ.get("SMTP_USERNAME")
        password = os.environ.get("SMTP_PASSWORD")
        sender = os.environ.get("SMTP_FROM", username or "")
        if not host or not sender:
            raise NotificationError("SMTP_HOST and SMTP_FROM are not configured")
        message = EmailMessage()
        message["Subject"] = f"GitHub issue change: #{issue_number}"
        message["From"] = sender
        message["To"] = ", ".join(self.recipients)
        message.set_content(
            f"An approved change was made in {repository}.\n\n"
            f"Issue: #{issue_number}\nAction: {action}\nResult: {summary}\n"
        )
        with smtplib.SMTP(host, port, timeout=20) as connection:
            connection.starttls()
            if username and password:
                connection.login(username, password)
            connection.send_message(message)
