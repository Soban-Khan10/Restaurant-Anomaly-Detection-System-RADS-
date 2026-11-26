"""Who may call each route: the access check every route actually has (read from the app's dependencies) must match
this table, built from "Roles (who may do what)" in docs/RADS_B_App_Server_Dashboard_ClaudeCode.md:

    View dashboard, incidents, evidence, live view ...... everyone logged in
    Review incidents ..................................... Admin, Manager
    Generate (and list, download) reports ................ Admin, Manager
    Cameras, zones, rules (change) ....................... Admin, Technical (reading them: everyone, read-only)
    Users, email alert settings, maintenance ............. Admin
    Pause / resume monitoring ............................ everyone (team decision)

A new route that is not in the table fails test_every_route_is_in_the_table, so its roles have to be decided."""
from __future__ import annotations

import pytest
from fastapi.routing import APIRoute

from app import deps
from app.main import app
from app_helpers import ctx  # noqa: F401  (ctx is a fixture)

ADMIN, MANAGER, TECH = "Admin", "Manager", "Technical"
LOGIN = "any logged-in user"
PUBLIC = "public"
INTERNAL = "worker only (localhost + X-RADS-Internal)"

EXPECTED: dict[tuple[str, str], str | frozenset[str]] = {
    ("POST", "/api/auth/login"): PUBLIC,
    ("GET", "/api/auth/me"): LOGIN,
    # viewing: everyone
    ("GET", "/api/incidents"): LOGIN,
    ("GET", "/api/incidents/summary"): LOGIN,
    ("GET", "/api/incidents/{incident_id}"): LOGIN,
    ("GET", "/api/evidence/{evidence_id}/image"): LOGIN,
    ("GET", "/api/alerts"): LOGIN,
    ("GET", "/api/hazards"): LOGIN,
    ("GET", "/api/cameras"): LOGIN,                      # rtsp_url (camera password) only for Admin/Technical
    ("GET", "/api/cameras/{camera_id}"): LOGIN,
    ("GET", "/api/cameras/{camera_id}/snapshot"): LOGIN,  # + ROLES_LIVE_VIEW inside (test below)
    ("GET", "/api/cameras/{camera_id}/stream"): LOGIN,
    ("GET", "/api/zones"): LOGIN,
    ("GET", "/api/zones/{zone_id}"): LOGIN,
    ("GET", "/api/rules"): LOGIN,                         # Detection rules page: read-only for Managers
    ("GET", "/api/rules/{rule_id}"): LOGIN,
    ("GET", "/api/monitoring/status"): LOGIN,
    ("GET", "/api/pauses"): LOGIN,
    # review
    ("PATCH", "/api/incidents/{incident_id}/review"): frozenset({ADMIN, MANAGER}),
    # reports
    ("POST", "/api/reports"): frozenset({ADMIN, MANAGER}),
    ("GET", "/api/reports"): frozenset({ADMIN, MANAGER}),
    ("GET", "/api/reports/{report_id}/download"): frozenset({ADMIN, MANAGER}),
    # cameras, zones, rules: changes
    ("POST", "/api/cameras"): frozenset({ADMIN, TECH}),
    ("PATCH", "/api/cameras/{camera_id}"): frozenset({ADMIN, TECH}),
    ("DELETE", "/api/cameras/{camera_id}"): frozenset({ADMIN, TECH}),
    ("POST", "/api/zones"): frozenset({ADMIN, TECH}),
    ("PATCH", "/api/zones/{zone_id}"): frozenset({ADMIN, TECH}),
    ("DELETE", "/api/zones/{zone_id}"): frozenset({ADMIN, TECH}),
    ("POST", "/api/rules"): frozenset({ADMIN, TECH}),
    ("PATCH", "/api/rules/{rule_id}"): frozenset({ADMIN, TECH}),
    ("DELETE", "/api/rules/{rule_id}"): frozenset({ADMIN, TECH}),
    # pause monitoring: every role
    ("POST", "/api/pauses"): frozenset({ADMIN, MANAGER, TECH}),
    ("POST", "/api/pauses/{pause_id}/end"): frozenset({ADMIN, MANAGER, TECH}),
    # Admin only
    ("GET", "/api/users"): frozenset({ADMIN}),
    ("POST", "/api/users"): frozenset({ADMIN}),
    ("GET", "/api/users/{user_id}"): frozenset({ADMIN}),
    ("PATCH", "/api/users/{user_id}"): frozenset({ADMIN}),
    ("DELETE", "/api/users/{user_id}"): frozenset({ADMIN}),
    ("GET", "/api/alerts/status"): frozenset({ADMIN}),
    ("POST", "/api/alerts/test-email"): frozenset({ADMIN}),
    ("POST", "/api/admin/maintenance/run"): frozenset({ADMIN}),
    # the worker
    ("POST", "/internal/incidents/new"): INTERNAL,
    ("POST", "/internal/incidents/closed"): INTERNAL,
    ("POST", "/internal/heartbeat"): INTERNAL,
}


def _access(dependant) -> str | frozenset[str]:
    """The strongest check among a route's dependencies (a role check beats a plain login check)."""
    found: list[str | frozenset[str]] = []

    def walk(d):
        for sub in d.dependencies:
            call = sub.call
            if getattr(call, "__qualname__", "").startswith("require_role.<locals>.checker"):
                found.append(frozenset(r.value for r in call.__closure__[0].cell_contents))
            elif call in (deps.get_current_user, deps.get_current_user_header_or_query):
                found.append(LOGIN)
            elif call is deps.require_internal:
                found.append(INTERNAL)
            else:
                walk(sub)

    walk(dependant)
    roles = [f for f in found if isinstance(f, frozenset)]
    return roles[0] if roles else (found[0] if found else PUBLIC)


def _routes():
    """(method, path) -> access, for every HTTP route of the app (included routers flattened)."""
    def flat(routes):
        for r in routes:
            if hasattr(r, "effective_candidates"):  # an included router, in newer FastAPI versions
                yield from r.effective_candidates()
                yield from r.effective_low_priority_routes()
            else:
                yield r

    out = {}
    for r in flat(app.routes):
        dependant, methods = getattr(r, "dependant", None), getattr(r, "methods", None)
        if dependant is None or not methods or not r.path.startswith(("/api", "/internal")):
            continue  # /ws (checked in its handler, tested in app_ws_test) and the docs pages
        for m in set(methods) - {"HEAD"}:
            out.setdefault((m, r.path), _access(dependant))
    assert out, "no routes found: has FastAPI's routing changed?"
    return out


def test_every_route_is_in_the_table():
    actual = _routes()
    assert sorted(set(actual) - set(EXPECTED)) == [], "new route(s): decide who may call them and add them above"
    assert sorted(set(EXPECTED) - set(actual)) == [], "route(s) in the table no longer exist"


@pytest.mark.parametrize("route", sorted(EXPECTED), ids=lambda r: f"{r[0]} {r[1]}")
def test_route_has_the_roles_from_the_table(route):
    assert _routes()[route] == EXPECTED[route]


# A few of them for real, through HTTP (the rest is the same require_role check):

@pytest.mark.parametrize("method, url, allowed", [
    ("GET", "/api/reports", {"admin", "manager"}),
    ("GET", "/api/users", {"admin"}),
    ("GET", "/api/alerts/status", {"admin"}),
    ("PATCH", "/api/incidents/1/review", {"admin", "manager"}),
])
def test_roles_over_http(ctx, method, url, allowed):
    ctx.add_incident()
    body = {"json": {"status": "Acknowledged"}} if method == "PATCH" else {}
    assert ctx.client.request(method, url, **body).status_code == 401
    for user in ("admin", "manager", "tech"):
        code = ctx.client.request(method, url, headers=ctx.headers(user), **body).status_code
        assert (code == 403) == (user not in allowed), (user, code)


def test_live_view_role_check_inside_the_route(ctx):
    """snapshot/stream take the token from the URL, so their role check is in the handler: ROLES_LIVE_VIEW
    (Managers: the open team question; for now every role may open the live view)."""
    from app.auth import ROLES_LIVE_VIEW
    assert {r.value for r in ROLES_LIVE_VIEW} == {ADMIN, MANAGER, TECH}
    assert ctx.client.get("/api/cameras/1/snapshot").status_code == 401
    for user in ("admin", "manager", "tech"):
        code = ctx.client.get(f"/api/cameras/1/snapshot?token={ctx.token(user)}").status_code
        assert code not in (401, 403), (user, code)  # allowed (the worker stand-in has no picture: not 2xx)
