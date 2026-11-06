# Changelog

## Unreleased

- Reuse a pooled `requests.Session` per `HttpClient` (`pool_size`), add request `timeout`, jitter retry backoff, honor `Retry-After`, retry connect timeouts (and read timeouts/connection errors for GET), and add `HttpClient.close()`

## v1.0.3

- [PR #14](https://github.com/equinor/omnia-timeseries-python/pull/14) - Add support for protobuf data response via `accept` header in data requests