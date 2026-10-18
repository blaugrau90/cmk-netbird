"""Plugin-API conformance. Needs the real cmk packages, skipped otherwise.

Run on a Checkmk site (site python) or with PYTHONPATH pointing at a Checkmk
2.5 `cmk` package tree.
"""

import pytest

pytest.importorskip("cmk.rulesets.v1")
pytest.importorskip("cmk.server_side_calls.v1")
pytest.importorskip("cmk.graphing.v1")
pytest.importorskip("pydantic")

from cmk.server_side_calls.v1 import EnvProxy, HostConfig, NoProxy, Secret, URLProxy
from conftest import load_module

ssc = load_module("netbird/server_side_calls/special_agent.py", "netbird_ssc")
rulesets = load_module("netbird/rulesets/netbird.py", "netbird_rulesets")
load_module("netbird/graphing/netbird.py", "netbird_graphing")


def command(params):
    cmd = list(ssc.special_agent_netbird(params, HostConfig(name="zz-netbird-test-host")))
    assert len(cmd) == 1
    return cmd[0].command_arguments


def test_all_form_specs_build():
    for name in dir(rulesets):
        if name.startswith("rule_spec_"):
            getattr(rulesets, name).parameter_form()


def test_defaults_command_line_uses_password_store_reference_only():
    secret = Secret(7)
    args = command({"token": secret})
    assert "--token-id" in args
    assert args[args.index("--token-id") + 1] == secret
    assert secret.pass_safely is True  # never switched to plain text
    assert "--token" not in args
    assert "--include-users" not in args and "--no-verify-ssl" not in args


def test_all_options():
    args = command({
        "token": Secret(1), "base_url": "https://nb.example", "verify_ssl": False, "timeout": 10,
        "proxy": URLProxy(url="http://proxy:3128"), "include_users": True,
    })
    assert "--no-verify-ssl" in args and "--include-users" in args
    assert args[args.index("--proxy") + 1] == "http://proxy:3128"
    assert "--reference-source" not in args
    assert args[args.index("--base-url") + 1] == "https://nb.example"


def test_no_proxy():
    assert "--no-proxy" in command({"token": Secret(1), "proxy": NoProxy()})
    assert "--proxy" not in command({"token": Secret(1), "proxy": EnvProxy()})
