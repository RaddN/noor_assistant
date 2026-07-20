from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass


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
        return ResearchResult(True, f"Weather for {area_name}", summary, [f"https://wttr.in/{encoded}"])
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
        return ResearchResult(True, title, "\n".join(entries), links)
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
            return ResearchResult(True, f"Research: {query}", "\n".join(entries), links[:5])
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
            return ResearchResult(True, f"Research: {query}", "\n".join(entries), links[:5])
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
        return ResearchResult(True, f"Search: {query}", "\n".join(entries), links[:5])
    except Exception as exc:
        return ResearchResult(False, f"Search: {query}", "", [], str(exc))


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
