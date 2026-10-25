"""Which URLs research may fetch, and how reliable each source is (learning-companion §6, §10).

* Only ``https`` on the default port, no credentials, no IP literals in private/loopback/link-local
  ranges, no single-label or ``.local``/``.internal`` hosts (SSRF guard). Redirect hops are checked
  again by the fetcher.
* ``deny`` always wins; a non-empty ``allow`` turns the policy into an allow-list.
* ``internal_hosts`` names b2c-owned services (the SearXNG instance) that may be reached over the
  internal network; nothing else private is ever fetched.

Reliability tiers: 1 academic / encyclopedic / publisher, 2 published reviews / interviews / press,
3 everything else (blogs, unknown sites). API adapters set the tier of what they render explicitly.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit

DEFAULT_DENY: tuple[str, ...] = (
    "pastebin.com", "ghostbin.com", "hastebin.com", "bit.ly", "t.co", "tinyurl.com", "goo.gl",
    "facebook.com", "instagram.com", "tiktok.com", "x.com", "twitter.com", "linkedin.com",
    "scribd.com", "coursehero.com", "studocu.com", "chegg.com",           # copies of copyrighted texts
    "libgen.is", "libgen.rs", "z-lib.org", "annas-archive.org", "sci-hub.se",
)

TIER1_DOMAINS: tuple[str, ...] = (
    "wikipedia.org", "wikidata.org", "openalex.org", "openlibrary.org", "archive.org", "loc.gov",
    "britannica.com", "plato.stanford.edu", "iep.utm.edu", "oxfordreference.com", "doi.org", "crossref.org",
    # publishers of the books we process and major academic presses
    "ucpress.edu", "sagepub.com", "us.sagepub.com", "cqpress.com", "press.princeton.edu", "cambridge.org",
    "global.oup.com", "yalebooks.yale.edu", "hup.harvard.edu", "press.uchicago.edu", "mitpress.mit.edu",
    "routledge.com", "bloomsbury.com", "versobooks.com", "cia.gov", "dni.gov",
)
TIER2_DOMAINS: tuple[str, ...] = (
    "jstor.org", "muse.jhu.edu", "tandfonline.com", "academic.oup.com", "onlinelibrary.wiley.com",
    "link.springer.com", "journals.sagepub.com", "h-net.org", "lrb.co.uk", "nybooks.com", "the-tls.co.uk",
    "theguardian.com", "nytimes.com", "washingtonpost.com", "economist.com", "foreignaffairs.com",
    "newleftreview.org", "jacobin.com", "marxists.org", "insidehighered.com", "timeshighereducation.com",
    "lawfaremedia.org", "warontherocks.com", "merip.org", "jadaliyya.com",
)


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def tier_for_url(url: str) -> int:
    host = (urlsplit(url).hostname or "").lower()
    if any(_host_matches(host, d) for d in TIER1_DOMAINS) or host.endswith((".edu", ".ac.uk")):
        return 1
    if any(_host_matches(host, d) for d in TIER2_DOMAINS):
        return 2
    return 3


@dataclass(frozen=True)
class DomainPolicy:
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = DEFAULT_DENY
    internal_hosts: tuple[str, ...] = ()

    def refusal(self, url: str) -> str | None:
        """None if the URL may be fetched, else the reason it may not."""
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError:
            return "unparseable URL"
        host = (parts.hostname or "").lower().rstrip(".")
        if host and host in self.internal_hosts:
            return None if parts.scheme in ("http", "https") else "scheme not allowed"
        if parts.scheme != "https":
            return "only https is fetched"
        if parts.username or parts.password:
            return "credentials in URL"
        if port not in (None, 443):
            return "non-default port"
        if not host:
            return "no host"
        try:
            ip = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            ip = None
        if ip is not None:
            return "IP literal hosts are not fetched"
        if "." not in host or host == "localhost" or host.endswith((".local", ".internal", ".localhost", ".lan")):
            return "local host name"
        if any(_host_matches(host, d) for d in self.deny):
            return "domain on the deny list"
        if self.allow and not any(_host_matches(host, d) for d in self.allow):
            return "domain not on the allow list"
        return None


__all__ = ["DEFAULT_DENY", "DomainPolicy", "TIER1_DOMAINS", "TIER2_DOMAINS", "tier_for_url"]
