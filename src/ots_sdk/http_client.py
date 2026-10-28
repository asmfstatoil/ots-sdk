import os
import time
import threading
from typing import Literal, Optional, Union, Dict, Any, Mapping, Sequence
from azure.identity import (
    InteractiveBrowserCredential,
    AuthenticationRecord,
    TokenCachePersistenceOptions,
)
from azure.core.credentials import TokenCredential
import requests
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
    request_type: RequestType,
    url: str,
    headers: Dict[str, Any],
    payload: Optional[Union[Mapping[str, Any], Sequence[Any]]] = None,
    params: Optional[Mapping[str, Any]] = None,
) -> Union[Dict[str, Any], bytes]:

    response = requests.request(
        request_type, url, headers=headers, json=payload, params=params
    )
    if not response.ok:
        raise TimeseriesRequestFailedException(response)
    if "Accept" not in headers or headers["Accept"] == "application/json":
        return response.json()
    else:
        return response.content


# Refresh this many seconds before the token actually expires.
TOKEN_EXPIRY_MARGIN_SECONDS = 300


class HttpClient:
    def __init__(self, azure_credential: TokenCredential, resource_id: str):
        self._azure_credential = azure_credential
        self._resource_id = resource_id
        self._token: Optional[str] = None
        self._token_expires_on: float = 0.0
        self._token_lock = threading.Lock()

    def _get_token(self, force_refresh: bool = False) -> str:
        with self._token_lock:
            if (
                force_refresh
                or self._token is None
                or time.time() >= self._token_expires_on - TOKEN_EXPIRY_MARGIN_SECONDS
            ):
                access_token = self._azure_credential.get_token(
                    f"{self._resource_id}/.default"
                )
                self._token = access_token.token
                self._token_expires_on = float(access_token.expires_on)
            return self._token

    def _send(
        self,
        token: str,
        request_type: RequestType,
        url: str,
        accept: ContentType,
        payload: Optional[Union[Mapping[str, Any], Sequence[Any]]],
        params: Optional[Mapping[str, Any]],
    ) -> Any:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": accept,
            "User-Agent": f"Omnia Timeseries SDK/{version} {system_version_string}",
        }
        return _request(
            request_type=request_type,
            url=url,
            headers=headers,
            payload=payload,
            params=params,
        )

    def request(
        self,
        request_type: RequestType,
        url: str,
        accept: ContentType = "application/json",
        payload: Optional[Union[Mapping[str, Any], Sequence[Any]]] = None,
        params: Optional[Mapping[str, Any]] = None,
    ) -> Any:

        try:
            return self._send(
                self._get_token(), request_type, url, accept, payload, params
            )
        except TimeseriesRequestFailedException as e:
            if e.status_code != 401:
                raise
            logger.debug("Request unauthorized, refreshing token and retrying once")
            return self._send(
                self._get_token(force_refresh=True),
                request_type,
                url,
                accept,
                payload,
                params,
            )
