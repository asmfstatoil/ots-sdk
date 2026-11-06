import os
from typing import Literal, Optional, Tuple, Union, Dict, Any, Mapping, Sequence
from azure.identity import (
    InteractiveBrowserCredential,
    AuthenticationRecord,
    TokenCachePersistenceOptions,
)
from azure.core.credentials import TokenCredential
import requests
from requests.adapters import HTTPAdapter
import logging

from ots_sdk.helpers import retry
from ots_sdk.models import TimeseriesRequestFailedException
from importlib import metadata
from opentelemetry.instrumentation.requests import RequestsInstrumentor
import platform

ContentType = Literal[
    "application/json", "application/protobuf", "application/x-google-protobuf"
]

RequestType = Literal["get", "put", "post", "patch", "delete"]

# Connections kept alive per host, sized for a moderately threaded caller
DEFAULT_POOL_SIZE = 32

# (connect, read) seconds, so a stalled connection fails and can be retried instead of blocking a thread forever
DEFAULT_TIMEOUT = (10.0, 120.0)

logger = logging.getLogger(__name__)
version = metadata.version("ots_sdk")
system_version_string = (
    f"({platform.system()}; Python {platform.version()})"
    if platform.system()
    else f"(Python {platform.version()})"
)

RequestsInstrumentor().instrument()


def get_interactive_browser_credential(
    tenant_id: Optional[str] = None,
    client_id: Optional[str] = None,
    auth_location: Optional[str] = None,
) -> InteractiveBrowserCredential:
    """Return InteractiveBrowserCredential that will persist token cache.

    Args:
        tenant_id (Optional[str], optional): Tenant id. Defaults to None.
        client_id (Optional[str], optional): Tenant. Defaults to None.
        auth_location (Optional[str], optional): _description_. Defaults to None, which will convert to f"{name}_auth.json" or "msal-bearer_auth.json" if client_id is not set.

    Returns:
        InteractiveBrowserCredential: Credential used to get token.
    """
    if client_id:
        name = client_id
    else:
        name = "ots_sdk"

    if auth_location is None:
        auth_location = f"{name}_auth.json"

    cache_options = TokenCachePersistenceOptions(name=name)

    if os.path.isfile(auth_location):
        try:
            with open(auth_location, "r") as f:
                auth_record = f.read()
        except Exception as e:
            print(
                f"Failed reading authentication record from {auth_location} due to {e}"
            )
            os.remove(auth_location)
            return get_interactive_browser_credential(
                tenant_id=tenant_id, client_id=client_id, auth_location=auth_location
            )

        credential = InteractiveBrowserCredential(
            tenant_id=tenant_id,
            client_id=client_id,  # nb! authentication_record contains client_id and will override this
            cache_persistence_options=TokenCachePersistenceOptions(),
            authentication_record=AuthenticationRecord.deserialize(auth_record),
        )
    else:
        if client_id is None:
            credential = InteractiveBrowserCredential(
                tenant_id=tenant_id, cache_persistence_options=cache_options
            )
        else:
            credential = InteractiveBrowserCredential(
                tenant_id=tenant_id,
                client_id=client_id,
                cache_persistence_options=cache_options,
            )
        auth_record = credential.authenticate()
        with open(auth_location, "w") as f:
            f.write(auth_record.serialize())

    return credential


@retry(logger=logger)
def _request(
    session: requests.Session,
    request_type: RequestType,
    url: str,
    headers: Dict[str, Any],
    payload: Optional[Union[Mapping[str, Any], Sequence[Any]]] = None,
    params: Optional[Mapping[str, Any]] = None,
    timeout: Optional[Union[float, Tuple[float, float]]] = None,
) -> Union[Dict[str, Any], bytes]:

    response = session.request(
        request_type,
        url,
        headers=headers,
        json=payload,
        params=params,
        timeout=timeout,
    )
    if not response.ok:
        raise TimeseriesRequestFailedException(response)
    if "Accept" not in headers or headers["Accept"] == "application/json":
        return response.json()
    else:
        return response.content


class HttpClient:
    def __init__(
        self,
        azure_credential: TokenCredential,
        resource_id: str,
        pool_size: int = DEFAULT_POOL_SIZE,
        timeout: Optional[Union[float, Tuple[float, float]]] = DEFAULT_TIMEOUT,
    ):
        self._azure_credential = azure_credential
        self._resource_id = resource_id
        self._timeout = timeout
        # A shared session keeps TLS connections alive between requests. Size the pool to the number of threads using the client; beyond that requests still succeed, but extra connections are not reused
        self._session = requests.Session()
        adapter = HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def request(
        self,
        request_type: RequestType,
        url: str,
        accept: ContentType = "application/json",
        payload: Optional[Union[Mapping[str, Any], Sequence[Any]]] = None,
        params: Optional[Mapping[str, Any]] = None,
    ) -> Any:

        access_token = self._azure_credential.get_token(
            f"{self._resource_id}/.default"
        )  # handles caching and refreshing internally
        headers = {
            "Authorization": f"Bearer {access_token.token}",
            "Content-Type": "application/json",
            "Accept": accept,
            "User-Agent": f"Omnia Timeseries SDK/{version} {system_version_string}",
        }
        return _request(
            session=self._session,
            request_type=request_type,
            url=url,
            headers=headers,
            payload=payload,
            params=params,
            timeout=self._timeout,
        )
