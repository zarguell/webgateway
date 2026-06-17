from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from webgateway.providers.base import ProviderError
from webgateway.providers.devdocs import DevDocsAdapter


@pytest.fixture
def adapter() -> DevDocsAdapter:
    return DevDocsAdapter(base_url="http://devdocs:9292", timeout=15)


_MANIFEST = {
    "javascript": {"name": "JavaScript", "version": "ECMAScript"},
    "python~3.14": {"name": "Python", "version": "3.14.0"},
    "typescript~5.8": {"name": "TypeScript", "version": "5.8"},
}

_JAVASCRIPT_INDEX = {
    "entries": [
        {"name": "Array.prototype.map()", "path": "global_objects/array/map", "type": "Method"},
        {"name": "Array.prototype.filter()", "path": "global_objects/array/filter", "type": "Method"},
        {"name": "fetch()", "path": "global_objects/fetch", "type": "Function"},
    ]
}

_PYTHON_INDEX = {
    "entries": [
        {"name": "list.sort()", "path": "library/stdtypes#list.sort", "type": "Method"},
        {"name": "sorted()", "path": "library/functions#sorted", "type": "Function"},
    ]
}


class TestDevDocsAdapter:
    async def test_name(self, adapter: DevDocsAdapter):
        assert adapter.name == "devdocs"

    async def test_metadata(self, adapter: DevDocsAdapter):
        meta = adapter.metadata
        assert meta.name == "devdocs"
        assert meta.self_hosted is True
        assert meta.specialization == "docs"
        assert "search" in meta.capabilities

    async def test_search_matches_entry_name(
        self, adapter: DevDocsAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="GET",
            url="http://devdocs:9292/docs.json",
            json=_MANIFEST,
        )
        httpx_mock.add_response(
            method="GET",
            url="http://devdocs:9292/docs/javascript/index.json",
            json=_JAVASCRIPT_INDEX,
        )
        httpx_mock.add_response(
            method="GET",
            url="http://devdocs:9292/docs/python~3.14/index.json",
            json=_PYTHON_INDEX,
        )
        httpx_mock.add_response(
            method="GET",
            url="http://devdocs:9292/docs/typescript~5.8/index.json",
            json={"entries": []},
        )

        result = await adapter.search("map", options=None)  # type: ignore[arg-type]
        assert len(result.results) >= 1
        assert "map()" in result.results[0].title
        assert "devdocs:9292" in result.results[0].url

    async def test_search_matches_via_fallback(
        self, adapter: DevDocsAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="GET",
            url="http://devdocs:9292/docs.json",
            json=_MANIFEST,
        )
        httpx_mock.add_response(
            method="GET",
            url="http://devdocs:9292/docs/javascript/index.json",
            json=_JAVASCRIPT_INDEX,
        )
        httpx_mock.add_response(
            method="GET",
            url="http://devdocs:9292/docs/python~3.14/index.json",
            json=_PYTHON_INDEX,
        )
        httpx_mock.add_response(
            method="GET",
            url="http://devdocs:9292/docs/typescript~5.8/index.json",
            json={"entries": []},
        )

        result = await adapter.search("fetch", options=None)  # type: ignore[arg-type]
        assert len(result.results) >= 1
        assert "fetch" in result.results[0].title.lower()

    async def test_search_no_match(
        self, adapter: DevDocsAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="GET",
            url="http://devdocs:9292/docs.json",
            json=_MANIFEST,
        )
        httpx_mock.add_response(
            method="GET",
            url="http://devdocs:9292/docs/javascript/index.json",
            json=_JAVASCRIPT_INDEX,
        )
        httpx_mock.add_response(
            method="GET",
            url="http://devdocs:9292/docs/python~3.14/index.json",
            json=_PYTHON_INDEX,
        )
        httpx_mock.add_response(
            method="GET",
            url="http://devdocs:9292/docs/typescript~5.8/index.json",
            json={"entries": []},
        )

        result = await adapter.search("zzzznotfound", options=None)  # type: ignore[arg-type]
        assert len(result.results) == 0

    async def test_search_manifest_unavailable(
        self, adapter: DevDocsAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="GET",
            url="http://devdocs:9292/docs.json",
            status_code=500,
        )

        result = await adapter.search("map", options=None)  # type: ignore[arg-type]
        assert len(result.results) == 0

    async def test_search_network_error(
        self, adapter: DevDocsAdapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_exception(ConnectionError("connection refused"))

        result = await adapter.search("map", options=None)  # type: ignore[arg-type]
        assert len(result.results) == 0

    async def test_search_empty_query(
        self, adapter: DevDocsAdapter, httpx_mock: HTTPXMock
    ):
        result = await adapter.search("", options=None)  # type: ignore[arg-type]
        assert len(result.results) == 0

    async def test_extract_unsupported(self, adapter: DevDocsAdapter):
        with pytest.raises(ProviderError) as exc:
            await adapter.extract("http://example.com", options=None)  # type: ignore[arg-type]
        assert "does not support extraction" in str(exc.value)

    async def test_health_check_ok(self, adapter: DevDocsAdapter, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="GET",
            url="http://devdocs:9292/ping",
            status_code=200,
        )
        assert await adapter.health_check() is True

    async def test_health_check_fail(self, adapter: DevDocsAdapter, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="GET",
            url="http://devdocs:9292/ping",
            status_code=500,
        )
        assert await adapter.health_check() is False

    async def test_health_check_network_error(self, adapter: DevDocsAdapter, httpx_mock: HTTPXMock):
        httpx_mock.add_exception(ConnectionError("connection refused"))
        assert await adapter.health_check() is False

    async def test_find_relevant_slugs(self, adapter: DevDocsAdapter):
        slugs = adapter._find_relevant_slugs("javascript", _MANIFEST)
        assert "javascript" in slugs

    async def test_find_relevant_slugs_no_match(self, adapter: DevDocsAdapter):
        slugs = adapter._find_relevant_slugs("zzzzz", _MANIFEST)
        assert len(slugs) == 0
