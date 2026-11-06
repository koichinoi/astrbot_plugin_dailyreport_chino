"""RSS / Atom parsing for the Touhou news section (stdlib only)."""
import datetime
import json
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET

DEFAULT_TOUHOU_SOURCES = [
    "东方四方山新闻（日文原文）|https://touhou-project.news/feed.rss",
]
MAX_FEED_BYTES = 2_000_000

class _Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []
    def handle_data(self, data):
        self.parts.append(data)

def _plain(value):
    parser = _Text(); parser.feed(value or "")
    return " ".join(unescape("".join(parser.parts)).split())

def _date(value):
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (ValueError, TypeError, OverflowError):
        try:
            dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone(datetime.timedelta(hours=8)))

def parse_feed(raw, source, base_url):
    if len(raw) > MAX_FEED_BYTES:
        raise ValueError("订阅内容超过 2 MB")
    upper = raw.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError("不支持包含 DTD 的订阅")
    root = ET.fromstring(raw)
    local = lambda tag: tag.rsplit("}", 1)[-1]
    if local(root.tag) not in ("rss", "feed", "RDF"):
        raise ValueError("接口没有返回 RSS / Atom")
    entries = []
    for node in root.iter():
        if local(node.tag) not in ("item", "entry"):
            continue
        fields = {}
        link = ""
        for child in node:
            key = local(child.tag)
            value = "".join(child.itertext()).strip()
            fields.setdefault(key, value)
            if key == "link":
                if child.get("href") and child.get("rel", "alternate") == "alternate":
                    link = child.get("href")
                elif not child.get("href") and value:
                    link = value
        title = _plain(fields.get("title", ""))[:240]
        if not link.strip():
            continue
        link = urljoin(base_url, link.strip())
        if not title or not link or urlsplit(link).scheme.lower() not in ("http", "https") or not urlsplit(link).netloc:
            continue
        dt = _date(fields.get("pubDate") or fields.get("published") or fields.get("updated") or fields.get("date"))
        entries.append({"title": title, "url": link, "source": source,
                        "date": dt.strftime("%Y-%m-%d") if dt else "日期未标注",
                        "timestamp": dt.timestamp() if dt else 0})
    return entries

def select_entries(entries, limit):
    seen = set(); result = []
    for entry in sorted(entries, key=lambda item: item["timestamp"], reverse=True):
        key = entry["url"].split("#", 1)[0]
        if key not in seen:
            seen.add(key); result.append(entry)
        if len(result) >= limit:
            break
    return result

def parse_translations(content, count):
    """Validate the complete indexed batch before using any translated title."""
    text = (content or "").strip()
    if len(text) > 30000:
        raise ValueError("翻译返回内容过长")
    if text.startswith("```") and text.endswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    values = json.loads(text)
    if not isinstance(values, list) or len(values) != count:
        raise ValueError("翻译条数不匹配")
    result = {}
    for item in values:
        if not isinstance(item, dict) or type(item.get("id")) is not int:
            raise ValueError("翻译条目缺少编号")
        index, title = item["id"], item.get("title")
        if index in result or not 0 <= index < count or not isinstance(title, str):
            raise ValueError("翻译编号或标题无效")
        title = _plain(title)
        if not title or len(title) > 500:
            raise ValueError("翻译标题为空或过长")
        result[index] = title
    return [result[index] for index in range(count)]
