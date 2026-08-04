import asyncio
import base64
import datetime
import html
import mimetypes
import re
import time
from pathlib import Path
from urllib.parse import urlencode

import aiohttp

from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult, MessageChain
from astrbot.api.star import Context, Star
import astrbot.api.message_components as Comp

# 农历 / 黄历（可选依赖，缺失则日历只显示公历）
try:
    import cnlunar
except Exception:  # noqa: BLE001
    cnlunar = None

# 定时任务（可选依赖，缺失则不启用定时推送）
try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
except Exception:  # noqa: BLE001
    AsyncIOScheduler = None
    CronTrigger = None

WEEK_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
WEEK_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DEFAULT_HOTLISTS = ["weibo", "zhihu", "bilibili"]
# 榜单 slug -> 中文名（有些源返回里不含榜单名，用它兜底美化）
HOTLIST_NAMES = {
    "weibo": "微博热搜", "zhihu": "知乎热榜", "bili": "B站热搜", "bilibili": "B站热搜",
    "douyin": "抖音热搜", "baidu": "百度热搜", "toutiao": "头条热搜", "kuaishou": "快手热点",
    "36kr": "36氪", "ithome": "IT之家", "hupu": "虎扑", "tieba": "百度贴吧",
    "douban": "豆瓣", "quark": "夸克热点", "kuaike": "夸克热点", "xiaohongshu": "小红书",
    "wbHot": "微博热搜", "zhihuHot": "知乎热榜", "douyinHot": "抖音热搜",
    # ALAPI tophub type
    "36k": "36氪", "juejin": "掘金热榜", "sspai": "少数派", "csdn": "CSDN",
    "163": "网易新闻", "xl": "新浪热点", "52pojie": "吾爱破解", "history": "历史上的今天",
}
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 AstrBot-DailyReport/1.1")
# Bangumi 要求 UA 为「开发者ID/应用名(项目主页)」格式，禁止 Bangumi/1.0、应用名/版本号 这类。
# 见 https://github.com/bangumi/api/blob/master/docs-raw/user%20agent.md
BGM_UA = "koichinoi/astrbot_plugin_dailyreport_chino (https://github.com/koichinoi/astrbot_plugin_dailyreport_chino)"
# 浏览器 UA，作为番剧请求的兜底（参考真寻日报的写法，公开接口可用）
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
# 显式版本号，便于用户在输出里确认当前加载的是哪一版
VERSION = "v2.1.0"

PLUGIN_DIR = Path(__file__).resolve().parent


def _asset_data_uri(filename):
    """把随插件打包的图片转成 data URI，确保本地 html_render 无需公网或 file:// 权限。"""
    path = PLUGIN_DIR / "assets" / filename
    try:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        payload = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{payload}"
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[dailyreport] 读取主题素材失败 {path.name}: {e}")
        return ""

# 香风智乃主题（淡蓝 / 薰衣草，Rabbit House 咖啡馆感）。Jinja2 模板，供 html_render 使用。
CHINO_LEGACY_TMPL = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { font-family: "PingFang SC","Microsoft YaHei","Hiragino Sans GB","Noto Sans CJK SC",sans-serif; background: #eef0fb; }
  .card {
    width: 820px; zoom: {{ scale | default(2) }}; position: relative; overflow: hidden;
    color: #5a6890;
    background:
      radial-gradient(circle at 16% 10%, rgba(210,222,255,0.55), transparent 34%),
      radial-gradient(circle at 86% 7%, rgba(245,221,239,0.55), transparent 36%),
      radial-gradient(circle at 78% 92%, rgba(216,228,255,0.5), transparent 42%),
      linear-gradient(162deg, #eaedfb 0%, #eef0fb 38%, #f3edfa 74%, #e9edfb 100%);
  }
  .hd { position: relative; display: flex; justify-content: space-between; align-items: center;
        padding: 30px 34px 10px 34px; }
  .hd-l { display: flex; align-items: center; gap: 14px; }
  .logo-cup { font-size: 40px; filter: saturate(0.85); }
  .title { font-size: 31px; font-weight: 800; color: #6b7bc4; letter-spacing: 0.5px; }
  .subtitle { font-size: 15px; color: #8b96c8; margin-top: 5px; }
  .emblem { border: 2px solid #cbb28c; border-radius: 14px; padding: 7px 16px; text-align: center;
            background: rgba(255,255,255,0.45); }
  .emblem-cup { font-size: 19px; line-height: 1; }
  .emblem-t { font-size: 12.5px; font-weight: 800; letter-spacing: 2px; color: #a98a5f; line-height: 1.25; margin-top: 2px; }
  .deco { position: absolute; opacity: 0.34; pointer-events: none; }
  .datebar {
    margin: 6px 34px 4px 34px; padding: 11px 20px; border-radius: 16px; text-align: center;
    background: linear-gradient(90deg,#aab4e6,#b7a6dd); color: #fff; font-size: 15px; font-weight: 600;
    box-shadow: 0 4px 14px rgba(150,150,200,0.28);
  }
  .body { position: relative; z-index: 1; padding: 16px 24px 6px 24px; }
  .row2 { display: flex; gap: 16px; }
  .row2 .sec { flex: 1; margin-bottom: 16px; }
  .sec {
    background: #ffffff; border-radius: 20px; padding: 18px 22px; margin-bottom: 16px;
    box-shadow: 0 6px 22px rgba(150,160,210,0.16);
  }
  .sec-h { font-size: 18px; font-weight: 800; color: #6b7bc4;
           padding-bottom: 9px; margin-bottom: 12px; border-bottom: 1.5px dashed #e6e9f6; }
  .yj { font-size: 14.5px; line-height: 1.95; color: #5a6890; }
  .pill { display: inline-block; padding: 1px 9px; border-radius: 9px; font-size: 12.5px; margin-right: 7px; font-weight: 700; }
  .pill-y { background: #e3f0fb; color: #5b91c4; }
  .pill-j { background: #fce8ef; color: #d97a9a; }
  .luck { display: flex; gap: 26px; margin-top: 11px; font-size: 14px; color: #6a76a0; }
  .swatch { display: inline-block; width: 14px; height: 14px; border-radius: 50%; margin: 0 5px; vertical-align: -2px;
            box-shadow: 0 0 0 2px rgba(255,255,255,0.8), 0 1px 3px rgba(0,0,0,0.12); }
  .luck b { color: #6b7bc4; font-size: 16px; }
  .note { background: #f4f6fd; border-radius: 12px; padding: 10px 14px; margin-top: 13px; font-size: 13.5px; color: #6a76a0; line-height: 1.6; }
  .note-h { font-size: 12.5px; font-weight: 700; color: #8b96c8; margin-bottom: 3px; }
  .sec-coffee { background: linear-gradient(135deg,#fbf4ea 0%,#f9efe0 100%); }
  .sec-h-c { color: #b8895a; border-bottom-color: #efe2d0; }
  .cf-top { display: flex; align-items: center; gap: 14px; }
  .cf-emoji { font-size: 46px; line-height: 1; }
  .cf-name { font-size: 23px; font-weight: 800; color: #b8895a; }
  .cf-en { font-size: 13px; color: #c2a98a; margin-top: 2px; letter-spacing: 0.5px; }
  .cf-stars { margin-top: 12px; font-size: 15px; color: #e3a72f; letter-spacing: 2px; }
  .cf-stars b { color: #b8895a; font-weight: 600; letter-spacing: 0; }
  .cf-suit { margin-top: 7px; font-size: 14px; color: #8a7a60; }
  .cf-tag { margin-top: 11px; padding-top: 9px; border-top: 1px dashed #ece0cf; font-size: 13px; color: #a89878; }
  .news2 { display: flex; gap: 26px; }
  .news-col { flex: 1; }
  .ni { display: flex; gap: 8px; margin-bottom: 9px; align-items: flex-start; }
  .nn { flex-shrink: 0; width: 19px; height: 19px; border-radius: 50%; background: #dfe7fa; color: #6b86c0;
        font-size: 11px; font-weight: 700; text-align: center; line-height: 19px; }
  .nt { font-size: 13px; color: #5a6890; line-height: 1.5; }
  .acg { display: flex; flex-wrap: wrap; gap: 13px; }
  .acg-i { width: 96px; }
  .acg-c { width: 96px; height: 135px; border-radius: 12px; object-fit: cover; display: block;
           box-shadow: 0 4px 12px rgba(120,140,190,0.22); background: #eef2fb; }
  .acg-n { display: flex; align-items: center; justify-content: center; font-size: 30px; }
  .acg-t { margin-top: 6px; font-size: 12px; line-height: 1.35; color: #5f80b6; max-width: 96px;
           display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .tags .tag { display: inline-block; margin: 0 8px 8px 0; padding: 6px 12px; border-radius: 14px;
               background: #eaf1fc; color: #5f80b6; font-size: 13.5px; }
  .hot2 { display: flex; flex-wrap: wrap; gap: 11px; }
  .hc { flex: 1 1 auto; min-width: 210px; max-width: 360px; display: flex; align-items: center; gap: 9px;
        background: #f7f9fe; border-radius: 12px; padding: 10px 13px; }
  .hc-n { flex-shrink: 0; width: 19px; height: 19px; border-radius: 6px; background: #ffe0cc; color: #d97a45;
          font-size: 11px; font-weight: 800; text-align: center; line-height: 19px; }
  .hc-t { flex: 1; font-size: 13px; color: #5a6890; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
  .hc-b { flex-shrink: 0; background: #fce8ef; color: #d97a9a; font-size: 10px; padding: 1px 7px; border-radius: 7px; font-weight: 700; }
  .err { color: #b6a0a0; font-size: 13.5px; }
  .quote-sec { text-align: center; padding: 20px 24px 6px 24px; position: relative; z-index: 1; }
  .quote-h { font-size: 14px; color: #8b96c8; }
  .quote-big { font-size: 27px; font-weight: 800; color: #6b7bc4; letter-spacing: 3px; margin: 10px 0 8px 0; }
  .quote-by { font-size: 14px; color: #9aa6c2; }
  .footer { position: relative; z-index: 1; text-align: center; padding: 15px; margin-top: 8px;
            color: #9aa6c2; font-size: 12.5px; border-top: 1px solid rgba(200,205,235,0.5); }
</style>
</head>
<body>
<div class="card">
  <span class="deco" style="top:18px;left:300px;font-size:18px;">&#9729;&#65039;</span>
  <span class="deco" style="top:70px;right:210px;font-size:15px;">&#10024;</span>
  <span class="deco" style="top:120px;left:30px;font-size:16px;">&#127800;</span>
  <span class="deco" style="bottom:120px;right:24px;font-size:20px;">&#128048;</span>
  <span class="deco" style="bottom:60px;left:30px;font-size:17px;">&#9749;</span>

  <div class="hd">
    <div class="hd-l">
      <div class="logo-cup">&#9749;</div>
      <div>
        <div class="title">智乃的 Rabbit House 日报</div>
        <div class="subtitle">{{ greet }}</div>
      </div>
    </div>
    <div class="emblem">
      <div class="emblem-cup">&#9749;</div>
      <div class="emblem-t">RABBIT<br/>HOUSE</div>
    </div>
  </div>
  <div class="datebar">&#128197; {{ date_main }} · {{ weekday }}{% if calendar and calendar.lunar %} ｜ {{ calendar.lunar }}{% endif %}</div>

  <div class="body">
    <div class="row2">
      <div class="sec sec-fortune">
        <div class="sec-h">&#128048; Rabbit House 今日运势</div>
        {% if calendar %}
        {% if calendar.good %}<div class="yj"><span class="pill pill-y">宜</span>{{ calendar.good }}</div>{% endif %}
        {% if calendar.bad %}<div class="yj"><span class="pill pill-j">忌</span>{{ calendar.bad }}</div>{% endif %}
        <div class="luck">
          <div>幸运色<span class="swatch" style="background:{{ lucky_hex }};"></span>{{ lucky_color }}</div>
          <div>幸运数字 <b>{{ lucky_num }}</b></div>
        </div>
        {% endif %}
        <div class="note"><div class="note-h">&#128172; 智乃的建议</div>{{ advice }}</div>
      </div>

      <div class="sec sec-coffee">
        <div class="sec-h sec-h-c">&#9749; 今日咖啡推荐</div>
        <div class="cf-top">
          <div class="cf-emoji">&#9749;</div>
          <div>
            <div class="cf-name">{{ coffee.name_cn }}</div>
            <div class="cf-en">{{ coffee.name_en }}</div>
          </div>
        </div>
        <div class="cf-stars">推荐指数 {% for _ in range(coffee.stars) %}&#9733;{% endfor %}{% for _ in range(5 - coffee.stars) %}&#9734;{% endfor %}</div>
        <div class="cf-suit">适合：{{ coffee.suitable }}</div>
        <div class="cf-tag">{{ coffee.tagline }}</div>
      </div>
    </div>

    {% if news %}
    <div class="sec">
      <div class="sec-h">&#128240; 今日新闻</div>
      {% if news.error %}<div class="err">获取失败：{{ news.error }}</div>
      {% elif news['items'] %}
        {% set half = (news['items']|length + 1)//2 %}
        <div class="news2">
          <div class="news-col">{% for t in news['items'][:half] %}<div class="ni"><span class="nn">{{ loop.index }}</span><span class="nt">{{ t }}</span></div>{% endfor %}</div>
          <div class="news-col">{% for t in news['items'][half:] %}<div class="ni"><span class="nn">{{ loop.index + half }}</span><span class="nt">{{ t }}</span></div>{% endfor %}</div>
        </div>
        {% if news.tip %}<div class="note"><div class="note-h">&#128172; 智乃点评</div>{{ news.tip }}</div>{% endif %}
      {% else %}<div class="err">暂无内容</div>{% endif %}
    </div>
    {% endif %}

    {% if anime %}
    <div class="sec">
      <div class="sec-h">&#128250; 智乃推荐动画 · {{ anime.weekday }}</div>
      {% if anime.error %}<div class="err">获取失败：{{ anime.error }}</div>
      {% elif anime.covers %}<div class="acg">{% for c in anime.covers %}<div class="acg-i">{% if c.img %}<img class="acg-c" src="{{ c.img }}"/>{% else %}<div class="acg-c acg-n">&#127845;</div>{% endif %}<div class="acg-t">{{ c.name }}</div></div>{% endfor %}</div>
        <div class="note"><div class="note-h">&#128172; 智乃的话</div>今天适合安静地看动画，享受悠闲的时光。</div>
      {% elif anime['items'] %}<div class="tags">{% for i in anime['items'] %}<span class="tag">{{ i.name }}</span>{% endfor %}</div>
      {% else %}<div class="err">今天没有更新的番剧</div>{% endif %}
    </div>
    {% endif %}

    {% if hot_cards %}
    <div class="sec">
      <div class="sec-h">&#128293; 今日热议</div>
      <div class="hot2">{% for t in hot_cards %}<div class="hc"><span class="hc-n">{{ loop.index }}</span><span class="hc-t">{{ t }}</span><span class="hc-b">热</span></div>{% endfor %}</div>
    </div>
    {% endif %}
  </div>

  <div class="quote-sec">
    <div class="quote-h">&#9749; 今日一句</div>
    <div class="quote-big">「{{ quote }}」</div>
    <div class="quote-by">—— 智乃</div>
  </div>
  <div class="footer">&#128048; Rabbit House ｜ 今天也谢谢你的到来，请多关照哦。</div>
</div>
</body>
</html>
"""

CHINO_MODERN_TMPL = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body {
    font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
    background: #dfeaf3;
  }
  .card {
    width: 820px;
    zoom: {{ scale | default(2) }};
    overflow: hidden;
    color: #314963;
    background: #edf5fa;
    position: relative;
  }
  .hero {
    height: 318px;
    position: relative;
    overflow: hidden;
    background-color: #263653;
    background-image:
      linear-gradient(90deg, rgba(21,31,51,.93) 0%, rgba(25,37,60,.83) 31%, rgba(31,48,74,.35) 57%, rgba(30,43,64,.04) 100%),
      {% if chino_art %}url("{{ chino_art }}"){% else %}linear-gradient(135deg,#314866,#87a8c7){% endif %};
    background-size: cover;
    background-position: center;
  }
  .hero:after {
    content: "";
    position: absolute;
    left: 0; right: 0; bottom: 0; height: 98px;
    background: linear-gradient(transparent, rgba(11,24,42,.66));
  }
  .stamp {
    position: absolute; top: 27px; left: 34px; z-index: 2;
    display: flex; align-items: center; gap: 9px;
    padding: 7px 13px 7px 8px;
    border: 1px solid rgba(255,255,255,.28); border-radius: 999px;
    background: rgba(255,255,255,.11); color: #f4f8fc;
    font-size: 10px; font-weight: 700; letter-spacing: 1.5px;
    backdrop-filter: blur(6px);
  }
  .stamp-rabbit {
    width: 27px; height: 27px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    background: #eff7fb; color: #527ba4; font-size: 16px;
  }
  .hero-copy { position: absolute; left: 37px; top: 91px; width: 430px; z-index: 2; color: white; }
  .eyebrow { color: #b9d8ef; font-size: 11px; font-weight: 700; letter-spacing: 3.1px; }
  .title {
    margin-top: 12px; max-width: 420px;
    font-size: 35px; line-height: 1.18; font-weight: 850; letter-spacing: 1px;
    text-shadow: 0 2px 18px rgba(0,0,0,.38);
  }
  .subtitle { margin-top: 12px; color: rgba(244,249,253,.84); font-size: 14px; line-height: 1.55; }
  .date-ticket {
    position: absolute; left: 34px; bottom: 23px; z-index: 3;
    display: flex; align-items: center; gap: 12px;
    color: #f5f8fc; font-size: 13px; font-weight: 650;
  }
  .date-dot { width: 6px; height: 6px; border-radius: 50%; background: #c1e5f5; box-shadow: 0 0 0 4px rgba(193,229,245,.15); }
  .date-lunar { color: #c9d6e5; font-weight: 400; }
  .paper {
    position: relative; z-index: 4;
    margin-top: -4px; padding: 25px 25px 12px;
    border-radius: 26px 26px 0 0;
    background:
      radial-gradient(circle at 0 0, rgba(158,204,230,.23), transparent 30%),
      radial-gradient(circle at 100% 72%, rgba(192,180,224,.18), transparent 34%),
      #edf5fa;
  }
  .rabbit-ears { position: absolute; right: 35px; top: -22px; width: 48px; height: 35px; }
  .ear { position: absolute; bottom: 0; width: 15px; height: 34px; background: #f9fcfe; border: 2px solid #bdd4e4; border-radius: 70% 70% 42% 42%; }
  .ear:after { content: ""; position: absolute; inset: 6px 4px 5px; border-radius: inherit; background: #dcecf5; }
  .ear:first-child { left: 7px; transform: rotate(-13deg); }
  .ear:last-child { right: 7px; transform: rotate(13deg); }
  .grid2 { display: flex; gap: 15px; }
  .grid2 > .panel { flex: 1; }
  .panel {
    margin-bottom: 15px; padding: 18px 20px;
    border: 1px solid rgba(140,178,205,.28); border-radius: 19px;
    background: rgba(255,255,255,.88);
    box-shadow: 0 8px 24px rgba(74,109,140,.10);
  }
  .panel-h { display: flex; align-items: center; gap: 10px; margin-bottom: 13px; }
  .panel-icon {
    flex: 0 0 31px; height: 31px; border-radius: 10px;
    display: flex; align-items: center; justify-content: center;
    background: #e3f1f8; color: #4d789d; font-size: 16px;
  }
  .panel-title { color: #355f84; font-size: 16.5px; font-weight: 800; letter-spacing: .3px; }
  .panel-kicker { margin-left: auto; color: #9bb1c3; font-size: 9.5px; font-weight: 700; letter-spacing: 1.1px; }
  .rule { height: 1px; margin: -4px 0 13px; background: linear-gradient(90deg,#d8e7f0,transparent); }
  .yj { font-size: 13.5px; line-height: 1.9; color: #536b80; }
  .pill { display: inline-block; min-width: 25px; margin-right: 8px; padding: 1px 7px; border-radius: 7px; text-align: center; font-size: 11px; font-weight: 800; }
  .pill-y { background: #e0f1ed; color: #408c7c; }
  .pill-j { background: #fae8ed; color: #c36d82; }
  .luck { display: flex; gap: 20px; margin-top: 10px; color: #6c8193; font-size: 12px; }
  .swatch { display: inline-block; width: 12px; height: 12px; margin: 0 5px; border: 2px solid white; border-radius: 50%; vertical-align: -2px; box-shadow: 0 0 0 1px #c9d9e4; }
  .luck b { color: #47769e; font-size: 15px; }
  .chino-note { margin-top: 12px; padding: 10px 12px; border-left: 3px solid #91bad4; border-radius: 5px 10px 10px 5px; background: #f2f8fb; color: #647c8f; font-size: 12.5px; line-height: 1.55; }
  .chino-note b { color: #4d7697; }
  .coffee-panel { background: linear-gradient(145deg,rgba(255,252,247,.94),rgba(250,242,230,.94)); border-color: rgba(202,172,127,.28); }
  .coffee-panel .panel-icon { background: #f2e7d8; color: #9a714b; }
  .coffee-panel .panel-title { color: #8c6846; }
  .coffee-main { display: flex; align-items: center; gap: 13px; }
  .cup-mark { font-size: 38px; filter: saturate(.7); }
  .coffee-name { color: #76563b; font-size: 20px; font-weight: 850; }
  .coffee-en { margin-top: 2px; color: #ac9073; font-size: 11.5px; letter-spacing: .5px; }
  .stars { margin-top: 11px; color: #dda743; font-size: 13px; letter-spacing: 1.5px; }
  .coffee-suit { margin-top: 6px; color: #8b7764; font-size: 12.5px; }
  .coffee-tag { margin-top: 10px; padding-top: 8px; border-top: 1px dashed #e4d5c4; color: #a38c76; font-size: 11.5px; }
  .news-grid { display: flex; gap: 25px; }
  .news-col { flex: 1; }
  .news-item { display: flex; gap: 9px; align-items: flex-start; margin-bottom: 9px; }
  .num { flex: 0 0 20px; height: 20px; border-radius: 7px; background: #e5f0f7; color: #5681a4; text-align: center; line-height: 20px; font-size: 10px; font-weight: 800; }
  .news-text { color: #465f74; font-size: 12.5px; line-height: 1.52; }
  .acg { display: flex; flex-wrap: wrap; gap: 13px; }
  .acg-i { width: 95px; }
  .acg-cover { width: 95px; height: 133px; display: block; object-fit: cover; border-radius: 12px; background: #e4eef5; box-shadow: 0 5px 14px rgba(66,98,128,.16); }
  .acg-none { display: flex; align-items: center; justify-content: center; color: #7698b4; font-size: 28px; }
  .acg-name { margin-top: 6px; color: #4d6e89; font-size: 11.5px; line-height: 1.35; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .tags .tag { display: inline-block; margin: 0 7px 7px 0; padding: 6px 11px; border: 1px solid #dceaf2; border-radius: 999px; background: #f3f8fb; color: #557895; font-size: 12px; }
  .hot-grid { display: flex; flex-wrap: wrap; gap: 9px; }
  .hot-item { flex: 1 1 320px; display: flex; align-items: center; gap: 9px; min-width: 0; padding: 9px 11px; border-radius: 11px; background: #f4f8fb; }
  .hot-rank { flex: 0 0 21px; height: 21px; border-radius: 7px; background: #e6eef7; color: #607fa1; text-align: center; line-height: 21px; font-size: 10px; font-weight: 800; }
  .hot-item:nth-child(-n+3) .hot-rank { background: #f6e5e4; color: #bd6c67; }
  .hot-text { flex: 1; min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; color: #4b6478; font-size: 12.5px; }
  .hot-badge { flex: 0 0 auto; padding: 2px 7px; border-radius: 999px; background: #e6f2ef; color: #568b80; font-size: 9px; font-weight: 800; }
  .error { color: #ad7a83; font-size: 12.5px; }
  .quote {
    margin: 3px 25px 0; padding: 22px 35px;
    position: relative; overflow: hidden; text-align: center;
    border: 1px solid rgba(126,169,199,.28); border-radius: 18px;
    background: linear-gradient(120deg,#dcecf5,#eef4fa 52%,#e8e4f4);
  }
  .quote:before { content: "“"; position: absolute; left: 17px; top: -15px; color: rgba(76,118,153,.13); font: 700 86px Georgia,serif; }
  .quote-label { color: #7995ad; font-size: 10px; font-weight: 750; letter-spacing: 2.2px; }
  .quote-text { margin: 8px 0 6px; color: #365e80; font-size: 23px; font-weight: 800; letter-spacing: 2px; }
  .quote-by { color: #8ba1b3; font-size: 11px; }
  .footer { padding: 18px 25px 20px; text-align: center; color: #8ba1b3; font-size: 11px; letter-spacing: .4px; }
</style>
</head>
<body>
<div class="card">
  <header class="hero">
    <div class="stamp"><span class="stamp-rabbit">&#128048;</span> RABBIT HOUSE · MORNING POST</div>
    <div class="hero-copy">
      <div class="eyebrow">CHINO'S DAILY LETTER</div>
      <div class="title">{{ title }}</div>
      <div class="subtitle">{{ greet }}</div>
    </div>
    <div class="date-ticket"><span class="date-dot"></span><span>{{ date_main }} · {{ weekday }}</span>{% if calendar and calendar.lunar %}<span class="date-lunar">{{ calendar.lunar }}</span>{% endif %}</div>
  </header>

  <main class="paper">
    <div class="rabbit-ears"><span class="ear"></span><span class="ear"></span></div>
    <div class="grid2">
      <section class="panel">
        <div class="panel-h"><span class="panel-icon">&#10024;</span><span class="panel-title">今日小运势</span><span class="panel-kicker">LUCKY NOTE</span></div>
        <div class="rule"></div>
        {% if calendar %}
        {% if calendar.good %}<div class="yj"><span class="pill pill-y">宜</span>{{ calendar.good }}</div>{% endif %}
        {% if calendar.bad %}<div class="yj"><span class="pill pill-j">忌</span>{{ calendar.bad }}</div>{% endif %}
        <div class="luck"><span>幸运色 <i class="swatch" style="background:{{ lucky_hex }}"></i>{{ lucky_color }}</span><span>数字 <b>{{ lucky_num }}</b></span></div>
        {% endif %}
        <div class="chino-note"><b>智乃的叮嘱：</b>{{ advice }}</div>
      </section>

      <section class="panel coffee-panel">
        <div class="panel-h"><span class="panel-icon">&#9749;</span><span class="panel-title">今日咖啡</span><span class="panel-kicker">BARISTA PICK</span></div>
        <div class="rule"></div>
        <div class="coffee-main"><span class="cup-mark">&#9749;</span><div><div class="coffee-name">{{ coffee.name_cn }}</div><div class="coffee-en">{{ coffee.name_en }}</div></div></div>
        <div class="stars">{% for _ in range(coffee.stars) %}&#9733;{% endfor %}{% for _ in range(5 - coffee.stars) %}&#9734;{% endfor %}</div>
        <div class="coffee-suit">适合：{{ coffee.suitable }}</div>
        <div class="coffee-tag">{{ coffee.tagline }}</div>
      </section>
    </div>

    {% if news %}
    <section class="panel">
      <div class="panel-h"><span class="panel-icon">&#128240;</span><span class="panel-title">今日新闻</span><span class="panel-kicker">DAILY BRIEFING</span></div>
      <div class="rule"></div>
      {% if news.error %}<div class="error">获取失败：{{ news.error }}</div>
      {% elif news['items'] %}
      {% set half = (news['items']|length + 1)//2 %}
      <div class="news-grid">
        <div class="news-col">{% for t in news['items'][:half] %}<div class="news-item"><span class="num">{{ loop.index }}</span><span class="news-text">{{ t }}</span></div>{% endfor %}</div>
        <div class="news-col">{% for t in news['items'][half:] %}<div class="news-item"><span class="num">{{ loop.index + half }}</span><span class="news-text">{{ t }}</span></div>{% endfor %}</div>
      </div>
      {% if news.tip %}<div class="chino-note"><b>智乃点评：</b>{{ news.tip }}</div>{% endif %}
      {% else %}<div class="error">今天还没有新的内容。</div>{% endif %}
    </section>
    {% endif %}

    {% if anime %}
    <section class="panel">
      <div class="panel-h"><span class="panel-icon">&#128250;</span><span class="panel-title">智乃的番剧柜 · {{ anime.weekday }}</span><span class="panel-kicker">ON AIR TODAY</span></div>
      <div class="rule"></div>
      {% if anime.error %}<div class="error">获取失败：{{ anime.error }}</div>
      {% elif anime.covers %}<div class="acg">{% for c in anime.covers %}<div class="acg-i">{% if c.img %}<img class="acg-cover" src="{{ c.img }}"/>{% else %}<div class="acg-cover acg-none">&#128048;</div>{% endif %}<div class="acg-name">{{ c.name }}</div></div>{% endfor %}</div>
      {% elif anime['items'] %}<div class="tags">{% for i in anime['items'] %}<span class="tag">{{ i.name }}</span>{% endfor %}</div>
      {% else %}<div class="error">今天没有查询到更新的番剧。</div>{% endif %}
    </section>
    {% endif %}

    {% if hot_cards %}
    <section class="panel">
      <div class="panel-h"><span class="panel-icon">&#128293;</span><span class="panel-title">正在热议</span><span class="panel-kicker">TRENDING NOW</span></div>
      <div class="rule"></div>
      <div class="hot-grid">{% for t in hot_cards %}<div class="hot-item"><span class="hot-rank">{{ loop.index }}</span><span class="hot-text">{{ t }}</span><span class="hot-badge">HOT</span></div>{% endfor %}</div>
    </section>
    {% endif %}
  </main>

  <section class="quote"><div class="quote-label">TODAY'S WORDS</div><div class="quote-text">「{{ quote }}」</div><div class="quote-by">—— 香风智乃</div></section>
  <footer class="footer">&#128048; Rabbit House · 愿今天也有一杯温柔的咖啡陪着你</footer>
</div>
</body>
</html>
"""

CHINO_TMPL = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body {
    font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
    background: #c9d6f5;
  }
  .page {
    width: 900px;
    zoom: {{ scale | default(2) }};
    padding: 9px;
    position: relative;
    overflow: hidden;
    color: #4f5e8d;
    background: linear-gradient(145deg,#b9caf5,#d7e1fa 52%,#b7c8f4);
  }
  .page:before {
    content: "";
    position: absolute; inset: 4px;
    border: 1px dashed rgba(255,255,255,.9);
    border-radius: 5px;
    pointer-events: none;
  }
  .sheet {
    overflow: hidden;
    position: relative;
    border-radius: 7px;
    background:
      radial-gradient(circle at 12% 16%,rgba(163,186,241,.11),transparent 22%),
      radial-gradient(circle at 88% 65%,rgba(187,171,229,.10),transparent 24%),
      #f8fbff;
    box-shadow: 0 5px 18px rgba(77,99,158,.18);
  }
  .cover {
    height: 320px;
    position: relative;
    overflow: hidden;
    background-color: #fffdf9;
    {% if chino_banner %}background-image: url("{{ chino_banner }}");{% endif %}
    background-size: cover;
    background-position: center;
    border-bottom: 4px solid #ccd8f8;
  }
  .cover:after {
    content: ""; position: absolute; inset: 10px;
    border: 1px dashed rgba(142,163,220,.48); border-radius: 7px;
    pointer-events: none;
  }
  .cover-copy {
    position: absolute; left: 220px; top: 45px; z-index: 2;
    width: 470px; text-align: center;
  }
  .cover-kicker { color: #738bd5; font-size: 15px; font-weight: 800; letter-spacing: 1.8px; }
  .cover-title {
    margin-top: 8px; color: #506cbd;
    font-family: Georgia,"Microsoft YaHei",serif;
    font-size: 35px; line-height: 1.25; font-weight: 850; letter-spacing: .4px;
    text-shadow: 0 2px 0 #fff, 0 0 14px rgba(255,255,255,.95);
  }
  .greet { margin-top: 10px; color: #7a84c0; font-size: 20px; font-weight: 600; text-shadow: 0 1px 0 white; }
  .date-pill {
    margin: 20px auto 0; width: 470px; padding: 12px 18px;
    border-radius: 999px;
    background: linear-gradient(90deg,#9caeee,#8398e1,#9ba8e9);
    color: white; font-size: 15px; font-weight: 650;
    box-shadow: 0 5px 14px rgba(91,112,184,.18);
  }
  .date-pill .sep { margin: 0 10px; opacity: .65; }
  .body { padding: 14px 12px 8px; }
  .top-grid { display: flex; gap: 16px; }
  .top-grid > .panel { flex: 1; min-width: 0; }
  .panel {
    position: relative; overflow: hidden;
    margin-bottom: 14px; padding: 15px 18px 17px;
    border: 1.5px solid #bdcbf2; border-radius: 8px;
    background: rgba(255,255,255,.93);
    box-shadow: 0 3px 10px rgba(92,113,169,.10), inset 0 0 0 3px rgba(239,244,255,.8);
  }
  .panel:after {
    content: "✿"; position: absolute; right: 14px; bottom: 8px;
    color: #dbe4fa; font-size: 22px; pointer-events: none;
  }
  .panel-head { display: flex; align-items: center; min-height: 31px; padding-bottom: 9px; border-bottom: 1px solid #d8e1f8; }
  .head-icon { margin-right: 8px; font-size: 18px; }
  .panel-title { color: #5873c4; font-size: 20px; font-weight: 850; letter-spacing: .5px; }
  .panel-en { margin-left: auto; color: #aab8df; font-size: 9px; font-weight: 800; letter-spacing: 1.2px; }
  .top-grid .panel { min-height: 300px; }
  .fortune-body { padding: 18px 5px 0; }
  .yj { margin-bottom: 8px; color: #46536f; font-size: 13.5px; line-height: 1.75; }
  .pill { display: inline-block; margin-right: 9px; min-width: 29px; padding: 2px 7px; border-radius: 9px; text-align: center; font-size: 11px; font-weight: 800; }
  .good { background: #dcf2e9; color: #4b987d; }
  .bad { background: #fde8eb; color: #cf7180; }
  .luck-row { display: flex; gap: 35px; margin: 18px 0 14px; color: #535d80; font-size: 13px; }
  .luck-row b { color: #5b75ca; font-size: 15px; }
  .swatch { display: inline-block; width: 13px; height: 13px; margin: 0 5px; border-radius: 50%; vertical-align: -2px; border: 2px solid white; box-shadow: 0 0 0 1px #bac8e4; }
  .note {
    position: relative; padding: 11px 14px 11px 45px;
    border: 1px solid #d6e0f8; border-radius: 8px;
    background: linear-gradient(100deg,#f3f7ff,#fafcff);
    color: #52618c; font-size: 12.5px; line-height: 1.65;
  }
  .note:before { content: "✦"; position: absolute; left: 15px; top: 12px; color: #8fa5df; font-size: 18px; opacity: .65; }
  .note b { color: #6780c9; }
  .coffee-panel {
    border-color: #dfcab5;
    {% if chino_coffee %}background-image: linear-gradient(90deg,rgba(255,255,255,.98) 0%,rgba(255,255,255,.94) 38%,rgba(255,255,255,.12) 68%),url("{{ chino_coffee }}");{% endif %}
    background-size: cover;
    background-position: center;
  }
  .coffee-panel .panel-title { color: #9b765e; }
  .coffee-info { width: 48%; padding: 22px 0 0 3px; }
  .coffee-cn { color: #9d745f; font: 800 24px Georgia,"Microsoft YaHei",serif; }
  .coffee-en { margin-top: 3px; color: #ad826b; font-size: 15px; }
  .rating { display: inline-block; margin-top: 15px; padding: 5px 10px; border-radius: 8px; background: #91a5eb; color: white; font-size: 12px; font-weight: 700; }
  .suitable { margin-top: 19px; padding-top: 11px; border-top: 1px dashed #dfcdbf; color: #7c665c; font-size: 12px; line-height: 1.75; }
  .tagline { margin-top: 8px; color: #b47d64; font-size: 11.5px; }
  .news-grid { display: flex; gap: 30px; padding: 15px 8px 5px; }
  .news-col { flex: 1; min-width: 0; }
  .news-col + .news-col { border-left: 1px dashed #d9e2f5; padding-left: 26px; }
  .news-item { display: flex; align-items: flex-start; gap: 10px; margin-bottom: 10px; }
  .rank { flex: 0 0 21px; height: 21px; border-radius: 50%; background: #94a9ea; color: white; text-align: center; line-height: 21px; font-size: 10px; font-weight: 800; }
  .news-text { color: #3f485e; font-size: 12.3px; line-height: 1.55; }
  .news-note { margin: 6px 8px 0; }
  .anime-wrap { padding: 15px 6px 1px; }
  .anime-list { display: flex; flex-wrap: wrap; gap: 10px; }
  .anime-item { width: 86px; }
  .anime-cover { width: 86px; height: 121px; display: block; object-fit: cover; border-radius: 8px; background: #eef3ff; box-shadow: 0 3px 8px rgba(65,81,125,.18); }
  .anime-none { display: flex; align-items: center; justify-content: center; color: #91a5df; font-size: 26px; }
  .anime-name { margin-top: 6px; color: #4f5d8e; font-size: 10.5px; line-height: 1.35; text-align: center; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .anime-tags { padding-top: 14px; }
  .anime-tag { display: inline-block; margin: 0 7px 7px 0; padding: 6px 11px; border: 1px solid #ccd8f5; border-radius: 999px; background: #f4f7ff; color: #5f72b4; font-size: 12px; }
  .anime-note { margin-top: 13px; }
  .hot-grid { display: grid; grid-template-columns: repeat(5, minmax(0,1fr)); gap: 9px; padding-top: 14px; }
  .hot-card { min-width: 0; padding: 10px 9px 9px; border-radius: 8px; background: linear-gradient(145deg,#f7f9ff,#fff); box-shadow: 0 2px 8px rgba(82,100,150,.09); }
  .hot-top { display: flex; align-items: center; gap: 6px; }
  .hot-num { flex: 0 0 19px; height: 19px; border-radius: 5px; background: #9eafea; color: white; text-align: center; line-height: 19px; font-size: 10px; font-weight: 800; }
  .hot-card:nth-child(1) .hot-num { background: #f08c3f; }
  .hot-card:nth-child(2) .hot-num { background: #ef789a; }
  .hot-title { min-width: 0; height: 32px; overflow: hidden; color: #3f4966; font-size: 10.5px; line-height: 1.45; font-weight: 700; }
  .hot-foot { margin-top: 8px; color: #8e99bc; font-size: 9.5px; }
  .hot-badge { float: right; padding: 1px 5px; border-radius: 6px; background: #fde5ed; color: #cc6987; font-weight: 800; }
  .error { padding: 16px 6px; color: #b47a85; font-size: 12px; }
  .quote {
    height: 240px; position: relative; margin-top: 2px;
    {% if chino_banner %}background-image: linear-gradient(rgba(255,255,255,.13),rgba(255,255,255,.13)),url("{{ chino_banner }}");{% endif %}
    background-size: cover; background-position: center;
    border-top: 1px solid #cbd7f4;
  }
  .quote-copy { position: absolute; left: 250px; top: 52px; width: 405px; text-align: center; }
  .quote-label { color: #7690d8; font-size: 16px; font-weight: 800; letter-spacing: 1.5px; }
  .quote-text { margin-top: 19px; color: #4e6abd; font: 850 29px Georgia,"Microsoft YaHei",serif; letter-spacing: 3px; }
  .quote-by { margin-top: 14px; color: #6980ca; font-size: 15px; font-weight: 700; }
  .footer { height: 49px; padding-top: 14px; text-align: center; color: white; font-size: 13px; font-weight: 650; letter-spacing: .5px; background: linear-gradient(90deg,#9caeee,#8398e1,#a2b0eb); }
</style>
</head>
<body>
<div class="page"><div class="sheet">
  <header class="cover">
    <div class="cover-copy">
      <div class="cover-kicker">☕ CHINO'S DAILY LETTER</div>
      <div class="cover-title">{{ title }}</div>
      <div class="greet">{{ greet }}</div>
      <div class="date-pill">▣&nbsp; {{ date_main }} · {{ weekday }}{% if calendar and calendar.lunar %}<span class="sep">|</span>{{ calendar.lunar }}{% endif %}</div>
    </div>
  </header>

  <main class="body">
    <div class="top-grid">
      <section class="panel fortune-panel">
        <div class="panel-head"><span class="head-icon">☕</span><span class="panel-title">Rabbit House 今日运势</span><span class="panel-en">LUCKY</span></div>
        <div class="fortune-body">
          {% if calendar %}
          {% if calendar.good %}<div class="yj"><span class="pill good">宜</span>{{ calendar.good }}</div>{% endif %}
          {% if calendar.bad %}<div class="yj"><span class="pill bad">忌</span>{{ calendar.bad }}</div>{% endif %}
          <div class="luck-row"><span>幸运色 <i class="swatch" style="background:{{ lucky_hex }}"></i>{{ lucky_color }}</span><span>幸运数字：<b>{{ lucky_num }}</b></span></div>
          {% endif %}
          <div class="note"><b>智乃的建议：</b><br/>{{ advice }}</div>
        </div>
      </section>

      <section class="panel coffee-panel">
        <div class="panel-head"><span class="head-icon">☕</span><span class="panel-title">今日咖啡推荐</span><span class="panel-en">COFFEE</span></div>
        <div class="coffee-info">
          <div class="coffee-cn">{{ coffee.name_cn }}</div><div class="coffee-en">{{ coffee.name_en }}</div>
          <div class="rating">推荐指数：{% for _ in range(coffee.stars) %}★{% endfor %}{% for _ in range(5 - coffee.stars) %}☆{% endfor %}</div>
          <div class="suitable">适合：<br/>{{ coffee.suitable }}</div>
          <div class="tagline">{{ coffee.tagline }}</div>
        </div>
      </section>
    </div>

    {% if news %}
    <section class="panel news-panel">
      <div class="panel-head"><span class="head-icon">▣</span><span class="panel-title">今日新闻</span><span class="panel-en">DAILY NEWS</span></div>
      {% if news.error %}<div class="error">获取失败：{{ news.error }}</div>
      {% elif news['items'] %}
      {% set half = (news['items']|length + 1)//2 %}
      <div class="news-grid">
        <div class="news-col">{% for t in news['items'][:half] %}<div class="news-item"><span class="rank">{{ loop.index }}</span><span class="news-text">{{ t }}</span></div>{% endfor %}</div>
        <div class="news-col">{% for t in news['items'][half:] %}<div class="news-item"><span class="rank">{{ loop.index + half }}</span><span class="news-text">{{ t }}</span></div>{% endfor %}</div>
      </div>
      {% if news.tip %}<div class="note news-note"><b>智乃点评：</b>{{ news.tip }}</div>{% endif %}
      {% else %}<div class="error">今天还没有新的内容。</div>{% endif %}
    </section>
    {% endif %}

    {% if anime %}
    <section class="panel anime-panel">
      <div class="panel-head"><span class="head-icon">▶</span><span class="panel-title">智乃推荐动画</span><span class="panel-en">{{ anime.weekday }} · ON AIR</span></div>
      {% if anime.error %}<div class="error">获取失败：{{ anime.error }}</div>
      {% elif anime.covers %}<div class="anime-wrap"><div class="anime-list">{% for c in anime.covers %}<div class="anime-item">{% if c.img %}<img class="anime-cover" src="{{ c.img }}"/>{% else %}<div class="anime-cover anime-none">🐰</div>{% endif %}<div class="anime-name">{{ c.name }}</div></div>{% endfor %}</div><div class="note anime-note"><b>智乃的话：</b>今天适合安静地看动画，享受悠闲的时光。</div></div>
      {% elif anime['items'] %}<div class="anime-tags">{% for i in anime['items'] %}<span class="anime-tag">{{ i.name }}</span>{% endfor %}</div>
      {% else %}<div class="error">今天没有查询到更新的番剧。</div>{% endif %}
    </section>
    {% endif %}

    {% if hot_cards %}
    <section class="panel hot-panel">
      <div class="panel-head"><span class="head-icon">◆</span><span class="panel-title">今日热议</span><span class="panel-en">TRENDING NOW</span></div>
      <div class="hot-grid">{% for t in hot_cards %}<div class="hot-card"><div class="hot-top"><span class="hot-num">{{ loop.index }}</span><span class="hot-title">{{ t }}</span></div><div class="hot-foot">热度上升中 <span class="hot-badge">热</span></div></div>{% endfor %}</div>
    </section>
    {% endif %}
  </main>

  <section class="quote"><div class="quote-copy"><div class="quote-label">☕ 今日一句</div><div class="quote-text">「{{ quote }}」</div><div class="quote-by">—— 智乃</div></div></section>
  <footer class="footer">♧ Rabbit House　|　今天也谢谢你的到来。请多关照哦。 ✿</footer>
</div></div>
</body>
</html>
"""

NIGHT_TMPL = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { font-family: "PingFang SC","Microsoft YaHei","Hiragino Sans GB","Noto Sans CJK SC",sans-serif; background: #20264a; }
  .card {
    width: 820px; zoom: {{ scale | default(2) }}; position: relative; overflow: hidden; color: #cdd2ee;
    background:
      radial-gradient(1.6px 1.6px at 12% 8%, rgba(255,255,255,0.9), transparent),
      radial-gradient(1.3px 1.3px at 78% 6%, rgba(255,255,255,0.7), transparent),
      radial-gradient(1.2px 1.2px at 33% 18%, rgba(255,255,255,0.6), transparent),
      radial-gradient(1.5px 1.5px at 88% 22%, rgba(255,255,255,0.8), transparent),
      radial-gradient(1.1px 1.1px at 55% 12%, rgba(255,255,255,0.55), transparent),
      radial-gradient(1.4px 1.4px at 22% 30%, rgba(255,255,255,0.6), transparent),
      radial-gradient(1.2px 1.2px at 68% 33%, rgba(255,255,255,0.5), transparent),
      radial-gradient(circle at 82% 14%, rgba(232,201,138,0.16), transparent 26%),
      radial-gradient(circle at 14% 88%, rgba(150,130,210,0.18), transparent 40%),
      linear-gradient(162deg, #181d3c 0%, #21275086 0%, #232a52 44%, #2b2a54 78%, #1d2347 100%);
  }
  .hd { position: relative; display: flex; justify-content: space-between; align-items: center; padding: 30px 34px 10px 34px; }
  .hd-l { display: flex; align-items: center; gap: 14px; }
  .logo-cup { font-size: 40px; }
  .title { font-size: 31px; font-weight: 800; color: #ecd6a4; letter-spacing: 0.5px; text-shadow: 0 0 18px rgba(236,214,164,0.3); }
  .subtitle { font-size: 15px; color: #a8aed6; margin-top: 5px; }
  .emblem { border: 2px solid rgba(236,201,138,0.55); border-radius: 14px; padding: 7px 16px; text-align: center; background: rgba(255,255,255,0.05); }
  .emblem-cup { font-size: 19px; line-height: 1; }
  .emblem-t { font-size: 12.5px; font-weight: 800; letter-spacing: 2px; color: #e3c285; line-height: 1.25; margin-top: 2px; }
  .deco { position: absolute; opacity: 0.55; pointer-events: none; }
  .datebar {
    margin: 6px 34px 4px 34px; padding: 11px 20px; border-radius: 16px; text-align: center;
    background: linear-gradient(90deg, #5b5286, #6a5290); color: #f3ecdc; font-size: 15px; font-weight: 600;
    box-shadow: 0 4px 16px rgba(0,0,0,0.3); border: 1px solid rgba(255,255,255,0.08);
  }
  .body { position: relative; z-index: 1; padding: 16px 24px 6px 24px; }
  .row2 { display: flex; gap: 16px; }
  .row2 .sec { flex: 1; margin-bottom: 16px; }
  .sec {
    background: rgba(38,44,80,0.72); border: 1px solid rgba(255,255,255,0.08); border-radius: 20px;
    padding: 18px 22px; margin-bottom: 16px; box-shadow: 0 6px 24px rgba(0,0,0,0.32);
  }
  .sec-h { font-size: 18px; font-weight: 800; color: #e8c98a; padding-bottom: 9px; margin-bottom: 12px; border-bottom: 1.5px dashed rgba(255,255,255,0.13); }
  .yj { font-size: 14.5px; line-height: 1.95; color: #c4cae8; }
  .pill { display: inline-block; padding: 1px 9px; border-radius: 9px; font-size: 12.5px; margin-right: 7px; font-weight: 700; }
  .pill-y { background: rgba(120,160,220,0.26); color: #a8c8f0; }
  .pill-j { background: rgba(220,130,160,0.24); color: #f0aac8; }
  .luck { display: flex; gap: 26px; margin-top: 11px; font-size: 14px; color: #b0b6da; }
  .swatch { display: inline-block; width: 14px; height: 14px; border-radius: 50%; margin: 0 5px; vertical-align: -2px; box-shadow: 0 0 0 2px rgba(255,255,255,0.25), 0 0 8px rgba(255,255,255,0.2); }
  .luck b { color: #ecd6a4; font-size: 16px; }
  .note { background: rgba(255,255,255,0.05); border-radius: 12px; padding: 10px 14px; margin-top: 13px; font-size: 13.5px; color: #b4bae0; line-height: 1.6; }
  .note-h { font-size: 12.5px; font-weight: 700; color: #c8aae8; margin-bottom: 3px; }
  .sec-coffee { background: linear-gradient(135deg, rgba(74,58,46,0.62) 0%, rgba(60,48,42,0.58) 100%); border-color: rgba(224,184,120,0.22); }
  .sec-h-c { color: #e6b878; border-bottom-color: rgba(230,184,120,0.26); }
  .cf-top { display: flex; align-items: center; gap: 14px; }
  .cf-emoji { font-size: 46px; line-height: 1; }
  .cf-name { font-size: 23px; font-weight: 800; color: #e6b878; }
  .cf-en { font-size: 13px; color: #b6a078; margin-top: 2px; letter-spacing: 0.5px; }
  .cf-stars { margin-top: 12px; font-size: 15px; color: #f3ca5e; letter-spacing: 2px; }
  .cf-suit { margin-top: 7px; font-size: 14px; color: #c4b496; }
  .cf-tag { margin-top: 11px; padding-top: 9px; border-top: 1px dashed rgba(255,255,255,0.12); font-size: 13px; color: #ab9c80; }
  .news2 { display: flex; gap: 26px; }
  .news-col { flex: 1; }
  .ni { display: flex; gap: 8px; margin-bottom: 9px; align-items: flex-start; }
  .nn { flex-shrink: 0; width: 19px; height: 19px; border-radius: 50%; background: rgba(120,140,200,0.32); color: #b8c8f0; font-size: 11px; font-weight: 700; text-align: center; line-height: 19px; }
  .nt { font-size: 13px; color: #c4cae8; line-height: 1.5; }
  .acg { display: flex; flex-wrap: wrap; gap: 13px; }
  .acg-i { width: 96px; }
  .acg-c { width: 96px; height: 135px; border-radius: 12px; object-fit: cover; display: block; box-shadow: 0 4px 14px rgba(0,0,0,0.45); background: #2a3052; }
  .acg-n { display: flex; align-items: center; justify-content: center; font-size: 30px; }
  .acg-t { margin-top: 6px; font-size: 12px; line-height: 1.35; color: #b8c0e8; max-width: 96px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .tags .tag { display: inline-block; margin: 0 8px 8px 0; padding: 6px 12px; border-radius: 14px; background: rgba(120,140,200,0.22); color: #b8c8f0; font-size: 13.5px; }
  .hot2 { display: flex; flex-wrap: wrap; gap: 11px; }
  .hc { flex: 1 1 auto; min-width: 210px; max-width: 360px; display: flex; align-items: center; gap: 9px; background: rgba(255,255,255,0.05); border-radius: 12px; padding: 10px 13px; }
  .hc-n { flex-shrink: 0; width: 19px; height: 19px; border-radius: 6px; background: rgba(240,160,100,0.3); color: #f0b878; font-size: 11px; font-weight: 800; text-align: center; line-height: 19px; }
  .hc-t { flex: 1; font-size: 13px; color: #c4cae8; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
  .hc-b { flex-shrink: 0; background: rgba(220,130,160,0.24); color: #f0aac8; font-size: 10px; padding: 1px 7px; border-radius: 7px; font-weight: 700; }
  .err { color: #c0a0a0; font-size: 13.5px; }
  .quote-sec { text-align: center; padding: 20px 24px 6px 24px; position: relative; z-index: 1; }
  .quote-h { font-size: 14px; color: #a8aed6; }
  .quote-big { font-size: 27px; font-weight: 800; color: #ecd6a4; letter-spacing: 3px; margin: 10px 0 8px 0; text-shadow: 0 0 16px rgba(236,214,164,0.25); }
  .quote-by { font-size: 14px; color: #9098c0; }
  .footer { position: relative; z-index: 1; text-align: center; padding: 15px; margin-top: 8px; color: #9098c0; font-size: 12.5px; border-top: 1px solid rgba(255,255,255,0.1); }
</style>
</head>
<body>
<div class="card">
  <span class="deco" style="top:24px;left:300px;font-size:22px;">&#127769;</span>
  <span class="deco" style="top:74px;right:210px;font-size:15px;">&#11088;</span>
  <span class="deco" style="top:130px;left:34px;font-size:15px;">&#10024;</span>
  <span class="deco" style="bottom:120px;right:26px;font-size:18px;">&#11088;</span>
  <span class="deco" style="bottom:58px;left:32px;font-size:16px;">&#9749;</span>

  <div class="hd">
    <div class="hd-l">
      <div class="logo-cup">&#9749;</div>
      <div>
        <div class="title">智乃的 Rabbit House 日报</div>
        <div class="subtitle">{{ greet }}</div>
      </div>
    </div>
    <div class="emblem">
      <div class="emblem-cup">&#9749;</div>
      <div class="emblem-t">RABBIT<br/>HOUSE</div>
    </div>
  </div>
  <div class="datebar">&#127769; {{ date_main }} · {{ weekday }}{% if calendar and calendar.lunar %} ｜ {{ calendar.lunar }}{% endif %}</div>

  <div class="body">
    <div class="row2">
      <div class="sec sec-fortune">
        <div class="sec-h">&#128048; Rabbit House 今日运势</div>
        {% if calendar %}
        {% if calendar.good %}<div class="yj"><span class="pill pill-y">宜</span>{{ calendar.good }}</div>{% endif %}
        {% if calendar.bad %}<div class="yj"><span class="pill pill-j">忌</span>{{ calendar.bad }}</div>{% endif %}
        <div class="luck">
          <div>幸运色<span class="swatch" style="background:{{ lucky_hex }};"></span>{{ lucky_color }}</div>
          <div>幸运数字 <b>{{ lucky_num }}</b></div>
        </div>
        {% endif %}
        <div class="note"><div class="note-h">&#128172; 智乃的建议</div>{{ advice }}</div>
      </div>

      <div class="sec sec-coffee">
        <div class="sec-h sec-h-c">&#9749; 今日咖啡推荐</div>
        <div class="cf-top">
          <div class="cf-emoji">&#9749;</div>
          <div>
            <div class="cf-name">{{ coffee.name_cn }}</div>
            <div class="cf-en">{{ coffee.name_en }}</div>
          </div>
        </div>
        <div class="cf-stars">推荐指数 {% for _ in range(coffee.stars) %}&#9733;{% endfor %}{% for _ in range(5 - coffee.stars) %}&#9734;{% endfor %}</div>
        <div class="cf-suit">适合：{{ coffee.suitable }}</div>
        <div class="cf-tag">{{ coffee.tagline }}</div>
      </div>
    </div>

    {% if news %}
    <div class="sec">
      <div class="sec-h">&#128240; 今日新闻</div>
      {% if news.error %}<div class="err">获取失败：{{ news.error }}</div>
      {% elif news['items'] %}
        {% set half = (news['items']|length + 1)//2 %}
        <div class="news2">
          <div class="news-col">{% for t in news['items'][:half] %}<div class="ni"><span class="nn">{{ loop.index }}</span><span class="nt">{{ t }}</span></div>{% endfor %}</div>
          <div class="news-col">{% for t in news['items'][half:] %}<div class="ni"><span class="nn">{{ loop.index + half }}</span><span class="nt">{{ t }}</span></div>{% endfor %}</div>
        </div>
        {% if news.tip %}<div class="note"><div class="note-h">&#128172; 智乃点评</div>{{ news.tip }}</div>{% endif %}
      {% else %}<div class="err">暂无内容</div>{% endif %}
    </div>
    {% endif %}

    {% if anime %}
    <div class="sec">
      <div class="sec-h">&#128250; 智乃推荐动画 · {{ anime.weekday }}</div>
      {% if anime.error %}<div class="err">获取失败：{{ anime.error }}</div>
      {% elif anime.covers %}<div class="acg">{% for c in anime.covers %}<div class="acg-i">{% if c.img %}<img class="acg-c" src="{{ c.img }}"/>{% else %}<div class="acg-c acg-n">&#127845;</div>{% endif %}<div class="acg-t">{{ c.name }}</div></div>{% endfor %}</div>
        <div class="note"><div class="note-h">&#128172; 智乃的话</div>夜深了，看部喜欢的动画，再睡个好觉吧。</div>
      {% elif anime['items'] %}<div class="tags">{% for i in anime['items'] %}<span class="tag">{{ i.name }}</span>{% endfor %}</div>
      {% else %}<div class="err">今天没有更新的番剧</div>{% endif %}
    </div>
    {% endif %}

    {% if hot_cards %}
    <div class="sec">
      <div class="sec-h">&#128293; 今日热议</div>
      <div class="hot2">{% for t in hot_cards %}<div class="hc"><span class="hc-n">{{ loop.index }}</span><span class="hc-t">{{ t }}</span><span class="hc-b">热</span></div>{% endfor %}</div>
    </div>
    {% endif %}
  </div>

  <div class="quote-sec">
    <div class="quote-h">&#127769; 今日一句</div>
    <div class="quote-big">「{{ quote }}」</div>
    <div class="quote-by">—— 智乃</div>
  </div>
  <div class="footer">&#128048; Rabbit House ｜ 夜色温柔，晚安，明天见。</div>
</div>
</body>
</html>
"""

ANIME_TMPL = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { font-family: "PingFang SC","Microsoft YaHei","Hiragino Sans GB","Noto Sans CJK SC",sans-serif; background: {{ '#20264a' if dark else '#eef0fb' }}; }
  .card {
    width: 820px; zoom: {{ scale | default(2) }}; position: relative; overflow: hidden;
    padding: 24px 26px 18px 26px;
    color: {{ '#cdd2ee' if dark else '#5a6890' }};
    background: {{ 'radial-gradient(1.4px 1.4px at 14% 10%, rgba(255,255,255,0.85), transparent), radial-gradient(1.2px 1.2px at 80% 8%, rgba(255,255,255,0.6), transparent), radial-gradient(1.3px 1.3px at 60% 16%, rgba(255,255,255,0.5), transparent), linear-gradient(160deg,#1b2040 0%,#232a52 55%,#2b2a54 100%)' if dark else 'radial-gradient(circle at 16% 10%, rgba(210,222,255,0.55), transparent 36%), radial-gradient(circle at 86% 8%, rgba(245,221,239,0.5), transparent 38%), linear-gradient(160deg,#eaedfb 0%,#eef0fb 50%,#f3edfa 100%)' }};
  }
  .hd { display: flex; align-items: center; gap: 12px; margin-bottom: 4px; }
  .cup { font-size: 30px; }
  .title { font-size: 23px; font-weight: 800; color: {{ '#ecd6a4' if dark else '#6b7bc4' }};
           {% if dark %}text-shadow: 0 0 16px rgba(236,214,164,0.3);{% endif %} }
  .sub { font-size: 13px; color: {{ '#a8aed6' if dark else '#8b96c8' }}; margin-top: 3px; }
  .panel {
    margin-top: 14px; padding: 18px 20px; border-radius: 18px;
    background: {{ 'rgba(38,44,80,0.72)' if dark else '#ffffff' }};
    box-shadow: 0 6px 22px rgba({{ '0,0,0,0.32' if dark else '150,160,210,0.16' }});
    {% if dark %}border: 1px solid rgba(255,255,255,0.08);{% endif %}
  }
  .acg { display: flex; flex-wrap: wrap; gap: 13px; }
  .acg-i { width: 94px; }
  .acg-c { width: 94px; height: 132px; border-radius: 11px; object-fit: cover; display: block;
           box-shadow: 0 4px 12px rgba({{ '0,0,0,0.45' if dark else '120,140,190,0.22' }});
           background: {{ '#2a3052' if dark else '#eef2fb' }}; }
  .acg-n { display: flex; align-items: center; justify-content: center; font-size: 28px; }
  .acg-t { margin-top: 5px; font-size: 11.5px; line-height: 1.35; color: {{ '#b8c0e8' if dark else '#5f80b6' }};
           max-width: 94px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .txt { font-size: 13.5px; line-height: 1.85; color: {{ '#c4cae8' if dark else '#5a6890' }}; }
  .err { color: {{ '#c0a0a0' if dark else '#b6a0a0' }}; font-size: 13.5px; }
  .foot { text-align: center; margin-top: 14px; font-size: 12px; color: {{ '#9098c0' if dark else '#9aa6c2' }}; }
</style>
</head>
<body>
<div class="card">
  <div class="hd">
    <div class="cup">&#128250;</div>
    <div>
      <div class="title">今日番剧 · {{ anime.weekday }}</div>
      <div class="sub">智乃为你整理的今日更新</div>
    </div>
  </div>
  <div class="panel">
    {% if anime.error %}<div class="err">获取失败：{{ anime.error }}</div>
    {% elif anime.covers %}<div class="acg">{% for c in anime.covers %}<div class="acg-i">{% if c.img %}<img class="acg-c" src="{{ c.img }}"/>{% else %}<div class="acg-c acg-n">&#127845;</div>{% endif %}<div class="acg-t">{{ c.name }}</div></div>{% endfor %}</div>
    {% elif anime['items'] %}<div class="txt">{% for i in anime['items'] %}· {{ i.name }}<br/>{% endfor %}</div>
    {% else %}<div class="err">今天没有查询到更新的番剧。</div>{% endif %}
  </div>
  <div class="foot">&#128048; Rabbit House ｜ {% if dark %}夜色温柔，挑一部慢慢看吧{% else %}今天也谢谢你的到来{% endif %}</div>
</div>
</body>
</html>
"""

HELP_TMPL = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { font-family: "PingFang SC","Microsoft YaHei","Hiragino Sans GB","Noto Sans CJK SC",sans-serif;
               background: {{ '#20264a' if dark else '#eef0fb' }}; }
  .card {
    width: 820px; zoom: {{ scale | default(2) }}; position: relative; overflow: hidden;
    color: {{ '#cdd2ee' if dark else '#5a6890' }};
    background: {{ 'radial-gradient(1.5px 1.5px at 12% 8%, rgba(255,255,255,0.85), transparent), radial-gradient(1.2px 1.2px at 80% 6%, rgba(255,255,255,0.6), transparent), radial-gradient(1.3px 1.3px at 55% 14%, rgba(255,255,255,0.5), transparent), radial-gradient(circle at 84% 12%, rgba(232,201,138,0.16), transparent 26%), linear-gradient(162deg,#181d3c 0%,#232a52 45%,#2b2a54 80%,#1d2347 100%)' if dark else 'radial-gradient(circle at 16% 10%, rgba(210,222,255,0.55), transparent 34%), radial-gradient(circle at 86% 8%, rgba(245,221,239,0.55), transparent 36%), radial-gradient(circle at 78% 92%, rgba(216,228,255,0.5), transparent 42%), linear-gradient(162deg,#eaedfb 0%,#eef0fb 38%,#f3edfa 74%,#e9edfb 100%)' }};
  }
  .hd { position: relative; display: flex; justify-content: space-between; align-items: center; padding: 28px 32px 8px 32px; }
  .hd-l { display: flex; align-items: center; gap: 13px; }
  .logo-cup { font-size: 38px; }
  .title { font-size: 29px; font-weight: 800; color: {{ '#ecd6a4' if dark else '#6b7bc4' }};
           {% if dark %}text-shadow: 0 0 16px rgba(236,214,164,0.3);{% endif %} }
  .subtitle { font-size: 14px; color: {{ '#a8aed6' if dark else '#8b96c8' }}; margin-top: 4px; }
  .emblem { border: 2px solid {{ 'rgba(236,201,138,0.55)' if dark else '#cbb28c' }}; border-radius: 13px; padding: 6px 15px;
            text-align: center; background: {{ 'rgba(255,255,255,0.05)' if dark else 'rgba(255,255,255,0.45)' }}; }
  .emblem-cup { font-size: 18px; line-height: 1; }
  .emblem-t { font-size: 12px; font-weight: 800; letter-spacing: 2px; color: {{ '#e3c285' if dark else '#a98a5f' }}; line-height: 1.25; margin-top: 2px; }
  .deco { position: absolute; opacity: {{ '0.5' if dark else '0.32' }}; pointer-events: none; }
  .body { position: relative; z-index: 1; padding: 12px 24px 6px 24px; }
  .group {
    background: {{ 'rgba(38,44,80,0.72)' if dark else '#ffffff' }};
    {% if dark %}border: 1px solid rgba(255,255,255,0.08);{% endif %}
    border-radius: 18px; padding: 16px 22px; margin-bottom: 14px;
    box-shadow: 0 6px 22px rgba({{ '0,0,0,0.3' if dark else '150,160,210,0.16' }});
  }
  .group-h { font-size: 16.5px; font-weight: 800; color: {{ '#e8c98a' if dark else '#6b7bc4' }};
             padding-bottom: 8px; margin-bottom: 8px; border-bottom: 1.5px dashed {{ 'rgba(255,255,255,0.13)' if dark else '#e6e9f6' }}; }
  .cmd { display: flex; align-items: baseline; gap: 12px; padding: 6px 2px; }
  .cmd + .cmd { border-top: 1px solid {{ 'rgba(255,255,255,0.05)' if dark else '#f1f3fb' }}; }
  .cmd-name { flex-shrink: 0; min-width: 132px; font-weight: 700; font-size: 14.5px;
              color: {{ '#f0d9a8' if dark else '#5b78c4' }}; }
  .cmd-desc { flex: 1; font-size: 13px; line-height: 1.5; color: {{ '#b4bae0' if dark else '#6a76a0' }}; }
  .cmd-tag { flex-shrink: 0; font-size: 10.5px; font-weight: 700; padding: 1px 7px; border-radius: 7px;
             background: {{ 'rgba(220,130,160,0.24)' if dark else '#fce8ef' }}; color: {{ '#f0aac8' if dark else '#d97a9a' }}; align-self: center; }
  .tip { text-align: center; font-size: 12.5px; color: {{ '#a8aed6' if dark else '#8b96c8' }}; margin: 6px 0 2px 0; }
  .footer { position: relative; z-index: 1; text-align: center; padding: 14px; margin-top: 6px;
            color: {{ '#9098c0' if dark else '#9aa6c2' }}; font-size: 12.5px;
            border-top: 1px solid {{ 'rgba(255,255,255,0.1)' if dark else 'rgba(200,205,235,0.5)' }}; }
</style>
</head>
<body>
<div class="card">
  <span class="deco" style="top:20px;left:290px;font-size:18px;">{{ '&#127769;' if dark else '&#9729;&#65039;' }}</span>
  <span class="deco" style="top:70px;right:200px;font-size:15px;">{{ '&#11088;' if dark else '&#10024;' }}</span>
  <span class="deco" style="bottom:60px;left:28px;font-size:16px;">&#9749;</span>

  <div class="hd">
    <div class="hd-l">
      <div class="logo-cup">&#9749;</div>
      <div>
        <div class="title">智乃的 Rabbit House 菜单</div>
        <div class="subtitle">{{ greet }}这些是智乃能为你做的事～</div>
      </div>
    </div>
    <div class="emblem">
      <div class="emblem-cup">&#9749;</div>
      <div class="emblem-t">RABBIT<br/>HOUSE</div>
    </div>
  </div>

  <div class="body">
    {% for g in groups %}
    <div class="group">
      <div class="group-h">{{ g.title }}</div>
      {% for c in g['items'] %}
      <div class="cmd"><span class="cmd-name">{{ c.cmd }}</span><span class="cmd-desc">{{ c.desc }}</span>{% if c.admin %}<span class="cmd-tag">管理员</span>{% endif %}</div>
      {% endfor %}
    </div>
    {% endfor %}
    <div class="tip">别名也能用，例如 /番剧 = /今日番剧 · /黄历 = /日历</div>
  </div>

  <div class="footer">&#128048; Rabbit House ｜ {% if dark %}夜深了，需要什么尽管吩咐{% else %}今天也请多关照哦{% endif %}</div>
</div>
</body>
</html>
"""

MURASAME_TMPL = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { font-family: "PingFang SC","Microsoft YaHei","Hiragino Sans GB","Noto Sans CJK SC",sans-serif; background: #f3ece0; }
  .card {
    width: 820px; zoom: {{ scale | default(2) }}; position: relative; overflow: hidden;
    color: #6b5d4f;
    background:
      radial-gradient(circle at 14% 8%, rgba(244,184,200,0.42), transparent 32%),
      radial-gradient(circle at 88% 10%, rgba(168,207,180,0.36), transparent 34%),
      radial-gradient(circle at 80% 94%, rgba(240,178,62,0.16), transparent 40%),
      radial-gradient(circle at 10% 90%, rgba(244,184,200,0.28), transparent 36%),
      linear-gradient(162deg, #f7f0e4 0%, #f5eee1 40%, #f7efe3 75%, #f4ecdf 100%);
  }
  .hd { position: relative; display: flex; justify-content: space-between; align-items: center;
        padding: 30px 34px 10px 34px; }
  .hd-l { display: flex; align-items: center; gap: 14px; }
  .logo-cup { font-size: 40px; }
  .title { font-size: 31px; font-weight: 800; color: #b5453c; letter-spacing: 0.5px; }
  .subtitle { font-size: 15px; color: #a08a76; margin-top: 5px; }
  .emblem { border: 2px solid #c9a227; border-radius: 14px; padding: 7px 16px; text-align: center;
            background: rgba(255,255,255,0.5); }
  .emblem-cup { font-size: 19px; line-height: 1; }
  .emblem-t { font-size: 12.5px; font-weight: 800; letter-spacing: 2px; color: #b08c2a; line-height: 1.25; margin-top: 2px; }
  .deco { position: absolute; opacity: 0.35; pointer-events: none; }
  .datebar {
    margin: 6px 34px 4px 34px; padding: 11px 20px; border-radius: 16px; text-align: center;
    background: linear-gradient(90deg,#cf6a54,#b5453c); color: #fff8ee; font-size: 15px; font-weight: 600;
    box-shadow: 0 4px 14px rgba(181,69,60,0.26);
  }
  .body { position: relative; z-index: 1; padding: 16px 24px 6px 24px; }
  .row2 { display: flex; gap: 16px; }
  .row2 .sec { flex: 1; margin-bottom: 16px; }
  .sec {
    background: #fffdf8; border-radius: 20px; padding: 18px 22px; margin-bottom: 16px;
    box-shadow: 0 6px 22px rgba(180,130,90,0.15);
  }
  .sec-h { font-size: 18px; font-weight: 800; color: #b5453c;
           padding-bottom: 9px; margin-bottom: 12px; border-bottom: 1.5px dashed #f0e2d0; }
  .yj { font-size: 14.5px; line-height: 1.95; color: #6b5d4f; }
  .pill { display: inline-block; padding: 1px 9px; border-radius: 9px; font-size: 12.5px; margin-right: 7px; font-weight: 700; }
  .pill-y { background: #e2f1e9; color: #3d8b7d; }
  .pill-j { background: #fbe3dd; color: #c05348; }
  .luck { display: flex; gap: 26px; margin-top: 11px; font-size: 14px; color: #8a7862; }
  .swatch { display: inline-block; width: 14px; height: 14px; border-radius: 50%; margin: 0 5px; vertical-align: -2px;
            box-shadow: 0 0 0 2px rgba(255,255,255,0.85), 0 1px 3px rgba(0,0,0,0.12); }
  .luck b { color: #b08c2a; font-size: 16px; }
  .note { background: #faf5ea; border-radius: 12px; padding: 10px 14px; margin-top: 13px; font-size: 13.5px; color: #857361; line-height: 1.6; }
  .note-h { font-size: 12.5px; font-weight: 700; color: #4a9e8f; margin-bottom: 3px; }
  .sec-wagashi { background: linear-gradient(135deg,#fdf2ec 0%,#fbe7dc 100%); }
  .sec-h-w { color: #c05348; border-bottom-color: #f2ddd0; }
  .wg-top { display: flex; align-items: center; gap: 14px; }
  .wg-emoji { font-size: 46px; line-height: 1; }
  .wg-name { font-size: 23px; font-weight: 800; color: #c05348; }
  .wg-en { font-size: 13px; color: #cf9384; margin-top: 2px; letter-spacing: 0.5px; }
  .wg-stars { margin-top: 12px; font-size: 15px; color: #e3a72f; letter-spacing: 2px; }
  .wg-suit { margin-top: 7px; font-size: 14px; color: #9a7a68; }
  .wg-tag { margin-top: 11px; padding-top: 9px; border-top: 1px dashed #f0dcd0; font-size: 13px; color: #b09480; }
  .news2 { display: flex; gap: 26px; }
  .news-col { flex: 1; }
  .ni { display: flex; gap: 8px; margin-bottom: 9px; align-items: flex-start; }
  .nn { flex-shrink: 0; width: 19px; height: 19px; border-radius: 50%; background: #e2f1e9; color: #3d8b7d;
        font-size: 11px; font-weight: 700; text-align: center; line-height: 19px; }
  .nt { font-size: 13px; color: #6b5d4f; line-height: 1.5; }
  .acg { display: flex; flex-wrap: wrap; gap: 13px; }
  .acg-i { width: 96px; }
  .acg-c { width: 96px; height: 135px; border-radius: 12px; object-fit: cover; display: block;
           box-shadow: 0 4px 12px rgba(160,110,80,0.22); background: #f5ecdd; }
  .acg-n { display: flex; align-items: center; justify-content: center; font-size: 30px; }
  .acg-t { margin-top: 6px; font-size: 12px; line-height: 1.35; color: #8a6f56; max-width: 96px;
           display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .tags .tag { display: inline-block; margin: 0 8px 8px 0; padding: 6px 12px; border-radius: 14px;
               background: #f2e8d6; color: #8a6f52; font-size: 13.5px; }
  .hot2 { display: flex; flex-wrap: wrap; gap: 11px; }
  .hc { flex: 1 1 auto; min-width: 210px; max-width: 360px; display: flex; align-items: center; gap: 9px;
        background: #faf6ec; border-radius: 12px; padding: 10px 13px; }
  .hc-n { flex-shrink: 0; width: 19px; height: 19px; border-radius: 6px; background: #e2f1e9; color: #3d8b7d;
          font-size: 11px; font-weight: 800; text-align: center; line-height: 19px; }
  .hc-t { flex: 1; font-size: 13px; color: #6b5d4f; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
  .hc-b { flex-shrink: 0; background: #fbe3dd; color: #c05348; font-size: 10px; padding: 1px 7px; border-radius: 7px; font-weight: 700; }
  .err { color: #b6a0a0; font-size: 13.5px; }
  .quote-sec { text-align: center; padding: 20px 24px 6px 24px; position: relative; z-index: 1; }
  .quote-h { font-size: 14px; color: #a08a76; }
  .quote-big { font-size: 27px; font-weight: 800; color: #b5453c; letter-spacing: 3px; margin: 10px 0 8px 0; }
  .quote-by { font-size: 14px; color: #ab998a; }
  .footer { position: relative; z-index: 1; text-align: center; padding: 15px; margin-top: 8px;
            color: #a89a88; font-size: 12.5px; border-top: 1px solid rgba(205,180,150,0.45); }
</style>
</head>
<body>
<div class="card">
  <span class="deco" style="top:18px;left:300px;font-size:19px;">&#9961;&#65039;</span>
  <span class="deco" style="top:72px;right:210px;font-size:15px;">&#127800;</span>
  <span class="deco" style="top:124px;left:30px;font-size:15px;">&#127800;</span>
  <span class="deco" style="bottom:118px;right:26px;font-size:18px;">&#127982;</span>
  <span class="deco" style="bottom:58px;left:32px;font-size:16px;">&#127841;</span>

  <div class="hd">
    <div class="hd-l">
      <div class="logo-cup">&#9961;&#65039;</div>
      <div>
        <div class="title">丛雨的今日报</div>
        <div class="subtitle">{{ greet }}</div>
      </div>
    </div>
    <div class="emblem">
      <div class="emblem-cup">&#9876;</div>
      <div class="emblem-t">MURA<br/>SAME</div>
    </div>
  </div>
  <div class="datebar">&#9961;&#65039; {{ date_main }} · {{ weekday }}{% if calendar and calendar.lunar %} ｜ {{ calendar.lunar }}{% endif %}</div>

  <div class="body">
    <div class="row2">
      <div class="sec sec-fortune">
        <div class="sec-h">&#9961;&#65039; 神社今日运势</div>
        {% if calendar %}
        {% if calendar.good %}<div class="yj"><span class="pill pill-y">宜</span>{{ calendar.good }}</div>{% endif %}
        {% if calendar.bad %}<div class="yj"><span class="pill pill-j">忌</span>{{ calendar.bad }}</div>{% endif %}
        <div class="luck">
          <div>幸运色<span class="swatch" style="background:{{ lucky_hex }};"></span>{{ lucky_color }}</div>
          <div>幸运数字 <b>{{ lucky_num }}</b></div>
        </div>
        {% endif %}
        <div class="note"><div class="note-h">&#128172; 丛雨的建议</div>{{ advice }}</div>
      </div>

      <div class="sec sec-wagashi">
        <div class="sec-h sec-h-w">&#127841; 今日和菓子</div>
        <div class="wg-top">
          <div class="wg-emoji">&#127841;</div>
          <div>
            <div class="wg-name">{{ wagashi.name_cn }}</div>
            <div class="wg-en">{{ wagashi.name_en }}</div>
          </div>
        </div>
        <div class="wg-stars">推荐指数 {% for _ in range(wagashi.stars) %}&#9733;{% endfor %}{% for _ in range(5 - wagashi.stars) %}&#9734;{% endfor %}</div>
        <div class="wg-suit">适合：{{ wagashi.suitable }}</div>
        <div class="wg-tag">{{ wagashi.tagline }}</div>
      </div>
    </div>

    {% if news %}
    <div class="sec">
      <div class="sec-h">&#128240; 今日新闻</div>
      {% if news.error %}<div class="err">获取失败：{{ news.error }}</div>
      {% elif news['items'] %}
        {% set half = (news['items']|length + 1)//2 %}
        <div class="news2">
          <div class="news-col">{% for t in news['items'][:half] %}<div class="ni"><span class="nn">{{ loop.index }}</span><span class="nt">{{ t }}</span></div>{% endfor %}</div>
          <div class="news-col">{% for t in news['items'][half:] %}<div class="ni"><span class="nn">{{ loop.index + half }}</span><span class="nt">{{ t }}</span></div>{% endfor %}</div>
        </div>
        {% if news.tip %}<div class="note"><div class="note-h">&#128172; 丛雨点评</div>{{ news.tip }}</div>{% endif %}
      {% else %}<div class="err">暂无内容</div>{% endif %}
    </div>
    {% endif %}

    {% if anime %}
    <div class="sec">
      <div class="sec-h">&#128250; 丛雨推荐动画 · {{ anime.weekday }}</div>
      {% if anime.error %}<div class="err">获取失败：{{ anime.error }}</div>
      {% elif anime.covers %}<div class="acg">{% for c in anime.covers %}<div class="acg-i">{% if c.img %}<img class="acg-c" src="{{ c.img }}"/>{% else %}<div class="acg-c acg-n">&#127845;</div>{% endif %}<div class="acg-t">{{ c.name }}</div></div>{% endfor %}</div>
        <div class="note"><div class="note-h">&#128172; 丛雨的话</div>今日也一起看番吧，コン。吾辈先挑好位置了。</div>
      {% elif anime['items'] %}<div class="tags">{% for i in anime['items'] %}<span class="tag">{{ i.name }}</span>{% endfor %}</div>
      {% else %}<div class="err">今天没有更新的番剧</div>{% endif %}
    </div>
    {% endif %}

    {% if hot_cards %}
    <div class="sec">
      <div class="sec-h">&#128293; 今日热议</div>
      <div class="hot2">{% for t in hot_cards %}<div class="hc"><span class="hc-n">{{ loop.index }}</span><span class="hc-t">{{ t }}</span><span class="hc-b">热</span></div>{% endfor %}</div>
    </div>
    {% endif %}
  </div>

  <div class="quote-sec">
    <div class="quote-h">&#127982; 今日一句</div>
    <div class="quote-big">「{{ quote }}」</div>
    <div class="quote-by">—— 丛雨</div>
  </div>
  <div class="footer">&#9961;&#65039; 神刀丛雨 ｜ 吾辈会一直守护着汝的，コン。</div>
</div>
</body>
</html>
"""


class DailyReportPlugin(Star):
    """每日日报：聚合每日新闻、今日番剧、各站热榜、日历/农历，支持指令查看、定时推送、香风智乃主题图片，并提供 LLM 工具。"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._chino_banner = _asset_data_uri("chino_stationery_banner.webp")
        self._chino_coffee = _asset_data_uri("chino_coffee.webp")
        self._scheduler = None
        self._cache = {}  # key -> (timestamp, value)
        self._setup_scheduler()

    # ------------------------------------------------------------------ #
    # 缓存：同一天 TTL 内复用，减少重复请求 & 封面重复下载
    # ------------------------------------------------------------------ #
    def _cache_ttl(self):
        return self._to_int(self.config.get("cache_ttl", 900), 900)

    def _cache_get(self, key, ttl):
        if ttl <= 0:
            return None
        hit = self._cache.get(key)
        if hit and (time.time() - hit[0]) < ttl:
            return hit[1]
        return None

    def _cache_set(self, key, val):
        self._cache[key] = (time.time(), val)
        if len(self._cache) > 200:  # 轻量清理：丢弃 24h 前的旧条目
            cutoff = time.time() - 86400
            self._cache = {k: v for k, v in self._cache.items() if v[0] >= cutoff}

    def _cache_clear(self):
        n = len(self._cache)
        self._cache = {}
        return n

    # ------------------------------------------------------------------ #
    # 定时推送
    # ------------------------------------------------------------------ #
    def _setup_scheduler(self):
        if not self.config.get("push_enabled", False):
            return
        if AsyncIOScheduler is None or CronTrigger is None:
            logger.warning("[dailyreport] 未安装 apscheduler，定时推送不可用（pip install apscheduler）。")
            return
        hh, mm = 8, 0
        try:
            t = str(self.config.get("push_time", "08:00") or "08:00").strip()
            hh, mm = int(t.split(":")[0]), int(t.split(":")[1])
        except Exception:  # noqa: BLE001
            hh, mm = 8, 0
        try:
            self._scheduler = AsyncIOScheduler()
            self._scheduler.add_job(self._push_job, CronTrigger(hour=hh, minute=mm), misfire_grace_time=300)
            self._scheduler.start()
            logger.info(f"[dailyreport] 定时推送已启用：每天 {hh:02d}:{mm:02d}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[dailyreport] 启动定时任务失败：{e}")

    def _img_chain(self, src):
        """按图片来源构建消息链：http(s) URL 用 fromURL，本地路径用 file_image。
        （html_render 走远程/本地文转图服务时返回的是 http URL，不能当文件路径。）"""
        s = str(src)
        if s.startswith("http://") or s.startswith("https://"):
            mc = MessageChain()
            try:
                return mc.url_image(s)
            except AttributeError:
                mc.chain.append(Comp.Image.fromURL(s))
                return mc
        return MessageChain().file_image(s)

    async def _push_job(self):
        targets = self._target_list()
        if not targets:
            return
        try:
            data = await self._gather()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[dailyreport] 定时构建日报失败：{e}")
            return
        img = await self._render_themed(data)
        text = self._report_text(data)
        images = self._news_images(data)
        for umo in targets:
            try:
                if img:
                    await self.context.send_message(umo, self._img_chain(img))
                else:
                    await self.context.send_message(umo, MessageChain().message(text))
                    for u in images:
                        try:
                            await self.context.send_message(umo, self._img_chain(u))
                        except Exception:  # noqa: BLE001
                            pass
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[dailyreport] 推送到 {umo} 失败：{e}")

    def _target_list(self):
        raw = self.config.get("push_targets", []) or []
        out = []
        if isinstance(raw, (list, tuple, set)):
            for x in raw:
                s = str(x).strip()
                if s and s not in out:
                    out.append(s)
        elif isinstance(raw, str):
            for s in raw.replace("，", " ").replace(",", " ").split():
                if s.strip() and s.strip() not in out:
                    out.append(s.strip())
        return out

    # ------------------------------------------------------------------ #
    # 指令
    # ------------------------------------------------------------------ #
    @filter.command("日报", alias={"dailyreport", "今日日报", "每日日报"})
    async def daily_report(self, event: AstrMessageEvent):
        """发送今日日报（日历 / 新闻 / 番剧 / 热榜），渲染为香风智乃主题图片。"""
        if self.config.get("show_progress", True):
            yield event.plain_result("📡 智乃正在整理今日日报…")
        async for r in self._send_report(event):
            yield r

    @filter.command("每日新闻", alias={"dailynews", "今日新闻", "60秒", "60s"})
    async def cmd_news(self, event: AstrMessageEvent):
        """今日新闻（60 秒读懂世界）。"""
        nd = await self._news_data()
        text = self._news_text(nd)
        imgs = [nd["image"]] if (nd.get("image") and self.config.get("news_with_image", True)) else []
        yield event.chain_result([Comp.Plain(text)] + [Comp.Image.fromURL(u) for u in imgs])

    @filter.command("今日番剧", alias={"bangumi", "番剧", "新番"})
    async def cmd_anime(self, event: AstrMessageEvent):
        """今天更新的番剧（数据来自 Bangumi），渲染成功只发图片卡片。"""
        ad = await self._anime_data()
        img = await self._render_anime_card(ad)
        if img:
            yield event.image_result(img)
            return
        yield event.plain_result(self._anime_text(ad))

    @filter.command("热榜", alias={"今日热榜", "hotlist", "热搜"})
    async def cmd_hotlist(self, event: AstrMessageEvent):
        """各站热榜。可指定站点，如 /热榜 weibo"""
        arg = self._strip(event.message_str, {"热榜", "今日热榜", "hotlist", "热搜"})
        names = [arg.strip()] if arg.strip() else self._hotlist_sources()
        blocks = [self._hotlist_text(await self._hotlist_data(n)) for n in names]
        yield event.plain_result("\n\n".join(b for b in blocks if b) or "没有获取到热榜数据。")

    @filter.command("日历", alias={"calendar", "黄历", "今日日历", "今日黄历"})
    async def cmd_calendar(self, event: AstrMessageEvent):
        """今日日历 / 农历 / 黄历。"""
        yield event.plain_result(self._calendar_text(self._calendar_data()))

    @filter.command("日报帮助", alias={"日报菜单", "dailyhelp"})
    async def cmd_help(self, event: AstrMessageEvent):
        """显示 Rabbit House 主题帮助菜单卡片。"""
        img = await self._render_help_card()
        if img:
            yield event.image_result(img)
            return
        yield event.plain_result(self._help_text())

    @filter.command("日报订阅", alias={"dailysub", "订阅日报"})
    async def cmd_sub(self, event: AstrMessageEvent):
        """（管理员）把当前会话加入每日推送名单。"""
        if not self._is_admin(event):
            yield event.plain_result("只有管理员能设置日报订阅。")
            return
        umo = event.unified_msg_origin
        targets = self._target_list()
        if umo in targets:
            yield event.plain_result("当前会话已经在每日日报推送名单里了。")
            return
        targets.append(umo)
        self.config["push_targets"] = targets
        self._save()
        note = ""
        if not self.config.get("push_enabled", False):
            note = ("\n注意：定时推送总开关 push_enabled 当前是关的，"
                    "需到配置里打开、并重载插件后才会真正按时发送。")
        yield event.plain_result(f"✅ 已把当前会话加入每日日报推送名单（共 {len(targets)} 个）。" + note)

    @filter.command("日报退订", alias={"dailyunsub", "取消订阅日报"})
    async def cmd_unsub(self, event: AstrMessageEvent):
        """（管理员）把当前会话移出每日推送名单。"""
        if not self._is_admin(event):
            yield event.plain_result("只有管理员能设置日报订阅。")
            return
        umo = event.unified_msg_origin
        targets = self._target_list()
        if umo not in targets:
            yield event.plain_result("当前会话不在推送名单里。")
            return
        targets = [t for t in targets if t != umo]
        self.config["push_targets"] = targets
        self._save()
        yield event.plain_result(f"✅ 已把当前会话移出推送名单（剩 {len(targets)} 个）。")

    @filter.command("日报状态", alias={"日报订阅状态", "dailystatus", "推送状态"})
    async def cmd_status(self, event: AstrMessageEvent):
        """查看定时推送状态：是否启用、几点发、下次触发时间、订阅会话。"""
        enabled = self.config.get("push_enabled", False)
        ptime = str(self.config.get("push_time", "08:00") or "08:00").strip()
        theme = self._resolve_theme()
        targets = self._target_list()
        umo = event.unified_msg_origin
        subbed = umo in targets
        lines = ["📋 日报定时推送状态", ""]
        lines.append(f"总开关 push_enabled：{'✅ 已开启' if enabled else '❌ 已关闭'}")
        lines.append(f"推送时间：每天 {ptime}")
        lines.append(f"当前主题：{theme}")
        if AsyncIOScheduler is None or CronTrigger is None:
            lines.append("调度器：❌ 未安装 apscheduler（pip install apscheduler）")
        elif not enabled:
            lines.append("调度器：未启动（总开关关闭）")
        elif self._scheduler is not None and getattr(self._scheduler, "running", False):
            lines.append("调度器：✅ 运行中")
            try:
                jobs = self._scheduler.get_jobs()
                if jobs and jobs[0].next_run_time:
                    lines.append(f"下次触发：{jobs[0].next_run_time.strftime('%Y-%m-%d %H:%M:%S')}")
            except Exception:  # noqa: BLE001
                pass
        else:
            lines.append("调度器：⚠️ 未运行（改了 push_enabled 后需重载插件）")
        lines.append(f"订阅会话数：{len(targets)}")
        lines.append(f"本会话已订阅：{'✅ 是' if subbed else '❌ 否'}")
        if enabled and not targets:
            lines.append("")
            lines.append("⚠️ 已开启但没有任何订阅会话，到点不会发！请在目标群发 /日报订阅。")
        elif not subbed:
            lines.append("（想让本群收到日报：发 /日报订阅）")
        lines.append("")
        lines.append("👉 立即验证推送链路：管理员发 /日报测试")
        yield event.plain_result("\n".join(lines))

    @filter.command("日报测试", alias={"测试日报", "dailytest", "日报推送测试"})
    async def cmd_testpush(self, event: AstrMessageEvent):
        """（管理员）立即执行一次推送（与定时完全相同的逻辑），用于验证链路是否通。"""
        if not self._is_admin(event):
            yield event.plain_result("只有管理员能测试推送。")
            return
        targets = self._target_list()
        if not targets:
            yield event.plain_result("当前没有任何订阅会话，无法测试。请先在目标群发 /日报订阅，再来测试。")
            return
        yield event.plain_result(f"📡 正在向 {len(targets)} 个已订阅会话执行一次推送（和每天定时用的是同一套逻辑）…")
        try:
            await self._push_job()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[dailyreport] 手动测试推送出错：{e!r}")
            yield event.plain_result(f"推送执行出错：{e}")
            return
        yield event.plain_result("✅ 推送已执行。若已订阅的会话收到了日报，就说明定时推送链路正常，到点会自动发。")

    @filter.command("日报清缓存", alias={"日报刷新", "dailyreportflush"})
    async def cmd_flush(self, event: AstrMessageEvent):
        """（管理员）清空缓存，下次请求强制实时拉取。"""
        if not self._is_admin(event):
            yield event.plain_result("只有管理员能清缓存。")
            return
        n = self._cache_clear()
        yield event.plain_result(f"🧹 已清空日报缓存（{n} 项）。下次 /日报 会实时拉取。")

    # ------------------------------------------------------------------ #
    # LLM 函数工具
    # ------------------------------------------------------------------ #
    @filter.llm_tool(name="get_daily_report")
    async def daily_report_tool(self, event: AstrMessageEvent, section: str = "") -> MessageEventResult:
        '''获取今日日报信息，包含每日新闻、今天更新的番剧、各大平台热榜、以及日历农历。当用户问“今天有什么新闻 / 番剧 / 热搜”“看看日报”“今天黄历 / 农历”等与每日资讯相关的问题时调用。

        Args:
            section(string): 可选，想看的板块：news（新闻）、anime（番剧）、hotlist（热榜）、calendar（日历）。留空表示全部。
        '''
        s = (section or "").strip().lower()
        if s in ("news", "新闻"):
            nd = await self._news_data()
            imgs = [nd["image"]] if (nd.get("image") and self.config.get("news_with_image", True)) else []
            yield event.chain_result([Comp.Plain(self._news_text(nd))] + [Comp.Image.fromURL(u) for u in imgs])
            return
        if s in ("anime", "番剧", "bangumi"):
            yield event.plain_result(self._anime_text(await self._anime_data()))
            return
        if s in ("hotlist", "热榜", "热搜"):
            blocks = [self._hotlist_text(await self._hotlist_data(n)) for n in self._hotlist_sources()]
            yield event.plain_result("\n\n".join(b for b in blocks if b) or "没有获取到热榜数据。")
            return
        if s in ("calendar", "日历", "黄历"):
            yield event.plain_result(self._calendar_text(self._calendar_data()))
            return
        async for r in self._send_report(event):
            yield r

    # ------------------------------------------------------------------ #
    # 汇总 + 渲染
    # ------------------------------------------------------------------ #
    async def _gather(self):
        """抓取所有板块，返回结构化数据（文字版与主题图片共用，避免重复请求）。"""
        now = datetime.datetime.now()
        d = now.date()
        data = {
            "title": str(self.config.get("report_title", "") or "智乃的今日日报").strip(),
            "date_main": f"{d.year}年{d.month:02d}月{d.day:02d}日",
            "weekday": WEEK_CN[d.weekday()],
            "calendar": None, "news": None, "anime": None, "hotlists": [],
        }
        if self.config.get("show_calendar", True):
            data["calendar"] = self._calendar_data()
        if self.config.get("show_news", True):
            data["news"] = await self._news_data()
        if self.config.get("show_anime", True):
            data["anime"] = await self._anime_data()
        if self.config.get("show_hotlists", True):
            for n in self._hotlist_sources():
                data["hotlists"].append(await self._hotlist_data(n))
        return data

    async def _send_report(self, event: AstrMessageEvent):
        data = await self._gather()
        img = await self._render_themed(data)
        if img:
            yield event.image_result(img)
            return
        text = self._report_text(data)
        images = self._news_images(data)
        yield event.chain_result([Comp.Plain(text)] + [Comp.Image.fromURL(u) for u in images])

    def _render_scale(self):
        try:
            scale = float(self.config.get("render_scale", 2.0) or 2.0)
        except Exception:  # noqa: BLE001
            scale = 2.0
        return min(4.0, max(1.0, scale))

    def _resolve_theme(self):
        """把 report_theme 解析为实际主题；auto = 白天 chino / 夜间 night。"""
        theme = str(self.config.get("report_theme", "chino") or "chino").strip().lower()
        if theme in ("auto", "自动", "自动切换", "time", "按时间", "daynight"):
            h = datetime.datetime.now().hour
            ns = self._to_int(self.config.get("night_start_hour", 18), 18)
            ne = self._to_int(self.config.get("night_end_hour", 6), 6)
            if ns <= ne:
                is_night = ns <= h < ne
            else:  # 跨午夜（默认 18→6）
                is_night = h >= ns or h < ne
            theme = "night" if is_night else "chino"
        return theme

    @staticmethod
    def _is_night(theme):
        return theme in ("night", "dark", "星夜", "深夜", "midnight", "黑夜", "夜间")

    async def _render_themed(self, data):
        """优先渲染主题图片；失败时退回文字转图；再失败返回 None（由调用方发文本）。"""
        if not self.config.get("report_as_image", True):
            return None
        theme = self._resolve_theme()
        if theme not in ("plain", "markdown", "none"):
            try:
                # 渲染精度：用 CSS zoom 放大物理像素（更清晰），>1 时卡片会撑满画布、消掉两侧留白
                scale = self._render_scale()
                rdata = dict(data)
                rdata["scale"] = scale
                if theme in ("murasame", "丛雨", "从雨"):
                    tmpl = MURASAME_TMPL
                    rdata.update(self._murasame_extras(data))
                elif self._is_night(theme):
                    tmpl = NIGHT_TMPL
                    rdata.update(self._rabbit_extras(data))
                else:
                    tmpl = CHINO_TMPL
                    rdata.update(self._rabbit_extras(data))
                    rdata["chino_banner"] = self._chino_banner
                    rdata["chino_coffee"] = self._chino_coffee
                rdata = await self._embed_anime_covers(rdata)
                url = await self.html_render(tmpl, rdata)
                if url:
                    return url
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[dailyreport] 主题渲染失败，改用文字转图：{e}")
        try:
            return await self.text_to_image(self._md_headers(self._report_text(data)))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[dailyreport] 文字转图失败，改用文本：{e}")
            return None

    async def _render_anime_card(self, ad):
        """把今日番剧渲染成一张图文卡片（封面+名称），主题随 report_theme（auto 时按时间）。失败返回 None。"""
        if not self.config.get("anime_image", True):
            return None
        theme = self._resolve_theme()
        if theme in ("plain", "markdown", "none"):
            return None
        try:
            rdata = {"anime": ad, "scale": self._render_scale(), "dark": self._is_night(theme)}
            rdata = await self._embed_anime_covers(rdata)
            return await self.html_render(ANIME_TMPL, rdata)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[dailyreport] 番剧卡片渲染失败，改用文本：{e}")
            return None

    @staticmethod
    def _help_groups():
        """帮助菜单的命令分组（卡片与文字兜底共用）。"""
        return [
            {"title": "📰 查看内容", "items": [
                {"cmd": "/日报", "desc": "完整今日日报卡片（新闻+番剧+热榜+运势+咖啡）", "admin": False},
                {"cmd": "/每日新闻", "desc": "每日 60 秒早报", "admin": False},
                {"cmd": "/今日番剧", "desc": "今天更新的番剧 · 封面图卡", "admin": False},
                {"cmd": "/热榜", "desc": "微博/知乎等各站热榜聚合", "admin": False},
                {"cmd": "/日历", "desc": "今日黄历 · 宜忌 · 运势", "admin": False},
            ]},
            {"title": "🔔 订阅与定时推送", "items": [
                {"cmd": "/日报订阅", "desc": "把本群加入每日定时推送名单", "admin": True},
                {"cmd": "/日报退订", "desc": "把本群移出定时推送名单", "admin": True},
                {"cmd": "/日报状态", "desc": "查看推送是否开启、下次触发时间、订阅情况", "admin": False},
                {"cmd": "/日报测试", "desc": "立即测试一次推送，验证链路是否正常", "admin": True},
            ]},
            {"title": "🔧 维护", "items": [
                {"cmd": "/日报清缓存", "desc": "清空缓存，下次强制实时拉取", "admin": True},
                {"cmd": "/日报帮助", "desc": "显示这张菜单（别名 /日报菜单）", "admin": False},
            ]},
        ]

    def _help_text(self):
        lines = ["🐰 智乃的 Rabbit House 菜单"]
        for g in self._help_groups():
            lines.append("")
            lines.append(g["title"])
            for c in g["items"]:
                tag = "（管理员）" if c["admin"] else ""
                lines.append(f"  {c['cmd']} - {c['desc']}{tag}")
        lines.append("")
        lines.append("别名也能用，例如 /番剧=/今日番剧 · /黄历=/日历")
        return "\n".join(lines)

    async def _render_help_card(self):
        """渲染 Rabbit House 主题帮助卡片，主题随 report_theme（auto 时按时间）。失败返回 None。"""
        theme = self._resolve_theme()
        if theme in ("plain", "markdown", "none"):
            return None
        try:
            ex = self._rabbit_extras({})
            rdata = {"groups": self._help_groups(), "scale": self._render_scale(),
                     "dark": self._is_night(theme), "greet": ex.get("greet", "")}
            return await self.html_render(HELP_TMPL, rdata)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[dailyreport] 帮助卡片渲染失败，改用文本：{e}")
            return None

    def _rabbit_extras(self, data):
        """为 Rabbit House 主题生成：问候语、幸运色/数字、智乃建议、今日咖啡、今日一句、热议卡片。
        除热议外均按当天日期确定（同一天稳定不变）。"""
        now = datetime.datetime.now()
        doy = now.timetuple().tm_yday
        h = now.hour
        if h < 11:
            greet = "Good Morning。今天也要努力哦。"
        elif h < 17:
            greet = "Good Afternoon。休息一下，喝杯咖啡吧。"
        else:
            greet = "Good Evening。今天也辛苦了哦。"
        colors = [("浅蓝色", "#9cc3ef"), ("樱花粉", "#f3b6cb"), ("薄荷绿", "#a8e0c2"), ("奶油黄", "#f2dca0"),
                  ("薰衣草紫", "#c3b3e8"), ("天空蓝", "#8fb8e8"), ("蜜桃色", "#f6c6a8"), ("珊瑚红", "#f0a0a0"),
                  ("抹茶绿", "#bcd29a"), ("雾霾蓝", "#9fb0d0")]
        cname, chex = colors[doy % len(colors)]
        lucky_num = (doy % 9) + 1
        advices = [
            "保持冷静，会有好运。今天适合喝一杯咖啡再开始工作哦。",
            "别着急，慢慢来，一步一步总会走到的。",
            "今天也要好好吃饭、好好休息哦。",
            "遇到困难时深呼吸，你已经做得很好了。",
            "把握当下的小确幸，今天会是温柔的一天。",
            "累了就停下来歇歇，Rabbit House 永远等你。",
            "对自己温柔一点，你值得被好好对待。",
            "微笑面对今天，好运会悄悄靠近的。",
        ]
        advice = advices[doy % len(advices)]
        coffees = [
            ("卡布奇诺", "Cappuccino", 5, "工作 · 学习 · 阅读", "一杯好的咖啡，能让心平静下来。"),
            ("拿铁", "Caffe Latte", 5, "放松 · 聊天 · 发呆", "温润顺滑，像被轻轻拥抱。"),
            ("美式", "Americano", 4, "提神 · 专注 · 赶工", "清醒一点，今天也加油。"),
            ("摩卡", "Mocha", 5, "治愈 · 甜点 · 小憩", "巧克力的甜，藏着小小的幸福。"),
            ("焦糖玛奇朵", "Caramel Macchiato", 5, "奖励自己 · 约会", "甜甜的，像今天的好心情。"),
            ("馥芮白", "Flat White", 4, "清晨 · 通勤 · 思考", "浓淡刚好，开启新的一天。"),
            ("抹茶拿铁", "Matcha Latte", 4, "放松 · 午后 · 阅读", "微苦回甘，静下心来。"),
            ("手冲单品", "Pour Over", 5, "品味 · 独处 · 慢生活", "慢慢冲，慢慢品，慢慢生活。"),
            ("热可可", "Hot Cocoa", 4, "取暖 · 睡前 · 治愈", "暖暖的，把烦恼都融化掉。"),
            ("浓缩", "Espresso", 4, "快速 · 醒神 · 干练", "一口浓缩，满血复活。"),
        ]
        cf = coffees[doy % len(coffees)]
        coffee = {"name_cn": cf[0], "name_en": cf[1], "stars": cf[2], "suitable": cf[3], "tagline": cf[4]}
        quotes = ["请慢用。", "欢迎光临 Rabbit House。", "今天也请多关照。",
                  "要来杯咖啡吗？", "愿你今天温柔又顺利。", "慢慢来，比较快。"]
        quote = quotes[doy % len(quotes)]
        hot_cards = []
        for hl in (data.get("hotlists") or []):
            for t in (hl.get("items") or []):
                if t and t not in hot_cards:
                    hot_cards.append(t)
        hot_cards = hot_cards[:6]
        return {"greet": greet, "lucky_color": cname, "lucky_hex": chex, "lucky_num": lucky_num,
                "advice": advice, "coffee": coffee, "quote": quote, "hot_cards": hot_cards}

    def _murasame_extras(self, data):
        """丛雨主题专用：和风幸运色、吾辈口吻建议、今日和菓子、今日一句、热议卡片。
        除热议外均按当天日期确定（同一天稳定不变）。"""
        now = datetime.datetime.now()
        doy = now.timetuple().tm_yday
        h = now.hour
        if h < 11:
            greet = "早安，コン。今日也是精神的一天也。"
        elif h < 17:
            greet = "午后好。吾辈陪汝喝杯茶如何？"
        else:
            greet = "晚上好。今日也辛苦了，コン。"
        colors = [("樱花粉", "#f4b8c8"), ("若草绿", "#a8cf8e"), ("山吹黄", "#f0b23e"), ("藤紫", "#b28fce"),
                  ("浅葱蓝", "#7db8c9"), ("桃色", "#f6a8b8"), ("珊瑚朱", "#ef8a6f"), ("抹茶绿", "#9fbb73"),
                  ("瑠璃蓝", "#5f7fc0"), ("朱红", "#d0604f")]
        cname, chex = colors[doy % len(colors)]
        lucky_num = (doy % 9) + 1
        advices = [
            "保持平常心，好运自会降临。吾辈为汝祈福了，コン。",
            "莫要焦躁，一步一步稳稳走便是。",
            "今日也要好好吃饭，吾辈盯着汝呢。",
            "遇到难事先深呼吸，汝已经做得很好了。",
            "小小的幸福也值得珍惜也。",
            "累了就到神社歇歇脚，吾辈一直都在。",
            "对自己温柔些，汝值得被善待。",
            "带着笑容出门，福气会跟着来的，コン。",
        ]
        advice = advices[doy % len(advices)]
        wagashis = [
            ("三色团子", "花見だんご", 5, "赏花 · 散步 · 下午茶", "一串三色，春意满满。"),
            ("最中", "もなか", 5, "配茶 · 小憩 · 追番", "脆皮裹豆沙，吾辈的最爱，コン！"),
            ("铜锣烧", "どら焼き", 4, "加餐 · 出游 · 补充元气", "两片松软，夹住甜甜的幸福。"),
            ("樱饼", "桜もち", 5, "春日 · 赏樱 · 拍照", "樱叶清香，一口就是春天。"),
            ("大福", "だいふく", 4, "饭后 · 治愈 · 犒劳自己", "软软糯糯，把烦恼都包起来。"),
            ("羊羹", "ようかん", 4, "配抹茶 · 静心 · 读书", "甜而不腻，细水长流。"),
            ("御手洗团子", "みたらし団子", 5, "散步 · 庙会 · 小饿", "咸甜酱香，越嚼越香也。"),
            ("鲷鱼烧", "たい焼き", 5, "冬日 · 暖手 · 边走边吃", "从头到尾都是满满的馅。"),
            ("金平糖", "こんぺいとう", 4, "小礼物 · 收藏 · 分享", "星星一样的小甜蜜。"),
            ("水馒头", "水まんじゅう", 4, "夏日 · 消暑 · 清爽", "透亮弹嫩，一口清凉。"),
        ]
        wg = wagashis[doy % len(wagashis)]
        wagashi = {"name_cn": wg[0], "name_en": wg[1], "stars": wg[2], "suitable": wg[3], "tagline": wg[4]}
        quotes = ["コン！", "吾辈可是很厉害的也", "今日也请多指教，コン", "汝今日也要加油哦",
                  "有吾辈在，安心便是", "慢慢来，福气不会跑"]
        quote = quotes[doy % len(quotes)]
        hot_cards = []
        for hl in (data.get("hotlists") or []):
            for t in (hl.get("items") or []):
                if t and t not in hot_cards:
                    hot_cards.append(t)
        hot_cards = hot_cards[:6]
        return {"greet": greet, "lucky_color": cname, "lucky_hex": chex, "lucky_num": lucky_num,
                "advice": advice, "wagashi": wagashi, "quote": quote, "hot_cards": hot_cards}

    async def _embed_anime_covers(self, data):
        """把番剧封面下载成 base64 内嵌进数据（避免渲染用的 Chromium 直连 bgm CDN 裂图）。"""
        if not self.config.get("anime_covers", True):
            return data
        anime = data.get("anime") or {}
        items = anime.get("items") or []
        if not items:
            return data
        n = self._to_int(self.config.get("anime_cover_max", 8), 8)
        sub = items[:n] if n > 0 else items
        covers = []
        for it in sub:
            name = it.get("name", "") if isinstance(it, dict) else str(it)
            url = it.get("cover", "") if isinstance(it, dict) else ""
            img = await self._fetch_image_b64(url) if url else ""
            covers.append({"name": name, "img": img})
        ok = sum(1 for c in covers if c["img"])
        logger.info(f"[dailyreport][anime] 封面内嵌 {ok}/{len(covers)} 成功")
        na = dict(anime)
        na["covers"] = covers
        data = dict(data)
        data["anime"] = na
        return data

    async def _fetch_image_b64(self, url):
        """下载图片并转为 data URI；失败返回空串。走配置的 proxy，按 URL 缓存。"""
        if not url:
            return ""
        ttl = self._cache_ttl()
        ckey = "cover:" + url
        if ttl > 0:
            cached = self._cache_get(ckey, max(ttl, 21600))  # 封面内容不变，至少缓存 6h
            if cached is not None:
                return cached
        timeout = aiohttp.ClientTimeout(total=self._to_int(self.config.get("timeout", 20), 20))
        proxy = str(self.config.get("proxy", "") or "").strip() or None
        try:
            async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as s:
                async with s.get(url, headers={"User-Agent": BROWSER_UA}, proxy=proxy) as r:
                    if r.status != 200:
                        return ""
                    raw = await r.read()
                    if not raw or len(raw) > 4_000_000:
                        return ""
                    ct = r.headers.get("Content-Type", "") or "image/jpeg"
                    if "image" not in ct:
                        ct = "image/jpeg"
                    out = f"data:{ct};base64," + base64.b64encode(raw).decode("ascii")
                    if ttl > 0:
                        self._cache_set(ckey, out)
                    return out
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[dailyreport][anime] 封面下载失败 {url}: {e!r}")
            return ""

    def _news_images(self, data):
        nd = data.get("news") or {}
        if nd.get("image") and self.config.get("news_with_image", True):
            return [nd["image"]]
        return []

    def _report_text(self, data):
        parts = [data.get("title", "今日日报")]
        sub = f"{data.get('date_main','')} {data.get('weekday','')}".strip()
        if sub:
            parts[0] = parts[0] + "\n" + sub
        if data.get("calendar"):
            parts.append(self._calendar_text(data["calendar"]))
        if data.get("news"):
            parts.append(self._news_text(data["news"]))
        if data.get("anime"):
            parts.append(self._anime_text(data["anime"]))
        for hl in data.get("hotlists", []):
            parts.append(self._hotlist_text(hl))
        return "\n\n".join(p for p in parts if p)

    @staticmethod
    def _md_headers(text):
        lines = text.split("\n")
        out = []
        for i, ln in enumerate(lines):
            if i == 0 and ln.strip():
                out.append("# " + ln.strip())
                continue
            m = re.match(r"^([^\w\s【】]*)\s*【(.+?)】\s*$", ln)
            if m:
                emoji = m.group(1).strip()
                title = m.group(2).strip()
                out.append(f"## {emoji + ' ' if emoji else ''}{title}")
            else:
                out.append(ln)
        return "\n".join(out)

    # ------------------------------------------------------------------ #
    # 板块：日历（本地，不依赖网络）
    # ------------------------------------------------------------------ #
    def _calendar_data(self):
        now = datetime.datetime.now()
        d = now.date()
        cd = {"date_main": f"{d.year}年{d.month:02d}月{d.day:02d}日", "weekday": WEEK_CN[d.weekday()],
              "lunar": "", "ganzhi": "", "term": "", "good": "", "bad": "",
              "officer": "", "peng": "", "doy": d.timetuple().tm_yday}
        if cnlunar is not None:
            try:
                a = cnlunar.Lunar(now, godType="8char")
                cd["lunar"] = ("农历 %s%s" % (getattr(a, "lunarMonthCn", ""), getattr(a, "lunarDayCn", ""))).strip()
                cd["ganzhi"] = ("%s[%s]年" % (getattr(a, "year8Char", ""), getattr(a, "chineseYearZodiac", ""))).strip()
                term = getattr(a, "todaySolarTerms", "") or ""
                cd["term"] = "" if term in ("无", "None") else term

                def _f(x):
                    if isinstance(x, (list, tuple)):
                        return "、".join(str(i) for i in x[:6])
                    return str(x) if x else ""

                cd["good"] = _f(getattr(a, "goodThing", None))
                cd["bad"] = _f(getattr(a, "badThing", None))
                # 建/破/平/收/闭等日本就「诸事不宜」，补充值神(建除十二神)+彭祖百忌让黄历有料、也解释为何诸事不宜
                try:
                    off = getattr(a, "get_today12DayOfficer", None)
                    if callable(off):
                        o = off()
                        cd["officer"] = " ".join(str(i) for i in o) if isinstance(o, (list, tuple)) else str(o)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    peng = getattr(a, "get_pengTaboo", None)
                    if callable(peng):
                        try:
                            txt = peng(long=4, delimit=" ")
                        except TypeError:
                            txt = peng()
                        cd["peng"] = str(txt).replace("\n", " ").replace("<br>", " ").strip()
                except Exception:  # noqa: BLE001
                    pass
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[dailyreport] 农历计算失败：{e}")
        return cd

    def _calendar_text(self, cd):
        lines = [f"📅 {cd.get('date_main','')} {cd.get('weekday','')}".rstrip()]
        if cd.get("lunar") or cd.get("ganzhi"):
            l2 = f"{cd.get('lunar','')}  {cd.get('ganzhi','')}".strip()
            if cd.get("term"):
                l2 += f"  节气：{cd['term']}"
            lines.append(l2)
            if cd.get("good"):
                lines.append("宜：" + cd["good"])
            if cd.get("bad"):
                lines.append("忌：" + cd["bad"])
            if cd.get("officer"):
                lines.append("值神：" + cd["officer"])
            if cd.get("peng"):
                lines.append("彭祖百忌：" + cd["peng"])
        else:
            lines.append(f"今年第 {cd.get('doy')} 天（安装 cnlunar 可显示农历 / 黄历）")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # 板块：每日新闻（60 秒）
    # ------------------------------------------------------------------ #
    async def _news_data(self):
        mode = str(self.config.get("news_mode", "json") or "json").strip().lower()
        api = str(self.config.get("news_api", "") or "https://v3.alapi.cn/api/zaobao").strip()
        alapi = self._is_alapi(api)
        if mode == "image":
            img = self._alapi_url(api, {"format": "image"}) if alapi else api
            return {"items": [], "tip": "", "image": img, "error": None}
        ttl = self._cache_ttl()
        ckey = "news:" + datetime.date.today().isoformat()
        cached = self._cache_get(ckey, ttl)
        if cached is not None:
            return cached
        url = self._alapi_url(api) if alapi else api
        try:
            data = await self._get_json(url)
        except Exception as e:  # noqa: BLE001
            return {"items": [], "tip": "", "image": "", "error": str(e)}
        err = self._alapi_error(data) if alapi else None
        if err:
            return {"items": [], "tip": "", "image": "", "error": err}
        d = data.get("data", data) if isinstance(data, dict) else {}
        if not isinstance(d, dict):
            d = {}
        news = d.get("news") or []
        items = []
        cap = self._to_int(self.config.get("news_max", 0), 0)
        for it in news:
            if isinstance(it, str):
                t = it
            elif isinstance(it, dict):
                t = it.get("title") or it.get("content") or ""
            else:
                t = str(it)
            if t:
                items.append(html.unescape(str(t)))
        if cap > 0:
            items = items[:cap]
        tip = d.get("tip") or d.get("weiyu") or d.get("weiyou") or ""
        image = (d.get("image") or d.get("cover") or d.get("imageUrl")
                 or d.get("img") or d.get("link_img") or d.get("head_image") or "")
        result = {"items": items, "tip": html.unescape(str(tip)) if tip else "", "image": image, "error": None}
        if items:
            self._cache_set(ckey, result)
        return result

    def _news_text(self, nd):
        if nd.get("error"):
            return f"【今日新闻】\n（获取失败：{nd['error']}）"
        lines = ["【今日新闻】"]
        for i, t in enumerate(nd.get("items", []), 1):
            lines.append(f"{i}. {t}")
        if nd.get("tip"):
            lines.append(f"💡 {nd['tip']}")
        if len(lines) == 1:
            lines.append("（暂无内容，可能需要在配置里调整 news_api）")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # 板块：今日番剧（Bangumi）
    # ------------------------------------------------------------------ #
    async def _anime_data(self):
        api = str(self.config.get("bangumi_api", "") or "https://api.bgm.tv/calendar").strip()
        wd_cn = WEEK_CN[datetime.datetime.now().weekday()]
        ttl = self._cache_ttl()
        ckey = "anime:" + datetime.date.today().isoformat()
        cached = self._cache_get(ckey, ttl)
        if cached is not None:
            return cached
        is_bgm = "bgm.tv" in api or "bangumi" in api
        ua = str(self.config.get("bangumi_ua", "") or "").strip() or BGM_UA
        token = str(self.config.get("bangumi_token", "") or "").strip()
        # 多重尝试：①合规UA(+token)；②浏览器UA、无token（参考真寻日报，公开接口不需要token，
        #   且能绕过无效token导致的401）。取第一个返回「列表」的结果。
        attempts = []
        if is_bgm:
            attempts.append((ua, {"Authorization": f"Bearer {token}"} if token else None, "primary"))
            attempts.append((BROWSER_UA, None, "browser-no-token"))
        else:
            attempts.append((None, None, "primary"))
        arr = None
        last_err = None
        last_nonlist = None
        for u, h, tag in attempts:
            try:
                r = await self._get_json(api, ua=u, extra_headers=h)
            except Exception as e:  # noqa: BLE001
                last_err = e
                logger.warning(f"[dailyreport][anime] 尝试[{tag}]请求失败: {e!r}")
                continue
            if isinstance(r, list):
                arr = r
                logger.info(f"[dailyreport][anime] 尝试[{tag}]成功，list:{len(r)}")
                break
            if isinstance(r, dict):
                cand = r.get("data") or r.get("result") or r.get("calendar")
                if isinstance(cand, list):
                    arr = cand
                    logger.info(f"[dailyreport][anime] 尝试[{tag}]成功(包裹)，list:{len(cand)}")
                    break
            last_nonlist = r
            if isinstance(r, dict):
                logger.warning(f"[dailyreport][anime] 尝试[{tag}]返回非列表: dict keys={list(r.keys())[:6]} "
                               f"status={r.get('status')} title={r.get('title')!r} "
                               f"detail={str(r.get('detail'))[:150]!r}")
            else:
                logger.warning(f"[dailyreport][anime] 尝试[{tag}]返回非列表: {type(r).__name__}")
        if not isinstance(arr, list):
            nl = type(last_nonlist).__name__ if last_nonlist is not None else "None"
            if last_err and "Timeout" in type(last_err).__name__:
                emsg = "连接 Bangumi 超时（服务器可能无法直连 api.bgm.tv，需在配置里设置 proxy 代理）"
            elif isinstance(last_nonlist, dict) and (last_nonlist.get("status") or last_nonlist.get("title")):
                st = last_nonlist.get("status")
                ti = str(last_nonlist.get("title") or "").strip()
                de = str(last_nonlist.get("detail") or "").strip()[:80]
                emsg = f"Bangumi 接口报错 {st} {ti}".strip()
                if de:
                    emsg += f"（{de}）"
            elif last_err:
                emsg = str(last_err) or type(last_err).__name__
            else:
                emsg = f"接口未返回放送列表(返回了{nl})"
            dbgkeys = list(last_nonlist.keys()) if isinstance(last_nonlist, dict) else None
            return {"weekday": wd_cn, "items": [],
                    "error": emsg,
                    "debug": f"no-list last_err={last_err!r} nonlist={nl} keys={dbgkeys}"}
        now = datetime.datetime.now()
        today_en = WEEK_EN[now.weekday()]
        today_id = now.isoweekday()  # Mon=1..Sun=7，与 Bangumi weekday.id 一致
        items = []
        matched = False
        for wd in arr:
            if not isinstance(wd, dict):
                continue
            w = wd.get("weekday", {}) or {}
            en = str(w.get("en", "")).strip()[:3].title()
            try:
                wid = int(w.get("id"))
            except Exception:  # noqa: BLE001
                wid = None
            # 有的镜像把星期放在顶层 air_weekday / id
            if not w:
                try:
                    wid = int(wd.get("air_weekday", wd.get("id")))
                except Exception:  # noqa: BLE001
                    wid = None
            if en == today_en or (wid is not None and wid == today_id):
                matched = True
                for it in (wd.get("items") or wd.get("subjects") or []):
                    if isinstance(it, dict):
                        nm = it.get("name_cn") or it.get("name") or it.get("name_jp") or ""
                        if nm:
                            items.append({"name": html.unescape(str(nm)), "cover": self._anime_cover(it)})
                break
        # 兜底：若按「星期分组」没取到，可能接口直接返回了平铺的番剧列表，按 air_weekday 过滤今天
        if not items:
            for it in arr:
                if not isinstance(it, dict) or "weekday" in it:
                    continue
                try:
                    aw = int(it.get("air_weekday"))
                except Exception:  # noqa: BLE001
                    aw = None
                if aw is not None and aw == today_id:
                    nm = it.get("name_cn") or it.get("name") or it.get("name_jp") or ""
                    if nm:
                        matched = True
                        items.append({"name": html.unescape(str(nm)), "cover": self._anime_cover(it)})
        cap = self._to_int(self.config.get("anime_max", 0), 0)
        if cap > 0:
            items = items[:cap]
        names = [i["name"] for i in items]
        # 强制诊断：无论结果如何都给出原始结构，便于定位
        sample = ""
        if isinstance(arr, list) and arr and isinstance(arr[0], dict):
            sample = "k0=" + ",".join(list(arr[0].keys())[:6])
            w0 = arr[0].get("weekday")
            if isinstance(w0, dict):
                sample += f" wd0(en={w0.get('en')},id={w0.get('id')})"
        dbg = (f"arr={'list:' + str(len(arr)) if isinstance(arr, list) else type(arr).__name__} "
               f"matched={matched} today(en={today_en},id={today_id}) names={len(items)} {sample}").strip()
        # 写进日志，避免被人格 LLM 改写吞掉；同时给出取到的前几个番名
        logger.info(f"[dailyreport][anime] {dbg} | url={api} token={'yes' if token else 'no'} "
                    f"head={names[:3]}")
        # 区分“接口返回空”与“今天确实无更新”，便于排查
        err = None
        if not items:
            if not arr:
                err = "接口返回空列表（可能被限流/拦截，过会儿再试）"
            elif not matched:
                err = f"接口返回了{len(arr)}天数据，但没匹配到今天（{wd_cn}）"
            else:
                err = f"今天（{wd_cn}）放送列表为空"
        result = {"weekday": wd_cn, "items": items, "error": err, "debug": dbg}
        if items:
            self._cache_set(ckey, result)
        return result

    @staticmethod
    def _anime_cover(it):
        """从 Bangumi item 提取封面 URL（优先清晰度适中的）。"""
        imgs = it.get("images") or it.get("image") or {}
        if isinstance(imgs, dict):
            url = (imgs.get("common") or imgs.get("large") or imgs.get("medium")
                   or imgs.get("grid") or imgs.get("small") or "")
        elif isinstance(imgs, str):
            url = imgs
        else:
            url = ""
        if url.startswith("//"):
            url = "https:" + url
        return url

    def _anime_text(self, ad):
        wd = ad.get("weekday", "")
        head = f"【今日番剧】（{wd}）"
        if ad.get("error"):
            return f"{head}\n（获取失败：{ad['error']}）"
        if not ad.get("items"):
            return f"{head}\n今天没有查询到更新的番剧。"
        return f"{head}\n" + "\n".join(f"· {i['name']}" for i in ad["items"])

    # ------------------------------------------------------------------ #
    # 板块：热榜
    # ------------------------------------------------------------------ #
    async def _hotlist_data(self, entry):
        display, target = self._split_source(entry)
        if not target:
            return {"title": "", "items": [], "error": None}
        ttl = self._cache_ttl()
        ckey = f"hot:{datetime.date.today().isoformat()}:{entry}"
        cached = self._cache_get(ckey, ttl)
        if cached is not None:
            return cached
        base = str(self.config.get("hotlist_base", "") or "https://v3.alapi.cn/api/new/wbtop").strip()
        count = self._to_int(self.config.get("hotlist_count", 5), 5)
        num = max(1, count)
        # target 可为「站点名(type/slug)」或「完整 URL」——后者让每个榜单能用不同的 API
        if target.lower().startswith("http"):
            url = target
            alapi = self._is_alapi(url)
            if alapi:
                # ALAPI v3 按站点的端点(如 /api/new/wbtop)只需 token + num
                url = self._alapi_url(url, {"num": num})
            slug = None
        else:
            slug = target
            alapi = self._is_alapi(base)
            url = self._alapi_url(base, {"type": slug, "num": num}) if alapi else f"{base.rstrip('/')}/{slug}"
        cn_name = display or (HOTLIST_NAMES.get(slug.lower(), HOTLIST_NAMES.get(slug, slug)) if slug else "热榜")
        try:
            data = await self._get_json(url)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[dailyreport][hotlist] 「{cn_name}」请求失败 url={url} err={e!r}")
            return {"title": cn_name, "items": [], "error": str(e)}
        err = self._alapi_error(data) if alapi else None
        if err:
            logger.warning(f"[dailyreport][hotlist] 「{cn_name}」接口报错 url={url} -> {err}")
            return {"title": cn_name, "items": [], "error": err}
        if not isinstance(data, (dict, list)):
            return {"title": cn_name, "items": [], "error": "数据格式异常"}
        # 找出榜单数组：兼容 {data:[...]}、{data:{list/items/data:[...]}}、直接 [...]
        payload = data.get("data", data) if isinstance(data, dict) else data
        if isinstance(payload, list):
            arr = payload
        elif isinstance(payload, dict):
            arr = (payload.get("list") or payload.get("items")
                   or payload.get("data") or payload.get("stories")
                   or payload.get("top_stories") or payload.get("news")
                   or payload.get("subList") or [])
        else:
            arr = []
        if not isinstance(arr, list):
            arr = []
        # 榜单名：显式名称 > 响应里的 name/title > 中文映射
        rt = ""
        if isinstance(data, dict):
            rt = data.get("title") or data.get("name") or ""
            if not rt and isinstance(payload, dict):
                rt = payload.get("name") or payload.get("title") or ""
        title = display or rt or cn_name
        sub = (data.get("subtitle") if isinstance(data, dict) else "") or ""
        items = []
        for it in arr[:max(1, count)]:
            if isinstance(it, dict):
                t = (it.get("title") or it.get("name") or it.get("word")
                     or it.get("query") or it.get("hot_word") or it.get("keyword")
                     or it.get("desc") or "")
            else:
                t = str(it)
            if t:
                items.append(html.unescape(str(t)))
        full = f"{title}{(' ' + str(sub)) if sub else ''}"
        result = {"title": full, "items": items, "error": None}
        if items:
            self._cache_set(ckey, result)
        return result

    def _hotlist_text(self, hd):
        title = hd.get("title") or ""
        if not title and not hd.get("items") and not hd.get("error"):
            return ""
        head = f"🔥【{title}】"
        if hd.get("error"):
            return f"{head}\n（获取失败：{hd['error']}）"
        lines = [head]
        for i, t in enumerate(hd.get("items", []), 1):
            lines.append(f"{i}. {t}")
        if len(lines) == 1:
            lines.append("（暂无内容）")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # 工具方法
    # ------------------------------------------------------------------ #
    def _hotlist_sources(self):
        raw = self.config.get("hotlist_sources", None)
        if raw is None:
            return list(DEFAULT_HOTLISTS)
        out = []
        if isinstance(raw, (list, tuple)):
            for x in raw:
                s = str(x).strip()
                if s:
                    out.append(s)
        elif isinstance(raw, str):
            for s in raw.replace("，", " ").replace(",", " ").split():
                if s.strip():
                    out.append(s.strip())
        return out or list(DEFAULT_HOTLISTS)

    @staticmethod
    def _is_alapi(url):
        return "alapi" in (url or "").lower()

    def _alapi_url(self, base, extra=None):
        """给 ALAPI 接口拼上 token 和额外参数（GET 方式）。"""
        params = {}
        token = str(self.config.get("alapi_token", "") or "").strip()
        if token:
            params["token"] = token
        if extra:
            params.update(extra)
        if not params:
            return base
        sep = "&" if "?" in base else "?"
        return base + sep + urlencode(params)

    @staticmethod
    def _alapi_error(data):
        """ALAPI 成功为 code==200；非 200 时返回错误文本，便于排查（如 token 无效/超额）。"""
        if isinstance(data, dict):
            code = data.get("code")
            if code not in (200, "200", None):
                msg = data.get("msg") or data.get("message") or ""
                return f"ALAPI {code}{('：' + str(msg)) if msg else ''}"
        return None

    async def _get_json(self, url, ua=None, retries=1, extra_headers=None, proxy=None):
        timeout = aiohttp.ClientTimeout(total=self._to_int(self.config.get("timeout", 20), 20))
        headers = {"User-Agent": ua or _UA, "Accept": "application/json, text/plain, */*"}
        if extra_headers:
            headers.update({k: v for k, v in extra_headers.items() if v})
        proxy = (proxy or str(self.config.get("proxy", "") or "")).strip() or None
        last = None
        for attempt in range(max(1, retries + 1)):
            try:
                # trust_env=True 让 aiohttp 读取 HTTP(S)_PROXY 环境变量；proxy 为显式代理（优先）
                async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as s:
                    async with s.get(url, headers=headers, proxy=proxy) as r:
                        return await r.json(content_type=None)
            except Exception as e:  # noqa: BLE001  # 偶发 5xx/超时，重试一次
                last = e
                if attempt < retries:
                    await asyncio.sleep(0.8)
        raise last

    @staticmethod
    def _split_source(entry):
        """解析热榜条目：支持 “名称|目标”，目标可为站点名(type/slug)或完整 URL。"""
        s = str(entry or "").strip()
        if "|" in s:
            a, b = s.split("|", 1)
            return (a.strip() or None), b.strip()
        return None, s

    @staticmethod
    def _to_int(v, default):
        try:
            return int(v)
        except Exception:  # noqa: BLE001
            return default

    @staticmethod
    def _strip(message_str, triggers):
        s = (message_str or "").strip()
        cands = sorted(triggers, key=len, reverse=True)
        for pre in ("/", "#", "!", "！", ""):
            for t in cands:
                head = pre + t
                if s == head:
                    return ""
                if s.startswith(head):
                    rest = s[len(head):]
                    if rest[:1] in (" ", "\t", "\u3000", ""):
                        return rest.strip()
        return s

    @staticmethod
    def _is_admin(event):
        try:
            if str(getattr(event, "role", "") or "") == "admin":
                return True
        except Exception:  # noqa: BLE001
            pass
        fn = getattr(event, "is_admin", None)
        if callable(fn):
            try:
                return bool(fn())
            except Exception:  # noqa: BLE001
                return False
        return False

    def _save(self):
        try:
            fn = getattr(self.config, "save_config", None)
            if callable(fn):
                fn()
                return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[dailyreport] 保存配置失败：{e}")
        return False

    async def terminate(self):
        """插件卸载/停用时停掉定时任务。"""
        try:
            if self._scheduler:
                self._scheduler.shutdown(wait=False)
                self._scheduler = None
        except Exception:  # noqa: BLE001
            pass
