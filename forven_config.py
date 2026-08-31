"""Resolving which Forven tenants to act on, and with which key.

The split matters:

  * API KEYS live in the environment. They are the application's credentials,
    one per Forven environment, and they are secrets.
  * TENANTS live in the database (sz_tenant_pairs). Whose interviews and where
    the reels go is the thing that varies between customers, so it is data -
    adding another organisation is a row, not a redeploy.

Keys are per-environment and never crossed: a staging key means nothing
against production.

The environment-only path below remains as a fallback so a deployment works
before any tenant pair has been configured.
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


def key_for_env(env: str) -> str:
    """The application's API key for one Forven environment."""
    name = "FORVEN_STAGING_API_KEY" if env == "staging" else "FORVEN_PROD_API_KEY"
    return _require(name)


def base_for_env(env: str) -> str:
    return STAGING_BASE if env == "staging" else PRODUCTION_BASE


def endpoints_for_pair(pair: dict) -> tuple:
    """(source, destination) endpoints for a configured tenant pair.

    The pair supplies the environments and tenant ids; the environment supplies
    the matching key.
    """
    source = Endpoint(
        base_for_env(pair["source_env"]),
        key_for_env(pair["source_env"]),
        pair["source_tenant_id"],
    )
    destination = Endpoint(
        base_for_env(pair["dest_env"]),
        key_for_env(pair["dest_env"]),
        pair["dest_tenant_id"],
    )
    return source, destination


def source_env() -> str:
    """Which environment interviews are read from: 'production' or 'staging'.

    Production is the real answer. Staging exists so the interface can be built
    and exercised against real data before the production tenant id is
    confirmed - the client is environment-agnostic, so switching back is a
    config change rather than a code change.
    """
    value = (os.environ.get("FORVEN_SOURCE_ENV") or "production").strip().lower()
    return "staging" if value == "staging" else "production"


def source_config() -> Endpoint:
    """Where interviews are read from. Production unless overridden."""
    if source_env() == "staging":
        return destination_config()
    return Endpoint(PRODUCTION_BASE, _require("FORVEN_PROD_API_KEY"),
                    _require("FORVEN_PROD_TENANT_ID"))


def source_expected_name() -> str:
    """Org name the source tenant should echo, for the guard. May be blank."""
    key = ("FORVEN_STAGING_TENANT_NAME" if source_env() == "staging"
           else "FORVEN_PROD_TENANT_NAME")
    return (os.environ.get(key) or "").strip()


def destination_config() -> Endpoint:
    """Staging: where finished reels are delivered."""
    return Endpoint(STAGING_BASE, _require("FORVEN_STAGING_API_KEY"),
                    _require("FORVEN_STAGING_TENANT_ID"))


def destination_expected_name() -> str:
    """Org name the destination tenant should echo. May be blank."""
    return (os.environ.get("FORVEN_STAGING_TENANT_NAME") or "").strip()


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
