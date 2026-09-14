"""Bounded, allowlisted web research adapter."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from html.parser import HTMLParser
import ipaddress
import json
import socket
from typing import Any, Protocol
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from medulla_node.adapters import MedullaAdapter
from medulla_protocol import Capability, CapabilityAvailability, CapabilityAvailabilityState, CapabilityEffect, CapabilityPermissions, CapabilityProvider, CapabilityRisk, CapabilityTimeout, MedullaNodeResource, NodeAction


DEFAULT_RESEARCH_DOMAINS = (
    "github.com",
    "ocw.mit.edu",
    "arxiv.org",
    "openstax.org",
    "doaj.org",
    "gutenberg.org",
    "wikinews.org",
)


class WebResearchBackend(Protocol):
    def search(self, query: str, limit: int) -> Sequence[Mapping[str, Any]]: ...
    def fetch(self, url: str, maximum_bytes: int) -> Mapping[str, Any]: ...


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self._in_title = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title": self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title": self._in_title = False

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text: return
        if self._in_title: self.title = f"{self.title} {text}".strip()
        self.parts.append(text)


class _SearchExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._href: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "a" and "result-link" in classes:
            self._href = attributes.get("href")
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None and data.strip():
            self._parts.append(" ".join(data.split()))

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            href = self._href
            parsed = urlparse(href)
            if parsed.path.startswith("/l/"):
                href = unquote(parse_qs(parsed.query).get("uddg", [href])[0])
            self.results.append({"url": href, "title": " ".join(self._parts)})
            self._href = None
            self._parts = []


class _BingSearchExtractor(HTMLParser):
    """Extract ordinary result links without evaluating page script."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._in_result = False
        self._in_heading = False
        self._href: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "li" and "b_algo" in classes:
            self._in_result = True
        elif self._in_result and tag == "h2":
            self._in_heading = True
        elif self._in_result and self._in_heading and tag == "a" and self._href is None:
            self._href = attributes.get("href")
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None and data.strip():
            self._parts.append(" ".join(data.split()))

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self.results.append({"url": self._href, "title": " ".join(self._parts)})
            self._href = None
            self._parts = []
        elif tag == "h2":
            self._in_heading = False
        elif tag == "li" and self._in_result:
            self._in_result = False


class StandardWebResearchBackend:
    """HTTPS backend with DNS-level local-network rejection."""

    def __init__(self, allowed_domains: Sequence[str] = DEFAULT_RESEARCH_DOMAINS) -> None:
        self._allowed_domains = tuple(allowed_domains)

    def _allowed(self, url: str) -> None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or not any(
            host == domain or host.endswith(f".{domain}")
            for domain in self._allowed_domains
        ):
            raise PermissionError("redirect leaves the approved web domains")

    def _safe_remote(self, url: str) -> None:
        host = urlparse(url).hostname
        if host is None:
            raise ValueError("URL has no hostname")
        for answer in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM):
            address = ipaddress.ip_address(answer[4][0])
            if not address.is_global:
                raise PermissionError("URL resolves to a non-public address")

    def fetch(self, url: str, maximum_bytes: int) -> Mapping[str, Any]:
        self._allowed(url)
        self._safe_remote(url)
        request = Request(url, headers={"Accept": "text/html,application/json,text/plain", "User-Agent": "medulla-web/0.1"})
        backend = self

        class SafeRedirect(HTTPRedirectHandler):
            def redirect_request(self, request, file_pointer, code, message, headers, new_url):
                backend._allowed(new_url)
                backend._safe_remote(new_url)
                return super().redirect_request(request, file_pointer, code, message, headers, new_url)

        with build_opener(SafeRedirect()).open(request, timeout=10) as response:
            final_url = response.geturl()
            self._allowed(final_url)
            self._safe_remote(final_url)
            raw = response.read(maximum_bytes + 1)
            content_type = response.headers.get_content_type()
            charset = response.headers.get_content_charset() or "utf-8"
        truncated = len(raw) > maximum_bytes
        raw = raw[:maximum_bytes]
        text = raw.decode(charset, errors="replace")
        if content_type == "application/json":
            content = json.dumps(json.loads(text), ensure_ascii=False)
            title = urlparse(final_url).path.rsplit("/", 1)[-1]
        elif content_type in {"text/html", "application/xhtml+xml"}:
            parser = _TextExtractor()
            parser.feed(text)
            title = parser.title
            content = "\n".join(parser.parts)
        else:
            title = urlparse(final_url).path.rsplit("/", 1)[-1]
            content = text
        return {"url": final_url, "title": title, "content": content, "content_type": content_type, "truncated": truncated}

    def search(self, query: str, limit: int) -> Sequence[Mapping[str, Any]]:
        # Discovery covers the public web. The adapter separately applies its
        # access policy to every returned URL before exposing results to Echo.
        providers: tuple[tuple[str, type[HTMLParser]], ...] = (
            (f"https://lite.duckduckgo.com/lite/?q={quote_plus(query)}", _SearchExtractor),
            (f"https://www.bing.com/search?q={quote_plus(query)}", _BingSearchExtractor),
        )
        for search_url, parser_type in providers:
            try:
                self._safe_remote(search_url)
                request = Request(search_url, headers={"User-Agent": "Mozilla/5.0 (compatible; medulla-web/0.9)", "Accept": "text/html"})
                with urlopen(request, timeout=10) as response:
                    html = response.read(524_288).decode("utf-8", errors="replace")
                parser = parser_type()
                parser.feed(html)
                results = getattr(parser, "results", [])
                if results:
                    return results[: limit * 4]
            except (OSError, ValueError):
                continue
        return ()


class WebResearchAdapter(MedullaAdapter):
    def __init__(self, provider: CapabilityProvider, *, allowed_domains: Sequence[str] = DEFAULT_RESEARCH_DOMAINS, maximum_bytes: int = 524_288, backend: WebResearchBackend | None = None) -> None:
        if not isinstance(provider, CapabilityProvider):
            raise TypeError("provider must be CapabilityProvider")
        domains = tuple(domain.lower().strip(".") for domain in allowed_domains)
        if not domains or any(not domain or "/" in domain or ":" in domain for domain in domains):
            raise ValueError("allowed_domains must contain hostnames")
        if len(domains) != len(set(domains)):
            raise ValueError("allowed_domains must not contain duplicates")
        if type(maximum_bytes) is not int or maximum_bytes < 1:
            raise ValueError("maximum_bytes must be positive")
        self._domains = domains
        self._maximum_bytes = maximum_bytes
        self._backend = backend or StandardWebResearchBackend(domains)
        self._started = False
        super().__init__(
            adapter_id="web-research",
            capabilities=(
                _capability(provider, "web.search", "Search the public web; returned URLs remain subject to Medulla access policy"),
                _capability(provider, "web.fetch", "Fetch and extract text from an approved HTTPS URL"),
            ),
            resources=tuple(MedullaNodeResource(resource_id=f"web.domain.{index}", description=f"Approved web domain {domain}", schema={"type": "string"}, metadata={"domain": domain}) for index, domain in enumerate(domains)),
        )

    async def start(self) -> None: self._started = True
    async def stop(self) -> None: self._started = False

    async def execute(self, action: NodeAction) -> Mapping[str, Any]:
        if not self._started: raise RuntimeError("web research adapter is not running")
        if action.type == "web.fetch":
            url = self._allowed_url(action.parameters.get("url"))
            result = dict(await asyncio.to_thread(self._backend.fetch, url, self._maximum_bytes))
            result["url"] = self._allowed_url(result.get("url", url))
            return result
        if action.type == "web.search":
            query = action.parameters.get("query")
            limit = action.parameters.get("limit", 8)
            if type(query) is not str or not query.strip(): raise ValueError("web.search requires a query")
            if type(limit) is not int or not 1 <= limit <= 20: raise ValueError("web.search limit must be between 1 and 20")
            raw = await asyncio.to_thread(self._backend.search, query.strip(), limit)
            results = []
            for item in raw:
                try: url = self._allowed_url(item.get("url"))
                except (TypeError, ValueError, PermissionError): continue
                results.append({"url": url, "title": str(item.get("title", "")), **({"snippet": str(item["snippet"])} if item.get("snippet") else {})})
                if len(results) >= limit: break
            return {
                "query": query.strip(),
                "results": results,
                "search_scope": "public_web",
                "access_policy": {
                    "preapproved_domains": list(self._domains),
                    "behavior": (
                        "These domains may be returned and fetched under predefined "
                        "approval policy; they do not define the complete searchable web."
                    ),
                },
            }
        raise ValueError(f"unsupported web research Action: {action.type}")

    def _allowed_url(self, value: Any) -> str:
        if type(value) is not str or not value: raise ValueError("web URL must be non-empty")
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or not host or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError("only credential-free HTTPS URLs on port 443 are allowed")
        if not any(host == domain or host.endswith(f".{domain}") for domain in self._domains):
            raise PermissionError(f"web domain is not approved: {host}")
        return value


def _capability(provider: CapabilityProvider, name: str, description: str) -> Capability:
    return Capability(capability_id=f"{provider.provider_id}.{name}.v1", name=name, description=description, provider=provider, input_schema={"type": "object"}, output_schema={"type": "object"}, availability=CapabilityAvailability(state=CapabilityAvailabilityState.AVAILABLE), permissions=CapabilityPermissions(risk=CapabilityRisk.NETWORK, required=(name,)), effect=CapabilityEffect.READ_ONLY, timeout=CapabilityTimeout(expected_seconds=1, maximum_seconds=15))


__all__ = ["DEFAULT_RESEARCH_DOMAINS", "StandardWebResearchBackend", "WebResearchAdapter", "WebResearchBackend"]
