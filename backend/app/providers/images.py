"""Container image references and digest resolution over the OCI distribution API.

Only well-known registry hosts are contacted (the caller passes the allowlist), and token
endpoints advertised by a registry must also be on an allowlist, so a crafted image reference
cannot make CloudBridge send requests to arbitrary or internal addresses.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from app.providers.base import ProviderError, ResolvedImage

DIGEST = re.compile(r"(?a)^sha256:[a-f0-9]{64}$")
MANIFEST_TYPES = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)
_AUTH_PARAM = re.compile(r'(\w+)="([^"]*)"')


@dataclass(frozen=True)
class ImageRef:
    host: str
    path: str
    tag: str | None
    digest: str | None

    @property
    def repository(self) -> str:
        return f"{self.host}/{self.path}"

    @property
    def reference(self) -> str:
        """What to ask the registry for: the digest when pinned, else the tag (default 'latest')."""
        return self.digest or self.tag or "latest"


def parse_image(image: str) -> ImageRef:
    """Parses host/path[:tag][@sha256:digest]. The reference is assumed to be syntactically validated."""
    digest: str | None = None
    rest = image
    if "@" in rest:
        rest, digest = rest.split("@", 1)
    host, _, path = rest.partition("/")
    tag: str | None = None
    last = path.rsplit("/", 1)[-1]
    if ":" in last:
        path, tag = path.rsplit(":", 1)
    return ImageRef(host=host, path=path, tag=tag, digest=digest)


def is_digest(value: str | None) -> bool:
    return bool(value and DIGEST.match(value))


class OciResolver:
    def __init__(
        self,
        realm_allowed: Callable[[str], bool],
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self._realm_allowed = realm_allowed
        self._client_factory = client_factory or (lambda: httpx.Client(timeout=10.0, follow_redirects=False))

    def resolve(self, ref: ImageRef, api_host: str, path: str, basic: tuple[str, str] | None = None) -> ResolvedImage:
        url = f"https://{api_host}/v2/{path}/manifests/{ref.reference}"
        headers = {"Accept": MANIFEST_TYPES}
        try:
            with self._client_factory() as client:
                response = client.head(url, headers=headers)
                if response.status_code == 401:
                    token = self._token(client, response.headers.get("www-authenticate", ""), basic)
                    headers["Authorization"] = f"Bearer {token}"
                    response = client.head(url, headers=headers)
                if response.status_code == 405:
                    response = client.get(url, headers=headers)
                digest = self._digest(client, url, headers, response)
        except httpx.TimeoutException:
            raise ProviderError("timed_out", "The image registry did not respond in time.", retryable=True) from None
        except httpx.HTTPError:
            raise ProviderError(
                "provider_unavailable", "The image registry could not be reached.", retryable=True
            ) from None
        if ref.digest and digest != ref.digest:
            raise ProviderError("image_not_found", f"The registry has no image with digest {ref.digest}.")
        return ResolvedImage(repository=ref.repository, digest=digest)

    def _digest(self, client: httpx.Client, url: str, headers: dict[str, str], response: httpx.Response) -> str:
        status = response.status_code
        if status in (401, 403):
            raise ProviderError(
                "image_access_denied", "CloudBridge is not allowed to read this image. Check the registry permissions."
            )
        if status == 404:
            raise ProviderError("image_not_found", "The image or tag was not found in the registry.")
        if status == 429 or status >= 500:
            raise ProviderError("provider_unavailable", "The image registry is unavailable.", retryable=True)
        if status != 200:
            raise ProviderError("image_not_found", "The registry did not return the image manifest.")
        digest = response.headers.get("docker-content-digest")
        if is_digest(digest):
            return str(digest)
        # Some registries omit the header on HEAD: the digest is the hash of the manifest bytes.
        body = response.content if response.request.method == "GET" else client.get(url, headers=headers).content
        return f"sha256:{hashlib.sha256(body).hexdigest()}"

    def _token(self, client: httpx.Client, challenge: str, basic: tuple[str, str] | None) -> str:
        if not challenge.lower().startswith("bearer "):
            raise ProviderError(
                "image_access_denied", "The image registry requires credentials CloudBridge does not have."
            )
        params = dict(_AUTH_PARAM.findall(challenge))
        realm = params.pop("realm", "")
        parts = urlsplit(realm)
        if parts.scheme != "https" or not parts.hostname or not self._realm_allowed(parts.hostname):
            raise ProviderError(
                "image_access_denied", "The image registry asked for authentication at an unexpected address."
            )
        response = client.get(realm, params=params, auth=basic)
        if response.status_code in (401, 403):
            raise ProviderError("image_access_denied", "CloudBridge is not allowed to read this image.")
        if response.status_code != 200:
            raise ProviderError("provider_unavailable", "The image registry did not issue a token.", retryable=True)
        try:
            data = response.json()
        except ValueError:
            data = None
        token = (data.get("token") or data.get("access_token")) if isinstance(data, dict) else None
        if not isinstance(token, str) or not token:
            raise ProviderError("provider_unavailable", "The image registry did not issue a token.", retryable=True)
        return token
