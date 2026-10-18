#!/usr/bin/env python3
"""Server-side calls for the NetBird special agent."""

from collections.abc import Iterator

from cmk.server_side_calls.v1 import (
    EnvProxy,
    HostConfig,
    NoProxy,
    Secret,
    SpecialAgentCommand,
    SpecialAgentConfig,
    URLProxy,
)
from pydantic import BaseModel


class Params(BaseModel):
    token: Secret
    base_url: str = "https://api.netbird.io"
    verify_ssl: bool = True
    timeout: int = 30
    proxy: URLProxy | NoProxy | EnvProxy | None = None
    include_users: bool = False
    reference: tuple[str, str | None] = ("github_fallback", None)


def _agent_arguments(
    params: Params, host_config: HostConfig
) -> Iterator[SpecialAgentCommand]:
    # Only the password store reference goes on the command line (--token-id);
    # the agent resolves the secret itself, so the token never appears in the
    # command line, the fetcher configuration or the process list.
    args: list[str | Secret] = [
        "--base-url", params.base_url,
        "--token-id", params.token,
        "--timeout", str(params.timeout),
    ]
    if not params.verify_ssl:
        args.append("--no-verify-ssl")
    if isinstance(params.proxy, URLProxy):
        args += ["--proxy", params.proxy.url]
    elif isinstance(params.proxy, NoProxy):
        args.append("--no-proxy")
    if params.include_users:
        args.append("--include-users")

    source, value = params.reference
    args += ["--reference-source", source]
    if source == "configured" and value:
        args += ["--reference-version", value]

    yield SpecialAgentCommand(command_arguments=args)


special_agent_netbird = SpecialAgentConfig(
    name="netbird",
    parameter_parser=Params.model_validate,
    commands_function=_agent_arguments,
)
