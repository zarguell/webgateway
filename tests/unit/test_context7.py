from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from serp_llm.providers.base import ProviderError
from serp_llm.providers.context7 import Context7Adapter


@pytest.fixture
def adapter() -> Context7Adapter:
    return Context7Adapter(api_key=None, timeout=15)


_LIBS_RESPONSE = {
    "results": [
        {"id": "/fastapi/fastapi", "title": "FastAPI", "benchmarkScore": 90}
    ]
}

_CONTEXT_RESPONSE = {
    "codeSnippets": [
        {
            "codeTitle": "Middleware Example",
            "codeDescription": "Shows middleware usage in FastAPI",
            "codeList": [
                {"language": "python", "code": "from fastapi import FastAPI\napp = FastAPI()"}
            ],
            "codeId": "https://github.com/fastapi/fastapi/blob/master/docs/middleware.md#snippet_0",
            "pageTitle": "Middleware",
        }
    ],
    "infoSnippets": [
        {
            "pageId": "https://fastapi.tiangolo.com/middleware/",
            "breadcrumb": "Tutorial > Middleware",
            "content": (
                "Middleware is a function that processes each request"
                " before it reaches the path operation."
            ),
        }
    ],
}


class TestContext7Adapter:
    async def test_name(self, adapter: Context7Adapter):
        assert adapter.name == "context7"

    async def test_metadata(self, adapter: Context7Adapter):
        meta = adapter.metadata
        assert meta.name == "context7"
        assert meta.mcp_native is True
        assert "search" in meta.capabilities
        assert meta.specialization == "docs"

    async def test_search_success(
        self, adapter: Context7Adapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="GET",
            url="https://context7.com/api/v2/libs/search?libraryName=FastAPI&query=FastAPI+middleware",
            json=_LIBS_RESPONSE,
        )
        httpx_mock.add_response(
            method="GET",
            url="https://context7.com/api/v2/context?libraryId=/fastapi/fastapi&query=FastAPI+middleware&type=json",
            json=_CONTEXT_RESPONSE,
        )

        result = await adapter.search("FastAPI middleware", options=None)  # type: ignore[arg-type]
        assert len(result.results) > 0
        assert result.results[0].title == "Middleware Example"
        assert "fastapi" in result.results[0].url

    async def test_search_no_library_found(
        self, adapter: Context7Adapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="GET",
            url="https://context7.com/api/v2/libs/search?libraryName=FastAPI&query=FastAPI+middleware",
            json={"results": []},
        )

        result = await adapter.search("FastAPI middleware", options=None)  # type: ignore[arg-type]
        assert len(result.results) == 0

    async def test_search_api_error(
        self, adapter: Context7Adapter, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(
            method="GET",
            url="https://context7.com/api/v2/libs/search?libraryName=FastAPI&query=FastAPI+middleware",
            status_code=500,
        )

        with pytest.raises(ProviderError):
            await adapter.search("FastAPI middleware", options=None)  # type: ignore[arg-type]

    async def test_extract_unsupported(self, adapter: Context7Adapter):
        with pytest.raises(ProviderError) as exc:
            await adapter.extract("http://example.com", options=None)  # type: ignore[arg-type]
        assert "does not support extraction" in str(exc.value)

    async def test_health_check_ok(self, adapter: Context7Adapter, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="GET",
            url="https://context7.com/api/v2/libs/search?libraryName=test&query=test",
            status_code=200,
        )
        assert await adapter.health_check() is True

    async def test_health_check_fail(self, adapter: Context7Adapter, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            method="GET",
            url="https://context7.com/api/v2/libs/search?libraryName=test&query=test",
            status_code=500,
        )
        assert await adapter.health_check() is False

    async def test_guess_library_name_first_capitalized(self, adapter: Context7Adapter):
        assert adapter._guess_library_name("FastAPI middleware") == "FastAPI"

    async def test_guess_library_name_fallback(self, adapter: Context7Adapter):
        assert adapter._guess_library_name("numpy arrays") == "numpy"
