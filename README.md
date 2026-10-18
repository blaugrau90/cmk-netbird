# NetBird – Checkmk 2.5 Plugin

A Checkmk 2.5 MKP plugin that monitors a **NetBird** account through the NetBird REST API. It watches two things and nothing else:

1. **API tokens (personal access tokens):** when they expire and when they were last used.
2. **Routing peers per NetBird network:** online state, redundancy and version.

The plugin is strictly read-only: the special agent only issues `GET` requests.

**Requires:** Checkmk 2.5.0+ (CRE/CEE/CME) · a NetBird personal access token · GPLv3 License

---

## Features

- One service **NetBird API**: reachability of the API with clear error texts (timeout, 401/403, rate limit, 5xx, invalid JSON). Partial failures (e.g. the tokens of one user cannot be read) turn it WARN while everything else keeps being monitored. It also warns when the agent's *own* token is about to expire.
- One service per token, **NetBird Token \<user\>/\<token\>**:
  - remaining lifetime (default WARN ≤ 14 days, CRIT ≤ 7 days, expired = CRIT)
  - unused since (default WARN ≥ 90 days, no CRIT). A token that was never used counts from its creation date and the text says "never used".
  - option to ignore tokens that have been expired for more than X days
  - regular expression on the token name to include/exclude tokens at discovery (e.g. spare tokens)
  - optionally tokens of regular users (shown as `[user] name/token`), default is service users only
- One service **NetBird Management Version**: the running management server version against the latest available one, from `GET /api/instance/version` (minor versions behind: WARN ≥ 1, CRIT ≥ 3; patch-only update: OK by default). No service is created if the endpoint is not available.
- One service per network, **NetBird Network \<name\>** (for networks with at least one enabled router):
  - all active routing peers online → OK, some offline → WARN, none online (also with a single router) → CRIT
  - routers bound to *peer groups* are resolved to peers; a peer counts once
  - offline peers are listed with the age of `last_seen`, and an expired login is pointed out as a likely cause
  - version check against a reference version (below); overall state is the worse of availability and version
- The API token is stored in the Checkmk **password store** and is passed to the agent only as a reference.
- Metrics and graphs: days until expiry, days since last use, routing peers online/total.

---

## NetBird token and permissions

Use a **service user** and create a personal access token for it (Team → Service Users in the NetBird dashboard).

NetBird has no endpoint that lists all tokens of an account, so the agent lists users and then asks for the tokens of **each user** (`GET /api/users/{id}/tokens`). Reading other users' tokens needs an administrative role. The endpoints used:

| Endpoint | Purpose |
|---|---|
| `GET /api/users?service_user=true` (without the filter if regular users are included) | users |
| `GET /api/users/{id}/tokens` | tokens per user |
| `GET /api/users/current` | find the agent's own token (best effort, failure is ignored) |
| `GET /api/peers` | peer state, groups, version |
| `GET /api/instance/version` | management server version (optional, failure is ignored) |
| `GET /api/networks`, `GET /api/networks/routers` | networks and routers |
| `GET /api/networks/{id}/routers` | only if a network's router list is empty |
| `GET https://api.github.com/repos/netbirdio/netbird/releases/latest` | reference version (optional); the NetBird token is **not** sent there |

The minimal role is **not yet verified** against a real instance (see "To verify" below). A read-only role that can read users, tokens, peers and networks is what you want; if NetBird only lets *admin*/*owner* read other users' tokens, use that for the service user.

NetBird Cloud allows 120 requests per minute; the agent needs roughly *number of users + 6* requests per run.

---

## Installation

1. Download `netbird-0.2.0.mkp` (or build it, see below).
2. In Checkmk: **Setup → Extension Packages → Upload package**, or as site user:

   ```bash
   mkp add netbird-0.2.0.mkp
   mkp enable netbird
   ```

3. **Setup → General → Passwords:** add the NetBird token as a password.
4. Create a host for the NetBird account (e.g. `netbird`) with *Checkmk agent / API integrations* set to **Configured API integrations, no Checkmk agent** and *IP address family* **No IP**.
5. **Setup → Agents → Other integrations → NetBird:** create a rule for this host.

| Field | Meaning |
|---|---|
| NetBird API token | Password store entry holding the personal access token |
| API base URL | `https://api.netbird.io` (default) or your self-hosted management URL |
| Verify SSL certificate | on by default |
| Request timeout | per request, default 30 s |
| Proxy | optional HTTP/HTTPS proxy (the default uses the environment) |
| Include tokens of regular users | off by default |
| Reference version | see below |

6. Run service discovery on the host. Enable periodic service discovery so new tokens and networks show up on their own.

### Reference version

Routing peers are compared against a reference version. NetBird is at 0.x, so the **minor** number counts and (major, minor, patch) are compared:

| Peer vs. reference | Default state |
|---|---|
| same minor (other patch), or newer | OK |
| 1 or more minor versions behind | WARN |
| 3 or more minor versions behind | CRIT |
| lower major version | CRIT |

Thresholds are configurable (rule *NetBird network*). Reference source (agent rule):

1. **Latest GitHub release, fall back to the highest peer version** (default). The Checkmk server needs internet access for GitHub. The text always names the source in use.
2. Latest GitHub release only. If GitHub is not reachable the version check is skipped (with a note).
3. A fixed target version.
4. The highest version among all peers.

Versions that cannot be parsed (empty, `development`) never raise an alert; the check names those peers. Pre-release suffixes such as `-rc1` are compared by their base version.

---

## Rules

| Rule (Setup → …) | Purpose |
|---|---|
| Agents → Other integrations → **NetBird** | connection, token, proxy, user scope, reference version |
| Service monitoring rules → **NetBird API** | levels for the agent's own token |
| Service monitoring rules → **NetBird token** | remaining lifetime, unused-since, ignore long-expired |
| Service monitoring rules → **NetBird management version** | minor-version levels, major-version state, state for a patch update |
| Service monitoring rules → **NetBird network** | minor-version levels, state for a major-version difference |
| Discovery rules → **NetBird token discovery** | include/exclude tokens by name (regular expression) |

All texts are English (the reference plugins are English-only as well).

---

## How it works

```
Checkmk check cycle
  └─ special agent  agent_netbird  (runs on the Checkmk site, GET only)
       ├─ users → tokens per user → peers → networks → network routers → reference version
       ├─ resolves router peer groups to peers, normalises timestamps and versions
       └─ prints five sections, one JSON object per line
  └─ sections: netbird_api  netbird_tokens  netbird_networks  netbird_reference_version  netbird_instance_version
  └─ check plugins: netbird_api · netbird_token · netbird_network · netbird_version
```

If the tokens of one user cannot be read (e.g. HTTP 403), that user's token services show "Item not found" and **NetBird API** is WARN and names the user.

If the API cannot be queried at all the agent still exits 0 and prints only `netbird_api` with the error, so the failure shows up in the **NetBird API** service. Other services of the host then go stale.

---

## To verify against a real NetBird instance

This plugin was written from the NetBird API documentation. The following points are **not confirmed** with real data yet, because they need a live account. The plugin handles each of them defensively:

| Question | How the plugin copes |
|---|---|
| Are already expired tokens still listed by `/api/users/{id}/tokens`? | If listed they are CRIT ("expired X ago") unless *ignore tokens expired for more than…* is set. If not listed, the service goes UNKNOWN/vanished. |
| How does `last_used` look for a never-used token (missing, `null`, zero time)? | All three are treated as "never used"; the time since `created_at` counts. |
| How often is `last_used` updated? | Affects the heuristic that finds the agent's own token and the accuracy of "unused since". |
| Minimal role of the service user | See above. If tokens of other users cannot be read, the **NetBird API** service is WARN and names the users. |
| Do router objects carry a network id? | Not needed: the assignment uses `routers` in `GET /api/networks`; the per-network endpoint is the fallback. |
| Version strings in `version` (empty, `-rc`, `development`) | Parsed by regex; anything unparsable is "unknown version" without a state change. |

Please report what you find in the GitHub issues.

---

## Password store API

The agent resolves the token through `cmk.password_store.v1_unstable` (`parser_add_secret_option` / `resolve_secret_option`), the interface Checkmk's own special agents use. It is labelled work-in-progress by Checkmk. The server-side call only puts `--token-id <reference>` on the command line. For debugging outside a site the agent also accepts `--token <value>`, which of course exposes the token in the process list.

---

## File Structure

```
netbird/
├── libexec/agent_netbird                 # Special agent (stdlib only)
├── server_side_calls/special_agent.py    # Builds the agent call
├── rulesets/netbird.py                   # Agent rule, check rules, discovery rule
├── agent_based/netbird.py                # Sections and 4 check plugins
├── graphing/netbird.py                   # Metrics and graphs
└── checkman/                             # Man pages for the 4 checks
packages/netbird                          # MKP manifest
tests/                                    # pytest unit tests and JSON fixtures
build_mkp.sh · lint.sh
```

## Building from source

```bash
./build_mkp.sh            # writes netbird-<version>.mkp, no Checkmk server needed
```

Unlike the other `cmk-*` build scripts this one assembles the MKP locally from `packages/netbird` instead of installing into a site's `local/` first.

### Tests and lint

```bash
pip install pytest ruff mypy pydantic
./lint.sh                 # ruff, mypy, pytest
```

`tests/conftest.py` supplies a small stand-in for `cmk.agent_based.v2` when it is not importable; with a Checkmk 2.5 `cmk` package on the `PYTHONPATH` (or inside a site) the same tests run against the real modules and `tests/test_cmk_api.py` additionally checks the rulesets and the server-side call.

### Testing the agent by hand

```bash
python3 netbird/libexec/agent_netbird --token <PAT> --base-url https://api.netbird.io
```

---

## License

GPLv3 — see [LICENSE](LICENSE)

## Author

Luca-Leon Hausdoerfer — [github.com/blaugrau90](https://github.com/blaugrau90)
