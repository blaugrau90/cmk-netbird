#!/usr/bin/env python3
"""Checkmk agent-based check plugins for NetBird.

Sections (JSON, one object per line), written by agent_netbird:
- netbird_api: connectivity, partial failures, the agent's own token
- netbird_tokens: personal access tokens
- netbird_networks: networks with their resolved routing peers
- netbird_reference_version: reference NetBird version for the version check

Check plugins:
- netbird_api:     "NetBird API"
- netbird_token:   "NetBird Token <User>/<Token>"
- netbird_network: "NetBird Network <Name>"
"""

import json
import re
import time
from collections.abc import Mapping
from typing import Any

from cmk.agent_based.v2 import (
    AgentSection,
    CheckPlugin,
    CheckResult,
    DiscoveryResult,
    Metric,
    Result,
    Service,
    State,
    StringTable,
    render,
)

DAY = 86400.0

TOKEN_DEFAULTS: Mapping[str, Any] = {
    "expiry_levels": ("fixed", (30 * DAY, 7 * DAY)),
    "unused_warn": 90 * DAY,
    "unused_crit": None,
    "ignore_expired_after": None,
}
NETWORK_DEFAULTS: Mapping[str, Any] = {
    "minor_levels": ("fixed", (1, 3)),
    "major_state": 2,
}
API_DEFAULTS: Mapping[str, Any] = {
    "own_token_levels": ("fixed", (30 * DAY, 7 * DAY)),
}
TOKEN_DISCOVERY_DEFAULTS: Mapping[str, Any] = {}

_STATE_NAMES = {0: State.OK, 1: State.WARN, 2: State.CRIT, 3: State.UNKNOWN}


# --------------------------------------------------------------------------
# Generic helpers
# --------------------------------------------------------------------------


def _json_rows(string_table: StringTable) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in string_table:
        if not line:
            continue
        try:
            row = json.loads(line[0])
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _worst(*states: State) -> State:
    """Worst state with Checkmk ordering (CRIT > UNKNOWN > WARN > OK)."""
    order = {State.OK: 0, State.WARN: 1, State.UNKNOWN: 2, State.CRIT: 3}
    return max(states, key=lambda s: order[s])


def _state(value: Any, default: State) -> State:
    return _STATE_NAMES.get(value, default) if isinstance(value, int) else default


def _levels(params: Mapping[str, Any], key: str, defaults: Mapping[str, Any]) -> tuple[float, float] | None:
    levels = params.get(key, defaults.get(key))
    if isinstance(levels, tuple) and len(levels) == 2 and levels[0] == "fixed":
        return levels[1]
    return None


def _p(params: Mapping[str, Any], key: str, defaults: Mapping[str, Any]) -> Any:
    return params[key] if key in params else defaults.get(key)


def _age(now: float, ts: float) -> str:
    return render.timespan(max(now - ts, 0.0))


def _unique_items(rows: list[tuple[str, str, dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    """Map item -> row. Colliding items get the object id appended (stable)."""
    result: dict[str, dict[str, Any]] = {}
    for base, ident, row in sorted(rows, key=lambda r: (r[0], r[1])):
        item = base if base not in result else f"{base} ({ident})"
        result[item] = row
    return result


# --------------------------------------------------------------------------
# netbird_api
# --------------------------------------------------------------------------


def parse_netbird_api(string_table: StringTable) -> dict[str, Any] | None:
    rows = _json_rows(string_table)
    return rows[0] if rows else None


def discover_netbird_api(section: dict[str, Any]) -> DiscoveryResult:
    yield Service()


_API_ERROR_TEXT = {
    "timeout": "API timed out",
    "auth": "Authentication failed (token invalid, expired or role lacks permission)",
    "rate_limit": "API rate limit exceeded",
    "server": "API server error",
    "connection": "Connection to API failed",
    "invalid_json": "API returned invalid data",
}


def check_netbird_api(params: Mapping[str, Any], section: dict[str, Any]) -> CheckResult:
    yield from check_netbird_api_at(params, section, time.time())


def check_netbird_api_at(params: Mapping[str, Any], section: dict[str, Any], now: float) -> CheckResult:
    if not section.get("ok"):
        kind = section.get("error_kind") or ""
        prefix = _API_ERROR_TEXT.get(kind, "API request failed")
        yield Result(state=State.CRIT, summary=f"{prefix}: {section.get('error') or 'unknown error'}")
        return

    counts = section.get("counts") or {}
    summary = "API reachable"
    if counts:
        summary += (
            f", {counts.get('tokens', 0)} tokens, {counts.get('networks', 0)} networks, "
            f"{counts.get('peers', 0)} peers"
        )
    yield Result(state=State.OK, summary=summary)

    partial = section.get("partial") or []
    if partial:
        yield Result(
            state=State.WARN,
            summary=f"Partial failure ({len(partial)})",
            details="Partial failures:\n" + "\n".join(f"- {p}" for p in partial),
        )

    own = section.get("own_token")
    if own and own.get("expiration_ts"):
        remaining = own["expiration_ts"] - now
        levels = _levels(params, "own_token_levels", API_DEFAULTS)
        state = State.OK
        if levels:
            warn, crit = levels
            state = State.CRIT if remaining <= crit else State.WARN if remaining <= warn else State.OK
        hedge = "" if own.get("certain") else " (probable)"
        label = f"Agent token '{own.get('name')}' of {own.get('user_name')}{hedge}"
        if remaining <= 0:
            yield Result(state=State.CRIT, summary=f"{label} has expired")
        elif state != State.OK:
            yield Result(state=state, summary=f"{label} expires in {render.timespan(remaining)}")
        else:
            yield Result(state=State.OK, notice=f"{label} expires in {render.timespan(remaining)}")


agent_section_netbird_api = AgentSection(name="netbird_api", parse_function=parse_netbird_api)

check_plugin_netbird_api = CheckPlugin(
    name="netbird_api",
    service_name="NetBird API",
    discovery_function=discover_netbird_api,
    check_function=check_netbird_api,
    check_ruleset_name="netbird_api",
    check_default_parameters=dict(API_DEFAULTS),
)


# --------------------------------------------------------------------------
# netbird_tokens
# --------------------------------------------------------------------------


def parse_netbird_tokens(string_table: StringTable) -> dict[str, dict[str, Any]]:
    rows = []
    for row in _json_rows(string_table):
        if row.get("read_error") or not row.get("user_name") or not row.get("name"):
            continue
        marker = "" if row.get("is_service_user") else "[user] "
        rows.append((f"{marker}{row['user_name']}/{row['name']}", str(row.get("token_id") or ""), row))
    return _unique_items(rows)


def discover_netbird_token(params: Mapping[str, Any], section: dict[str, dict[str, Any]]) -> DiscoveryResult:
    include = params.get("name_include")
    exclude = params.get("name_exclude")
    for item, token in section.items():
        name = token.get("name", "")
        if include and not re.search(include, name):
            continue
        if exclude and re.search(exclude, name):
            continue
        yield Service(item=item)


def check_netbird_token(
    item: str, params: Mapping[str, Any], section: dict[str, dict[str, Any]]
) -> CheckResult:
    yield from check_netbird_token_at(item, params, section, time.time())


def check_netbird_token_at(
    item: str, params: Mapping[str, Any], section: dict[str, dict[str, Any]], now: float
) -> CheckResult:
    token = section.get(item)
    if token is None:
        return

    # --- remaining lifetime ---
    expiry = token.get("expiration_ts")
    if expiry is None:
        yield Result(state=State.OK, summary="No expiration date")
    else:
        remaining = expiry - now
        yield Metric("netbird_token_days_to_expiry", round(remaining / DAY, 2))
        if remaining <= 0:
            ignore_after = _p(params, "ignore_expired_after", TOKEN_DEFAULTS)
            if ignore_after is not None and -remaining > ignore_after:
                yield Result(
                    state=State.OK,
                    summary=f"Expired {render.timespan(-remaining)} ago (ignored after "
                    f"{render.timespan(ignore_after)})",
                )
                return
            yield Result(state=State.CRIT, summary=f"Expired {render.timespan(-remaining)} ago")
        else:
            state = State.OK
            levels = _levels(params, "expiry_levels", TOKEN_DEFAULTS)
            if levels:
                warn, crit = levels
                state = State.CRIT if remaining <= crit else State.WARN if remaining <= warn else State.OK
            yield Result(
                state=state,
                summary=f"Expires in {render.timespan(remaining)} ({render.date(expiry)})",
            )

    # --- unused since ---
    last_used = token.get("last_used_ts")
    created = token.get("created_ts")
    basis = last_used if last_used is not None else created
    if basis is None:
        yield Result(state=State.OK, notice="No usage information available")
        return
    unused = max(now - basis, 0.0)
    yield Metric("netbird_token_days_unused", round(unused / DAY, 2))
    warn_after = _p(params, "unused_warn", TOKEN_DEFAULTS)
    crit_after = _p(params, "unused_crit", TOKEN_DEFAULTS)
    state = State.OK
    if crit_after is not None and unused >= crit_after:
        state = State.CRIT
    elif warn_after is not None and unused >= warn_after:
        state = State.WARN
    if last_used is None:
        text = f"Never used (created {render.timespan(unused)} ago)"
    else:
        text = f"Last used {render.timespan(unused)} ago"
    yield Result(state=state, summary=text)


agent_section_netbird_tokens = AgentSection(name="netbird_tokens", parse_function=parse_netbird_tokens)

check_plugin_netbird_token = CheckPlugin(
    name="netbird_token",
    sections=["netbird_tokens"],
    service_name="NetBird Token %s",
    discovery_function=discover_netbird_token,
    discovery_ruleset_name="netbird_token_discovery",
    discovery_default_parameters=dict(TOKEN_DISCOVERY_DEFAULTS),
    check_function=check_netbird_token,
    check_ruleset_name="netbird_token",
    check_default_parameters=dict(TOKEN_DEFAULTS),
)


# --------------------------------------------------------------------------
# netbird_networks / netbird_reference_version
# --------------------------------------------------------------------------


def parse_netbird_networks(string_table: StringTable) -> dict[str, dict[str, Any]]:
    rows = [
        (str(row["name"]), str(row.get("id") or ""), row)
        for row in _json_rows(string_table)
        if row.get("name")
    ]
    return _unique_items(rows)


def parse_netbird_reference_version(string_table: StringTable) -> dict[str, Any] | None:
    rows = _json_rows(string_table)
    return rows[0] if rows else None


def active_router_peers(network: dict[str, Any]) -> list[dict[str, Any]]:
    """Distinct peers of all enabled routers (a peer counts once)."""
    peers: dict[str, dict[str, Any]] = {}
    for router in network.get("routers") or []:
        if not router.get("enabled"):
            continue
        for peer in router.get("peers") or []:
            peers.setdefault(str(peer.get("id")), peer)
    return list(peers.values())


def _has_active_router(network: dict[str, Any]) -> bool:
    return any(router.get("enabled") for router in network.get("routers") or [])


def discover_netbird_network(
    section_netbird_networks: dict[str, dict[str, Any]] | None,
    section_netbird_reference_version: dict[str, Any] | None,
) -> DiscoveryResult:
    for item, network in (section_netbird_networks or {}).items():
        if _has_active_router(network):
            yield Service(item=item)


_REFERENCE_LABEL = {
    "github": "latest GitHub release",
    "configured": "configured",
    "peers": "highest peer version",
}


def resolve_reference(
    reference: dict[str, Any] | None, networks: dict[str, dict[str, Any]]
) -> tuple[tuple[int, int, int] | None, str]:
    """Return (version, description of where it comes from)."""
    reference = reference or {}
    version = reference.get("version")
    source = reference.get("source", "peers")
    note = ""
    if not version and reference.get("requested") in ("github_fallback", "github", "configured"):
        note = f" ({reference.get('error') or 'lookup failed'})"
        if reference.get("requested") != "github_fallback":
            return None, f"reference version unavailable{note}"
        source = "peers"
        version = reference.get("peers_max")
        note = f" - fallback, {reference.get('error') or 'GitHub lookup failed'}"
    elif not version:
        source = "peers"
        version = reference.get("peers_max")
    if not version:
        # last resort: highest version among the peers we know
        found = [
            tuple(p["version_t"])
            for n in networks.values()
            for p in active_router_peers(n)
            if p.get("version_t")
        ]
        if not found:
            return None, "no reference version available"
        version, source = list(max(found)), "peers"
    label = _REFERENCE_LABEL.get(source, source)
    return (version[0], version[1], version[2]), f"{'.'.join(map(str, version))} ({label}){note}"


def _version_state(
    peer: tuple[int, int, int], ref: tuple[int, int, int], params: Mapping[str, Any]
) -> tuple[State, str]:
    if peer >= ref:
        return State.OK, ""
    if peer[0] < ref[0]:
        return _state(_p(params, "major_state", NETWORK_DEFAULTS), State.CRIT), "major version behind"
    behind = ref[1] - peer[1]
    if behind <= 0:
        return State.OK, ""
    levels = _levels(params, "minor_levels", NETWORK_DEFAULTS)
    state = State.OK
    if levels:
        warn, crit = levels
        state = State.CRIT if behind >= crit else State.WARN if behind >= warn else State.OK
    return state, f"{behind} minor version{'s' if behind != 1 else ''} behind"


def check_netbird_network(
    item: str,
    params: Mapping[str, Any],
    section_netbird_networks: dict[str, dict[str, Any]] | None,
    section_netbird_reference_version: dict[str, Any] | None,
) -> CheckResult:
    yield from check_netbird_network_at(
        item, params, section_netbird_networks, section_netbird_reference_version, time.time()
    )


def check_netbird_network_at(
    item: str,
    params: Mapping[str, Any],
    section_netbird_networks: dict[str, dict[str, Any]] | None,
    section_netbird_reference_version: dict[str, Any] | None,
    now: float,
) -> CheckResult:
    networks = section_netbird_networks or {}
    network = networks.get(item)
    if network is None:
        return

    peers = active_router_peers(network)
    online = [p for p in peers if p.get("connected")]
    offline = [p for p in peers if not p.get("connected")]
    total = len(peers)

    if total == 0:
        avail_state, text = State.CRIT, "No routing peers configured (0 online)"
    elif not online:
        avail_state, text = State.CRIT, f"No routing peer online (0/{total} online)"
    elif offline:
        avail_state, text = State.WARN, f"{len(online)}/{total} routing peers online"
    else:
        avail_state, text = State.OK, f"{len(online)}/{total} routing peers online"
    yield Result(state=avail_state, summary=text)
    yield Metric("netbird_routers_online", len(online))
    yield Metric("netbird_routers_total", total)

    if offline:
        parts = []
        for peer in sorted(offline, key=lambda p: str(p.get("name"))):
            seen = peer.get("last_seen_ts")
            piece = f"{peer.get('name')} (last seen {_age(now, seen)} ago" if seen else (
                f"{peer.get('name')} (never seen"
            )
            if peer.get("login_expired"):
                piece += ", login expired - possible cause"
            parts.append(piece + ")")
        yield Result(state=State.OK, summary="Offline: " + ", ".join(parts))

    # --- version check ---
    ref, ref_text = resolve_reference(section_netbird_reference_version, networks)
    if ref is None:
        yield Result(state=State.OK, summary=f"Version check skipped: {ref_text}")
        return
    worst = State.OK
    outdated: list[str] = []
    unknown: list[str] = []
    for peer in sorted(peers, key=lambda p: str(p.get("name"))):
        parsed = peer.get("version_t")
        if not parsed:
            shown = peer.get("version") or "empty"
            unknown.append(f"{peer.get('name')} ({shown})")
            continue
        state, reason = _version_state((parsed[0], parsed[1], parsed[2]), ref, params)
        if state != State.OK or reason:
            outdated.append(f"{peer.get('name')} {peer.get('version')}: {reason}")
        worst = _worst(worst, state)
    if outdated:
        yield Result(
            state=worst,
            summary=f"Outdated: {', '.join(outdated)}",
            details=f"Reference version {ref_text}",
        )
    else:
        yield Result(state=State.OK, summary=f"Versions up to date (reference {ref_text})")
    if unknown:
        yield Result(
            state=State.OK,
            summary=f"Version unknown for {', '.join(unknown)}",
        )


check_plugin_netbird_network = CheckPlugin(
    name="netbird_network",
    sections=["netbird_networks", "netbird_reference_version"],
    service_name="NetBird Network %s",
    discovery_function=discover_netbird_network,
    check_function=check_netbird_network,
    check_ruleset_name="netbird_network",
    check_default_parameters=dict(NETWORK_DEFAULTS),
)

agent_section_netbird_networks = AgentSection(name="netbird_networks", parse_function=parse_netbird_networks)

agent_section_netbird_reference_version = AgentSection(
    name="netbird_reference_version", parse_function=parse_netbird_reference_version
)
