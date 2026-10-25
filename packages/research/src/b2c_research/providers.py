"""The standard provider set: official/open APIs first, web search only when configured."""
from __future__ import annotations

from typing import Any

from .meta import BookMeta
from .sources import (OpenAlexProvider, OpenLibraryProvider, SourceBuilder, WebResearch, WikidataProvider,
                      WikipediaProvider)


def standard_providers(builder: SourceBuilder, meta: BookMeta, *, fetcher: Any, openalex_api_key: str | None = None,
                       mailto: str | None = None, web_search: Any | None = None, max_web_pages: int = 5) -> list[Any]:
    providers: list[Any] = [
        OpenAlexProvider(fetcher, builder, api_key=openalex_api_key, mailto=mailto),
        OpenLibraryProvider(fetcher, builder),
        WikidataProvider(fetcher, builder),
        WikipediaProvider(fetcher, builder, context_keywords=meta.context_keywords, search_hint=meta.search_hint),
    ]
    if web_search is not None:
        providers.append(WebResearch(web_search, fetcher, builder, max_pages=max_web_pages))
    return providers


__all__ = ["standard_providers"]
