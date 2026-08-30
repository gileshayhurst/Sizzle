"""Unit tests for Forven API configuration and the tenant echo guard."""

import pytest

import forven_config


def test_source_and_destination_are_separate_pairs(monkeypatch):
    monkeypatch.setenv("FORVEN_PROD_API_KEY", "fvk_prod")
    monkeypatch.setenv("FORVEN_PROD_TENANT_ID", "prod-tenant")
    monkeypatch.setenv("FORVEN_STAGING_API_KEY", "fvk_stg")
    monkeypatch.setenv("FORVEN_STAGING_TENANT_ID", "stg-tenant")

    source = forven_config.source_config()
    destination = forven_config.destination_config()

    assert source.api_key == "fvk_prod"
    assert source.tenant_public_id == "prod-tenant"
    assert "www.forven.ai" in source.base_url
    assert destination.api_key == "fvk_stg"
    assert "staging.forven.ai" in destination.base_url


def test_missing_configuration_is_an_error(monkeypatch):
    monkeypatch.delenv("FORVEN_PROD_API_KEY", raising=False)
    monkeypatch.delenv("FORVEN_PROD_TENANT_ID", raising=False)

    with pytest.raises(forven_config.ConfigError):
        forven_config.source_config()


def test_echo_guard_passes_when_the_name_matches():
    class _Client:
        def list_interviews(self, tenant, **kwargs):
            class _Page:
                tenant_name = "Zoe Enterprises"
            return _Page()

    assert forven_config.assert_tenant(_Client(), "t1", "Zoe Enterprises") == "Zoe Enterprises"


def test_echo_guard_raises_on_the_wrong_org():
    class _Client:
        def list_interviews(self, tenant, **kwargs):
            class _Page:
                tenant_name = "Someone Else"
            return _Page()

    with pytest.raises(forven_config.WrongTenant):
        forven_config.assert_tenant(_Client(), "t1", "Zoe Enterprises")
