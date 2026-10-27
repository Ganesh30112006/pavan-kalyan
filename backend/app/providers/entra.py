"""CloudBridge's keyless identity for Microsoft Entra ID (Azure, AKS) and OIDC-trusting clusters.

CloudBridge obtains a short-lived signed token for its own identity (AWS outbound identity federation, or
an OIDC token file). Entra ID exchanges it for an access token of the customer's app registration, which
trusts CloudBridge through a federated identity credential. No client secrets or certificates exist.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable

import httpx

from app.config import Settings
from app.providers.base import ProviderError

logger = logging.getLogger(__name__)

LOGIN = "https://login.microsoftonline.com"
ENTRA_AUDIENCE = "api://AzureADTokenExchange"
_AADSTS = re.compile(r"AADSTS(\d+)")

TokenSource = Callable[[str], str]
"""Returns CloudBridge's signed identity token for an audience (injected in tests)."""


class FederatedIdentity:
    def __init__(
        self,
        settings: Settings,
        client_factory: Callable[[], httpx.Client],
        token_source: TokenSource | None = None,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory
        self._token_source = token_source
        self._lock = threading.Lock()
        self._tokens: dict[str, tuple[str, float]] = {}

    def assertion(self, audience: str = ENTRA_AUDIENCE) -> str:
        """CloudBridge's own signed identity token for `audience`."""
        if self._token_source is not None:
            return self._token_source(audience)
        source = self._settings.azure_token_source
        if source == "none":
            raise ProviderError(
                "platform_not_configured",
                "CloudBridge has no federated identity configured. Ask your CloudBridge administrator to configure it.",
                connection_status="error",
            )
        if source == "file":
            try:
                with open(str(self._settings.azure_token_file), encoding="utf-8") as handle:
                    token = handle.read().strip()
            except OSError:
                raise ProviderError(
                    "platform_not_configured",
                    "CloudBridge could not read its identity token.",
                    connection_status="error",
                ) from None
            if not token:
                raise ProviderError(
                    "platform_not_configured", "CloudBridge's identity token file is empty.", connection_status="error"
                )
            return token
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            response = boto3.client("sts", region_name=self._settings.aws_sts_region).get_web_identity_token(
                Audience=[audience], SigningAlgorithm="RS256", DurationSeconds=300
            )
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            logger.warning("AWS did not issue a web identity token", extra={"code": code})
            raise ProviderError(
                "platform_not_configured",
                "CloudBridge could not obtain its identity token from AWS. "
                "Ask your CloudBridge administrator to check outbound identity federation.",
                connection_status="error",
            ) from None
        except BotoCoreError:
            raise ProviderError(
                "platform_not_configured",
                "CloudBridge has no AWS identity to present.",
                connection_status="error",
            ) from None
        return str(response["WebIdentityToken"])

    def entra_token(self, tenant_id: str, client_id: str, scope: str, connection_id: str) -> str:
        """An access token of the customer's app registration for `scope`, cached until shortly before expiry."""
        key = f"{tenant_id}|{client_id}|{scope}"
        with self._lock:
            cached = self._tokens.get(key)
            if cached and cached[1] > time.monotonic() + 120:
                return cached[0]
        form = {
            "client_id": client_id,
            "scope": scope,
            "grant_type": "client_credentials",
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": self.assertion(ENTRA_AUDIENCE),
        }
        try:
            with self._client_factory() as client:
                response = client.post(f"{LOGIN}/{tenant_id}/oauth2/v2.0/token", data=form)
        except httpx.TimeoutException:
            raise ProviderError("timed_out", "Microsoft Entra ID did not respond in time.", retryable=True) from None
        except httpx.HTTPError:
            raise ProviderError(
                "provider_unavailable", "Microsoft Entra ID could not be reached.", retryable=True
            ) from None
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code == 200 and isinstance(data, dict) and isinstance(data.get("access_token"), str):
            expires = int(data.get("expires_in", 3600))
            with self._lock:
                self._tokens[key] = (str(data["access_token"]), time.monotonic() + expires)
            return str(data["access_token"])
        if response.status_code >= 500 or response.status_code == 429:
            raise ProviderError("provider_unavailable", "Microsoft Entra ID is unavailable.", retryable=True)
        description = str(data.get("error_description", "")) if isinstance(data, dict) else ""
        codes = set(_AADSTS.findall(description))
        if isinstance(data, dict) and isinstance(data.get("error_codes"), list):
            codes |= {str(code) for code in data["error_codes"]}
        logger.info("Entra ID token exchange failed", extra={"aadsts": sorted(codes), "connection_id": connection_id})
        if codes & {"90002", "900023"}:
            message = "The Azure tenant was not found. Check the tenant ID."
        elif codes & {"700016"}:
            message = "The app registration was not found in this tenant. Check the client ID."
        elif codes & {"70021", "70025", "700211", "700212", "700213", "700222"}:
            message = (
                "The app registration has no federated credential matching CloudBridge's issuer, subject and "
                "audience. Check the federated credential."
            )
        elif codes & {"7000215", "700027", "50013"}:
            message = "Microsoft Entra ID rejected CloudBridge's identity token."
        else:
            message = "Microsoft Entra ID did not issue a token for the app registration."
        raise ProviderError("invalid_credentials", message)
