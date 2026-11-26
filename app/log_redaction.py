"""Keep secrets out of the logs.

The dashboard has to put its login token (a JWT, valid for 8 hours) in the address where it cannot send a header:
the live-update socket `/ws?token=...` and the live view `<img src=".../stream?token=...">`. uvicorn logs every
address, and the console output is often saved to a file (app.log). Every log line therefore passes through
RedactSecrets:

    token=<anything>            -> token=***        (also access_token=..., in any URL or text)
    Bearer <jwt>                -> Bearer ***       (an Authorization header, if anything ever prints one)
    X-RADS-Internal: <token>    -> X-RADS-Internal: ***
    api.telegram.org/bot<token> -> api.telegram.org/bot***   (httpx logs each request URL at INFO)
    the value of SMTP_PASSWORD, RADS_JWT_SECRET, RADS_INTERNAL_TOKEN, WHATSAPP_TOKEN, TELEGRAM_BOT_TOKEN -> ***

install() attaches the filter to uvicorn's own loggers (so records are cleaned where they are made) and to every
handler of the root and uvicorn loggers (so the app's rads.* loggers and libraries such as httpx are covered).
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

SECRET = "***"
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(token=)[^&\s\"'#,;)\]}]+", re.IGNORECASE), rf"\g<1>{SECRET}"),
    (re.compile(r"(\bbearer\s+)[^\s\"',;)\]}]+", re.IGNORECASE), rf"\g<1>{SECRET}"),
    (re.compile(r"(x-rads-internal[\"']?\s*[:=]\s*[\"']?)[^\s\"',;)\]}]+", re.IGNORECASE), rf"\g<1>{SECRET}"),
    (re.compile(r"(/bot)\d+:[\w-]+"), rf"\g<1>{SECRET}"),
]
_FORMATTER = logging.Formatter()


# Settings whose values must never appear in a log line, whatever prints them (read at log time).
SECRET_SETTINGS = ("SMTP_PASSWORD", "RADS_JWT_SECRET", "RADS_INTERNAL_TOKEN", "WHATSAPP_TOKEN", "TELEGRAM_BOT_TOKEN")


def _secret_values() -> list[str]:
    values = []
    for name in SECRET_SETTINGS:
        v = os.environ.get(name, "").split(" #", 1)[0].strip()
        for form in {v, v.replace(" ", "")}:  # a Gmail app password may be written with or without its spaces
            if len(form) >= 6:  # very short values would match ordinary words
                values.append(form)
    return sorted(values, key=len, reverse=True)


def redact(text: str) -> str:
    for secret in _secret_values():
        if secret in text:
            text = text.replace(secret, SECRET)
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


def _clean_arg(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if value is None or isinstance(value, (bool, int, float, bytes)):
        return value  # keeps %d / %f formatting working (uvicorn's access line ends with the status code)
    try:
        text = str(value)  # e.g. an httpx.URL or an exception: replaced only if it actually contains a secret
    except Exception:  # noqa: BLE001
        return value
    safe = redact(text)
    return value if safe == text else safe


def _clean_args(args: Any) -> Any:
    if isinstance(args, tuple):
        return tuple(_clean_arg(a) for a in args)
    if isinstance(args, dict):
        return {k: _clean_arg(v) for k, v in args.items()}
    return args


class RedactSecrets(logging.Filter):
    """Checks the finished line, never the bare format string (redacting "Bearer %s" would break the %s).

    If cleaning the arguments one by one makes the line safe, the arguments are kept as they are, because
    uvicorn's access formatter reads them (client, method, path, version, status). Otherwise, for example when
    "Bearer" is in the format string and the token in an argument, the record gets the redacted finished line.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 – a broken log call: leave it to logging's own error handling
            return True
        if redact(message) != message:
            record.args = _clean_args(record.args)
            try:
                safe = redact(record.getMessage()) == record.getMessage()
            except Exception:  # noqa: BLE001
                safe = False
            if not safe:
                record.msg, record.args = redact(message), None
        if record.exc_info and not record.exc_text:
            record.exc_text = redact(_FORMATTER.formatException(record.exc_info))  # handlers reuse exc_text
        return True


FILTER = RedactSecrets()
UVICORN_LOGGERS = ("uvicorn", "uvicorn.access", "uvicorn.error")


def install() -> None:
    """Attach the filter (safe to call again: e.g. at import and after logging.basicConfig at startup)."""
    for name in ("uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        if FILTER not in logger.filters:
            logger.addFilter(FILTER)
    for name in ("", *UVICORN_LOGGERS):
        for handler in logging.getLogger(name).handlers:
            if FILTER not in handler.filters:
                handler.addFilter(FILTER)
