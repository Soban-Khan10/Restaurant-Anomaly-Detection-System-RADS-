"""No login token, internal token or bot token in the logs (app.log is the server's console output).

The log calls below are the ones uvicorn 0.54 makes (protocols/http/h11_impl.py, protocols/websockets/*), formatted
with uvicorn's own formatters, so the redaction is checked on the lines that really end up in app.log.
"""
from __future__ import annotations

import io
import logging

import httpx
import pytest
from uvicorn.logging import AccessFormatter, DefaultFormatter

from app import log_redaction

JWT = "abc.def.ghi"


@pytest.fixture
def out():
    """Handlers like uvicorn's (access + default) and the app's root handler, all writing to one buffer."""
    buf = io.StringIO()
    access = logging.StreamHandler(buf)
    access.setFormatter(AccessFormatter('%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
                                        use_colors=False))
    default = logging.StreamHandler(buf)
    default.setFormatter(DefaultFormatter("%(levelprefix)s %(message)s", use_colors=False))
    root = logging.StreamHandler(buf)
    root.setFormatter(logging.Formatter("%(levelname)s: %(name)s: %(message)s"))
    setup = [("uvicorn.access", access), ("uvicorn", default), ("", root)]
    saved = {n: (logging.getLogger(n).level, logging.getLogger(n).propagate) for n, _ in setup}
    for name, handler in setup:
        logger = logging.getLogger(name)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").propagate = False  # as uvicorn configures it
    logging.getLogger("uvicorn").propagate = False
    log_redaction.install()
    yield buf
    for name, handler in setup:
        logger = logging.getLogger(name)
        logger.removeHandler(handler)
        logger.setLevel(saved[name][0])
        logger.propagate = saved[name][1]


def test_ws_token_in_the_access_log_is_hidden(out):
    logging.getLogger("uvicorn.access").info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:52000", "GET",
                                             f"/ws?token={JWT}", "1.1", 101)
    line = out.getvalue()
    assert '"GET /ws?token=*** HTTP/1.1" 101' in line and JWT not in line


def test_ws_token_in_the_websocket_lines_is_hidden(out):
    err = logging.getLogger("uvicorn.error")
    err.info('%s - "WebSocket %s" [accepted]', "127.0.0.1:52000", f"/ws?token={JWT}")
    err.info('%s - "WebSocket %s" 403', "127.0.0.1:52001", f"/ws?token={JWT}")
    text = out.getvalue()
    assert text.count('"WebSocket /ws?token=***"') == 2 and JWT not in text


def test_live_view_token_in_the_address_is_hidden_and_the_rest_kept(out):
    logging.getLogger("uvicorn.access").info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:52000", "GET",
                                             f"/api/cameras/1/stream?token={JWT}&_=2", "1.1", 200)
    assert "/api/cameras/1/stream?token=***&_=2" in out.getvalue() and JWT not in out.getvalue()


def test_bearer_and_internal_token_in_app_logs_are_hidden(out):
    log = logging.getLogger("rads.app.test")
    log.warning("request headers: %s", {"Authorization": f"Bearer {JWT}", "X-RADS-Internal": "internal-s3cret"})
    log.warning("Authorization: Bearer %s", JWT)
    text = out.getvalue()
    assert "Bearer ***" in text and "'X-RADS-Internal': '***'" in text
    assert JWT not in text and "internal-s3cret" not in text


def test_telegram_bot_token_in_httpx_request_lines_is_hidden(out):
    url = httpx.URL("https://api.telegram.org/bot123456:AAH-xyz_0987/sendMessage")
    logging.getLogger("httpx").info('HTTP Request: %s %s "%s %d %s"', "POST", url, "HTTP/1.1", 200, "OK")
    text = out.getvalue()
    assert "https://api.telegram.org/bot***/sendMessage" in text and "AAH-xyz_0987" not in text


def test_tokens_in_tracebacks_are_hidden(out):
    try:
        raise RuntimeError(f"GET /ws?token={JWT} failed")
    except RuntimeError:
        logging.getLogger("rads.app.test").exception("stream broke")
    assert "token=***" in out.getvalue() and JWT not in out.getvalue()


def test_ordinary_lines_are_unchanged(out):
    logging.getLogger("rads.app.internal").info("incident %s opened (%s %s), sent to %d dashboard(s)",
                                                42, "H-01", "Camera 1", 2)
    logging.getLogger("uvicorn.access").info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:52000", "GET",
                                             "/api/incidents?page=2", "1.1", 200)
    text = out.getvalue()
    assert "incident 42 opened (H-01 Camera 1), sent to 2 dashboard(s)" in text
    assert '"GET /api/incidents?page=2 HTTP/1.1" 200' in text


def test_install_twice_adds_the_filter_once(out):
    log_redaction.install()
    log_redaction.install()
    assert logging.getLogger("uvicorn.access").filters.count(log_redaction.FILTER) == 1
    assert all(h.filters.count(log_redaction.FILTER) == 1 for h in logging.getLogger().handlers)
