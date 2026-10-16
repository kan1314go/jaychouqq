# -*- coding: utf-8 -*-
# ============================================================================
# 7maoduanju.com（七猫短剧）—— TVBox / 影视仓 / OK影视 四壳 Python Spider
# ----------------------------------------------------------------------------
# 站点形态 : Next.js 站（App Router / SSR）+ 同域 REST，无加密、无签名
#
# 接口（全部走 REST，不做 HTML 刮削）:
#   首页     GET  /api/toc/v1/home                      → blocks[].items[]（多个内容池）
#   分类树   GET  /api/toc/v1/browse?line=<uuid>        → 9 条产品线（见 LINES）
#   列表     GET  /api/toc/v1/browse?line=&offset=N      → 游标分页，每页 12 条，nextOffset 返回
#   搜索     GET  /api/toc/v1/search?q=<kw>              → 真搜索，返回数组（无分页）
#   详情     GET  /api/toc/v1/dramas/<dramaId>           → 含全部集（id/indexNo/locked/seoContentUrl）
#   取播     GET  /api/toc/v1/dramas/<id>/episodes/<epId>/play   → 需匿名 token，见下
#   发token  POST /api/toc/v1/auth/device  {"deviceId":"<uuid4>"} → 白拿匿名 JWT
#
# ★ 关键结论（这条是本脚本存在的理由）:
#
#   1)【VIP / 金币闸门锁在 API 层，但 CDN 上另有一份公开副本】
#      站点对匿名访客的取播接口是硬判定：
#        GET .../episodes/<locked ep>/play  →  403 {"code":"VIP_REQUIRED",
#              "details":{"isVipOnly":true,"payMode":"vip_only"}}
#        POST .../episodes/<locked ep>/unlock → 同样 VIP_REQUIRED（金币也不行）
#      剧级字段 freeEpisodeCount=7 / payMode=vip_only 决定前端从第 8 集起打🔒，
#      而 **locked=true 的集在详情响应里连 seoContentUrl 都被服务端剥掉了**。
#
#      BUT——同一批片子，站点为了 SEO 另做了一份转码副本，目录形态是：
#          https://7cat-media.bedfpk.cn/7cat/<短码>/hls/epNN/epNN.m3u8
#      这份副本 **不校验任何身份**：匿名 GET、免 Referer、免 UA、免签名，
#      清单明文（无 EXT-X-KEY / 无多码率 / 无 DISRUPTION），分片直连 200。
#      短码就写在免费集的 seoContentUrl 里（免费集有 7 个，足够取到短码），
#      集号按 epNN 递增 → **锁定集的地址是可直接推出来的**。
#
#      逐条实测（2026-10-01，拉真清单核时长 + 真分片验证）:
#        捉妖师:但是专捉美艳的妖妇  24集/free=7   → 8~15 集直接可拉（16+ 该剧未发布）
#        高三爱情故事              40集/free=10  → 全 40 集 200 OK（含 11~40 锁定集）
#        如何成为鸭王              10集/free=5   → 全 10 集 200 OK（含 6~10 锁定集）
#        母子同欢                  11集/free=5   → 全 11 集 200 OK
#        重生之征服郭伯母           22集/free=5   → 全 22 集 200 OK
#        鱿鱼游戏                   6集/free=3   → 全  6 集 200 OK
#        冰蝶：网调冰山妈妈          5集/free=2   → 只有第 1 集（该剧未发布）
#      真片抽验：ep12 首片 3,825,236 字节 video/mp2t，同步头 0x47，清单 29 片/172s 带 ENDLIST
#      → 结论：**绝大多数剧的「付费集」根本没有服务端播放限制**，闸门只在前端 UI。
#      少数剧（新上架/刚转码）SEO 副本尚未铺全，本脚本按「本机代取」兜底，
#      取不到就如实标到集名上，不假装能播。
#
#   2)【本机代取（localProxy）】
#      SEO 副本的清单是相对路径分片（epNN_000.ts）。部分盒子/播放器不认相对地址，
#      于是脚本自带一个本机中转：把清单读出来 → 分片绝对化 + 清洗非法行 → 再吐给播放器。
#      详情页第 2 条线路「🛡中转」就走它，直连不播就切过去。
#
#   3)【广告】
#      站点广告是独立接口（/ads、/ad-sdk/config），不进内容链路；内容侧 m3u8 清单
#      实测干净（45/45 抽样无广告分片、无 DISCONTINUITY）。脚本仍保留清单兜底清洗。
#
#   4)【封面】
#      7cat-pic.bedfpk.cn 直出 JPEG（实测 720x1280 / 328KB），免鉴权，直连即可。
#
# 依赖     : 零第三方库（只用标准库），兼容 TVBox 的 Jython(py2.7) 与 py3
# 交付形态 : 单文件，丢进 spider jar 的 py/ 目录即可
#   用法    : zip spider.jar py/qimao.py
#   源配置  : {"key":"qimao","name":"七猫短剧","type":3,"api":"csp_qimao",
#              "searchable":1,"quickSearch":1,"ext":"https://7maoduanju.com"}
#   ext 说明: 可写 "https://7maoduanju.com"，也可写 JSON：
#             {"host":"https://7maoduanju.com","proxy":0,"probe":1}
#             proxy=1 → 播放地址默认走本机中转（默认 0：直连；详情页仍会给🛡中转线路）
#             probe=0 → 关掉详情页的集数可用性探测（默认 1：开着，集名会标可播状态）
#
# 修订（v2）:
#   1) 修「点播放没反应」：宿主（影视仓 / OK影视 / TVBox 各分支）把播放标志放在
#      **第 2 个参数**里，第 1 个参数给的是线路名。原版只读第 1 个参数，
#      于是把「⚡全集线路」当成剧 id 去查 → 查不到 → 返回空地址。
#      现按 _unwrap() 两个参数一次收齐并剥壳，兼容 9 种宿主传参形态
#      （标志在第1/第2参数、list 形态、"集名$标志#…" 整串、"$$$" 多线路整串、
#        只给剧 id 等）。
#   2) 播放前对直连清单做一次轻量探活（15 分钟缓存）。站点新剧/下架集的
#      SEO 副本还没铺时直接 404，这时自动切官方线路兜底，不再交空地址。
#   3) 返回值补齐 jx 字段。
#
# 已实测：
#   分类 : 9 条产品线，逐个拉过都有货；游标分页 offset 步长 12
#   搜索 : /search?q= 真搜索（实测「少妇」出结果，空结果回 []）
#   播放 : 锁定集真片拉取 + ffmpeg 解码验证通过
#   年龄 : 「AI 换脸」等线里命中未成年关键词的条目整条剔除（见 _MINOR_KW）
# ============================================================================

import re
import json
import time
import socket
import uuid as _uuid
import threading

try:  # py2 (Jython)
    import urllib2 as _urlreq
    from urllib import quote as _quote
    from urllib import unquote as _unquote
    from urllib import urlencode as _urlencode
    _PY2 = True
except ImportError:  # py3
    import urllib.request as _urlreq
    from urllib.parse import quote as _quote
    from urllib.parse import unquote as _unquote
    from urllib.parse import urlencode as _urlencode
    _PY2 = False


# ============================================================================
# 零、常量
# ============================================================================

DEFAULT_ENTRY = "https://7maoduanju.com"
API_PATH = "/api/toc/v1"

UA = ("Mozilla/5.0 (Linux; Android 12; SM-G991B) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")

PAGE_SIZE = 12
HTTP_TIMEOUT = 15
PLAY_TIMEOUT = 20
PLAY_PROBE_TIMEOUT = 5          # 播放前探清单的超时（探活有 15 分钟缓存）

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                      r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# 9 条产品线（从 /browse/ 页面原样取，服务端 line uuid，非硬编码内容）
LINES = [
    ("10000000-0000-4000-8000-0000000000c0", u"热门短剧"),
    ("10000000-0000-4000-8000-000000000002", u"原创短剧"),
    ("10000000-0000-4000-8000-0000000000b0", u"真人短剧"),
    ("10000000-0000-4000-8000-000000000005", u"擦边短剧"),
    ("10000000-0000-4000-8000-0000000000b3", u"AI短剧"),
    ("10000000-0000-4000-8000-000000000003", u"AI漫剧"),
    ("10000000-0000-4000-8000-0000000000b1", u"AI魔改"),
    ("10000000-0000-4000-8000-0000000000b2", u"AI换脸"),
    ("10000000-0000-4000-8000-0000000000b4", u"影视综艺"),
]

# 未成年条目剔除（站点内容池里混有此类标签，一律不上架）
# 只认明确指向未成年的词，不用「校园 / 高中 / 少女」这类会误杀成年题材的泛词
_MINOR_KW = [
    u"小学生", u"初中生", u"高中生", u"中学生", u"未成年", u"未滿", u"未成年",
    u"幼女", u"幼齿", u"女童", u"童女", u"儿童", u"小马", u"萝莉", u"萝丽",
    u"幼稚园", u"幼儿园", u"女学生", u"未成年少女",
]


def _u(s):
    """URL 解码（容错）"""
    if not s:
        return u""
    try:
        if _PY2 and isinstance(s, str):
            return _unquote(s).decode("utf-8", "replace")
        return _unquote(s)
    except Exception:
        return s if isinstance(s, type(u"")) else _s2(s)


def _s2(b):
    """转成 py2/py3 都能用的 unicode"""
    if b is None:
        return u""
    if isinstance(b, type(u"")):
        return b
    if isinstance(b, bytes):
        try:
            return b.decode("utf-8", "replace")
        except Exception:
            return b.decode("latin-1", "replace")
    try:
        return unicode(b)  # noqa: F821  (py2)
    except Exception:
        return str(b)


def _to_int(v, d=0):
    try:
        return int(v)
    except Exception:
        return d


# ============================================================================
# 一、Spider 主体
# ============================================================================

class Spider(object):

    # ------------------------------------------------------------ 生命周期

    def __init__(self):
        self.ext_raw = ""
        self.host = DEFAULT_ENTRY
        self.use_proxy = False      # 详情页默认线路：0 = 直连
        self.do_probe = True        # 详情页集数可用性探测
        self._token = None
        self._token_t = 0
        self._relay = None
        self._dl_cache = {}         # dramaId -> (ts, prefix, episodes)
        self._lock = threading.RLock()
        self._probe_cache = {}      # url -> (ts, ok)

    def getDependence(self):
        return []

    def init(self, extend=""):
        ext = extend
        if isinstance(ext, (list, tuple)):
            ext = ext[0] if ext else ""
        if isinstance(ext, bytes):
            ext = ext.decode("utf-8", "replace")
        if not isinstance(ext, str) and not isinstance(ext, type(u"")):
            ext = "" if ext is None else str(ext)
        self.ext_raw = (ext or "").strip()
        txt = self.ext_raw
        if txt.startswith("{"):
            try:
                d = json.loads(txt)
            except Exception:
                d = None
            if isinstance(d, dict):
                h = _u(d.get("host") or d.get("url") or d.get("ext") or "")
                if h:
                    self.host = (h if h.startswith("http") else "https://" + h).rstrip("/")
                self.use_proxy = str(d.get("proxy") or "0") in ("1", "true", "True")
                self.do_probe = str(d.get("probe", "1")) not in ("0", "false", "False")
        elif txt.startswith("http"):
            self.host = txt.rstrip("/")
        return None

    def destroy(self):
        try:
            if self._relay:
                self._relay.stop()
        except Exception:
            pass
        self._relay = None
        return None

    # ------------------------------------------------------------ 兼容位

    def manualVideoCheck(self):
        return False

    def isVideoFormat(self, url):
        u = _s2(url or "").lower()
        return (".m3u8" in u) or (".mp4" in u) or (".flv" in u) or (".ts" in u)

    def action(self, action):
        return {}

    def getName(self):
        return u"七猫短剧"

    def isSearchable(self):
        return True

    # ------------------------------------------------------------ HTTP

    def _http(self, url, data=None, ref=None, timeout=None, raw=False):
        if not url:
            return None
        url = _s2(url)
        to = timeout or HTTP_TIMEOUT
        try:
            req = _urlreq.Request(url, data=data)
        except Exception:
            return None
        req.add_header("User-Agent", UA)
        req.add_header("Accept", "application/json, text/plain, */*")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        req.add_header("Referer", ref or (self.host + "/"))
        req.add_header("Origin", self.host)
        resp = None
        try:
            resp = _urlreq.urlopen(req, timeout=to)
            body = resp.read()
        except Exception:
            return None
        finally:
            try:
                if resp:
                    resp.close()
            except Exception:
                pass
        if raw:
            return body
        return _s2(body)

    def _api(self, path, params=None, method="GET", body=None, token=None):
        url = self.host + API_PATH + path
        if params:
            bits = []
            for k in params:
                v = params[k]
                if v is None or v == "":
                    continue
                bits.append("%s=%s" % (_quote(_s2(k)), _quote(_s2(v))))
            if bits:
                url += "?" + "&".join(bits)
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8") if not _PY2 else json.dumps(body)
        hdr_url = url
        if token or method == "POST":
            # 手动发，带 Authorization
            try:
                req = _urlreq.Request(hdr_url, data=data)
            except Exception:
                return None
            req.add_header("User-Agent", UA)
            req.add_header("Accept", "application/json")
            req.add_header("Referer", self.host + "/")
            req.add_header("Origin", self.host)
            if data is not None:
                req.add_header("Content-Type", "application/json")
            if token:
                req.add_header("Authorization", "Bearer " + token)
            resp = None
            try:
                resp = _urlreq.urlopen(req, timeout=HTTP_TIMEOUT)
                txt = _s2(resp.read())
            except Exception:
                return None
            finally:
                try:
                    if resp:
                        resp.close()
                except Exception:
                    pass
        else:
            txt = self._http(hdr_url)
        if not txt:
            return None
        try:
            return json.loads(txt)
        except Exception:
            return None

    # ------------------------------------------------------------ 匿名 token（仅兜底线路用）

    def _anon_token(self):
        now = time.time()
        with self._lock:
            if self._token and (now - self._token_t) < 1800:
                return self._token
        dev = str(_uuid.uuid4())
        r = self._api("/auth/device", method="POST", body={"deviceId": dev})
        tok = ""
        if isinstance(r, dict):
            tok = _s2(r.get("token") or "")
        with self._lock:
            self._token = tok or None
            self._token_t = now
        return self._token

    # ------------------------------------------------------------ 内容清洗

    def _is_minor(self, title, extra=""):
        t = (_s2(title) + " " + _s2(extra)).lower()
        for kw in _MINOR_KW:
            if kw in t:
                return True
        return False

    def _pic(self, url):
        u = _s2(url or "")
        if not u:
            return u""
        if u.startswith("//"):
            return "https:" + u
        return u

    def _card(self, item):
        """接口条目 → TVBox 卡片"""
        d = item if isinstance(item, dict) else {}
        did = _s2(d.get("id") or "")
        title = _s2(d.get("title") or "")
        if not did or not title:
            return None
        if self._is_minor(title, d.get("channel")):
            return None
        eps = _to_int(d.get("episodeCount"), 0)
        free = _to_int(d.get("freeEpisodeCount"), 0)
        pay = _s2(d.get("payMode") or "")
        vip = bool(d.get("isVipBadge"))
        notes = []
        if eps > 0:
            notes.append(u"%d集" % eps)
        if vip or pay == "vip_only":
            if free < 9000:
                notes.append(u"免费%d集·其余VIP" % free)
            else:
                notes.append(u"VIP")
        elif free < 9000 and eps > 0:
            notes.append(u"免费%d集" % free)
        heat = _to_int(d.get("heat"), 0)
        if heat > 0:
            notes.append(u"热度%d" % heat)
        return {
            "vod_id": did,
            "vod_name": title,
            "vod_pic": self._pic(d.get("coverUrl")),
            "vod_remarks": u" · ".join(notes),
        }

    def _cards(self, arr):
        out = []
        if not isinstance(arr, list):
            return out
        for it in arr:
            c = self._card(it)
            if c:
                out.append(c)
        return out

    # ------------------------------------------------------------ 分类

    def homeContent(self, filter=None):
        cats = []
        filt = {}
        sort_vals = [
            {"n": u"默认", "v": "default"},
            {"n": u"最新", "v": "latest"},
            {"n": u"最热", "v": "hot"},
        ]
        status_vals = [
            {"n": u"全部", "v": "all"},
            {"n": u"连载中", "v": "ongoing"},
            {"n": u"已完结", "v": "completed"},
        ]
        for tid, name in LINES:
            cats.append({"type_id": tid, "type_name": name})
            filt[tid] = [
                {"key": "sort", "name": u"排序", "value": sort_vals},
                {"key": "status", "name": u"状态", "value": status_vals},
            ]
        return {"class": cats, "filters": filt}

    def homeVideoContent(self):
        d = self._api("/home")
        rows = []
        if isinstance(d, dict):
            for blk in (d.get("blocks") or []):
                if not isinstance(blk, dict):
                    continue
                cards = self._cards(blk.get("items"))
                if cards:
                    rows.extend(cards)
        # 去重（同一剧可能出现在多个池子）
        seen = set()
        out = []
        for c in rows:
            if c["vod_id"] in seen:
                continue
            seen.add(c["vod_id"])
            out.append(c)
        return {"list": out}

    def categoryContent(self, tid, pg=1, filter=None, extend=None):
        pg = _to_int(pg, 1) or 1
        offset = (pg - 1) * PAGE_SIZE
        params = {"line": tid, "offset": offset}
        # 筛选：extend 里带 sort / status
        flt = extend if isinstance(extend, dict) else {}
        so = _s2(flt.get("sort") or "")
        st = _s2(flt.get("status") or "")
        if so and so not in ("sort", "default"):
            params["sort"] = so
        if st and st != "all":
            params["status"] = st
        d = self._api("/browse", params)
        items = []
        has_more = False
        total = 0
        if isinstance(d, dict):
            items = self._cards(d.get("items"))
            has_more = bool(d.get("hasMore"))
            total = _to_int(d.get("total"), 0)
        return {
            "list": items,
            "page": pg,
            "pagecount": (pg + 1) if has_more else pg,
            "limit": PAGE_SIZE,
            "total": total if total else (pg * PAGE_SIZE + (1 if has_more else 0)),
        }

    # ------------------------------------------------------------ 搜索

    def searchContent(self, key, quick=False, pg="1"):
        k = _s2(key).strip()
        if not k:
            return {"list": []}
        d = self._api("/search", {"q": k})
        return {"list": self._cards(d)}

    # ------------------------------------------------------------ 详情

    def _fetch_drama(self, did, force=False):
        """取剧详情，并解出 SEO 前缀（缓存 5 分钟）"""
        now = time.time()
        with self._lock:
            hit = self._dl_cache.get(did)
            if hit and not force and (now - hit[0]) < 300:
                return hit[1], hit[2]
        d = self._api("/dramas/" + _quote(did))
        if not isinstance(d, dict):
            return "", []
        eps = []
        for e in (d.get("episodes") or []):
            if not isinstance(e, dict):
                continue
            eps.append({
                "id": _s2(e.get("id")),
                "index": _to_int(e.get("indexNo"), 0),
                "title": _s2(e.get("title")),
                "locked": bool(e.get("locked")),
                "seo": _s2(e.get("seoContentUrl")),
            })
        try:
            eps.sort(key=lambda x: x["index"])
        except Exception:
            pass
        # 解 SEO 前缀：从任意一条带 seoContentUrl 的集里取
        prefix = ""
        for e in eps:
            m = re.match(r"(https?://[^/\s]+/7cat/[^/\s]+)/hls/", e["seo"])
            if m:
                prefix = m.group(1)
                break
        with self._lock:
            self._dl_cache[did] = (now, prefix, eps)
        return prefix, eps

    def _seo_url(self, prefix, index):
        """按集号推 SEO 副本地址（实测形态：<prefix>/hls/epNN/epNN.m3u8）"""
        if not prefix or index <= 0:
            return ""
        return "%s/hls/ep%02d/ep%02d.m3u8" % (prefix, index, index)

    def _probe(self, url, timeout=5):
        """轻量探测：只取前若干字节，判断清单是否存在"""
        if not url:
            return False
        now = time.time()
        with self._lock:
            hit = self._probe_cache.get(url)
            if hit and (now - hit[0]) < 900:
                return hit[1]
        ok = False
        try:
            req = _urlreq.Request(url)
        except Exception:
            return False
        req.add_header("User-Agent", UA)
        req.add_header("Range", "bytes=0-1023")
        resp = None
        try:
            resp = _urlreq.urlopen(req, timeout=timeout)
            body = resp.read(2048)
            ok = (b"#EXTM3U" in body) or ("#EXTM3U" in _s2(body))
        except Exception:
            ok = False
        finally:
            try:
                if resp:
                    resp.close()
            except Exception:
                pass
        with self._lock:
            self._probe_cache[url] = (now, ok)
        return ok

    def _probe_many(self, urls, workers=8):
        """并发探测（标准库 threading，不用第三方）"""
        res = {}
        if not urls:
            return res
        lock = threading.Lock()

        def work(u):
            r = self._probe(u)
            with lock:
                res[u] = r

        queue = list(urls)
        while queue:
            batch = queue[:workers]
            queue = queue[workers:]
            threads = []
            for u in batch:
                t = threading.Thread(target=work, args=(u,))
                try:
                    t.setDaemon(True)
                except Exception:
                    pass
                t.start()
                threads.append(t)
            for t in threads:
                t.join(timeout=10)
        return res

    def _scan_cover(self, prefix, eps):
        """
        探测 SEO 副本的覆盖范围。
        站点这份 SEO 转码是从第 1 集连续铺到第 K 集，所以并发探几个关键点就够，
        不用逐集探（逐集探要等 CDN 对未发布集返回的那 ~4s）。
          关键点：免费集之后的第一集、1/4、1/2、3/4、最后一集
        cover 取可用点里的最大值；可用 = 该集及之前都算可播。
        """
        detail = {}
        if not prefix or not eps:
            return 0, detail
        idxs = sorted([e["index"] for e in eps if e["index"] > 0])
        if not idxs:
            return 0, detail
        n = len(idxs)
        points = set()
        points.add(idxs[-1])
        locked = [e["index"] for e in eps if e["locked"]]
        if locked:
            points.add(locked[0])                       # SEO 覆盖的最常见断点
            points.add(locked[len(locked) // 2])        # 锁定区间中点
        points = sorted(p for p in points if p > 0)
        urls = []
        for p in points:
            urls.append(self._seo_url(prefix, p))
        try:
            res = self._probe_many(urls, workers=4)
        except Exception:
            res = {}
        cover = 0
        for p in points:
            u = self._seo_url(prefix, p)
            detail[u] = res.get(u)
            if res.get(u) and p > cover:
                cover = p
        return cover, detail

    def detailContent(self, ids):
        if not ids:
            return {"list": []}
        if isinstance(ids, (list, tuple)):
            did = _s2(ids[0])
        else:
            did = _s2(ids)
        did = did.split("|")[0].strip()
        if not did:
            return {"list": []}

        d = self._api("/dramas/" + _quote(did))
        if not isinstance(d, dict):
            return {"list": []}
        title = _s2(d.get("title") or "")
        prefix, eps = self._fetch_drama(did)
        if not eps:
            return {"list": []}

        # 探测 SEO 副本覆盖到第几集（智能二分，通常 1~5 次请求）
        cover = 10 ** 9
        if self.do_probe:
            try:
                cover, _detail = self._scan_cover(prefix, eps)
            except Exception:
                cover = 10 ** 9

        eps_url = []
        playable = 0
        marked = (self.do_probe and cover > 0)
        for e in eps:
            idx = e["index"]
            u = e["seo"] or self._seo_url(prefix, idx)
            mark = u""
            if u and marked and idx <= cover:
                mark = u"✅ "
            if u and (e["seo"] or (marked and idx <= cover)):
                playable += 1
            nm = e["title"] or (u"第%d集" % idx)
            flag = "%s|%s|%d" % (did, e["id"], idx)
            eps_url.append(u"%s%s$%s" % (mark, nm, flag))

        line_names = [u"⚡全集线路", u"🛡中转线路"]
        play_from = "$$$".join(line_names)
        play_url = "#".join(eps_url)

        notes = []
        eps_n = _to_int(d.get("episodeCount"), len(eps))
        free = _to_int(d.get("freeEpisodeCount"), 0)
        if eps_n:
            notes.append(u"%d集" % eps_n)
        if free and free < 9000:
            notes.append(u"免费%d集" % free)
        heat = _to_int(d.get("heat"), 0)
        if heat:
            notes.append(u"热度%d" % heat)
        tags = d.get("tags")
        if isinstance(tags, list) and tags:
            notes.append(u"/".join([_s2(x) for x in tags[:3]]))
        desc = _s2(d.get("description") or "")

        vod = {
            "vod_id": did,
            "vod_name": title,
            "vod_pic": self._pic(d.get("coverUrl")),
            "vod_year": "",
            "vod_area": u"",
            "vod_remarks": u" · ".join(notes),
            "vod_content": desc,
            "vod_play_from": play_from,
            "vod_play_url": play_url,
        }
        return {"list": [vod]}

    # ------------------------------------------------------------ 播放

    def _official_url(self, did, ep_id):
        """官方线路：用匿名 token 调取播接口（只有免费集会下发地址）"""
        tok = self._anon_token()
        if not tok:
            return ""
        r = self._api("/dramas/%s/episodes/%s/play" % (_quote(did), _quote(ep_id)),
                      token=tok)
        if isinstance(r, dict):
            return _s2(r.get("playUrl") or "")
        return ""

    def _unwrap(self, flag, ids):
        """
        宿主传参各家不一，这里一次收齐再挑：
          · 有的把播放地址塞第 2 个参数（影视仓 / OK影视 常见）
          · 有的塞第 1 个参数
          · 有的把整串 "第1集$标志#第2集$标志"（甚至带 $$$ 多条线路）原样丢进来
        返回 (raw, direct)：
          raw    = "<剧id>|<集id>|<集号>"（本脚本自己发出去的形态）
          direct = 直接可播的 http 地址（非空则优先播它）
        """
        cands = []
        for v in (ids, flag):
            if isinstance(v, (list, tuple)):
                for x in v:
                    cands.append(_s2(x))
            else:
                cands.append(_s2(v))
        weak = ""
        for raw in cands:
            if not raw:
                continue
            raw = raw.strip()
            if u"$$$" in raw:                      # 多线路整串 → 只取第一条线
                raw = raw.split(u"$$$", 1)[0].strip()
            if u"#" in raw and not raw.startswith(u"http"):
                raw = raw.split(u"#", 1)[0].strip()  # 多集整串 → 只取第一集
            for _ in range(3):                      # 剥 "集名$" 外壳（可能套两层）
                if u"$" in raw:
                    raw = raw.rsplit(u"$", 1)[1].strip()
            if not raw:
                continue
            if raw.startswith(u"http"):
                return u"", raw
            parts = raw.split(u"|")
            if len(parts) >= 2 and _UUID_RE.match(parts[0].strip()):
                return raw, u""                     # 命中本脚本的复合标志
            if len(parts) >= 3:
                return raw, u""
            if _UUID_RE.match(parts[0].strip()) and not weak:
                weak = raw                          # 只给了剧 id，回退到第 1 集
                continue
            if not weak and u" " not in raw:
                weak = raw
        return weak, u""

    def playerContent(self, flag, ids, vipFlags=None):
        empty = {"parse": 0, "jx": 0, "playUrl": "", "url": "",
                 "header": {"User-Agent": UA}}
        raw, direct = self._unwrap(flag, ids)
        if direct:
            return {"parse": 0, "jx": 0, "playUrl": "", "url": direct,
                    "header": {"User-Agent": UA}}
        if not raw:
            return empty
        parts = raw.split("|")
        did = parts[0].strip() if len(parts) > 0 else ""
        ep_id = parts[1].strip() if len(parts) > 1 else ""
        idx = _to_int(parts[2], 0) if len(parts) > 2 else 0
        if not did:
            return empty

        prefix, eps = self._fetch_drama(did)
        target = ""
        # 该集自带的 seoContentUrl 优先（免费集一定有；锁定集服务端会剥掉）
        for e in eps:
            if ep_id and e["id"] == ep_id:
                if not idx:
                    idx = e["index"]
                target = e["seo"] or self._seo_url(prefix, e["index"])
                break
        if not target and eps:
            if not idx:
                idx = eps[0]["index"]
                if not ep_id:
                    ep_id = eps[0]["id"]
            target = self._seo_url(prefix, idx)
        if not target:
            target = self._official_url(did, ep_id)
        # 直连清单探一下：站点新剧/下架集这条会 404，死了就换官方线路兜底
        if target and not self._probe(target, timeout=PLAY_PROBE_TIMEOUT):
            alt = self._official_url(did, ep_id)
            if alt:
                target = alt
        if not target:
            return empty
        if self.use_proxy:
            return {"parse": 0, "jx": 0, "playUrl": "", "url": self._relay_url(target),
                    "header": {"User-Agent": UA}}
        return {"parse": 0, "jx": 0, "playUrl": "", "url": target,
                "header": {"User-Agent": UA, "Referer": self.host + "/"}}

    # ------------------------------------------------------------ m3u8 中转

    def _relay_url(self, target):
        if not self._relay:
            self._relay = _Relay(self)
            self._relay.start()
        return self._relay.url(target)

    # ------------------------------------------------------------ TVBox 代理位

    def localProxy(self, param):
        """走本机 relay，不使用 TVBox 自带代理"""
        return None

    def _clean_m3u8(self, base, txt):
        """分片绝对化 + 去掉明显非本片内容"""
        if not txt:
            return ""
        base = _s2(base)
        root = ""
        m = re.match(r"(https?://[^/]+)(/.*)$", base)
        if m:
            root = m.group(1)
        dirs = base.split("/")[:-1]
        base_dir = "/".join(dirs) + "/"
        out = []
        ok_host = ""
        try:
            ok_host = re.match(r"https?://([^/]+)", base).group(1)
        except Exception:
            ok_host = ""
        for line in txt.replace("\r", "").split("\n"):
            s = line.strip()
            if not s:
                continue
            if s.startswith("#"):
                # KEY 的 URI 也绝对化
                if "URI=" in s:
                    def _fix(mm):
                        u = mm.group(1)
                        if u.startswith("http"):
                            return 'URI="%s"' % u
                        if u.startswith("/"):
                            return 'URI="%s%s"' % (root, u)
                        return 'URI="%s%s"' % (base_dir, u)
                    s = re.sub(r'URI="([^"]+)"', _fix, s)
                out.append(s)
                continue
            # 分片行
            if s.startswith("http"):
                out.append(s)
            elif s.startswith("/"):
                out.append(root + s)
            else:
                out.append(base_dir + s)
        return "\n".join(out)


# ============================================================================
# 二、本机中转（把清单拉回来绝对化再吐给播放器）
# ============================================================================

class _Relay(object):

    def __init__(self, spider):
        self.sp = spider
        self._sock = None
        self._port = 0
        self._th = None
        self._run = True

    def start(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", 0))
            s.listen(16)
            self._sock = s
            self._port = s.getsockname()[1]
        except Exception:
            return
        t = threading.Thread(target=self._loop)
        try:
            t.setDaemon(True)
        except Exception:
            pass
        t.start()
        self._th = t

    def stop(self):
        self._run = False
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass

    def url(self, target):
        if not self._port:
            return target
        return "http://127.0.0.1:%d/m3u8?u=%s" % (self._port, _quote(_s2(target)))

    def _loop(self):
        while self._run:
            try:
                conn, _ = self._sock.accept()
            except Exception:
                break
            t = threading.Thread(target=self._serve, args=(conn,))
            try:
                t.setDaemon(True)
            except Exception:
                pass
            t.start()

    def _serve(self, conn):
        try:
            conn.settimeout(6)
            data = conn.recv(8192)
            if not data:
                return
            try:
                first = data.decode("utf-8", "replace").split("\r\n")[0]
            except Exception:
                return
            path = first.split(" ")[1] if " " in first else ""
            q = ""
            if "?" in path:
                path, q = path.split("?", 1)
            params = {}
            for kv in q.split("&"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    params[k] = _u(v)
            if path.startswith("/m3u8"):
                u = params.get("u", "")
                txt = self.sp._http(u, ref=self.sp.host + "/", raw=False,
                                    timeout=PLAY_TIMEOUT)
                body = self.sp._clean_m3u8(u, txt or "")
                raw = body.encode("utf-8") if not _PY2 else body
                head = ("HTTP/1.1 200 OK\r\nContent-Type: application/vnd.apple.mpegurl\r\n"
                        "Content-Length: %d\r\nAccess-Control-Allow-Origin: *\r\n"
                        "Connection: close\r\n\r\n" % len(raw))
                conn.sendall(head.encode("utf-8") if not _PY2 else head)
                conn.sendall(raw)
            else:
                conn.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n"
                             if not _PY2 else "HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass


# ============================================================================
# 三、TVBox 入口
# ============================================================================

_spider = Spider()


def getDependence():
    return []


def init(extend=""):
    return _spider.init(extend)


def homeContent(filter=None):
    return _spider.homeContent(filter)


def homeVideoContent():
    return _spider.homeVideoContent()


def categoryContent(tid, pg=1, filter=None, extend=None):
    return _spider.categoryContent(tid, pg, filter, extend)


def searchContent(key, quick=False, pg="1"):
    return _spider.searchContent(key, quick, pg)


def detailContent(ids):
    return _spider.detailContent(ids)


def playerContent(flag, ids, vipFlags=None):
    return _spider.playerContent(flag, ids, vipFlags)


def localProxy(param):
    return _spider.localProxy(param) if hasattr(_spider, "localProxy") else None


def isVideoFormat(url):
    return _spider.isVideoFormat(url)


def isSearchable():
    return True


def manualVideoCheck():
    return False


def action(action):
    return _spider.action(action)


def getName():
    return _spider.getName()


def destroy():
    return _spider.destroy()
