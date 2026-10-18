"""Test infrastructure for the NetBird Checkmk plugin.

The real `cmk.agent_based.v2` module only exists inside a Checkmk site's
Python environment. To unit-test the check plugin logic without a full
Checkmk installation, a minimal stand-in module is registered in
`sys.modules` before the plugin module is imported. It reproduces just the
runtime surface the plugin actually uses - it is not a general Checkmk API
stub. (Inside a site the real module is used, so the same tests run there.)
"""

import importlib.util
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import ModuleType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"


def _install_fake_cmk_agent_based_v2() -> None:
    try:
        import cmk.agent_based.v2  # noqa: F401

        return
    except ImportError:
        pass

    class State(Enum):
        OK = 0
        WARN = 1
        CRIT = 2
        UNKNOWN = 3

    @dataclass
    class Result:
        state: "State"
        summary: str | None = None
        notice: str | None = None
        details: str | None = None

    @dataclass
    class Service:
        item: str | None = None

    @dataclass
    class Metric:
        name: str
        value: float
        levels: Any = None
        boundaries: Any = None

    class _Plugin:
        def __init__(self, **kwargs: Any) -> None:
            self.__dict__.update(kwargs)

    def _timespan(seconds: float) -> str:
        # Mirrors the shape of cmk.agent_based.v2.render.timespan ("3 days 0 hours").
        seconds = int(seconds)
        days, rest = divmod(seconds, 86400)
        hours, rest = divmod(rest, 3600)
        minutes, secs = divmod(rest, 60)
        if days:
            return f"{days} days {hours} hours"
        if hours:
            return f"{hours} hours {minutes} minutes"
        if minutes:
            return f"{minutes} minutes {secs} seconds"
        return f"{secs} seconds"

    class _Render:
        timespan = staticmethod(_timespan)

        @staticmethod
        def date(ts: float) -> str:
            import time

            return time.strftime("%Y-%m-%d", time.gmtime(ts))

    v2 = ModuleType("cmk.agent_based.v2")
    v2.State = State  # type: ignore[attr-defined]
    v2.Result = Result  # type: ignore[attr-defined]
    v2.Service = Service  # type: ignore[attr-defined]
    v2.Metric = Metric  # type: ignore[attr-defined]
    v2.AgentSection = _Plugin  # type: ignore[attr-defined]
    v2.CheckPlugin = _Plugin  # type: ignore[attr-defined]
    v2.render = _Render()  # type: ignore[attr-defined]
    v2.StringTable = list  # type: ignore[attr-defined]
    v2.CheckResult = Iterable  # type: ignore[attr-defined]
    v2.DiscoveryResult = Iterable  # type: ignore[attr-defined]

    agent_based_pkg = ModuleType("cmk.agent_based")
    agent_based_pkg.v2 = v2  # type: ignore[attr-defined]
    cmk_pkg = ModuleType("cmk")
    cmk_pkg.agent_based = agent_based_pkg  # type: ignore[attr-defined]
    sys.modules.setdefault("cmk", cmk_pkg)
    sys.modules["cmk.agent_based"] = agent_based_pkg
    sys.modules["cmk.agent_based.v2"] = v2


_install_fake_cmk_agent_based_v2()


def load_module(relative_path: str, module_name: str) -> ModuleType:
    """Load a plugin file (which may have no .py extension) as a module by path."""
    path = REPO_ROOT / relative_path
    loader = SourceFileLoader(module_name, str(path))
    spec = importlib.util.spec_from_loader(module_name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module
