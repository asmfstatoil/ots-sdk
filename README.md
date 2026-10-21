# Omnia Timeseries Python SDK

Python package for interacting with the [Omnia Industrial IoT Timeseries API](https://github.com/equinor/OmniaPlant/wiki).

Forked version of Official python sdk available at https://github.com/equinor/omnia-timeseries-python.

## Installation

Install from PyPI:

```
pip install ots-sdk
```

Install from a specific Git tag (for example, if you need an earlier SDK version):

```
pip install git+https://github.com/equinor/omnia-timeseries-python.git@vX.Y.Z
```

Supported Python versions: 3.10+

For support, create an issue on GitHub.

## Example usage

For fundamental questions please refer to the MSAL documentation which has [code examples for multiple programming languages and scenarios](https://learn.microsoft.com/en-us/entra/identity-platform/sample-v2-code?tabs=apptype).

You should also familiarize yourself with the [azure.identity](https://learn.microsoft.com/en-us/python/api/overview/azure/identity-readme?view=azure-python) package, which we will use below.

Follow [usage examples](https://github.com/equinor/omnia-timeseries-python/blob/main/examples/usage_examples.ipynb) to learn how to retrieve data in `Json` format.  
Follow [get data as protobuf example](https://github.com/equinor/omnia-timeseries-python/blob/main/examples/get_data_protobuf.ipynb) to learn how to retrieve data in `Protobuf` format.

### Preparing Azure authentication

Read https://github.com/equinor/OmniaPlant/wiki/Authentication-&-Authorization to familiarize yourself with how Timeseries API handles authentication and authorization.

The `TimeseriesAPI` client accepts any `azure.identity` credential that inherits from `TokenCredential`, so you can pick the flow that suits your environment. The options we currently cover are:
- **User Impersonation with interactive login**
This is the primary reason for this fork existing. Using msal and a public azure application that supports user impersonation.
- **Service principal with client secret** (`ClientSecretCredential`) for headless service-to-service scenarios.
- **Service principal with certificate** (`ClientCertificateCredential`) when you prefer cert-based authentication instead of a secret.
- **Managed identities / default credential chain** (`ManagedIdentityCredential` or `DefaultAzureCredential`) when running inside Azure.
- **Interactive browser login** (`InteractiveBrowserCredential`) for local development where you can complete the sign-in with a browser.
- **User impersonation** (`DeviceCodeCredential` or other interactive flows that acquire a user token) when you need to act on behalf of a signed-in user; this requires the Timeseries API app to consent to the `user_impersonation` scope.
- **On-behalf-of flow** (`OnBehalfOfCredential`) when a middle-tier service needs to exchange a signed-in user's token for a Timeseries API token and act with delegated permissions.

The supported credential setups are shown below.

#### With user impersonation

Read [Authenticating by user impersonation without any shared secret (For people with Equinor accounts)](https://github.com/equinor/OmniaPlant/wiki/Authentication-&-Authorization#authenticating-by-user-impersonation-without-any-shared-secret-for-people-with-equinor-accounts) and ensure prerequisite steps have been done.

```python
client = ots.TimeseriesAPI()
```

During authentication, this will open a web page with no user action needed if authentication went right.

#### With service principal credentials (client secret)

Read [Service-to-service using a shared secret](https://github.com/equinor/OmniaPlant/wiki/Authentication-&-Authorization#service-to-service-using-a-shared-secret) and ensure prerequisite steps have been done.

```python
from azure.identity import ClientSecretCredential
import os
credentials = ClientSecretCredential(
    tenant_id=os.environ['AZURE_TENANT_ID'],
    client_id=os.environ['AZURE_CLIENT_ID'],
    client_secret=os.environ['AZURE_CLIENT_SECRET']
)
```

#### With service principal credentials (client certificate)

Use a certificate instead of a shared secret when you want a stronger identity proof and avoid storing plain secrets.

```python
from azure.identity import ClientCertificateCredential
import os
credentials = ClientCertificateCredential(
    tenant_id=os.environ['AZURE_TENANT_ID'],
    client_id=os.environ['AZURE_CLIENT_ID'],
    certificate_path=os.environ['AZURE_CLIENT_CERT_PATH']
)
```

#### With interactive browser login

When developing locally, you can open a browser window and sign in manually.

```python
from azure.identity import InteractiveBrowserCredential
import os
credentials = InteractiveBrowserCredential(
    tenant_id=os.environ['AZURE_TENANT_ID'],
    client_id=os.environ['AZURE_CLIENT_ID']
)
```

#### With user impersonation (device code)

Use a device code or other interactive credential that requests the `user_impersonation` scope so the signed-in user is impersonated by the Timeseries API.

```python
from azure.identity import DeviceCodeCredential
import os
credentials = DeviceCodeCredential(
    tenant_id=os.environ['AZURE_TENANT_ID'],
    client_id=os.environ['AZURE_CLIENT_ID']
)
```

#### With managed / default credentials

Read [Managed Service Identity (For Equinor applications in Azure)](https://github.com/equinor/OmniaPlant/wiki/Authentication-&-Authorization#managed-service-identity-for-equinor-applications-in-azure) and ensure prerequisite steps have been done.

```python
from azure.identity import DefaultAzureCredential
credentials = DefaultAzureCredential()
```

#### With on-behalf-of flow

Use `OnBehalfOfCredential` when a backend service receives a user token (the user assertion) and needs to call the Timeseries API on that user's behalf.

```python
from azure.identity import OnBehalfOfCredential
import os
credentials = OnBehalfOfCredential(
    tenant_id=os.environ['AZURE_TENANT_ID'],
    client_id=os.environ['AZURE_CLIENT_ID'],
    client_secret=os.environ['AZURE_CLIENT_SECRET'],
    user_assertion=os.environ['USER_ASSERTION']
)
```

### Output

The `Json` response from Timeseries API looks like this:
```
>> {'items': [{'id': 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx', 'datapoints': [{'time': '2021-02-01T09:54:05.4200000Z', 'value': -0.000286102294921875, 'status': 192}]}]}
```

The `Protobuf` response from Timeseries API looks like this:
```
{ "data": [ { "id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx", "totalCount": "1", "fields": [ "time", "value", "status" ], "values": [ { "int64": "1727263834898000000" }, { "double": 246.56092834472656 }, { "uint32": 192 } ] } ] }
```

### Convert JSON responses to a DataFrame

```python
from ots_sdk.helpers import _ots_response_to_frame

response = client.get_datapoints(timeseries_id)
frame = _ots_response_to_frame(response, column_name="measurement", value_key="value")
```

The helper converts all series in a JSON `data.items` response to a pandas
DataFrame indexed by `Europe/Oslo` timestamps. Series are aligned on the union
of their timestamps; missing measurements remain `NaN`, without interpolation
or forward filling. It sorts timestamps, keeps the first value for duplicate
timestamps within each series, and returns an empty DataFrame (`frame.empty`
is `True`) when there are no items or datapoints.

For multiple series, columns default to the series IDs. Pass a mapping from
series IDs to column names to rename them:

```python
frame = _ots_response_to_frame(
    response,
    column_name={pressure_id: "pressure", temperature_id: "temperature"},
    value_key="avg",
)
```

A string `column_name` renames a single-series response; multiple-series
responses use IDs instead. For aggregated data, pass the aggregate field
(for example, `"avg"`) as `value_key`; missing or null values fall back to the
`"value"` field. API methods continue to return their original responses.

### Convert a single series to a pandas Series

Use `TimeseriesAPI.read()` to fetch datapoints directly as a pandas Series:

```python
series = client.read(timeseries_id, startTime=start_time, endTime=end_time)
```

`read()` accepts the same parameters as `get_datapoints()` and forwards them
to that method. It requires `accept="application/json"`; Protobuf output is
not supported. The response must contain exactly one item. The returned Series
is named with the item ID, indexed in `Europe/Oslo`, sorted, and deduplicated.
An item with no datapoints returns an empty Series.

Use `read_aggregates()` for an aggregated Series:

```python
series = client.read_aggregates(
    timeseries_id,
    aggregateFunction=["avg"],
    startTime=start_time,
    endTime=end_time,
    processingInterval="1h",
)
```

It accepts the same parameters as `get_aggregates()`, including `alignToCache`,
but requires JSON output and exactly one aggregate function. The requested
aggregate field supplies the Series values; timestamp and empty-item handling
are the same as `read()`.

To convert an existing response:

```python
from ots_sdk.helpers import _ots_response_to_series

series = _ots_response_to_series(response, name="measurement", value_key="value")
```

This helper requires exactly one item in `data.items` and raises `ValueError`
for zero or multiple items. It uses the same timestamp normalization,
deduplication, sorting, and value fallback as the DataFrame helper. The Series
name defaults to the item ID. An item with no datapoints returns an empty
Series (`series.empty` is `True`) with an Oslo-time index.

### Read timeseries metadata

```python
items = client.get_metadata(timeseries_id)
for item in items:
    print(item.get("name"), item.get("description"), item.get("unit"))
```

Look up metadata by name or tag number instead of ID:

```python
items = client.get_metadata(name=tag_name, source=source, facility=facility_code)
items = client.get_metadata(tag_no=tag_number, source=source)
```

`get_metadata(id)` wraps `get_timeseries_by_id(id)`. Name and tag-number
lookups wrap `get_timeseries()`, with `tag_no` as an alias for `name` and
optional `source` and `facility` filters. Provide either an ID or name/tag
filters, not both; if both `name` and `tag_no` are supplied, they must match.
The method returns the metadata items directly as a list, without the
`data.items` envelope, or `[]` when no items are present. All returned fields
are preserved. Name/tag lookups return one response page, not all pages.

### Other use cases

Please consult the [API Reference](https://api.equinor.com/api-details#api=Timeseries-api-v1-7) for a full overview of the API endpoints.
