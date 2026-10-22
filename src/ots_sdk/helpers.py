from functools import wraps
import logging
import time
from typing import Any, Dict, Optional, Union

import pandas as pd

from ots_sdk.models import TimeseriesRequestFailedException

# Reasonable status codes to retry, based on descriptions at https://en.wikipedia.org/wiki/List_of_HTTP_status_codes
retry_status_codes = [408, 409, 425, 429, 502, 503, 504]


def _ots_response_to_frame(
    response: Any,
    column_name: Optional[Union[str, Dict[str, str]]] = None,
    value_key: str = "value",
) -> pd.DataFrame:
    """Align OTS series by timestamp, using IDs or mapped names for columns."""
    items = (response or {}).get("data", {}).get("items", [])
    if not items:
        columns = (
            list(column_name.values())
            if isinstance(column_name, dict)
            else [column_name] if column_name is not None else []
        )
        return pd.DataFrame(
            columns=columns, index=pd.DatetimeIndex([], tz="Europe/Oslo")
        )

    frames = []
    for position, item in enumerate(items):
        series_id = item.get("id", f"series_{position + 1}")
        name = series_id
        if isinstance(column_name, dict):
            name = column_name.get(series_id, series_id)
        elif column_name is not None and len(items) == 1:
            name = column_name

        datapoints = item.get("datapoints", [])
        times = [datapoint.get("time") for datapoint in datapoints]
        values = []
        for datapoint in datapoints:
            value = datapoint.get(value_key)
            if value is None:
                value = datapoint.get("value")
            values.append(value)

        index = pd.to_datetime(times, format="ISO8601", utc=True).tz_convert(
            "Europe/Oslo"
        )
        frame = pd.DataFrame({name: values}, index=index)
        frames.append(frame[~frame.index.duplicated(keep="first")])

    return pd.concat(frames, axis=1).sort_index()


def _ots_response_to_series(
    response: Any,
    name: Optional[str] = None,
    value_key: str = "value",
) -> pd.Series:
    """Normalize exactly one OTS response item into a tz-aware Series."""
    items = (response or {}).get("data", {}).get("items", [])
    if len(items) != 1:
        raise ValueError(f"Expected exactly one OTS response item, got {len(items)}")

    return _ots_response_to_frame(response, column_name=name, value_key=value_key).iloc[
        :, 0
    ]


def retry(
    total_tries=3,
    initial_wait=0.5,
    backoff_factor=2,
    logger: Optional[logging.Logger] = None,
):
    def retry_decorator(f):
        @wraps(f)
        def func_with_retries(*args, **kwargs):
            _tries, _delay = total_tries, initial_wait
            exception = None
            while _tries > 0:
                if logger is not None:
                    logger.debug(f"{total_tries + 1 - _tries}. try:")
                try:
                    return f(*args, **kwargs)
                except TimeseriesRequestFailedException as e:
                    _tries -= 1
                    if e.status_code in retry_status_codes:
                        if logger is not None:
                            if _tries == 0:
                                msg = str(
                                    f"Function: {f.__name__} Failed despite best efforts after {total_tries} tries."
                                )
                            else:
                                msg = str(
                                    f"Function: {f.__name__} failed with {e}. Retrying in {_delay} seconds, with {_tries} retries remaining!\n"
                                )
                            logger.warning(msg)
                        time.sleep(_delay)
                        _delay *= backoff_factor
                        exception = e
                    else:
                        if logger is not None:
                            logger.debug(f"Status code {e.status_code} not retryable")
                        exception = e
                        break
            if exception is not None:
                raise exception

        return func_with_retries

    return retry_decorator
