"""Read THBWiki's public schedule, preserving source dates and links."""
import datetime
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlsplit, unquote, urlencode, parse_qs

SCHEDULE_URL = "https://thwiki.cc/%E6%97%A5%E7%A8%8B%E8%A1%A8"
DEFAULT_EVENTS_API = "https://thwiki.cc/api.php?action=parse&page=%E6%97%A5%E7%A8%8B%E8%A1%A8&prop=text&format=json&formatversion=2"

def china_today():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).date()

class _Schedule(HTMLParser):
    def __init__(self):
        super().__init__(); self.country=""; self.in_h2=False; self.heading=None; self.current=None; self.events=[]
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if tag == "h2":
            self.in_h2=True
        if tag == "span" and self.in_h2 and "mw-headline" in attrs.get("class", "").split():
            self.heading=[]
        if tag == "li" and self.country:
            self.current={"parts":[], "links":[], "country":self.country}
        if tag == "a" and self.current is not None and attrs.get("href"):
            self.current["links"].append(attrs["href"])
        if tag == "br" and self.current is not None:
            self.current["parts"].append(" ")
    def handle_data(self, data):
        if self.heading is not None:
            self.heading.append(data)
        if self.current is not None:
            self.current["parts"].append(data)
    def handle_endtag(self, tag):
        if tag == "span" and self.heading is not None:
            self.country="".join(self.heading).strip(); self.heading=None
        if tag == "h2":
            self.in_h2=False
        if tag == "li" and self.current is not None:
            item=self.current; self.current=None
            text=" ".join("".join(item["parts"]).split())
            match=re.match(r"^(\d{4})年(\d{1,2})月(\d{1,2})日\s+(\S+)\s+(.+?)\s+于\s+(.+?)\s+举办$", text)
            if not match or not item["links"]:
                return
            year,month,day,kind,title,location=match.groups()
            try:
                date=datetime.date(int(year),int(month),int(day))
            except ValueError:
                return
            url=urljoin("https://thwiki.cc/",item["links"][0])
            if urlsplit(url).scheme.lower() not in ("http", "https"):
                return
            self.events.append({"title":title[:300],"date":date.isoformat(),"kind":kind,
                                "location":location[:500],"country":item["country"],"url":url})

def parse_schedule(html):
    if not isinstance(html,str) or len(html)>2_000_000:
        raise ValueError("活动日程内容无效或过长")
    parser=_Schedule(); parser.feed(html)
    if not parser.events:
        raise ValueError("活动源未返回可识别的日程，请检查接口")
    return parser.events

def filter_events(events, *, today=None, days=60, limit=5, regions=(), types=("展会",)):
    today=today or china_today()
    end=today+datetime.timedelta(days=days)
    result=[]; seen=set()
    for item in sorted(events,key=lambda row:(row["date"],row["title"])):
        date=datetime.date.fromisoformat(item["date"])
        if not (today<=date<=end) or not item["country"].startswith("中国"):
            continue
        if types and not any(kind in item["kind"].split("/") for kind in types):
            continue
        where=item["location"]+" "+item["title"]
        if regions and not any(region.casefold() in where.casefold() for region in regions):
            continue
        key=(item["date"],item["url"])
        if key in seen:
            continue
        seen.add(key); result.append(dict(item,days_left=(date-today).days))
        if len(result)>=limit:
            break
    return result

def event_page_api(event_url):
    parts=urlsplit(event_url)
    if parts.hostname not in ("thwiki.cc", "www.thwiki.cc", "thbwiki.cc", "www.thbwiki.cc"):
        return ""
    title=unquote(parts.path.lstrip("/"))
    if title == "index.php":
        title=parse_qs(parts.query).get("title", [""])[0]
    if not title or not re.fullmatch(r"ev-\d+", parts.fragment):
        return ""
    return "https://thwiki.cc/api.php?"+urlencode(dict(action="parse",page=title,prop="text",format="json",formatversion=2))

def _edition(value):
    if value.isdigit():
        return int(value)
    digits={char:index for index,char in enumerate("零一二三四五六七八九")}
    if "十" in value:
        left,right=value.split("十",1)
        if (not left or left in digits) and (not right or right in digits):
            return (digits[left] if left else 1)*10+(digits[right] if right else 0)
    return digits.get(value)

class _Posters(HTMLParser):
    def __init__(self, edition):
        super().__init__();self.edition=edition;self.in_gallery=False;self.urls=[]
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if tag == "li" and "gallerybox" in attrs.get("class", "").split():
            self.in_gallery=True
        if tag != "img" or not self.in_gallery:
            return
        alt=attrs.get("alt", "")
        match=re.search(r"第?([0-9零一二三四五六七八九十]+)届",alt)
        if not match or _edition(match.group(1)) != self.edition:
            return
        # Select only a URL actually advertised by the source, up to its 2x thumbnail.
        src=attrs.get("src", "")
        options=[]
        for value in attrs.get("srcset", "").split(","):
            pair=value.strip().rsplit(None,1)
            if len(pair)==2 and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?x",pair[1]):
                if float(pair[1][:-1]) <= 2:
                    options.append((float(pair[1][:-1]),pair[0]))
        if options:
            src=max(options)[1]
        for candidate in (src,attrs.get("src", "")):
            candidate=urljoin("https://thwiki.cc/",candidate)
            parts=urlsplit(candidate)
            if parts.scheme in ("http","https") and parts.hostname in ("upload.thbwiki.cc","upload.thwiki.cc"):
                if candidate not in self.urls:
                    self.urls.append(candidate)
    def handle_endtag(self, tag):
        if tag == "li":
            self.in_gallery=False

def event_poster_urls(html, event_url):
    match=re.fullmatch(r"ev-(\d+)",urlsplit(event_url).fragment)
    if not match or not isinstance(html,str) or len(html)>2_000_000:
        return []
    parser=_Posters(int(match.group(1)));parser.feed(html)
    return parser.urls[:2]
