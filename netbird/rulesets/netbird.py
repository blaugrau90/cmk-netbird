#!/usr/bin/env python3
"""Rulesets for the NetBird special agent and its checks."""

from cmk.rulesets.v1 import Help, Label, Title
from cmk.rulesets.v1.form_specs import (
    BooleanChoice,
    DefaultValue,
    DictElement,
    Dictionary,
    Integer,
    LevelDirection,
    MatchingScope,
    Password,
    Proxy,
    ProxySchema,
    RegularExpression,
    ServiceState,
    SimpleLevels,
    String,
    TimeMagnitude,
    TimeSpan,
    migrate_to_password,
    migrate_to_proxy,
    validators,
)
from cmk.rulesets.v1.rule_specs import (
    CheckParameters,
    DiscoveryParameters,
    HostAndItemCondition,
    HostCondition,
    SpecialAgent,
    Topic,
)

DAY = 86400.0
_DAYS = [TimeMagnitude.DAY]


# --------------------------------------------------------------------------
# Special agent
# --------------------------------------------------------------------------


def _special_agent_formspec() -> Dictionary:
    return Dictionary(
        title=Title("NetBird"),
        help_text=Help(
            "Monitors a NetBird account through its REST API: personal access tokens "
            "(expiry, last use) and the routing peers of every network. The special agent "
            "only reads from the API. Use a dedicated service user and a token of that user."
        ),
        elements={
            "token": DictElement(
                required=True,
                parameter_form=Password(
                    title=Title("NetBird API token"),
                    help_text=Help(
                        "A personal access token, preferably of a service user. Stored in the "
                        "Checkmk password store - choose an entry there."
                    ),
                    migrate=migrate_to_password,
                ),
            ),
            "base_url": DictElement(
                required=True,
                parameter_form=String(
                    title=Title("API base URL"),
                    help_text=Help(
                        "NetBird Cloud uses https://api.netbird.io. For self-hosted "
                        "installations enter the URL of your management server, "
                        "without a trailing /api."
                    ),
                    prefill=DefaultValue("https://api.netbird.io"),
                    custom_validate=(validators.LengthInRange(min_value=1),),
                ),
            ),
            "verify_ssl": DictElement(
                required=True,
                parameter_form=BooleanChoice(
                    title=Title("Verify SSL certificate"),
                    label=Label("Verify the server certificate"),
                    prefill=DefaultValue(True),
                ),
            ),
            "timeout": DictElement(
                required=True,
                parameter_form=Integer(
                    title=Title("Request timeout (seconds)"),
                    help_text=Help(
                        "Timeout per HTTP request. The agent makes one request per user "
                        "plus a handful of others."
                    ),
                    prefill=DefaultValue(30),
                    custom_validate=(validators.NumberInRange(min_value=1, max_value=300),),
                ),
            ),
            "proxy": DictElement(
                required=False,
                parameter_form=Proxy(
                    allowed_schemas=frozenset({ProxySchema.HTTP, ProxySchema.HTTPS}),
                    migrate=migrate_to_proxy,
                ),
            ),
            "include_users": DictElement(
                required=False,
                parameter_form=BooleanChoice(
                    title=Title("Include tokens of regular users"),
                    label=Label("Also monitor personal access tokens of regular (non-service) users"),
                    help_text=Help(
                        "By default only tokens of service users are read. Reading the tokens "
                        "of regular users needs an admin/owner role and one extra request per user."
                    ),
                    prefill=DefaultValue(False),
                ),
            ),
        },
    )


rule_spec_netbird_agent = SpecialAgent(
    topic=Topic.CLOUD,
    name="netbird",
    title=Title("NetBird"),
    parameter_form=_special_agent_formspec,
)


# --------------------------------------------------------------------------
# Check parameters
# --------------------------------------------------------------------------


def _api_formspec() -> Dictionary:
    return Dictionary(
        title=Title("NetBird API"),
        elements={
            "own_token_levels": DictElement(
                required=True,
                parameter_form=SimpleLevels(
                    title=Title("Remaining lifetime of the agent's own token"),
                    help_text=Help(
                        "The API does not say which token a request used. The agent assumes it "
                        "is the most recently used, unexpired token of the authenticating user."
                    ),
                    form_spec_template=TimeSpan(displayed_magnitudes=_DAYS),
                    level_direction=LevelDirection.LOWER,
                    prefill_fixed_levels=DefaultValue((14 * DAY, 7 * DAY)),
                ),
            ),
        },
    )


rule_spec_netbird_api_params = CheckParameters(
    name="netbird_api",
    topic=Topic.CLOUD,
    title=Title("NetBird API"),
    parameter_form=_api_formspec,
    condition=HostCondition(),
)


def _token_formspec() -> Dictionary:
    return Dictionary(
        title=Title("NetBird token"),
        elements={
            "expiry_levels": DictElement(
                required=True,
                parameter_form=SimpleLevels(
                    title=Title("Remaining lifetime"),
                    help_text=Help("Alert when the token expires within this time."),
                    form_spec_template=TimeSpan(displayed_magnitudes=_DAYS),
                    level_direction=LevelDirection.LOWER,
                    prefill_fixed_levels=DefaultValue((14 * DAY, 7 * DAY)),
                ),
            ),
            "unused_warn": DictElement(
                required=True,
                parameter_form=TimeSpan(
                    title=Title("Warn if unused for"),
                    help_text=Help(
                        "Time since the token was last used. A token that was never used counts "
                        "from its creation."
                    ),
                    displayed_magnitudes=_DAYS,
                    prefill=DefaultValue(90 * DAY),
                ),
            ),
            "unused_crit": DictElement(
                required=False,
                parameter_form=TimeSpan(
                    title=Title("Critical if unused for"),
                    help_text=Help("Disabled by default."),
                    displayed_magnitudes=_DAYS,
                    prefill=DefaultValue(180 * DAY),
                ),
            ),
            "ignore_expired_after": DictElement(
                required=False,
                parameter_form=TimeSpan(
                    title=Title("Ignore tokens expired for more than"),
                    help_text=Help(
                        "An expired token is CRITICAL. Once it has been expired for longer than "
                        "this, the service turns OK, so forgotten old tokens do not alert forever."
                    ),
                    displayed_magnitudes=_DAYS,
                    prefill=DefaultValue(30 * DAY),
                ),
            ),
        },
    )


rule_spec_netbird_token_params = CheckParameters(
    name="netbird_token",
    topic=Topic.CLOUD,
    title=Title("NetBird token"),
    parameter_form=_token_formspec,
    condition=HostAndItemCondition(item_title=Title("Token (user/name)")),
)


def _version_formspec() -> Dictionary:
    return Dictionary(
        title=Title("NetBird management version"),
        help_text=Help(
            "Compares the running management server version with the latest available one "
            "(from GET /api/instance/version). NetBird is at 0.x, so the minor version counts."
        ),
        elements={
            "minor_levels": DictElement(
                required=True,
                parameter_form=SimpleLevels(
                    title=Title("Minor versions behind the latest version"),
                    form_spec_template=Integer(),
                    level_direction=LevelDirection.UPPER,
                    prefill_fixed_levels=DefaultValue((1, 3)),
                ),
            ),
            "major_state": DictElement(
                required=True,
                parameter_form=ServiceState(
                    title=Title("State if the management server is a major version behind"),
                    prefill=DefaultValue(ServiceState.CRIT),
                ),
            ),
            "update_state": DictElement(
                required=True,
                parameter_form=ServiceState(
                    title=Title("State if only a patch update is available"),
                    prefill=DefaultValue(ServiceState.OK),
                ),
            ),
        },
    )


rule_spec_netbird_version_params = CheckParameters(
    name="netbird_version",
    topic=Topic.CLOUD,
    title=Title("NetBird management version"),
    parameter_form=_version_formspec,
    condition=HostCondition(),
)


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def _token_discovery_formspec() -> Dictionary:
    return Dictionary(
        title=Title("NetBird token discovery"),
        help_text=Help(
            "Restrict which tokens become services, e.g. to leave out spare tokens. "
            "Regular expressions are matched against the token name only (not the user)."
        ),
        elements={
            "name_include": DictElement(
                required=False,
                parameter_form=RegularExpression(
                    title=Title("Only tokens whose name matches"),
                    predefined_help_text=MatchingScope.INFIX,
                ),
            ),
            "name_exclude": DictElement(
                required=False,
                parameter_form=RegularExpression(
                    title=Title("Skip tokens whose name matches"),
                    predefined_help_text=MatchingScope.INFIX,
                ),
            ),
        },
    )


rule_spec_netbird_token_discovery = DiscoveryParameters(
    name="netbird_token_discovery",
    topic=Topic.CLOUD,
    title=Title("NetBird token discovery"),
    parameter_form=_token_discovery_formspec,
)
