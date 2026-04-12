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

    has_new = bool(new_jobs)
    subject = (
        f"[Job Alert] {len(new_jobs)} new Schengen ML posting(s)"
        if has_new else
        "[Job Monitor] No new postings — bot is running"
    )

    html  = _build_html(new_jobs)
    plain = _build_plain(new_jobs)

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
        logger.info(
            f"Email sent to {to} — "
            f"{'%d new job(s)' % len(new_jobs) if has_new else 'no new jobs'}."
        )
    except Exception as e:
        logger.error(f"Email failed: {e}")


def _build_html(new_jobs: list) -> str:
    ts      = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    has_new = bool(new_jobs)

    html = (
        '<html><body style="font-family:Arial,sans-serif;max-width:840px;'
        'margin:0 auto;color:#222;padding:16px">'
    )

    if has_new:
        html += (
            f'<h2 style="color:#27ae60;margin-bottom:4px">'
            f'&#x2705; {len(new_jobs)} new Schengen ML posting(s)</h2>'
            f'<p style="color:#888;font-size:13px;margin-top:0">Detected at {ts}</p>'
            f'<p style="color:#555;font-size:13px">'
            f'All jobs below are <strong>NEW</strong> since the last scan '
            f'and are in the Schengen area (or explicitly Europe-remote).</p>'
            f'<hr/>'
        )

        by_source: dict[str, list] = {}
        for j in new_jobs:
            by_source.setdefault(j.get("source", "other"), []).append(j)

        for source, jobs in by_source.items():
            label = source.replace(":", " \u203a ").replace("_", " ").title()
            html += (
                f'<h3 style="color:#2980b9;border-bottom:2px solid #3498db;'
                f'padding-bottom:4px">{label} ({len(jobs)} new)</h3>'
                '<table style="width:100%;border-collapse:collapse;font-size:14px;margin-bottom:16px">'
                '<tr style="background:#eaf4fb">'
                '<th align="left" style="padding:9px;border-bottom:1px solid #ccc">Title</th>'
                '<th align="left" style="padding:9px;border-bottom:1px solid #ccc">Company</th>'
                '<th align="left" style="padding:9px;border-bottom:1px solid #ccc">Location</th>'
                '</tr>'
            )
            for i, j in enumerate(jobs):
                bg = "#fff" if i % 2 == 0 else "#f4f9ff"
                html += (
                    f'<tr style="background:{bg}">'
                    f'<td style="padding:9px;border-bottom:1px solid #eee">'
                    f'<span style="background:#27ae60;color:#fff;font-size:10px;'
                    f'padding:2px 5px;border-radius:3px;margin-right:6px">NEW</span>'
                    f'<a href="{j.get("url","")}" style="color:#2980b9;font-weight:500">'
                    f'{j.get("title","")}</a></td>'
                    f'<td style="padding:9px;border-bottom:1px solid #eee;color:#555">'
                    f'{j.get("company","")}</td>'
                    f'<td style="padding:9px;border-bottom:1px solid #eee;color:#888">'
                    f'{j.get("location","")}</td>'
                    f'</tr>'
                )
            html += "</table>"
    else:
        html += (
            f'<h2 style="color:#7f8c8d;margin-bottom:4px">'
            f'&#x1F50D; No new postings this scan</h2>'
            f'<p style="color:#888;font-size:13px;margin-top:0">Checked at {ts}</p>'
            f'<hr/>'
            f'<p style="color:#555;font-size:14px">'
            f'All monitored companies were scanned. No new Schengen ML / Embedded AI '
            f'postings found since the last check.<br><br>'
            f'The bot is running normally every 2 hours.</p>'
            f'<p style="color:#aaa;font-size:13px">'
            f'<strong>Watching:</strong> Axelera AI, Innatera, Bosch, Infineon, Fraunhofer, '
            f'Prophesee, Plumerai, Mistral AI, NXP, Nordic Semi, TomTom, Green Fusion '
            f'+ Arbeitnow &amp; Remotive discovery feeds.</p>'
        )

    html += (
        '<hr/><p style="color:#bbb;font-size:11px;margin-top:16px">'
        'Embedded ML / Edge AI job monitor &mdash; Schengen Europe only &mdash; '
        'GitHub Actions every 2 hours'
        '</p></body></html>'
    )
    return html


def _build_plain(new_jobs: list) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if not new_jobs:
        return (
            f"Job Monitor — No new postings at {ts}\n\n"
            "All monitored Schengen companies checked. No new ML/Embedded AI roles found.\n"
            "The bot is running normally.\n"
        )
    plain = f"Job Alert — {len(new_jobs)} NEW Schengen ML posting(s) at {ts}\n\n"
    for j in new_jobs:
        plain += (
            f"[NEW] {j.get('title','')} @ {j.get('company','')} "
            f"({j.get('location','')})\n"
            f"      {j.get('url','')}\n\n"
        )
    return plain
