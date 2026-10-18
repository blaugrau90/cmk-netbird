"""Tests for the NetBird special agent (parsing, resolution, errors, token safety)."""

import argparse
import io
import json
import subprocess
import sys
import threading
import urllib.error
from datetime import UTC, datetime
from email.message import Message
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from conftest import FIXTURES, REPO_ROOT, load_module

agent = load_module("netbird/libexec/agent_netbird", "agent_netbird")

SECRET = "nbp_SUPERSECRET_TOKEN_VALUE_12345"
NOW = datetime(2026, 10, 18, 12, 0, 0, tzinfo=UTC).timestamp()


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


# ---------------------------------------------------------------- timestamps
@pytest.mark.parametrize(
    "value, expected",
    [
        ("2023-05-04T12:45:25.9723616Z", datetime(2023, 5, 4, 12, 45, 25, 972361, tzinfo=UTC)),
        ("2023-05-04T12:45:25Z", datetime(2023, 5, 4, 12, 45, 25, tzinfo=UTC)),
        ("2023-05-04T12:45:25.5Z", datetime(2023, 5, 4, 12, 45, 25, 500000, tzinfo=UTC)),
        ("2023-05-04T14:45:25.123456789+02:00", datetime(2023, 5, 4, 12, 45, 25, 123456, tzinfo=UTC)),
        ("2023-05-04T12:45:25", datetime(2023, 5, 4, 12, 45, 25, tzinfo=UTC)),
    ],
)
def test_parse_timestamp(value, expected):
    assert agent.parse_timestamp(value) == pytest.approx(expected.timestamp(), abs=1e-3)


@pytest.mark.parametrize("value", [None, "", "  ", "0001-01-01T00:00:00Z", "garbage", 17, "2023-13-45T00:00:00Z"])
def test_parse_timestamp_never_or_invalid(value):
    assert agent.parse_timestamp(value) is None


# ------------------------------------------------------------------ versions
@pytest.mark.parametrize(
    "value, expected",
    [
        ("0.60.2", (0, 60, 2)),
        ("v0.60.2", (0, 60, 2)),
        ("0.60.2-rc1", (0, 60, 2)),
        ("0.60", (0, 60, 0)),
        ("1.2.3+build5", (1, 2, 3)),
    ],
)
def test_parse_version(value, expected):
    assert agent.parse_version(value) == expected


@pytest.mark.parametrize("value", [None, "", "development", "unknown", "0.60.2abc", 5])
def test_parse_version_unparsable(value):
    assert agent.parse_version(value) is None


# ------------------------------------------------------------- fake transport
class FakeResponse:
    def __init__(self, body):
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, body=b"", headers=None):
    msg = Message()
    for key, value in (headers or {}).items():
        msg[key] = value
    return urllib.error.HTTPError("http://x", code, "err", msg, io.BytesIO(body))


class Seq(list):
    """Responses/exceptions handed out one per request."""


class FakeOpener:
    """Maps URL path -> response | exception | Seq of those (consumed in order)."""

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def open(self, req, timeout=None):
        self.requests.append(req)
        path = req.full_url.split("://", 1)[1].split("/", 1)[1]
        path = "/" + path
        entry = self.routes.get(path, http_error(404))
        if isinstance(entry, Seq):
            entry = entry.pop(0)
        if isinstance(entry, BaseException):
            raise entry
        return FakeResponse(entry)


def make_client(routes, sleeps=None):
    opener = FakeOpener(routes)
    sleeps = sleeps if sleeps is not None else []
    return agent.Client("https://nb.example/", SECRET, 5, opener, sleep=sleeps.append), opener


def args(**overrides):
    base = dict(include_users=False, reference_source="peers", reference_version=None)
    base.update(overrides)
    return argparse.Namespace(**base)


def good_routes():
    return {
        "/api/users?service_user=true": [u for u in fixture("users.json") if u["is_service_user"]],
        "/api/users": fixture("users.json"),
        "/api/users/svc1/tokens": fixture("tokens_svc1.json"),
        "/api/users/svc2/tokens": fixture("tokens_svc2.json"),
        "/api/users/usr1/tokens": fixture("tokens_usr1.json"),
        "/api/users/current": fixture("users.json")[0],
        "/api/peers": fixture("peers.json"),
        "/api/networks": fixture("networks.json"),
        "/api/networks/routers": fixture("routers.json"),
        "/api/instance/version": {
            "dashboard_available_version": "2.28.0",
            "management_available_version": "0.64.1",
            "management_current_version": "0.64.0",
            "management_update_available": True,
        },
    }


def sections(lines):
    result, current = {}, None
    for line in lines:
        if line.startswith("<<<"):
            current = line[3:].split(":")[0]
            result[current] = []
        else:
            result[current].append(json.loads(line))
    return result


# ---------------------------------------------------------------- happy path
def test_full_run_emits_all_sections():
    client, opener = make_client(good_routes())
    out = sections(agent.run(args(), client, now=NOW))

    assert set(out) == {"netbird_api", "netbird_tokens", "netbird_networks", "netbird_reference_version",
                       "netbird_instance_version"}
    assert out["netbird_api"][0]["ok"] is True
    assert out["netbird_api"][0]["counts"]["tokens"] == 3
    paths = [r.full_url for r in opener.requests]
    assert "https://nb.example/api/users?service_user=true" in paths
    assert not any("usr1" in p for p in paths)  # regular users excluded by default


def test_include_users_reads_all_user_tokens():
    client, _ = make_client(good_routes())
    out = sections(agent.run(args(include_users=True), client, now=NOW))
    names = {t["name"] for t in out["netbird_tokens"]}
    assert "laptop" in names
    laptop = next(t for t in out["netbird_tokens"] if t["name"] == "laptop")
    assert laptop["is_service_user"] is False
    assert laptop["last_used_ts"] is None


def test_token_timestamps_normalised():
    client, _ = make_client(good_routes())
    out = sections(agent.run(args(), client, now=NOW))
    tokens = {t["name"]: t for t in out["netbird_tokens"]}
    assert tokens["agent"]["expiration_ts"] is not None  # 7-digit fraction parsed
    assert tokens["reserve"]["last_used_ts"] is None  # Go zero time == never used
    assert tokens["ci"]["last_used_ts"] is None  # missing field


def test_group_router_resolved_and_deduplicated():
    client, _ = make_client(good_routes())
    networks = {n["name"]: n for n in sections(agent.run(args(), client, now=NOW))["netbird_networks"]}
    office = networks["Office"]
    r1 = next(r for r in office["routers"] if r["id"] == "r1")
    r2 = next(r for r in office["routers"] if r["id"] == "r2")
    assert [p["id"] for p in r1["peers"]] == ["p1"]
    assert sorted(p["id"] for p in r2["peers"]) == ["p1", "p2"]  # group -> peers
    disabled = networks["Disabled"]["routers"][0]
    assert disabled["enabled"] is False


def test_peer_with_unparsable_version():
    client, _ = make_client(good_routes())
    networks = {n["name"]: n for n in sections(agent.run(args(), client, now=NOW))["netbird_networks"]}
    peer = networks["Lab"]["routers"][0]["peers"][0]
    assert peer["version"] == "development"
    assert peer["version_t"] is None


def test_unknown_peer_in_router_is_offline_not_crash():
    routes = good_routes()
    routes["/api/networks/routers"] = [
        {"id": "r1", "peer": "gone", "enabled": True}, *fixture("routers.json")[1:]
    ]
    client, _ = make_client(routes)
    office = next(n for n in sections(agent.run(args(), client, now=NOW))["netbird_networks"] if n["name"] == "Office")
    gone = next(p for r in office["routers"] for p in r["peers"] if p["id"] == "gone")
    assert gone["connected"] is False


def test_router_fallback_per_network_when_id_list_empty():
    routes = good_routes()
    networks = fixture("networks.json")
    networks[0]["routers"] = []
    routes["/api/networks"] = networks
    routes["/api/networks/n1/routers"] = fixture("routers.json")[:2]
    client, _ = make_client(routes)
    office = next(n for n in sections(agent.run(args(), client, now=NOW))["netbird_networks"] if n["name"] == "Office")
    assert len(office["routers"]) == 2


def test_own_token_detected_by_most_recent_use():
    client, _ = make_client(good_routes())
    own = sections(agent.run(args(), client, now=NOW))["netbird_api"][0]["own_token"]
    assert own["name"] == "agent"
    assert own["certain"] is False  # two unexpired tokens -> heuristic


def test_own_token_lookup_failure_is_ignored():
    routes = good_routes()
    routes["/api/users/current"] = http_error(404)
    client, _ = make_client(routes)
    assert sections(agent.run(args(), client, now=NOW))["netbird_api"][0]["own_token"] is None


# ------------------------------------------------------------ reference version
def test_reference_peers_source_uses_highest_peer_version():
    client, _ = make_client(good_routes())
    ref = sections(agent.run(args(), client, now=NOW))["netbird_reference_version"][0]
    assert ref["peers_max"] == [0, 61, 0]
    assert ref["version"] is None


def test_reference_configured():
    client, _ = make_client(good_routes())
    ref = sections(agent.run(args(reference_source="configured", reference_version="0.59.1"), client, now=NOW))[
        "netbird_reference_version"
    ][0]
    assert ref["version"] == [0, 59, 1]
    assert ref["error"] is None


def test_reference_configured_invalid():
    client, _ = make_client(good_routes())
    ref = sections(agent.run(args(reference_source="configured", reference_version="nope"), client, now=NOW))[
        "netbird_reference_version"
    ][0]
    assert ref["version"] is None and ref["error"]


def test_reference_github_success_and_no_token_sent_to_github():
    class Opener(FakeOpener):
        def open(self, req, timeout=None):
            self.requests.append(req)
            if "api.github.com" in req.full_url:
                return FakeResponse({"tag_name": "v0.62.1"})
            return super().open(req, timeout)

    opener = Opener(good_routes())
    client = agent.Client("https://nb.example", SECRET, 5, opener, sleep=lambda s: None)
    ref = sections(agent.run(args(reference_source="github_fallback"), client, now=NOW))["netbird_reference_version"][0]
    assert ref["version"] == [0, 62, 1] and ref["source"] == "github"
    gh = [r for r in opener.requests if "api.github.com" in r.full_url]
    assert gh and "Authorization" not in {k.title() for k in gh[0].headers}
    assert SECRET not in json.dumps([dict(r.header_items()) for r in gh])


def test_reference_github_failure_reports_error_and_keeps_peers_max():
    class Opener(FakeOpener):
        def open(self, req, timeout=None):
            if "api.github.com" in req.full_url:
                raise http_error(403, b"rate limit")
            return super().open(req, timeout)

    client = agent.Client("https://nb.example", SECRET, 5, Opener(good_routes()), sleep=lambda s: None)
    ref = sections(agent.run(args(reference_source="github_fallback"), client, now=NOW))["netbird_reference_version"][0]
    assert ref["version"] is None
    assert "GitHub lookup failed" in ref["error"]
    assert ref["peers_max"] == [0, 61, 0]


# -------------------------------------------------------------------- errors
@pytest.mark.parametrize(
    "error, kind",
    [
        (http_error(401, b'{"message":"unauthorized"}'), "auth"),
        (http_error(403), "auth"),
        (http_error(500), "server"),
        (http_error(503), "server"),
        (http_error(404), "http"),
        (TimeoutError("timed out"), "timeout"),
        (urllib.error.URLError(TimeoutError("timed out")), "timeout"),
        (urllib.error.URLError("name resolution failed"), "connection"),
        (b"<html>not json</html>", "invalid_json"),
    ],
)
def test_fatal_errors_become_api_section_not_exception(error, kind):
    routes = good_routes()
    routes["/api/users?service_user=true"] = error
    client, _ = make_client(routes)
    lines = agent.run(args(), client, now=NOW)
    out = sections(lines)
    assert list(out) == ["netbird_api"]
    assert out["netbird_api"][0]["ok"] is False
    assert out["netbird_api"][0]["error_kind"] == kind


def test_unexpected_json_shape_is_error():
    routes = good_routes()
    routes["/api/users?service_user=true"] = {"unexpected": "object"}
    client, _ = make_client(routes)
    out = sections(agent.run(args(), client, now=NOW))
    assert out["netbird_api"][0]["error_kind"] == "invalid_json"


def test_429_is_retried_once_after_waiting():
    routes = good_routes()
    routes["/api/peers"] = Seq([http_error(429, headers={"Retry-After": "3"}), fixture("peers.json")])
    sleeps: list[float] = []
    client, _ = make_client(routes, sleeps)
    out = sections(agent.run(args(), client, now=NOW))
    assert out["netbird_api"][0]["ok"] is True
    assert sleeps == [3.0]


def test_429_twice_is_an_error():
    routes = good_routes()
    routes["/api/peers"] = Seq([http_error(429), http_error(429)])
    client, _ = make_client(routes, [])
    out = sections(agent.run(args(), client, now=NOW))
    assert out["netbird_api"][0]["error_kind"] == "rate_limit"


def test_partial_failure_marks_single_user_and_continues():
    routes = good_routes()
    routes["/api/users/svc2/tokens"] = http_error(403, b"forbidden")
    client, _ = make_client(routes)
    out = sections(agent.run(args(), client, now=NOW))
    api = out["netbird_api"][0]
    assert api["ok"] is True
    assert len(api["partial"]) == 1 and "terraform" in api["partial"][0]
    assert {t["name"] for t in out["netbird_tokens"] if "read_error" not in t} == {"agent", "reserve"}
    assert any(t.get("read_error") for t in out["netbird_tokens"])
    assert out["netbird_networks"]  # rest still runs


# ---------------------------------------------------------------- token safety
def test_token_sent_only_as_authorization_header():
    client, opener = make_client(good_routes())
    agent.run(args(), client, now=NOW)
    first = opener.requests[0]
    assert first.get_header("Authorization") == f"Token {SECRET}"
    assert SECRET not in first.full_url


def test_error_messages_never_contain_token():
    echo = http_error(401, f'invalid token {SECRET}'.encode())
    routes = good_routes()
    routes["/api/users?service_user=true"] = echo
    client, _ = make_client(routes)
    text = "\n".join(agent.run(args(), client, now=NOW))
    assert SECRET not in text
    assert "<redacted>" in text


def test_redact():
    assert agent.redact(f"a {SECRET} b", SECRET) == "a <redacted> b"
    assert agent.redact("plain", None) == "plain"


# --------------------------------------------- end-to-end against a real socket
class _Handler(BaseHTTPRequestHandler):
    routes: dict = {}
    seen_auth: list = []

    def do_GET(self):
        _Handler.seen_auth.append(self.headers.get("Authorization"))
        if self.headers.get("Authorization") != f"Token {SECRET}":
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"message":"unauthorized"}')
            return
        body = _Handler.routes.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    _Handler.routes = good_routes()
    _Handler.seen_auth = []
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def run_cli(url, token=SECRET, *extra):
    return subprocess.run(
        [sys.executable, "-I", str(REPO_ROOT / "netbird/libexec/agent_netbird"),
         "--base-url", url, "--token", token, "--no-proxy", "--reference-source", "peers", *extra],
        capture_output=True, text=True, timeout=60,
    )


def test_cli_end_to_end(server):
    proc = run_cli(server)
    assert proc.returncode == 0, proc.stderr
    assert SECRET not in proc.stdout and SECRET not in proc.stderr
    out = sections(proc.stdout.splitlines())
    assert set(out) == {"netbird_api", "netbird_tokens", "netbird_networks", "netbird_reference_version",
                       "netbird_instance_version"}


def test_cli_wrong_token_reports_auth_error_without_leaking(server):
    wrong = "nbp_WRONG_TOKEN_VALUE"
    proc = run_cli(server, wrong)
    assert proc.returncode == 0
    assert wrong not in proc.stdout and wrong not in proc.stderr
    api = sections(proc.stdout.splitlines())["netbird_api"][0]
    assert api["ok"] is False and api["error_kind"] == "auth"


def test_cli_connection_refused_is_clean_error():
    proc = run_cli("http://127.0.0.1:1")
    assert proc.returncode == 0
    api = sections(proc.stdout.splitlines())["netbird_api"][0]
    assert api["ok"] is False and api["error_kind"] in ("connection", "timeout")
    assert SECRET not in proc.stdout + proc.stderr


# ---------------------------------------------------------- password store
def test_get_token_plain_option():
    ns = argparse.Namespace(token=SECRET, token_id=None)
    assert agent.get_token(ns) == SECRET


def test_get_token_from_password_store_reference(monkeypatch):
    class FakeSecret:
        def reveal(self):
            return SECRET

    seen = []
    monkeypatch.setattr(agent, "resolve_secret_option", lambda ns, name: seen.append((ns.token_id, name)) or FakeSecret())
    assert agent.get_token(argparse.Namespace(token=None, token_id="ident:/some/store")) == SECRET
    assert seen == [("ident:/some/store", "token")]


def test_token_id_without_password_store_api_is_a_clean_error(monkeypatch):
    monkeypatch.setattr(agent, "resolve_secret_option", None)
    with pytest.raises(SystemExit):
        agent.get_token(argparse.Namespace(token=None, token_id="x:y"))


def test_cli_unreadable_password_store_does_not_traceback():
    proc = subprocess.run(
        [sys.executable, "-I", str(REPO_ROOT / "netbird/libexec/agent_netbird"), "--token-id", "nope:/nonexistent"],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr


# ------------------------------------------------------- instance version
def test_instance_version_section():
    client, _ = make_client(good_routes())
    row = sections(agent.run(args(), client, now=NOW))["netbird_instance_version"][0]
    assert row["ok"] is True
    assert row["management_current_t"] == [0, 64, 0] and row["management_available_t"] == [0, 64, 1]
    assert row["dashboard_available"] == "2.28.0" and row["update_available"] is True


@pytest.mark.parametrize("error", [http_error(404), http_error(403), http_error(500), b"nonsense"])
def test_instance_version_failure_does_not_break_the_run(error):
    routes = good_routes()
    routes["/api/instance/version"] = error
    client, _ = make_client(routes)
    out = sections(agent.run(args(), client, now=NOW))
    assert out["netbird_api"][0]["ok"] is True and out["netbird_api"][0]["partial"] == []
    assert out["netbird_instance_version"][0]["ok"] is False
    assert out["netbird_tokens"] and out["netbird_networks"]
