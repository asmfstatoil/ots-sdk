import copy
import inspect
import json
from urllib import parse

import pandas as pd
import requests_mock
import pytest
from azure.core.credentials import AccessToken
from ots_sdk import TimeseriesAPI, TimeseriesEnvironment
from ots_sdk.api import FederationSource, IMSSubscriptionsManagementAPI
from ots_sdk.http_client import HttpClient, TimeseriesRequestFailedException


class DummyCredentials:
    def get_token(self, *scopes: str, **kwargs) -> AccessToken:
        return AccessToken("dummytoken", 0)


class CountingCredentials:
    def __init__(self, expiries=(10**10,)):
        self.expiries = expiries
        self.calls = 0

    def get_token(self, *scopes: str, **kwargs) -> AccessToken:
        self.calls += 1
        expiry_index = min(self.calls - 1, len(self.expiries) - 1)
        return AccessToken(f"token-{self.calls}", self.expiries[expiry_index])


@pytest.fixture
def api():
    env = TimeseriesEnvironment.Dev()
    api = TimeseriesAPI(azure_credential=DummyCredentials(), environment=env)
    api._base_url = "https://test"
    return api


@pytest.fixture
def imssubscriptionsmanagementapi():
    env = TimeseriesEnvironment.Dev()
    api = IMSSubscriptionsManagementAPI(
        azure_credential=DummyCredentials(),
        environment=env,
    )
    api._base_url = "https://test"
    return api


def should_match_get_datapoints_parameters_for_read():
    assert (
        inspect.signature(TimeseriesAPI.read).parameters
        == inspect.signature(TimeseriesAPI.get_datapoints).parameters
    )


def should_reject_non_json_output_for_read(api):
    with pytest.raises(ValueError, match="requires accept='application/json'"):
        api.read("1234", accept="application/x-protobuf")


def should_match_get_aggregates_parameters_for_read_aggregates():
    assert (
        inspect.signature(TimeseriesAPI.read_aggregates).parameters
        == inspect.signature(TimeseriesAPI.get_aggregates).parameters
    )


def should_reject_non_json_output_for_read_aggregates(api):
    with pytest.raises(ValueError, match="requires accept='application/json'"):
        api.read_aggregates("1234", ["avg"], accept="application/x-protobuf")


@pytest.mark.parametrize("aggregate_functions", [[], ["avg", "max"]])
def should_require_one_aggregate_function_for_read_aggregates(api, aggregate_functions):
    with pytest.raises(ValueError, match="requires exactly one aggregate function"):
        api.read_aggregates("1234", aggregate_functions)


@pytest.mark.parametrize(
    "aggregate_function",
    [None, "avg", "min", "max", "sum", "stddev", "count", "first", "last"],
)
def should_read_series_and_forward_parameters(
    api, documented_response, aggregate_function
):
    item = documented_response["data"]["items"][0]
    datapoint = item["datapoints"][0]
    expected_value = datapoint["value"]
    parameters = {
        "id": item["id"],
        "startTime": "2022-01-01T00:00:00.000Z",
        "endTime": "2022-01-02T00:00:00.000Z",
        "status": [192],
        "limit": 10,
        "continuationToken": "test-token",
        "federationSource": FederationSource.IMS,
        "accept": "application/json",
    }
    expected_query = {
        "starttime": [parameters["startTime"]],
        "endtime": [parameters["endTime"]],
        "status": ["192"],
        "limit": ["10"],
        "continuationtoken": ["test-token"],
        "federationsource": ["IMS"],
        "debug": ["True"],
    }
    api._debug_mode = True
    url = f"https://test/{item['id']}/data"
    if aggregate_function is None:
        method = api.read
        parameters["includeOutsidePoints"] = False
        expected_query["includeoutsidepoints"] = ["False"]
    else:
        method = api.read_aggregates
        url += "/aggregates"
        datapoint[aggregate_function] = datapoint.pop("value")
        parameters.update(
            aggregateFunction=[aggregate_function],
            processingInterval="1h",
            fill="none",
            alignToCache=False,
        )
        expected_query.update(
            aggregatefunction=[aggregate_function],
            processinginterval=["1h"],
            fill=["none"],
            aligntocache=["False"],
        )

    with requests_mock.Mocker() as transport:
        transport.get(url, json=documented_response)
        series = method(**parameters)

        assert transport.call_count == 1
        request = transport.request_history[0]
        actual_query = {
            key.lower(): values
            for key, values in parse.parse_qs(parse.urlsplit(request.url).query).items()
        }
        assert actual_query == expected_query
        assert request.headers["Accept"] == "application/json"

    expected = pd.Series(
        [expected_value],
        index=pd.to_datetime([datapoint["time"]], utc=True).tz_convert("Europe/Oslo"),
        name=item["id"],
    )
    pd.testing.assert_series_equal(series, expected)


@pytest.mark.parametrize("method_name", ["read", "read_aggregates"])
@pytest.mark.parametrize("item_count", [0, 1, 2])
def should_handle_empty_and_multiple_read_items(
    api, documented_response, method_name, item_count
):
    item = documented_response["data"]["items"][0]
    item["datapoints"].clear()
    documented_response["data"]["items"] = [
        copy.deepcopy(item) for _ in range(item_count)
    ]
    parameters = {"id": item["id"]}
    url = f"https://test/{item['id']}/data"
    if method_name == "read_aggregates":
        parameters["aggregateFunction"] = ["avg"]
        url += "/aggregates"

    with requests_mock.Mocker() as transport:
        transport.get(url, json=documented_response)
        if item_count == 1:
            series = getattr(api, method_name)(**parameters)
            assert isinstance(series, pd.Series)
            assert series.empty
            assert series.name == item["id"]
            assert str(series.index.tz) == "Europe/Oslo"
        else:
            with pytest.raises(
                ValueError, match="Expected exactly one OTS response item"
            ):
                getattr(api, method_name)(**parameters)


@pytest.fixture
def metadata_item():
    return {
        "id": "series-1",
        "name": "test-tag",
        "description": "Test metadata record",
        "source": "test-source",
        "facility": "test-facility",
        "unit": "bar",
        "metadata": {"test": True},
    }


@pytest.mark.parametrize("selector", ["id", "name", "tag_no", "matching_aliases"])
def should_get_metadata_as_list(api, metadata_item, selector):
    if selector == "id":
        parameters = {"id": metadata_item["id"]}
        url = "https://test/series-1"
        expected_query = {}
    else:
        parameters = {
            "source": metadata_item["source"],
            "facility": metadata_item["facility"],
        }
        if selector == "matching_aliases":
            parameters.update(name="test-tag", tag_no="test-tag")
        else:
            parameters[selector] = metadata_item["name"]
        url = "https://test/"
        expected_query = {
            "name": ["test-tag"],
            "source": ["test-source"],
            "facility": ["test-facility"],
        }

    with requests_mock.Mocker() as transport:
        transport.get(url, json={"data": {"items": [metadata_item]}})
        items = api.get_metadata(**parameters)

        assert items == [metadata_item]
        assert transport.call_count == 1
        assert transport.request_history[0].qs == expected_query


@pytest.mark.parametrize("selector", [{"id": "series-1"}, {"tag_no": "test-tag"}])
@pytest.mark.parametrize("response", [{}, {"data": {}}, {"data": {"items": []}}])
def should_return_empty_metadata_list(api, selector, response):
    url = "https://test/series-1" if "id" in selector else "https://test/"
    with requests_mock.Mocker() as transport:
        transport.get(url, json=response)
        assert api.get_metadata(**selector) == []


@pytest.mark.parametrize(
    "parameters",
    [
        {},
        {"id": ""},
        {"source": "test-source"},
        {"name": ""},
        {"tag_no": ""},
        {"id": "series-1", "name": "test-tag"},
        {"id": "series-1", "tag_no": "test-tag"},
        {"id": "series-1", "source": "test-source"},
        {"id": "series-1", "facility": "test-facility"},
        {"name": "test-tag", "tag_no": "different-tag"},
    ],
)
def should_reject_invalid_metadata_selectors_without_request(api, parameters):
    with requests_mock.Mocker() as transport:
        with pytest.raises(ValueError):
            api.get_metadata(**parameters)
        assert transport.call_count == 0


@pytest.mark.parametrize("method_name", ["read", "read_aggregates", "get_metadata"])
def should_preserve_api_errors_in_wrappers(api, method_name):
    parameters = {"id": "series-1"}
    url = "https://test/series-1"
    if method_name != "get_metadata":
        url += "/data"
    if method_name == "read_aggregates":
        url += "/aggregates"
        parameters["aggregateFunction"] = ["avg"]
    with requests_mock.Mocker() as transport:
        transport.get(
            url, status_code=403, json={"message": "Forbidden", "traceId": "test"}
        )
        with pytest.raises(TimeseriesRequestFailedException) as error:
            getattr(api, method_name)(**parameters)
        assert error.value.status_code == 403
        assert transport.call_count == 1


def should_retry_request_when_failing_on_retryable_error_code_503_service_is_unavailable(
    api,
):
    with requests_mock.Mocker() as m:
        m.register_uri(
            "GET",
            "https://test/1234/data/latest",
            status_code=503,
            text="""{"message": "Service is unavailable", "traceId": "1"}""",
        )
        with pytest.raises(TimeseriesRequestFailedException):
            api.get_latest_datapoint("1234")
    assert m.call_count == 3, "Unexpected number of retries"


def should_not_retry_request_when_failing_on_non_retryable_error_code_403_forbidden(
    api,
):
    with requests_mock.Mocker() as m:
        m.register_uri(
            "GET",
            "https://test/1234/data/latest",
            status_code=403,
            text="""{"message": "Service is unavailable", "traceId": "1"}""",
        )
        with pytest.raises(TimeseriesRequestFailedException):
            api.get_latest_datapoint("1234")
    assert m.call_count == 1, "Unexpected number of retries"


def should_reuse_cached_token_and_refresh_at_expiry_margin(monkeypatch):
    now = [1000]
    monkeypatch.setattr("ots_sdk.http_client.time.time", lambda: now[0])
    credentials = CountingCredentials(expiries=(2000, 3000))
    client = HttpClient(credentials, "https://resource")

    assert client._get_token() == "token-1"
    now[0] = 1699
    assert client._get_token() == "token-1"
    assert credentials.calls == 1

    now[0] = 1700
    assert client._get_token() == "token-2"
    assert credentials.calls == 2


def should_retry_unauthorized_request_only_once_and_propagate_second_failure():
    credentials = CountingCredentials()
    client = HttpClient(credentials, "https://resource")
    with requests_mock.Mocker() as transport:
        transport.register_uri(
            "GET",
            "https://test",
            [
                {"status_code": 401, "text": '{"message":"Unauthorized"}'},
                {"status_code": 401, "text": '{"message":"Still unauthorized"}'},
            ],
        )

        with pytest.raises(TimeseriesRequestFailedException) as error:
            client.request("get", "https://test")

    assert error.value.status_code == 401
    assert transport.call_count == 2
    assert credentials.calls == 2


@pytest.mark.parametrize("responseText", [(""), ("blah")])
def should_try_parse_response_when_failing_on_502_bad_gateway_due_to_api_timeout(
    api, responseText
):
    with requests_mock.Mocker() as m:
        m.register_uri(
            "POST", "https://test/query/data", status_code=502, text=responseText
        )

        with pytest.raises(TimeseriesRequestFailedException):
            api.get_multi_datapoints(
                [
                    {
                        "id": "some guid",
                        "statusFilter": [192],
                        "includeOutsidePoints": False,
                        "startTime": "2022-01-01T00:00:00.000Z",
                        "endTime": "2022-01-02T00:00:00.000Z",
                        "aggregateFunctions": ["avg"],
                        "processingInterval": "1h",
                        "fill": "none",
                    },
                ]
            )
    assert m.call_count == 3, "Unexpected number of retries"


def should_forward_align_to_cache_parameter_for_multi_datapoints(api):
    with requests_mock.Mocker() as m:
        m.register_uri(
            "POST",
            "https://test/query/data",
            json={"data": {"items": []}},
        )
        api.get_multi_datapoints([{"id": "series-1"}], alignToCache=True)
        assert m.call_count == 1, "Expected a single request"
        request = m.request_history[0]
        assert request.qs.get("aligntocache") == [
            "true"
        ], "alignToCache should be forwarded"


def should_forward_align_to_cache_parameter_for_get_aggregates(api):
    with requests_mock.Mocker() as m:
        m.register_uri(
            "GET",
            "https://test/1234/data/aggregates",
            json={"data": {"items": []}},
        )
        api.get_aggregates("1234", ["avg"], alignToCache=False)
        assert m.call_count == 1, "Expected a single request"
        request = m.request_history[0]
        assert request.qs.get("aligntocache") == [
            "false"
        ], "alignToCache should be forwarded as false"


def should_include_debug_query_param_when_debug_mode_is_enabled(api):
    api._debug_mode = True
    with requests_mock.Mocker() as m:
        m.register_uri(
            "POST",
            "https://test/query/data",
            json={"data": {"items": []}},
        )
        api.get_multi_datapoints([{"id": "series-1"}])
        assert m.call_count == 1, "Expected a single request"
        request = m.request_history[0]
        assert request.qs.get("debug") == [
            "true"
        ], "Debug mode should inject debug=true"


def should_patch_subscription_by_uid_with_state_fields_only(
    imssubscriptionsmanagementapi,
):
    payload = {
        "tsdbEventsExported": 0,
        "tsdbFirstExportedTime": None,
        "tsdbLastExportedTime": None,
        "dlEventsExported": 0,
        "dlFirstExportedTime": None,
        "dlLastExportedTime": None,
        "sourceFirstCountedTime": None,
        "sourceLastCountedTime": None,
    }

    with requests_mock.Mocker() as m:
        m.register_uri(
            "PATCH",
            "https://test/uid/sub-123",
            json={"data": {"items": []}, "count": 0},
        )

        response = imssubscriptionsmanagementapi.patch_subscription_by_uid(
            uid="sub-123",
            request=payload,
        )

        assert m.call_count == 1
        request = m.request_history[0]
        assert request.method == "PATCH"
        assert json.loads(request.text) == payload
        assert response["count"] == 0


def should_patch_subscription_by_uid_with_subscription_and_state_fields(
    imssubscriptionsmanagementapi,
):
    payload = {
        "plantStidCode": "asdf-1234",
        "plantSapCode": "1234",
        "tsdbEventsExported": 0,
        "tsdbFirstExportedTime": None,
        "tsdbLastExportedTime": None,
        "dlEventsExported": 0,
        "dlFirstExportedTime": None,
        "dlLastExportedTime": None,
        "sourceFirstCountedTime": None,
        "sourceLastCountedTime": None,
    }

    with requests_mock.Mocker() as m:
        m.register_uri(
            "PATCH",
            "https://test/uid/sub-123",
            json={"data": {"items": []}, "count": 0},
        )

        response = imssubscriptionsmanagementapi.patch_subscription_by_uid(
            uid="sub-123",
            request=payload,
        )

        assert m.call_count == 1
        request = m.request_history[0]
        assert request.method == "PATCH"
        assert json.loads(request.text) == payload
        assert response["count"] == 0


def should_raise_on_patch_subscription_by_uid_when_api_returns_400(
    imssubscriptionsmanagementapi,
):
    payload = {
        "tsdbEventsExported": 1,
        "notAllowedField": "boom",  # field intentionally unknown; API returns 400
    }

    with requests_mock.Mocker() as m:
        m.register_uri(
            "PATCH",
            "https://test/uid/sub-123",
            status_code=400,
            text='{"message":"Invalid field: notAllowedField","traceId":"trace-1"}',
        )

        with pytest.raises(TimeseriesRequestFailedException) as exc:
            imssubscriptionsmanagementapi.patch_subscription_by_uid(
                uid="sub-123",
                request=payload,
            )

        assert m.call_count == 1
        assert exc.value.status_code == 400
        assert "Invalid field" in exc.value.message


def should_patch_subscription_by_uid_reset_with_empty_payload(
    imssubscriptionsmanagementapi,
):
    payload = {}
    resetState = True

    with requests_mock.Mocker() as m:
        m.register_uri(
            "PATCH",
            "https://test/uid/sub-123",
            json={"data": {"items": []}, "count": 0},
        )

        response = imssubscriptionsmanagementapi.patch_subscription_by_uid(
            uid="sub-123",
            request=payload,
            resetState=resetState,
        )

        assert m.call_count == 1
        request = m.request_history[0]
        assert request.method == "PATCH"
        assert json.loads(request.text) == payload
        assert request.qs.get("resetstate") == ["true"]
        assert response["count"] == 0
