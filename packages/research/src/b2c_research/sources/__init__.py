"""Search/fetch providers behind one interface: official APIs first, then web search."""
from .base import KnowledgeProvider, ResearchRunner, SourceBuilder, SourceText, source_id_for
from .openalex import OpenAlexProvider
from .openlibrary import OpenLibraryProvider
from .web import PaidSearchAPI, SearchHit, SearchNotConfigured, SearxngSearch, WebResearch, WebSearchProvider
from .wiki import WikidataProvider, WikipediaProvider

__all__ = ["KnowledgeProvider", "OpenAlexProvider", "OpenLibraryProvider", "PaidSearchAPI", "ResearchRunner",
           "SearchHit", "SearchNotConfigured", "SearxngSearch", "SourceBuilder", "SourceText", "WebResearch",
           "WebSearchProvider", "WikidataProvider", "WikipediaProvider", "source_id_for"]
