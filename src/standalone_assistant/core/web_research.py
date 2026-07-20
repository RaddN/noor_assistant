from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.error import HTTPError, URLError


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) ESEOAssistant/0.1 Safari/537.36"
BLOCKED_RESULT_TERMS = {
    "porn",
    "sex",
    "xxx",
    "nude",
    "chud",
    "lund",
    "pela",
    "desi maja",
}
STOPWORDS = {"what", "when", "where", "which", "about", "with", "from", "into", "research", "search", "latest", "news"}


@dataclass
class ResearchResult:
    ok: bool
    title: str
    summary: str
    links: list[str]
    error: str = ""
    confidence: str = "low"
    evidence_count: int = 0


def fetch_text(url: str, timeout: int = 12) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(700_000)
        charset = response.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


def compact_text(value: str, limit: int = 900) -> str:
    cleaned = re.sub(r"\s+", " ", html.unescape(value)).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def plain_text_from_html(value: str) -> str:
    value = re.sub(r"(?is)<(script|style|noscript|svg).*?</\1>", " ", value)
    value = re.sub(r"(?is)<br\s*/?>", "\n", value)
    value = re.sub(r"(?is)</p>|</li>|</h[1-6]>", "\n", value)
    value = re.sub(r"(?is)<.*?>", " ", value)
    return compact_text(value, 9000)


def domain_from_url(url: str) -> str:
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def query_tokens(query: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]{4,}", query.lower()) if token not in STOPWORDS]


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [compact_text(part, 320) for part in parts if 40 <= len(part.strip()) <= 360]


def sentence_score(sentence: str, tokens: list[str]) -> int:
    lowered = sentence.lower()
    score = sum(3 for token in tokens if token in lowered)
    if re.search(r"\b(is|are|means|refers to|used for|because|can|should|steps?|how to)\b", lowered):
        score += 2
    if re.search(r"\b\d{4}\b|\b\d+(?:\.\d+)?%?\b", lowered):
        score += 1
    return score


def weather(location: str) -> ResearchResult:
    location = location.strip() or "Dhaka"
    encoded = urllib.parse.quote(location)
    url = f"https://wttr.in/{encoded}?format=j1"
    try:
        payload = json.loads(fetch_text(url))
        current = payload.get("current_condition", [{}])[0]
        area = payload.get("nearest_area", [{}])[0]
        area_name = area.get("areaName", [{}])[0].get("value", location)
        country = area.get("country", [{}])[0].get("value", "")
        temp_c = current.get("temp_C", "?")
        feels = current.get("FeelsLikeC", "?")
        desc = current.get("weatherDesc", [{}])[0].get("value", "weather")
        humidity = current.get("humidity", "?")
        wind = current.get("windspeedKmph", "?")
        summary = f"{area_name}, {country}: {desc}, {temp_c} C, feels like {feels} C, humidity {humidity}%, wind {wind} km/h."
        return ResearchResult(True, f"Weather for {area_name}", summary, [f"https://wttr.in/{encoded}"], confidence="high", evidence_count=1)
    except Exception as exc:
        return ResearchResult(False, "Weather unavailable", "", [], str(exc))


def news(topic: str = "") -> ResearchResult:
    query = topic.strip()
    if query:
        rss_url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(query) + "&hl=en-US&gl=US&ceid=US:en"
        title = f"News for {query}"
    else:
        rss_url = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"
        title = "Top news"
    try:
        xml_text = fetch_text(rss_url)
        root = ET.fromstring(xml_text)
        items = root.findall("./channel/item")[:5]
        entries = []
        links = []
        for item in items:
            item_title = compact_text(item.findtext("title") or "", 160)
            link = item.findtext("link") or ""
            if item_title:
                entries.append(f"- {item_title}")
            if link:
                links.append(link)
        if not entries:
            return ResearchResult(False, title, "", [], "No news items were returned.")
        return ResearchResult(True, title, "\n".join(entries), links, confidence="medium", evidence_count=len(entries))
    except Exception as exc:
        return ResearchResult(False, title, "", [], str(exc))


def search_web(query: str) -> ResearchResult:
    query = query.strip()
    if not query:
        return ResearchResult(False, "Search", "", [], "Search query is empty.")
    lite_url = "https://lite.duckduckgo.com/lite/?q=" + urllib.parse.quote(query)
    try:
        page = fetch_text(lite_url)
        pattern = re.compile(r'<a rel="nofollow" href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>', re.S)
        entries = []
        links = []
        for match in pattern.finditer(page):
            href = normalize_duckduckgo_href(match.group("href"))
            title = compact_text(re.sub(r"<.*?>", "", match.group("title")), 180)
            if not is_relevant_result(query, title, href):
                continue
            if title:
                entries.append(f"- {title}")
            if href:
                links.append(href)
            if len(entries) >= 5:
                break
        if entries:
            return ResearchResult(True, f"Research: {query}", "\n".join(entries), links[:5], confidence="low", evidence_count=len(entries))
    except Exception:
        pass

    bing_url = "https://www.bing.com/search?q=" + urllib.parse.quote(query) + "&format=rss"
    try:
        xml_text = fetch_text(bing_url)
        root = ET.fromstring(xml_text)
        items = root.findall("./channel/item")[:5]
        entries = []
        links = []
        for item in items:
            item_title = compact_text(item.findtext("title") or "", 150)
            description = compact_text(item.findtext("description") or "", 220)
            link = item.findtext("link") or ""
            if not is_relevant_result(query, item_title + " " + description, link):
                continue
            if item_title and description:
                entries.append(f"- {item_title}: {description}")
            elif item_title:
                entries.append(f"- {item_title}")
            if link:
                links.append(link)
        if entries:
            return ResearchResult(True, f"Research: {query}", "\n".join(entries), links[:5], confidence="low", evidence_count=len(entries))
    except Exception:
        pass

    url = "https://duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    try:
        page = fetch_text(url)
        pattern = re.compile(r'<a rel="nofollow" class="result__a" href="(?P<href>.*?)".*?>(?P<title>.*?)</a>', re.S)
        entries = []
        links = []
        for match in pattern.finditer(page):
            href = normalize_duckduckgo_href(match.group("href"))
            title = compact_text(re.sub(r"<.*?>", "", match.group("title")), 180)
            if not is_relevant_result(query, title, href):
                continue
            if title:
                entries.append(f"- {title}")
            if href:
                links.append(href)
            if len(entries) >= 5:
                break
        if not entries:
            return ResearchResult(False, f"Search: {query}", "", [url], "No search results were parsed.")
        return ResearchResult(True, f"Search: {query}", "\n".join(entries), links[:5], confidence="low", evidence_count=len(entries))
    except Exception as exc:
        return ResearchResult(False, f"Search: {query}", "", [], str(exc))


def answer_question(query: str, *, max_pages: int = 3) -> ResearchResult:
    base = search_web(query)
    if not base.ok:
        return base
    tokens = query_tokens(query)
    if not tokens:
        return base
    evidence: list[tuple[int, str, str]] = []
    visited: set[str] = set()
    for link in base.links[: max(1, min(max_pages, 5))]:
        if not link.startswith(("http://", "https://")):
            continue
        domain = domain_from_url(link)
        if not domain or domain in visited:
            continue
        visited.add(domain)
        try:
            page_text = plain_text_from_html(fetch_text(link, timeout=8))
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeError):
            continue
        for sentence in split_sentences(page_text):
            score = sentence_score(sentence, tokens)
            if score > 0:
                evidence.append((score, sentence, link))
    if not evidence:
        return base
    evidence.sort(key=lambda item: item[0], reverse=True)
    selected: list[tuple[str, str]] = []
    seen_sentences: set[str] = set()
    for _, sentence, link in evidence:
        key = sentence.casefold()
        if key in seen_sentences:
            continue
        seen_sentences.add(key)
        selected.append((sentence, link))
        if len(selected) >= 4:
            break
    if not selected:
        return base
    summary_lines = [sentence for sentence, _ in selected]
    links = []
    for _, link in selected:
        if link not in links:
            links.append(link)
    confidence = "high" if len(selected) >= 3 and len(links) >= 2 else "medium"
    summary = " ".join(summary_lines)
    return ResearchResult(
        True,
        f"Research answer: {query}",
        compact_text(summary, 1300),
        links[:3],
        confidence=confidence,
        evidence_count=len(selected),
    )


def normalize_duckduckgo_href(href: str) -> str:
    href = html.unescape(href)
    if href.startswith("//duckduckgo.com/l/?"):
        parsed = urllib.parse.urlparse("https:" + href)
        params = urllib.parse.parse_qs(parsed.query)
        return params.get("uddg", [href])[0]
    return href


def is_relevant_result(query: str, title: str, href: str) -> bool:
    haystack = f"{title} {href}".lower()
    if any(term in haystack for term in BLOCKED_RESULT_TERMS):
        return False
    tokens = [token for token in re.findall(r"[a-z0-9]{4,}", query.lower()) if token not in STOPWORDS]
    if not tokens:
        return True
    return any(token in haystack for token in tokens)
