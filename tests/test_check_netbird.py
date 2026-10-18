"""Tests for the NetBird check plugins."""

import json

import pytest
from conftest import load_module

check = load_module("netbird/agent_based/netbird.py", "netbird_checks")
State = check.State

DAY = 86400.0
NOW = 1_800_000_000.0


def table(*rows):
    return [[json.dumps(r)] for r in rows]


def run(gen):
    return list(gen)


def results(gen):
    return [r for r in gen if isinstance(r, check.Result)]


def metrics(gen):
    return {m.name: m.value for m in gen if isinstance(m, check.Metric)}


def overall(gen):
    res = results(gen)
    order = {State.OK: 0, State.WARN: 1, State.UNKNOWN: 2, State.CRIT: 3}
    return max((r.state for r in res), key=lambda s: order[s])


def text(gen):
    return " | ".join((r.summary or getattr(r, "notice", None) or getattr(r, "details", None) or "") for r in results(gen))


# ----------------------------------------------------------------- tokens
def token(**kw):
    base = {
        "user_id": "u1", "user_name": "svc", "is_service_user": True, "token_id": "t1",
        "name": "agent", "expiration_ts": NOW + 100 * DAY, "created_ts": NOW - 10 * DAY,
        "last_used_ts": NOW - 1 * DAY,
    }
    base.update(kw)
    return base


def section(**kw):
    return check.parse_netbird_tokens(table(token(**kw)))


def tcheck(params=None, now=NOW, **kw):
    sec = section(**kw)
    return run(check.check_netbird_token_at("svc/agent", params or {}, sec, now))


def test_parse_token_items_and_user_marker():
    sec = check.parse_netbird_tokens(
        table(token(), token(user_name="Alice", is_service_user=False, token_id="t2"),
              {"user_id": "u9", "user_name": "broken", "read_error": "HTTP 403"})
    )
    assert set(sec) == {"svc/agent", "[user] Alice/agent"}


def test_parse_token_colliding_items_get_suffix():
    sec = check.parse_netbird_tokens(table(token(token_id="a1"), token(token_id="a2")))
    assert set(sec) == {"svc/agent", "svc/agent (a2)"}


def test_token_ok():
    res = tcheck()
    assert overall(res) == State.OK
    assert metrics(res)["netbird_token_days_to_expiry"] == pytest.approx(100.0)
    assert metrics(res)["netbird_token_days_unused"] == pytest.approx(1.0)


@pytest.mark.parametrize(
    "days, state",
    [(15, State.OK), (14, State.WARN), (8, State.WARN), (7, State.CRIT), (1, State.CRIT)],
)
def test_token_expiry_levels(days, state):
    assert overall(tcheck(expiration_ts=NOW + days * DAY)) == state


def test_token_expired_is_crit_with_clear_text():
    res = tcheck(expiration_ts=NOW - 3 * DAY)
    assert overall(res) == State.CRIT
    assert "Expired 3 days" in text(res)


def test_expired_ignored_after_threshold():
    params = {"ignore_expired_after": 30 * DAY}
    assert overall(tcheck(params, expiration_ts=NOW - 29 * DAY)) == State.CRIT
    res = tcheck(params, expiration_ts=NOW - 31 * DAY)
    assert overall(res) == State.OK
    assert "ignored" in text(res)


def test_token_without_expiration_is_ok():
    res = tcheck(expiration_ts=None)
    assert overall(res) == State.OK
    assert "No expiration" in text(res)


def test_token_unused_warn_default_90_days():
    assert overall(tcheck(last_used_ts=NOW - 89 * DAY)) == State.OK
    assert overall(tcheck(last_used_ts=NOW - 91 * DAY)) == State.WARN


def test_token_unused_crit_only_when_configured():
    old = NOW - 400 * DAY
    assert overall(tcheck(last_used_ts=old, created_ts=old)) == State.WARN
    assert overall(tcheck({"unused_crit": 365 * DAY}, last_used_ts=old, created_ts=old)) == State.CRIT


def test_never_used_counts_from_creation_and_says_so():
    res = tcheck(last_used_ts=None, created_ts=NOW - 120 * DAY)
    assert overall(res) == State.WARN
    assert "Never used" in text(res)
    res = tcheck(last_used_ts=None, created_ts=NOW - 5 * DAY)
    assert overall(res) == State.OK
    assert "Never used" in text(res)


def test_no_last_used_and_no_created_is_not_a_crash():
    res = tcheck(last_used_ts=None, created_ts=None)
    assert overall(res) == State.OK
    assert "netbird_token_days_unused" not in metrics(res)


def test_worse_of_both_checks_wins():
    res = tcheck(expiration_ts=NOW + 3 * DAY, last_used_ts=NOW - 100 * DAY)
    assert overall(res) == State.CRIT


def test_custom_expiry_levels():
    params = {"expiry_levels": ("fixed", (10 * DAY, 2 * DAY))}
    assert overall(tcheck(params, expiration_ts=NOW + 20 * DAY)) == State.OK
    assert overall(tcheck(params, expiration_ts=NOW + 5 * DAY)) == State.WARN


def test_vanished_token_yields_nothing():
    assert run(check.check_netbird_token_at("gone", {}, section(), NOW)) == []


# ---------------------------------------------------- token discovery filter
def disc(params):
    sec = check.parse_netbird_tokens(
        table(token(), token(name="reserve-1", token_id="t2"), token(name="ci", token_id="t3"))
    )
    return sorted(s.item for s in check.discover_netbird_token(params, sec))


def test_discovery_no_filter():
    assert disc({}) == ["svc/agent", "svc/ci", "svc/reserve-1"]


def test_discovery_exclude_regex():
    assert disc({"name_exclude": "^reserve"}) == ["svc/agent", "svc/ci"]


def test_discovery_include_regex():
    assert disc({"name_include": "^(agent|ci)$"}) == ["svc/agent", "svc/ci"]


def test_discovery_include_and_exclude():
    assert disc({"name_include": "e", "name_exclude": "reserve"}) == ["svc/agent"]


# ------------------------------------------------------------------- API
def api(**kw):
    base = {"ok": True, "error": None, "error_kind": None, "partial": [], "own_token": None,
            "counts": {"tokens": 3, "networks": 2, "peers": 5}}
    base.update(kw)
    return base


def acheck(section, params=None):
    return run(check.check_netbird_api_at(params or {}, section, NOW))


def test_api_ok():
    res = acheck(api())
    assert overall(res) == State.OK
    assert "3 tokens" in text(res)


@pytest.mark.parametrize("kind", ["timeout", "auth", "server", "connection", "invalid_json", "rate_limit"])
def test_api_errors_are_crit(kind):
    res = acheck(api(ok=False, error="HTTP 401", error_kind=kind))
    assert overall(res) == State.CRIT
    assert "HTTP 401" in text(res)


def test_api_partial_is_warn():
    res = acheck(api(partial=["tokens of bob: HTTP 403"]))
    assert overall(res) == State.WARN
    assert "bob" in (results(res)[1].details or "")


def own(days, certain=True):
    return {"user_name": "svc", "name": "agent", "expiration_ts": NOW + days * DAY, "certain": certain}


def test_api_own_token_expiring_soon():
    assert overall(acheck(api(own_token=own(60)))) == State.OK
    assert overall(acheck(api(own_token=own(10)))) == State.WARN
    assert overall(acheck(api(own_token=own(3)))) == State.CRIT
    assert overall(acheck(api(own_token=own(-1)))) == State.CRIT


def test_api_own_token_uncertain_is_marked_probable():
    assert "probable" in text(acheck(api(own_token=own(3, certain=False))))


def test_api_auth_error_mentions_expiry_possibility():
    assert "expired" in text(acheck(api(ok=False, error="HTTP 401", error_kind="auth")))


# --------------------------------------------------------------- networks
def peer(pid, name=None, connected=True, version="0.60.2", last_seen=NOW - 60, login_expired=False):
    parsed = check_parse(version)
    return {"id": pid, "name": name or pid, "connected": connected, "last_seen_ts": last_seen,
            "version": version, "version_t": parsed, "login_expired": login_expired}


def check_parse(version):
    import re

    m = re.match(r"^v?(\d+)\.(\d+)(?:\.(\d+))?", version or "")
    return [int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)] if m else None


def router(rid, peers, enabled=True):
    return {"id": rid, "enabled": enabled, "metric": 100, "peers": peers}


def net(name, routers, nid="n1"):
    return {"id": nid, "name": name, "routers": routers}


def netsec(*networks):
    return check.parse_netbird_networks(table(*networks))


def ref(version=(0, 60, 2), source="github", **kw):
    row = {"requested": "github_fallback", "source": source, "version": list(version) if version else None,
           "peers_max": None, "error": None}
    row.update(kw)
    return row


def ncheck(item, networks, reference=None, params=None):
    return run(check.check_netbird_network_at(item, params or {}, networks, reference, NOW))


def test_network_all_online_ok():
    sec = netsec(net("Office", [router("r1", [peer("p1"), peer("p2")])]))
    res = ncheck("Office", sec, ref())
    assert overall(res) == State.OK
    assert "2/2 routing peers online" in text(res)
    assert metrics(res) == {"netbird_routers_online": 2, "netbird_routers_total": 2}


def test_network_some_offline_is_warn_with_details():
    sec = netsec(net("Office", [router("r1", [
        peer("p1"), peer("p2", "router-b", connected=False, last_seen=NOW - 3 * 3600, login_expired=True)])]))
    res = ncheck("Office", sec, ref())
    assert overall(res) == State.WARN
    t = text(res)
    assert "1/2 routing peers online" in t
    assert "router-b" in t and "last seen 3 hours" in t and "login expired" in t


def test_network_none_online_is_crit_even_with_single_router():
    sec = netsec(net("Lab", [router("r1", [peer("p1", connected=False)])]))
    res = ncheck("Lab", sec, ref())
    assert overall(res) == State.CRIT
    assert "No routing peer online" in text(res)


def test_network_disabled_router_not_counted():
    sec = netsec(net("Office", [
        router("r1", [peer("p1")]), router("r2", [peer("p2", connected=False)], enabled=False)]))
    res = ncheck("Office", sec, ref())
    assert overall(res) == State.OK
    assert metrics(res)["netbird_routers_total"] == 1


def test_network_duplicate_peer_counts_once():
    sec = netsec(net("Office", [
        router("r1", [peer("p1")]), router("r2", [peer("p1"), peer("p2", connected=False)])]))
    res = ncheck("Office", sec, ref())
    assert metrics(res) == {"netbird_routers_online": 1, "netbird_routers_total": 2}
    assert overall(res) == State.WARN


def test_network_group_router_with_no_peers_is_crit():
    sec = netsec(net("Office", [router("r1", [])]))
    assert overall(ncheck("Office", sec, ref())) == State.CRIT


def test_network_never_seen_peer_text():
    sec = netsec(net("Office", [router("r1", [peer("p1"), peer("p2", connected=False, last_seen=None)])]))
    assert "never seen" in text(ncheck("Office", sec, ref()))


def test_discovery_only_networks_with_active_router():
    sec = netsec(
        net("A", [router("r1", [peer("p1")])], "a"),
        net("B", [router("r2", [peer("p1")], enabled=False)], "b"),
        net("C", [], "c"),
    )
    assert [s.item for s in check.discover_netbird_network(sec, None)] == ["A"]


def test_vanished_network_yields_nothing():
    assert ncheck("gone", netsec(), ref()) == []


# ---------------------------------------------------------------- versions
@pytest.mark.parametrize(
    "peer_version, state",
    [
        ("0.60.2", State.OK),
        ("0.60.0", State.OK),  # same minor, other patch
        ("0.61.0", State.OK),  # newer than reference
        ("0.59.9", State.WARN),
        ("0.58.0", State.WARN),
        ("0.57.9", State.CRIT),  # 3 minors behind
        ("0.10.0", State.CRIT),
    ],
)
def test_version_minor_distance(peer_version, state):
    sec = netsec(net("N", [router("r1", [peer("p1", version=peer_version)])]))
    assert overall(ncheck("N", sec, ref((0, 60, 2)))) == state


def test_version_major_difference_is_crit():
    sec = netsec(net("N", [router("r1", [peer("p1", version="0.99.0")])]))
    assert overall(ncheck("N", sec, ref((1, 0, 0)))) == State.CRIT


def test_version_major_state_configurable():
    sec = netsec(net("N", [router("r1", [peer("p1", version="0.99.0")])]))
    assert overall(ncheck("N", sec, ref((1, 0, 0)), {"major_state": 1})) == State.WARN


def test_version_custom_minor_levels():
    sec = netsec(net("N", [router("r1", [peer("p1", version="0.58.0")])]))
    assert overall(ncheck("N", sec, ref((0, 60, 2)), {"minor_levels": ("fixed", (3, 6))})) == State.OK


@pytest.mark.parametrize("version", ["", "development", "garbage", None])
def test_unparsable_version_is_not_crit_and_is_mentioned(version):
    sec = netsec(net("N", [router("r1", [peer("p1", version=version or "")])]))
    res = ncheck("N", sec, ref())
    assert overall(res) == State.OK
    assert "Version unknown" in text(res)
    assert "up to date" not in text(res)  # nothing was actually compared


def test_rc_version_is_compared_by_base_version():
    sec = netsec(net("N", [router("r1", [peer("p1", version="0.60.2-rc1")])]))
    assert overall(ncheck("N", sec, ref((0, 60, 2)))) == State.OK


def test_status_is_max_of_availability_and_version():
    sec = netsec(net("N", [router("r1", [peer("p1", version="0.40.0"), peer("p2", connected=False)])]))
    assert overall(ncheck("N", sec, ref())) == State.CRIT
    sec = netsec(net("N", [router("r1", [peer("p1", version="0.59.0"), peer("p2", connected=False)])]))
    assert overall(ncheck("N", sec, ref())) == State.WARN


def test_version_text_names_outdated_peer_and_reference_source():
    sec = netsec(net("N", [router("r1", [peer("p1", "old-router", version="0.57.0")])]))
    res = ncheck("N", sec, ref())
    assert "old-router" in text(res)
    assert "latest GitHub release" in (results(res)[-1].details or text(res))


def test_reference_fallback_to_peers_max_when_github_failed():
    sec = netsec(net("N", [router("r1", [peer("p1", version="0.50.0")])]))
    reference = ref(version=None, source="github", peers_max=[0, 60, 0], error="GitHub lookup failed: timeout")
    res = ncheck("N", sec, reference)
    assert overall(res) == State.CRIT  # 10 minors behind the highest peer version
    assert "highest peer version" in (results(res)[-1].details or "") + text(res)
    assert "GitHub lookup failed" in (results(res)[-1].details or "") + text(res)


def test_reference_strict_github_failure_skips_version_check():
    sec = netsec(net("N", [router("r1", [peer("p1", version="0.1.0")])]))
    reference = ref(version=None, requested="github", error="GitHub lookup failed: timeout")
    res = ncheck("N", sec, reference)
    assert overall(res) == State.OK
    assert "Version check skipped" in text(res)


def test_reference_missing_section_falls_back_to_router_peers():
    sec = netsec(net("N", [router("r1", [peer("p1", version="0.60.2"), peer("p2", version="0.58.0")])]))
    res = ncheck("N", sec, None)
    assert overall(res) == State.WARN


def test_reference_configured():
    sec = netsec(net("N", [router("r1", [peer("p1", version="0.58.0")])]))
    reference = ref(version=(0, 58, 5), source="configured", requested="configured")
    res = ncheck("N", sec, reference)
    assert overall(res) == State.OK
    assert "configured" in text(res)


# ------------------------------------------------------------ section parsing
def test_parse_ignores_garbage_lines():
    assert check.parse_netbird_tokens([["not json"], [], [json.dumps(token())]]).keys() == {"svc/agent"}
    assert check.parse_netbird_api([]) is None


# ------------------------------------------------- plugin registration contract
def test_check_function_signatures_match_what_checkmk_expects():
    """Checkmk validates the argument names of registered functions strictly."""
    import inspect

    def names(function):
        return list(inspect.signature(function).parameters)

    assert names(check.check_netbird_api) == ["params", "section"]
    assert names(check.check_netbird_token) == ["item", "params", "section"]
    assert names(check.check_netbird_network) == [
        "item", "params", "section_netbird_networks", "section_netbird_reference_version"]
    assert names(check.discover_netbird_api) == ["section"]
    assert names(check.discover_netbird_token) == ["params", "section"]
    assert names(check.discover_netbird_network) == [
        "section_netbird_networks", "section_netbird_reference_version"]


def test_public_check_functions_run_with_the_current_time():
    sec = section(expiration_ts=2e9, last_used_ts=None, created_ts=None)
    assert results(check.check_netbird_token("svc/agent", {}, sec))


# ----------------------------------------------------- management version
def inst(**kw):
    base = {"ok": True, "management_current": "0.64.0", "management_current_t": [0, 64, 0],
            "management_available": "0.64.0", "management_available_t": [0, 64, 0],
            "dashboard_available": "2.28.0", "update_available": False}
    base.update(kw)
    return base


def vcheck(section, params=None):
    return run(check.check_netbird_version(params or {}, section))


def test_version_discovery_only_when_endpoint_worked():
    assert [s for s in check.discover_netbird_version(inst())] != []
    assert list(check.discover_netbird_version({"ok": False, "error": "HTTP 404"})) == []


def test_version_up_to_date():
    res = vcheck(inst())
    assert overall(res) == State.OK
    assert "Up to date" in text(res) and "0.64.0" in text(res)


def test_version_patch_update_ok_by_default_and_configurable():
    sec = inst(management_available="0.64.1", management_available_t=[0, 64, 1], update_available=True)
    res = vcheck(sec)
    assert overall(res) == State.OK
    assert "0.64.1" in text(res) and "patch update" in text(res)
    assert overall(vcheck(sec, {"update_state": 1})) == State.WARN


@pytest.mark.parametrize("available, state", [((0, 65, 0), State.WARN), ((0, 66, 3), State.WARN),
                                              ((0, 67, 0), State.CRIT), ((1, 0, 0), State.CRIT)])
def test_version_minor_and_major_distance(available, state):
    sec = inst(management_available=".".join(map(str, available)), management_available_t=list(available),
               update_available=True)
    assert overall(vcheck(sec)) == state


def test_version_endpoint_failure_is_unknown_not_crit():
    res = vcheck({"ok": False, "error": "HTTP 404"})
    assert overall(res) == State.UNKNOWN
    assert "HTTP 404" in text(res)


def test_version_unparsable_never_alerts():
    res = vcheck(inst(management_current="development", management_current_t=None))
    assert overall(res) == State.OK
    assert "cannot be compared" in text(res)


def test_version_signatures():
    import inspect

    assert list(inspect.signature(check.check_netbird_version).parameters) == ["params", "section"]
    assert list(inspect.signature(check.discover_netbird_version).parameters) == ["section"]
