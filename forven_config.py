"""Two credential pairs, never mixed, plus the tenant echo guard.

Sources are read from PRODUCTION; reels are delivered to STAGING. Keys are
per-environment and a key from one environment means nothing in the other.
"""

import os
from dataclasses import dataclass

from forven_api import PRODUCTION_BASE, STAGING_BASE


class ConfigError(RuntimeError):
    """Required Forven API configuration is missing."""


class WrongTenant(RuntimeError):
    """The tenant echo did not match the expected organization."""


@dataclass
class Endpoint:
    base_url: str
    api_key: str
    tenant_public_id: str


def _require(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise ConfigError(f"{name} is not set.")
    return value


def source_config() -> Endpoint:
    """Production: where interviews are read from."""
    return Endpoint(PRODUCTION_BASE, _require("FORVEN_PROD_API_KEY"),
                    _require("FORVEN_PROD_TENANT_ID"))


def destination_config() -> Endpoint:
    """Staging: where finished reels are delivered."""
    return Endpoint(STAGING_BASE, _require("FORVEN_STAGING_API_KEY"),
                    _require("FORVEN_STAGING_TENANT_ID"))


def assert_tenant(client, tenant_public_id: str, expected_name: str) -> str:
    """Fail fast if the configured tenant is not the org we meant.

    A wrong-but-valid tenant id returns another org's data silently, so this is
    checked on startup rather than discovered in the output.
    """
    page = client.list_interviews(tenant_public_id, page_size=1)
    if page.tenant_name != expected_name:
        raise WrongTenant(
            f"tenant {tenant_public_id} is {page.tenant_name!r}, expected {expected_name!r}"
        )
    return page.tenant_name
