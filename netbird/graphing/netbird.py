#!/usr/bin/env python3
"""Metric, graph and perfometer definitions for the NetBird plugin."""

from cmk.graphing.v1 import Title
from cmk.graphing.v1.graphs import Graph, MinimalRange
from cmk.graphing.v1.metrics import Color, DecimalNotation, Metric, Unit

UNIT_DAYS = Unit(DecimalNotation("d"))
UNIT_COUNT = Unit(DecimalNotation(""))

metric_netbird_token_days_to_expiry = Metric(
    name="netbird_token_days_to_expiry",
    title=Title("Days until token expiry"),
    unit=UNIT_DAYS,
    color=Color.BLUE,
)

metric_netbird_token_days_unused = Metric(
    name="netbird_token_days_unused",
    title=Title("Days since token was last used"),
    unit=UNIT_DAYS,
    color=Color.ORANGE,
)

metric_netbird_routers_online = Metric(
    name="netbird_routers_online",
    title=Title("Routing peers online"),
    unit=UNIT_COUNT,
    color=Color.GREEN,
)

metric_netbird_routers_total = Metric(
    name="netbird_routers_total",
    title=Title("Routing peers total"),
    unit=UNIT_COUNT,
    color=Color.GRAY,
)

graph_netbird_token_days = Graph(
    name="netbird_token_days",
    title=Title("NetBird token lifetime and usage"),
    minimal_range=MinimalRange(0, 100),
    simple_lines=["netbird_token_days_to_expiry", "netbird_token_days_unused"],
)

graph_netbird_routers = Graph(
    name="netbird_routers",
    title=Title("NetBird routing peers"),
    minimal_range=MinimalRange(0, 2),
    simple_lines=["netbird_routers_total", "netbird_routers_online"],
)
