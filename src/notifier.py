import os
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def send_email(new_jobs: list):
    user = os.environ.get("GMAIL_USER", "")
    pwd  = os.environ.get("GMAIL_APP_PASSWORD", "")
    to   = os.environ.get("NOTIFY_EMAIL") or user

    if not user or not pwd:
        logger.error("Set GMAIL_USER and GMAIL_APP_PASSWORD in GitHub Secrets.")
        return
    if not new_jobs:
        logger.info("No new jobs — skipping email.")
        return

    html    = _build_html(new_jobs)
    plain   = _build_plain(new_jobs)
    subject = f"[Job Alert] {len(new_jobs)} new embedded ML / AI posting(s)"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = user
    msg["To"]      = to
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
            srv.login(user, pwd)
            srv.sendmail(user, to, msg.as_string())
        logger.info(f"Email sent to {to} with {len(new_jobs)} job(s).")
    except Exception as e:
        logger.error(f"Email failed: {e}")


def _build_html(new_jobs: list) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    by_source: dict[str, list] = {}
    for j in new_jobs:
        by_source.setdefault(j.get("source", "other"), []).append(j)

    html = (
        '<html><body style="font-family:Arial,sans-serif;max-width:820px;margin:0 auto;color:#222">'
        f'<h2 style="color:#2c3e50">Job Alert \u2014 {len(new_jobs)} new posting(s)</h2>'
        f'<p style="color:#888;font-size:13px">Detected at {ts}</p><hr/>'
    )

    for source, jobs in by_source.items():
        label = source.replace(":", " \u203a ").replace("_", " ").title()
        html += (
            f'<h3 style="color:#2980b9;border-bottom:1px solid #eee;padding-bottom:4px">'
            f'{label} ({len(jobs)})</h3>'
            '<table style="width:100%;border-collapse:collapse;font-size:14px">'
            '<tr style="background:#f0f4f8">'
            '<th align="left" style="padding:8px">Title</th>'
            '<th align="left" style="padding:8px">Company</th>'
            '<th align="left" style="padding:8px">Location</th>'
            '</tr>'
        )
        for i, j in enumerate(jobs):
            bg = "#fff" if i % 2 == 0 else "#f8fafc"
            html += (
                f'<tr style="background:{bg}">'
                f'<td style="padding:8px">'
                f'<a href="{j.get("url","")}" style="color:#2980b9">{j.get("title","")}</a>'
                f'</td>'
                f'<td style="padding:8px;color:#555">{j.get("company","")}</td>'
                f'<td style="padding:8px;color:#888">{j.get("location","")}</td>'
                f'</tr>'
            )
        html += "</table><br/>"

    html += (
        '<p style="color:#ccc;font-size:11px">'
        'Sent by your GitHub Actions job monitor (Embedded ML / Edge AI Europe)'
        '</p></body></html>'
    )
    return html


def _build_plain(new_jobs: list) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    plain = f"Job Alert - {len(new_jobs)} new posting(s) at {ts}\n\n"
    for j in new_jobs:
        plain += (
            f"- {j.get('title','')} @ {j.get('company','')} ({j.get('location','')})\n"
            f"  {j.get('url','')}\n\n"
        )
    return plain
