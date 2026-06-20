"""Gmail SMTP dispatcher.

Renders alerts through Jinja2 templates and sends them. The Gmail App Password
is read from the macOS Keychain at send time; nothing is logged or persisted.

Two send paths:

- :meth:`send_alert` — one alert per email (high/medium severity)
- :meth:`send_digest` — one digest per email (weekly, bundles digest-severity items)
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .humanize import humanize_rule_name
from .rules.base import Alert, Severity
from .secrets import SecretKey, get_secret
from .tiers_loader import EmailConfig

log = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"


class EmailDispatcher:
    def __init__(self, cfg: EmailConfig) -> None:
        self.cfg = cfg
        self.env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.env.filters["humanize_rule"] = humanize_rule_name

    # ------------------------------------------------------------------ public

    def send_alert(self, alert: Alert, *, dry_run: bool = False) -> None:
        template = self.env.get_template("alert.html.j2")
        html = template.render(alert=alert, severity=alert.severity.value)
        subject = f"[{alert.severity.value.upper()}] {alert.title}"
        self._send(subject=subject, html=html, dry_run=dry_run)

    def send_digest(self, alerts: Iterable[Alert], *, dry_run: bool = False) -> None:
        alerts_list = list(alerts)
        if not alerts_list:
            log.info("Digest: no items to send.")
            return

        # Group by rule for readability.
        groups: dict[str, list[Alert]] = {}
        for a in alerts_list:
            groups.setdefault(a.rule, []).append(a)

        template = self.env.get_template("digest.html.j2")
        html = template.render(groups=groups, total=len(alerts_list))
        self._send(
            subject=f"Weekly portfolio digest — {len(alerts_list)} items",
            html=html,
            dry_run=dry_run,
        )

    # --------------------------------------------------------------- internals

    def _send(self, *, subject: str, html: str, dry_run: bool) -> None:
        from_address = self.cfg.from_address or get_secret(SecretKey.GMAIL_ADDRESS)
        to_address = self.cfg.to_address

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = from_address
        msg["To"] = to_address
        msg.set_content("This email contains HTML — view in a modern client.")
        msg.add_alternative(html, subtype="html")

        if dry_run:
            log.info("[DRY-RUN] Would send: %r → %s", subject, to_address)
            return

        password = get_secret(SecretKey.GMAIL_APP_PASSWORD)
        with smtplib.SMTP(self.cfg.smtp_host, self.cfg.smtp_port) as server:
            server.starttls()
            server.login(from_address, password)
            server.send_message(msg)
        log.info("Sent: %r", subject)


# ----------------------------------------------------------------------- helpers


def severity_color(s: Severity | str) -> str:
    """Used by templates to badge alerts by severity."""
    val = s.value if isinstance(s, Severity) else s
    return {"high": "#c0392b", "medium": "#d68910", "digest": "#34495e"}.get(val, "#34495e")
