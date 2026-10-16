# -*- coding: utf-8 -*-
# JavMenu (javmenu.com) TVBox 蜘蛛 (FongMi 5.6.4 / Chaquopy)
# 分类: 有码/无码 x 在线/全部, 列表解析 javmenu 卡片页(带分页)
# 详情: 在线线路 = 详情页 data-m3u8 直链 HLS(已实测无防盗链, 分片可直接下载)
#       磁力线路 = 详情页磁力表, 原样交给播放器(能否播取决于宿主磁力支持)
# 搜索: /search?wd=关键词&page=N
import html as html_mod
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    from base.spider import Spider as BaseSpider
except Exception:
    class BaseSpider(object):
        pass

BASE = "https://javmenu.com"
PC_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

CLASSES = [
    ("有码在线", "censored/online"),
    ("无码在线", "uncensored/online"),
    ("有码全部", "censored"),
    ("无码全部", "uncensored"),
]


def _http_urllib(url, headers=None, timeout=20, retries=2):
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as e:
            last = e
            time.sleep(1 + i)
    raise last


_JAVA_CLIENT = [None]


def _java_client():
    if _JAVA_CLIENT[0] is not None:
        return _JAVA_CLIENT[0] or None
    try:
        from java import jclass
        Builder = jclass("okhttp3.OkHttpClient$Builder")
        TimeUnit = jclass("java.util.concurrent.TimeUnit")
        b = Builder()
        b.followRedirects(True)
        b.followSslRedirects(True)
        b.connectTimeout(15, TimeUnit.SECONDS)
        b.readTimeout(35, TimeUnit.SECONDS)
        b.writeTimeout(15, TimeUnit.SECONDS)
        _JAVA_CLIENT[0] = b.build()
    except Exception:
        _JAVA_CLIENT[0] = False
    return _JAVA_CLIENT[0] or None


def _java_http_bytes(url, headers=None):
    client = _java_client()
    if client is None:
        return None
    try:
        from java import jclass
        ReqBuilder = jclass("okhttp3.Request$Builder")
        rb = ReqBuilder()
        rb.url(str(url))
        for k, v in (headers or {}).items():
            if str(k).lower() in ("content-length", "host", "content-type"):
                continue
            try:
                rb.header(str(k), str(v))
            except Exception:
                pass
        resp = client.newCall(rb.build()).execute()
        code = 0
        try:
            code = int(resp.code())
        except Exception:
            pass
        out = b""
        try:
            bd = resp.body()
            if bd is not None:
                out = bytes(bd.bytes())
        except Exception:
            pass
        try:
            resp.close()
        except Exception:
            pass
        if code and not (200 <= code < 400) and not out:
            return None
        return out
    except Exception:
        return None


def _http(url, headers=None, timeout=20, retries=2):
    try:
        return _http_urllib(url, headers=headers, timeout=timeout, retries=retries)
    except Exception:
        for _ in range(2):
            out = _java_http_bytes(url, headers=headers)
            if out:
                return out
            time.sleep(1)
        raise


def _fetch_text(url):
    raw = _http(url, headers={"User-Agent": PC_UA, "Referer": BASE + "/"})
    return raw.decode("utf-8", "ignore")


def _parse_cards(page_html):
    items, seen = [], set()
    for block in page_html.split("video-list-item")[1:]:
        m = re.search(r'href="(https://javmenu\.com/([A-Za-z0-9_\-]+))"', block)
        if not m:
            continue
        code = m.group(2)
        if code in seen or code in ("censored", "uncensored", "search", "zh", "en", "ja"):
            continue
        if "/" in code:
            continue
        ds = re.search(r'data-src="([^"]+)"', block)
        pic = ds.group(1) if ds else ""
        tm = re.search(r'<p class="card-text[^"]*" title="([^"]*)"', block)
        if not tm:
            tm = re.search(r'alt="([^"]+)"', block)
        title = html_mod.unescape(tm.group(1)) if tm else ""
        title = re.sub(r"\s*-\s*JAV目錄大全.*$", "", title).strip()
        dm = re.search(r'<span class="text-muted">(\d{4}-\d{2}-\d{2})</span>', block)
        date = dm.group(1) if dm else ""
        seen.add(code)
        items.append({
            "vod_id": code,
            "vod_name": (code + " " + title).strip(),
            "vod_pic": pic,
            "vod_remarks": date,
        })
    return items


def _pagecount(page_html, fallback_full=False):
    nums = [int(x) for x in re.findall(r"[?&]page=(\d+)", html_mod.unescape(page_html))]
    if nums:
        return max(nums)
    return 2 if fallback_full else 1


class Spider(BaseSpider):
    def __init__(self):
        try:
            super().__init__()
        except Exception:
            pass

    def getName(self):
        return "JavMenu"

    def init(self, extend=""):
        return True

    def homeContent(self, filter=False):
        classes = [{"type_id": tid, "type_name": name} for name, tid in CLASSES]
        lst = []
        try:
            lst = self.categoryContent("censored/online", "1", False, {})["list"]
        except Exception:
            pass
        return {"class": classes, "list": lst}

    def homeVideoContent(self):
        try:
            return {"list": self.categoryContent("censored/online", "1", False, {})["list"]}
        except Exception:
            return {"list": []}

    def categoryContent(self, tid, pg="1", filter=False, extend=None):
        try:
            p = int(pg)
        except Exception:
            p = 1
        url = "%s/%s?page=%d" % (BASE, tid, p)
        try:
            page = _fetch_text(url)
        except Exception:
            return {"list": [], "page": p, "pagecount": 1, "limit": 24, "total": 0}
        items = _parse_cards(page)
        pc = _pagecount(page, fallback_full=len(items) >= 20)
        return {"list": items, "page": p, "pagecount": pc,
                "limit": 24, "total": pc * 24}

    def detailContent(self, ids):
        code = ""
        if isinstance(ids, (list, tuple)):
            code = str(ids[0]) if ids else ""
        else:
            code = str(ids or "")
        code = code.strip()
        if not code:
            return {"list": []}
        try:
            page = _fetch_text(BASE + "/" + urllib.parse.quote(code))
        except Exception:
            return {"list": []}

        tm = re.search(r"<title>([^<]*)</title>", page)
        title = html_mod.unescape(tm.group(1)) if tm else code
        title = title.split("|")[0].strip()
        og = re.search(r'property="og:image" content="([^"]+)"', page)
        pic = og.group(1) if og else ""
        dg = re.search(r'property="og:description" content="([^"]+)"', page)
        desc = html_mod.unescape(dg.group(1)) if dg else ""
        actor = ""
        am = re.search(r"主演女優是([^，,]+)", desc)
        if am:
            actor = am.group(1)
        ym = re.search(r"發佈日期為(\d{4})", desc)
        year = ym.group(1) if ym else ""

        froms, urls = [], []
        mm = re.search(r'data-m3u8="([^"]+)"', page)
        if mm:
            froms.append("在线")
            urls.append("播放$" + html_mod.unescape(mm.group(1)))
        mag_eps = []
        rows = re.findall(r'<tr class="col">(.*?)</tr>', page, re.S)
        for r in rows:
            hm = re.search(r'href="(magnet:[^"]+)"', r)
            if not hm:
                continue
            mag = html_mod.unescape(hm.group(1))
            txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r)).strip()
            txt = txt.replace("複制", "").replace("复制", "").strip()
            if not txt:
                txt = code
            mag_eps.append("%s$%s" % (txt[:60], mag))
        if mag_eps:
            froms.append("磁力")
            urls.append("#".join(mag_eps))

        vod = {
            "vod_id": code,
            "vod_name": title,
            "vod_pic": pic,
            "vod_year": year,
            "vod_actor": actor,
            "vod_content": desc,
            "vod_remarks": code,
            "vod_play_from": "$$$".join(froms),
            "vod_play_url": "$$$".join(urls),
        }
        return {"list": [vod]}

    def playerContent(self, flag, id, vipFlags=None):
        u = str(id or "")
        return {"parse": 0, "playUrl": "", "url": u,
                "header": {"User-Agent": PC_UA, "Referer": BASE + "/"}}

    def searchContent(self, key, quick=False, pg="1"):
        try:
            p = int(pg)
        except Exception:
            p = 1
        url = "%s/search?wd=%s&page=%d" % (BASE, urllib.parse.quote(str(key or "")), p)
        try:
            page = _fetch_text(url)
        except Exception:
            return {"list": [], "page": p, "pagecount": 1, "limit": 24, "total": 0}
        items = _parse_cards(page)
        pc = _pagecount(page, fallback_full=len(items) >= 20)
        return {"list": items, "page": p, "pagecount": pc,
                "limit": 24, "total": len(items)}

    def destroy(self):
        return True
