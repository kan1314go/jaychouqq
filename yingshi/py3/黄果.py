# -*- coding: utf-8 -*-
"""
黄果短剧（huangguoai.com）· TVBox Python 爬虫
==============================================

站点：https://huangguoai.com/  （自研站，SSR 直出 HTML，数据全在页面里）

链路说明
--------
站点没有对外开放的 JSON 列表接口，但详情页把整部剧的数据直接内联成 JSON，
所以全程走网页解析 + 一个公开的播放接口，零签名、零登录、零第三方依赖：

    首页        /                                        → 精选推荐 / 最近上新 等栏目，12 条
    频道列表    /ai-duanju/<n>/                           → 一页 20 条，共 40+ 页
                /ai-manju/  /ai-huanlian/  /ai-mogai/     同上
                页面里同时内联了 3 个隐藏面板（无需 ajax）：
                  data-channel-panel="latest"   最新更新（可无限翻页）
                  data-channel-panel="hot"      当前热播（固定 TOP20）
                  data-channel-panel="original" 独家原创（固定 TOP20）
    热门推荐    /recommend/<n>/                           → 一页 20 条，共 119 页
    题材标签    /tag/<slug>/page/<n>/                     → 一页 20 条
    专题        /topics/  列表 ； /topics/<slug>/<n>/     详情分页
    排行榜      /ranks/hot|potential|recommend/           → 固定 TOP20
    吃瓜        /chigua/[remen|yuanchuang]/<n>/           → 一页 13 条（图文+视频帖）
    搜索        /search/video/<urlencode>/<n>/            → 一页 20 条
    详情        /video/<id>/                              → <script id="videoInitialData"> JSON
                字段：title / coverSrc / description / tags / epPlaySrcs / videoSrc …
                data-history="{...}" 里带 total（总集数）
    分集播放    /api/videos/<id>/play?ep=<n>              → {"data":{"video_url": "…m3u8…"}}
    帖子详情    /api/posts/<id>                           → 正文 + video_url（部分帖带片）

播放地址实测
------------
    m3u8 裸拉 200，不需要 Referer、不需要 Cookie；
    清单里的分片和 AES-128 的 key 都是绝对地址，播放器自己去取就行；
    auth_key 是「请求时刻」现签的，所以播放地址有时效——
    爬虫默认在**点播的那一刻**重新签一次（详见 playerContent），不做一次性死链。

域名轮换
--------
官网 huangguoai.com 之外，官方「回家的路」页给了两个同源备用域名，实测都通：
    https://yss4.vqojfzoq.cc   https://f3v0.vqojfzoq.cc
爬虫内置候选表 + 探活，谁通用谁，某轮请求挂了自动换下一个重试；也能在 ext 里钉死：

    {"host": "https://yss4.vqojfzoq.cc"}

ext 可选项（JSON 字符串，不填也能用）
------------------------------------
host     : 手动钉死入口域名，例如 {"host": "https://yss4.vqojfzoq.cc"}
ntags    : 1 → 分类栏里隐藏 85 个题材标签（只留固定分区）
ntopics  : 1 → 分类栏里隐藏专题
ncg      : 1 → 分类栏里隐藏「吃瓜」分区
noheader : 1 → 播放不带 Referer（默认也不带，留着以防以后要加）
epmax    : 单部剧最多取多少集，默认 120
epthread : 取分集的并发数，默认 8

TVBox 配置（sites 里加一条）
----------------------------
{
  "key": "huangguo",
  "name": "黄果短剧",
  "type": 3,
  "api": "huangguo_tvbox.py",
  "searchable": 1,
  "quickSearch": 1,
  "filterable": 1
}

纯 Python 标准库，零第三方依赖。命令行自测：python3 huangguo_tvbox.py
"""

import sys
import re
import json
import time
import gzip
import zlib
import html as _htmlmod
import threading
import urllib.request
import urllib.parse
import urllib.error

sys.path.append('..')

try:
    from base.spider import Spider as BaseSpider
except Exception:
    BaseSpider = object


# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

# 主域名 + 官方备用域名（“回家的路”页公布的），实测都通
MIRRORS = [
    "https://huangguoai.com",
    "https://yss4.vqojfzoq.cc",
    "https://f3v0.vqojfzoq.cc",
    "https://hcp46.vqojfzoq.cc",
]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

TIMEOUT = 20
PAGE_SIZE = 20           # 列表一页 20 条（吃瓜一页 13 条）
CACHE_TTL = 300          # 列表页缓存 5 分钟
SEARCH_TTL = 180         # 搜索页缓存 3 分钟
PLAY_TTL = 120           # 播放地址本地缓存 2 分钟（签的是短时效链，别久存）

# 四个主频道：slug → 名字
CHANNELS = [
    ("ai-duanju", "AI成人短剧"),
    ("ai-manju", "AI成人漫剧"),
    ("ai-huanlian", "AI换脸"),
    ("ai-mogai", "AI魔改"),
]

# 频道页内联的三个面板（不用发 ajax，直接从同一份 HTML 里切）
PANELS = [
    ("latest", "🆕 最新更新"),
    ("hot", "🔥 当前热播"),
    ("original", "⭐ 独家原创"),
]

# 排行榜
RANKS = [
    ("hot", "热播榜"),
    ("potential", "潜力榜"),
    ("recommend", "推荐榜"),
]

# 吃瓜分区
GOSSIPS = [
    ("", "全部"),
    ("remen", "热门吃瓜"),
    ("yuanchuang", "AI原创"),
]

# 题材标签（站内 @/tag/<slug>/）
TAGS = [
    ("chaonengli", "超能力"), ("chuanyue", "穿越"), ("dananzhu", "大男主"), ("danvzhu", "大女主"),
    ("dushi", "都市"), ("funv", "父女"), ("hougong", "后宫"), ("jiedi", "姐弟"),
    ("kehuan", "科幻"), ("luanlun", "乱伦"), ("lvmao", "绿帽"), ("mori", "末日"),
    ("muzi", "母子"), ("naodong", "脑洞"), ("nianxia", "年下"), ("nixi", "逆袭"),
    ("qihuan", "奇幻"), ("qingchun", "清纯"), ("quanmou", "权谋"), ("sangshi", "丧尸"),
    ("shunv", "熟女"), ("tongxing", "同性"), ("xiandai", "现代"), ("xiangcun", "乡村"),
    ("xitong", "系统"), ("youxi", "游戏"),
    ("baisi", "白丝"), ("bangonshi", "办公室"), ("bazong", "霸总"), ("changfa", "长发"),
    ("chongsheng", "重生"), ("duanfa", "短发"), ("fanzui", "犯罪"), ("gaotiao", "高挑"),
    ("gaoyanzhi", "高颜值"), ("gongxi", "公媳"), ("gufeng", "古风"), ("heisi", "黑丝"),
    ("houru", "后入"), ("jianshenfang", "健身房"), ("jiaoshi", "教室"), ("jiudian", "酒店"),
    ("juesebanyan", "角色扮演"), ("koubao", "口爆"), ("koujiao", "口交"), ("meiru", "美乳"),
    ("meitui", "美腿"), ("mengzhai", "萌宅"), ("mgdsj", "魔改电视剧"), ("miaotiao", "苗条"),
    ("minguo", "民国"), ("mingxing", "明星"), ("minsu", "民宿"), ("ntr", "NTR"),
    ("nuelian", "虐恋"), ("qiaotun", "翘臀"), ("qicheng", "骑乘"), ("qunjiao", "群交"),
    ("renqi", "人妻"), ("shoujiao", "手交"), ("shuangfei", "双飞"), ("taitui", "抬腿"),
    ("tianchong", "甜宠"), ("tianmei", "甜美"), ("tongshi", "同事"), ("tongxue", "同学"),
    ("waiguoren", "外国人"), ("wuxia", "武侠"), ("xianxia", "仙侠"), ("xiaoyuan", "校园"),
    ("xingzhuan", "性转"), ("xiongmei", "兄妹"), ("xiuxian", "修仙"), ("xuanhuan", "玄幻"),
    ("xuanyi", "悬疑"), ("yewai", "野外"), ("youmao", "有毛"), ("yujie", "御姐"),
    ("yulequan", "娱乐圈"), ("zhanlihouru", "站立后入"), ("zhengchangwei", "正常位"),
    ("zhichang", "职场"), ("zhifu", "制服"), ("zhuixu", "赘婿"), ("zujiao", "足交"),
]

# 兜底专题表（/topics/ 页拉不到时用）
TOPICS_FALLBACK = [
    ("hot-aiduanju", "精品高分AI短剧"),
    ("luanlun-aiduanju", "家庭伦理AI短剧"),
    ("paranormal-aiduanju", "灵异诡事AI短剧"),
    ("gufen-wuxiachuanyue", "古风穿越AI短剧"),
    ("xianxia-qihuanyaoxiai", "仙侠奇幻AI短剧"),
    ("tiancai-nantong", "天菜男同AI短剧"),
    ("oumei-duanju", "欧美精选AI短剧"),
    ("magic-drama", "精选魔改AI影剧"),
    ("mingxing-huanlian", "热门明星AI换脸"),
]

# 频道页排序筛选项
SORT_ITEMS = [
    {"n": "🆕 最新更新", "v": "latest"},
    {"n": "🔥 当前热播", "v": "hot"},
    {"n": "⭐ 独家原创", "v": "original"},
]
SORT_VALID = set(x["v"] for x in SORT_ITEMS)


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def _s(v):
    """安全转字符串"""
    if v is None:
        return ""
    if isinstance(v, bytes):
        try:
            return v.decode('utf-8', 'ignore')
        except Exception:
            return ""
    return str(v)


def _txt(v):
    """去标签 + 转义符还原 + 收空白"""
    t = _s(v)
    t = re.sub(r'<br\s*/?>', '\n', t, flags=re.I)
    t = re.sub(r'</p>', '\n', t, flags=re.I)
    t = re.sub(r'<[^>]+>', '', t)
    t = _htmlmod.unescape(t)
    t = t.replace('\u00a0', ' ')
    t = re.sub(r'[ \t\r\f\v]+', ' ', t)
    t = re.sub(r'\n\s*\n+', '\n', t)
    return t.strip()


def _json_block(text, start):
    """从 start（'{' 的位置）抠出配对的一段 JSON 文本"""
    depth = 0
    i = start
    n = len(text)
    in_str = False
    esc = False
    while i < n:
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        i += 1
    return ""


def _naked(u):
    """去掉封面上的 auth_key（实测裸地址也能取到，且永不过期）"""
    u = _s(u).strip()
    if not u:
        return ""
    if u.startswith('//'):
        u = 'https:' + u
    if u.startswith('/'):
        return u
    q = u.find('?')
    return u[:q] if q > 0 else u


# --------------------------------------------------------------------------
# 封面解密（修复：原版拿不到封面的根因）
# --------------------------------------------------------------------------
# 图床下发的不是明文图片，而是整图 AES-128-CBC 加密后的字节（密钥/IV 固定，
# 藏在站点 crypto-worker.js 里）。网页靠 Web Worker 解密成 blob 显示，播放器
# 直接加载原地址必然失败。故蜘蛛在本机起图片代理：vod_pic 指向 127.0.0.1，
# 代理拉密文 → 解密 → 回吐真图；明文图原样透传，不受影响。
_IMG_KEY = b"f5d965df75336270"
_IMG_IV = b"97b60394abc2fbe1"


def _img_magic_type(b):
    if not b:
        return ""
    if b[:2] == b"\xff\xd8":
        return "image/jpeg"
    if b[:4] == b"\x89PNG":
        return "image/png"
    if b[:4] == b"GIF8":
        return "image/gif"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    if b[:2] == b"BM":
        return "image/bmp"
    return ""


# --- 纯 Python AES-128（仅 Java Cipher 不可用时的兜底，真机走 Java 原生） ---
def _aes_xtime(a):
    a <<= 1
    if a & 0x100:
        a ^= 0x11B
    return a & 0xFF


def _aes_build_tables():
    exp = [0] * 512
    log = [0] * 256
    x = 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        x = _aes_xtime(x) ^ x
    for i in range(255, 512):
        exp[i] = exp[i - 255]

    def mul(a, b):
        if a == 0 or b == 0:
            return 0
        return exp[log[a] + log[b]]

    inv = [0] * 256
    for i in range(1, 256):
        inv[i] = exp[255 - log[i]]
    sbox = [0] * 256
    isbox = [0] * 256
    for i in range(256):
        v = inv[i]
        s = v
        for r in (1, 2, 3, 4):
            s ^= ((v << r) | (v >> (8 - r))) & 0xFF
        s = (s ^ 0x63) & 0xFF
        sbox[i] = s
        isbox[s] = i
    return mul, sbox, isbox


_AES_MUL, _AES_SBOX, _AES_ISBOX = _aes_build_tables()
_AES_RK = None


def _aes_key_expand(key):
    w = list(key)
    rcon = 1
    while len(w) < 176:
        t = w[-4:]
        if len(w) % 16 == 0:
            t = [_AES_SBOX[t[1]] ^ rcon, _AES_SBOX[t[2]],
                 _AES_SBOX[t[3]], _AES_SBOX[t[0]]]
            rcon = _aes_xtime(rcon)
        for b in t:
            w.append(w[-16] ^ b)
    return w


def _aes_decrypt_block(rk, blk):
    s = [blk[i] ^ rk[160 + i] for i in range(16)]
    for rnd in range(9, -1, -1):
        # InvShiftRows + InvSubBytes（state 下标 = 行 + 4*列）
        s = [_AES_ISBOX[s[(i % 4) + 4 * (((i // 4) - (i % 4)) % 4)]]
             for i in range(16)]
        base = 16 * rnd
        s = [s[i] ^ rk[base + i] for i in range(16)]
        if rnd == 0:
            break
        out = [0] * 16
        for c in range(4):
            a0, a1, a2, a3 = s[4 * c], s[4 * c + 1], s[4 * c + 2], s[4 * c + 3]
            out[4 * c] = (_AES_MUL(a0, 14) ^ _AES_MUL(a1, 11)
                          ^ _AES_MUL(a2, 13) ^ _AES_MUL(a3, 9))
            out[4 * c + 1] = (_AES_MUL(a0, 9) ^ _AES_MUL(a1, 14)
                              ^ _AES_MUL(a2, 11) ^ _AES_MUL(a3, 13))
            out[4 * c + 2] = (_AES_MUL(a0, 13) ^ _AES_MUL(a1, 9)
                              ^ _AES_MUL(a2, 14) ^ _AES_MUL(a3, 11))
            out[4 * c + 3] = (_AES_MUL(a0, 11) ^ _AES_MUL(a1, 13)
                              ^ _AES_MUL(a2, 9) ^ _AES_MUL(a3, 14))
        s = out
    return s


def _aes_cbc_decrypt_py(data, key, iv):
    global _AES_RK
    if _AES_RK is None:
        _AES_RK = _aes_key_expand(key)
    rk = _AES_RK
    out = bytearray()
    prev = iv
    for off in range(0, len(data), 16):
        blk = data[off:off + 16]
        dec = _aes_decrypt_block(rk, list(blk))
        out.extend(dec[i] ^ prev[i] for i in range(16))
        prev = blk
    return bytes(out)


def _img_decrypt(raw):
    """先试 Java Cipher（真机原生速度），失败再走纯 Python"""
    if not raw or len(raw) % 16:
        return None
    try:
        from java import jclass
        Cipher = jclass("javax.crypto.Cipher")
        SecretKeySpec = jclass("javax.crypto.spec.SecretKeySpec")
        IvParameterSpec = jclass("javax.crypto.spec.IvParameterSpec")
        c = Cipher.getInstance("AES/CBC/NoPadding")
        c.init(2, SecretKeySpec(bytes(_IMG_KEY), "AES"),
               IvParameterSpec(bytes(_IMG_IV)))
        return bytes(c.doFinal(bytes(raw)))
    except Exception:
        pass
    try:
        return _aes_cbc_decrypt_py(raw, _IMG_KEY, _IMG_IV)
    except Exception:
        return None


# --- 本地图片代理 ---
_IMGPROXY = {"port": 0, "started": False, "lock": threading.Lock(),
             "cache": {}}


def _img_fetch_bytes(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": MIRRORS[0] + "/",
        "Accept-Encoding": "identity",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def _img_serve(url):
    """返回 (content_type, bytes)：明文图透传，密文解密后回吐"""
    cache = _IMGPROXY["cache"]
    hit = cache.get(url)
    if hit:
        return hit
    raw = _img_fetch_bytes(url)
    ct = _img_magic_type(raw)
    data = raw
    if not ct:
        dec = _img_decrypt(raw)
        if dec:
            dct = _img_magic_type(dec)
            if dct:
                ct, data = dct, dec
    if not ct:
        ct = "application/octet-stream"
    if len(cache) > 64:
        cache.clear()
    cache[url] = (ct, data)
    return ct, data


def _ensure_img_proxy():
    if _IMGPROXY["started"]:
        return _IMGPROXY["port"]
    with _IMGPROXY["lock"]:
        if _IMGPROXY["started"]:
            return _IMGPROXY["port"]
        try:
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

            class _ImgHandler(BaseHTTPRequestHandler):
                def _reply(self, with_body):
                    q = urllib.parse.urlparse(self.path)
                    if q.path != "/img":
                        self.send_response(404)
                        self.end_headers()
                        return
                    u = (urllib.parse.parse_qs(q.query).get("u") or [""])[0]
                    if not u:
                        self.send_response(400)
                        self.end_headers()
                        return
                    ct, data = _img_serve(u)
                    self.send_response(200)
                    self.send_header("Content-Type", ct)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.end_headers()
                    if with_body:
                        self.wfile.write(data)

                def do_GET(self):
                    try:
                        self._reply(True)
                    except Exception:
                        try:
                            self.send_response(502)
                            self.end_headers()
                        except Exception:
                            pass

                def do_HEAD(self):
                    try:
                        self._reply(False)
                    except Exception:
                        pass

                def log_message(self, *a):
                    pass

            srv = ThreadingHTTPServer(("127.0.0.1", 0), _ImgHandler)
            _IMGPROXY["port"] = srv.server_address[1]
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            _IMGPROXY["started"] = True
        except Exception:
            return 0
    return _IMGPROXY["port"]


def _pic_proxy(u):
    """封面地址改走本地代理解密；代理起不来时原样返回（不比原版更差）"""
    u = _s(u).strip()
    if not u or not (u.startswith("http://") or u.startswith("https://")):
        return u
    port = _ensure_img_proxy()
    if not port:
        return u
    return "http://127.0.0.1:%d/img?u=%s" % (port, urllib.parse.quote(u, safe=""))


# --------------------------------------------------------------------------
# 爬虫主体
# --------------------------------------------------------------------------

class Spider(BaseSpider):

    def __init__(self):
        self._host = MIRRORS[0]
        self._host_ok = False
        self._lock = threading.Lock()
        self._cache = {}
        self._cache_lock = threading.Lock()
        self._ext = {}
        self._epmax = 120
        self._epthread = 8
        self._noheader = False

    # ---------------------------------------------------------------- 基础
    def getName(self):
        return "黄果短剧"

    def init(self, extend=""):
        self._parse_ext(extend)
        try:
            _ensure_img_proxy()
        except Exception:
            pass
        return self

    def isVideoFormat(self, url):
        u = _s(url).lower()
        return bool(re.search(r'\.(m3u8|mp4|flv|mkv|ts)(\?|$)', u))

    def manualVideoCheck(self):
        return False

    def destroy(self):
        return None

    def _parse_ext(self, extend):
        ext = {}
        if isinstance(extend, dict):
            ext = extend
        elif _s(extend).strip():
            try:
                ext = json.loads(extend)
            except Exception:
                ext = {}
        if not isinstance(ext, dict):
            ext = {}
        self._ext = ext
        try:
            self._epmax = int(ext.get('epmax') or 120)
        except Exception:
            self._epmax = 120
        try:
            self._epthread = max(1, min(16, int(ext.get('epthread') or 8)))
        except Exception:
            self._epthread = 8
        self._noheader = _s(ext.get('noheader') or '').strip() in ('1', 'true', 'yes')
        h = _s(ext.get('host') or '').strip().rstrip('/')
        if h:
            if not h.startswith('http'):
                h = 'https://' + h
            self._host = h
            self._host_ok = True

    # ------------------------------------------------------------ 域名探活
    def _probe(self, base):
        try:
            req = urllib.request.Request(
                base + '/', headers={'User-Agent': UA, 'Accept': 'text/html,*/*'})
            with urllib.request.urlopen(req, timeout=10) as r:
                if r.status != 200:
                    return False
                head = r.read(4096).decode('utf-8', 'ignore')
                return ('hg-' in head) or ('huangguo' in head.lower()) or ('黄果' in head)
        except Exception:
            return False

    def _ensure_host(self, force=False):
        if self._host_ok and not force:
            return self._host
        with self._lock:
            if self._host_ok and not force:
                return self._host
            cands = [self._host] + [m for m in MIRRORS if m != self._host]
            for b in cands:
                if self._probe(b):
                    self._host = b
                    self._host_ok = True
                    return self._host
            self._host_ok = True   # 全挂也别卡死，照原 host 试
            return self._host

    def _headers(self, referer=None):
        h = {
            'User-Agent': UA,
            'Accept': 'text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        }
        if referer:
            h['Referer'] = referer
        return h

    # ---------------------------------------------------------------- 请求
    def _http(self, url, referer=None, use_cache=True, ttl=CACHE_TTL, timeout=None):
        if use_cache:
            with self._cache_lock:
                it = self._cache.get(url)
                if it and it[0] > time.time():
                    return it[1]

        last = None
        bases = [self._host] + [m for m in MIRRORS if m != self._host]
        for base in bases:
            u = url if url.startswith('http') else base + url
            for attempt in range(2):
                try:
                    req = urllib.request.Request(u, headers=self._headers(referer))
                    with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as r:
                        raw = r.read()
                        enc = (r.headers.get('Content-Encoding') or '').lower()
                        if 'gzip' in enc:
                            try:
                                raw = gzip.decompress(raw)
                            except Exception:
                                pass
                        elif 'deflate' in enc:
                            try:
                                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
                            except Exception:
                                try:
                                    raw = zlib.decompress(raw)
                                except Exception:
                                    pass
                        text = raw.decode('utf-8', 'ignore')
                    if use_cache and text:
                        with self._cache_lock:
                            self._cache[url] = (time.time() + ttl, text)
                    return text
                except urllib.error.HTTPError as e:
                    last = e
                    if e.code in (403, 503, 520, 521, 522, 523, 524):
                        time.sleep(0.3)
                        continue
                    break
                except Exception as e:
                    last = e
                    time.sleep(0.3)
            # 这个域名不行，换下一个
        if last:
            raise last
        return ""

    def _fetch(self, url, referer=None, use_cache=True, ttl=CACHE_TTL):
        return self._http(url, referer=referer, use_cache=use_cache, ttl=ttl)

    def _get_json(self, url, referer=None, ttl=PLAY_TTL):
        text = self._http(url, referer=referer or self._host + '/', use_cache=True,
                          ttl=ttl, timeout=15)
        text = _s(text).strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except Exception:
            i = text.find('{')
            if i >= 0:
                blk = _json_block(text, i)
                if blk:
                    try:
                        return json.loads(blk)
                    except Exception:
                        pass
            return {}

    # ------------------------------------------------------------ 列表解析
    def _abs(self, href):
        href = _s(href).strip()
        if not href:
            return ""
        if href.startswith('//'):
            return 'https:' + href
        if href.startswith('http'):
            return href
        return self._host + '/' + href.lstrip('/')

    def _cards(self, text, limit=0):
        """解析 hg-drama-card 卡片（首页/频道/搜索/标签/专题通用）"""
        out = []
        if not text:
            return out
        marks = [m.start() for m in re.finditer(r'<div class="hg-drama-card"', text)]
        seen = set()
        for idx, p in enumerate(marks):
            end = marks[idx + 1] if idx + 1 < len(marks) else min(len(text), p + 6000)
            blk = text[p:end]

            m = re.search(r'data-track-id="(\d+)"', blk)
            if not m:
                continue
            vid = m.group(1)
            if vid in seen:
                continue
            seen.add(vid)

            name = ""
            m = re.search(r'data-track-title="([^"]*)"', blk)
            if m:
                name = _htmlmod.unescape(m.group(1)).strip()
            if not name:
                m = re.search(r'<h3 class="hg-drama-card__title">\s*<a[^>]*>(.*?)<', blk, re.S)
                if m:
                    name = _txt(m.group(1))

            pic = ""
            m = re.search(r'data-src="([^"]+)"', blk)
            if m:
                pic = _naked(m.group(1))
            if not pic:
                m = re.search(r'<img[^>]+src="([^"]+)"', blk)
                if m and 'cover-placeholder' not in m.group(1):
                    pic = _naked(m.group(1))

            remarks = ""
            m = re.search(r'class="hg-drama-card__episode"[^>]*>(.*?)</span>', blk, re.S)
            if m:
                remarks = _txt(m.group(1))
            if remarks:
                remarks = re.sub(r'^\s*[\d天小时分秒前]+\s*', '', remarks).strip()

            score = ""
            m = re.search(r'class="hg-drama-card__score">([^<]*)<', blk)
            if m:
                score = _txt(m.group(1))

            desc = ""
            m = re.search(r'class="hg-drama-card__desc">(.*?)</p>', blk, re.S)
            if m:
                desc = _txt(m.group(1))

            tags = [_txt(x) for x in re.findall(r'class="hg-tag"[^>]*>(.*?)<', blk, re.S)]
            tags = [t for t in tags if t]

            cate = ""
            m = re.search(r'data-track-type-name="([^"]*)"', blk)
            if m:
                cate = _htmlmod.unescape(m.group(1))

            out.append({
                "vod_id": vid,
                "vod_name": name or vid,
                "vod_pic": _pic_proxy(pic),
                "vod_remarks": remarks or score,
                "vod_content": desc,
                "type_name": cate,
                "_tags": tags,
            })
            if limit and len(out) >= limit:
                break
        return out

    def _panel_cards(self, text, panel, limit=0):
        """从频道页里切出指定面板（latest / hot / original / random）"""
        if not text:
            return []
        pat = re.compile(r'<div class="hg-card-grid[^"]*"[^>]*data-channel-panel="%s"[^>]*>' % re.escape(panel))
        m = pat.search(text)
        if not m:
            return []
        seg = text[m.end():]
        nxt = re.search(r'<div class="hg-card-grid|<nav class="hg-pager|</section>', seg)
        if nxt:
            seg = seg[:nxt.start()]
        return self._cards(seg, limit)

    def _rank_cards(self, text, limit=0):
        """排行榜条目（hg-rank-item 结构）"""
        out = []
        if not text:
            return out
        marks = [m.start() for m in re.finditer(r'class="hg-rank-item"', text)]
        seen = set()
        for idx, p in enumerate(marks):
            end = marks[idx + 1] if idx + 1 < len(marks) else min(len(text), p + 4000)
            blk = text[p:end]
            m = re.search(r'data-track-id="(\d+)"', blk)
            if not m:
                m = re.search(r'href="/video/(\d+)/"', blk)
            if not m:
                continue
            vid = m.group(1)
            if vid in seen:
                continue
            seen.add(vid)
            name = ""
            m = re.search(r'data-track-title="([^"]*)"', blk)
            if m:
                name = _htmlmod.unescape(m.group(1)).strip()
            if not name:
                m = re.search(r'<h2[^>]*>(.*?)</h2>', blk, re.S)
                if m:
                    name = _txt(m.group(1))
            pic = ""
            m = re.search(r'data-src="([^"]+)"', blk)
            if m:
                pic = _naked(m.group(1))
            cate = ""
            m = re.search(r'data-track-type-name="([^"]*)"', blk)
            if m:
                cate = _htmlmod.unescape(m.group(1))
            metric = ""
            m = re.search(r'class="hg-rank-metric-value">([^<]*)<', blk)
            if m:
                metric = _txt(m.group(1))
            out.append({
                "vod_id": vid,
                "vod_name": name or vid,
                "vod_pic": _pic_proxy(pic),
                "vod_remarks": metric,
                "type_name": cate,
            })
            if limit and len(out) >= limit:
                break
        return out

    def _post_cards(self, text, limit=0):
        """吃瓜帖子卡片（hg-post-card）"""
        out = []
        if not text:
            return out
        marks = [m.start() for m in re.finditer(r'class="hg-post-card"', text)]
        seen = set()
        for idx, p in enumerate(marks):
            end = marks[idx + 1] if idx + 1 < len(marks) else min(len(text), p + 4000)
            blk = text[p:end]
            m = re.search(r'href="/archives/(\d+)/"', blk)
            if not m:
                continue
            pid = m.group(1)
            if pid in seen:
                continue
            seen.add(pid)
            name = ""
            m = re.search(r'<h3[^>]*>(.*?)</h3>', blk, re.S)
            if m:
                name = _txt(m.group(1))
            pic = ""
            m = re.search(r'data-src="([^"]+)"', blk)
            if m:
                pic = _naked(m.group(1))
            meta = _txt(re.sub(r'<[^>]+>', ' ', blk))
            cat = ""
            m = re.search(r'class="hg-post-card__cat">([^<]*)<', blk)
            if m:
                cat = _txt(m.group(1))
            date = ""
            m = re.search(r'class="hg-post-card__date">([^<]*)<', blk)
            if m:
                date = _txt(m.group(1))
            out.append({
                "vod_id": "post:" + pid,
                "vod_name": name or ("吃瓜帖 " + pid),
                "vod_pic": _pic_proxy(pic),
                "vod_remarks": (cat + (" · " + date if date else "")).strip(' ·'),
                "_meta": meta,
            })
            if limit and len(out) >= limit:
                break
        return out

    def _pagecount(self, text, base_path):
        """从分页条里抠出总页数"""
        if not text:
            return 1
        mx = 1
        pat = re.escape(base_path.rstrip('/')) + r'/(\d+)/?'
        for m in re.finditer(r'href="([^"]+)"', text):
            href = m.group(1)
            mm = re.match(pat + r'$', href)
            if mm:
                try:
                    mx = max(mx, int(mm.group(1)))
                except Exception:
                    pass
        m = re.search(r'第\s*\d+\s*/\s*(\d+)\s*页', text)
        if m:
            try:
                mx = max(mx, int(m.group(1)))
            except Exception:
                pass
        return mx

    # ------------------------------------------------------------ 分类构造
    def _topics(self):
        """专题列表（缓存的，失败就用兜底表）"""
        try:
            text = self._fetch('/topics/', ttl=CACHE_TTL)
        except Exception:
            text = ""
        out = []
        for m in re.finditer(r'<a class="hg-topic-card"[^>]*href="/topics/([a-z0-9\-]+)/"[^>]*>(.*?)</a>',
                             text or '', re.S):
            slug = m.group(1)
            body = m.group(2)
            title = ""
            mm = re.search(r'<h3[^>]*>(.*?)</h3>', body, re.S) or re.search(r'<p[^>]*>(.*?)</p>', body, re.S)
            if mm:
                title = _txt(mm.group(1))
            if not title:
                title = _txt(body)
                title = re.sub(r'^\d+部剧情\s*', '', title)
            out.append((slug, title.strip() or slug))
        if not out:
            out = list(TOPICS_FALLBACK)
        return out

    def _classes(self):
        ext = self._ext
        cats = [
            {"type_id": "home", "type_name": "🏠 首页精选"},
            {"type_id": "reco", "type_name": "🎬 热门推荐"},
        ]
        for slug, name in CHANNELS:
            cats.append({"type_id": "ch:" + slug, "type_name": "🎞 " + name})
        for k, name in RANKS:
            cats.append({"type_id": "rank:" + k, "type_name": "🏆 " + name})
        if _s(ext.get('ncg') or '').strip() not in ('1', 'true', 'yes'):
            for k, name in GOSSIPS:
                cats.append({"type_id": "cg:" + (k or "all"), "type_name": "🍉 吃瓜·" + name})
        if _s(ext.get('ntopics') or '').strip() not in ('1', 'true', 'yes'):
            for slug, name in self._topics():
                cats.append({"type_id": "topic:" + slug, "type_name": "📚 " + name})
        if _s(ext.get('ntags') or '').strip() not in ('1', 'true', 'yes'):
            for slug, name in TAGS:
                cats.append({"type_id": "tag:" + slug, "type_name": "🏷 " + name})
        return cats

    def _filters(self):
        out = []
        vals = [{"n": x["n"], "v": x["v"]} for x in SORT_ITEMS]
        for slug, name in CHANNELS:
            out.append({"key": "ch:" + slug, "name": "排序", "value": vals})
        return out

    # ------------------------------------------------------------ 首页
    def homeContent(self, filter):
        result = {"class": [], "filters": {}, "list": []}
        try:
            self._ensure_host()
            result["class"] = self._classes()
            if filter:
                f = {}
                for item in self._filters():
                    f[item["key"]] = [{"key": item["key"], "name": item["name"], "value": item["value"]}]
                result["filters"] = f
            try:
                text = self._fetch('/', ttl=CACHE_TTL)
                result["list"] = self._cards(text, 60)
            except Exception:
                pass
        except Exception:
            pass
        return result

    def homeVideoContent(self):
        try:
            self._ensure_host()
            text = self._fetch('/', ttl=CACHE_TTL)
            return {"list": self._cards(text, 60)}
        except Exception:
            return {"list": []}

    # ------------------------------------------------------------ 分类列表
    def categoryContent(self, tid, pg, filter, extend):
        out = {"list": [], "page": 1, "pagecount": 1, "limit": PAGE_SIZE, "total": 0}
        try:
            self._ensure_host()
            try:
                pg = max(1, int(pg or 1))
            except Exception:
                pg = 1
            tid = _s(tid)

            # 首页精选
            if tid == "home":
                text = self._fetch('/', ttl=CACHE_TTL)
                items = self._cards(text, 60)
                out.update({"list": items, "page": 1, "pagecount": 1,
                            "total": len(items), "limit": len(items) or PAGE_SIZE})
                return out

            # 热门推荐
            if tid == "reco":
                path = '/recommend/' if pg <= 1 else '/recommend/%d/' % pg
                text = self._fetch(path, ttl=CACHE_TTL)
                items = self._cards(text, PAGE_SIZE)
                pc = self._pagecount(text, '/recommend')
                out.update({"list": items, "page": pg, "pagecount": max(pc, pg),
                            "total": max(pc, pg) * PAGE_SIZE, "limit": PAGE_SIZE})
                return out

            # 频道（带 panels 排序）
            if tid.startswith("ch:"):
                slug = tid[3:]
                panel = "latest"
                ex = extend
                if not isinstance(ex, dict) and _s(ex).strip().startswith('{'):
                    try:
                        ex = json.loads(ex)
                    except Exception:
                        ex = {}
                if isinstance(ex, dict):
                    panel = _s(ex.get("ch:" + slug) or ex.get("sort") or "latest")
                if panel not in SORT_VALID:
                    panel = "latest"
                path = '/%s/' % slug if pg <= 1 else '/%s/%d/' % (slug, pg)
                text = self._fetch(path, ttl=CACHE_TTL)
                if panel == "latest":
                    items = self._panel_cards(text, "latest", PAGE_SIZE) or self._cards(text, PAGE_SIZE)
                    pc = self._pagecount(text, '/' + slug)
                    out.update({"list": items, "page": pg, "pagecount": max(pc, pg),
                                "total": max(pc, pg) * PAGE_SIZE, "limit": PAGE_SIZE})
                else:
                    items = self._panel_cards(text, panel, PAGE_SIZE)
                    out.update({"list": items, "page": 1, "pagecount": 1,
                                "total": len(items), "limit": len(items) or PAGE_SIZE})
                return out

            # 排行榜
            if tid.startswith("rank:"):
                key = tid[5:] or "hot"
                text = self._fetch('/ranks/%s/' % key, ttl=CACHE_TTL)
                items = self._rank_cards(text, 50)
                if not items:
                    items = self._cards(text, 50)
                out.update({"list": items, "page": 1, "pagecount": 1,
                            "total": len(items), "limit": len(items) or PAGE_SIZE})
                return out

            # 吃瓜
            if tid.startswith("cg:"):
                key = tid[3:]
                if key in ('', 'all'):
                    # 全部帖子的翻页是 /chigua/page/<n>/，跟分区不一样
                    path = '/chigua/' if pg <= 1 else '/chigua/page/%d/' % pg
                    pcbase = '/chigua/page'
                else:
                    base = '/chigua/' + key
                    path = base + '/' if pg <= 1 else base + '/%d/' % pg
                    pcbase = base
                text = self._fetch(path, ttl=CACHE_TTL)
                items = self._post_cards(text, 60)
                pc = self._pagecount(text, pcbase)
                out.update({"list": items, "page": pg, "pagecount": max(pc, pg),
                            "total": max(pc, pg) * 13, "limit": 13})
                return out

            # 专题
            if tid.startswith("topic:"):
                slug = tid[6:]
                path = '/topics/%s/' % slug if pg <= 1 else '/topics/%s/%d/' % (slug, pg)
                text = self._fetch(path, ttl=CACHE_TTL)
                items = self._cards(text, PAGE_SIZE)
                pc = self._pagecount(text, '/topics/' + slug)
                out.update({"list": items, "page": pg, "pagecount": max(pc, pg),
                            "total": max(pc, pg) * PAGE_SIZE, "limit": PAGE_SIZE})
                return out

            # 题材标签
            if tid.startswith("tag:"):
                slug = tid[4:]
                path = '/tag/%s/' % slug if pg <= 1 else '/tag/%s/page/%d/' % (slug, pg)
                text = self._fetch(path, ttl=CACHE_TTL)
                items = self._cards(text, PAGE_SIZE)
                pc = self._pagecount(text, '/tag/%s/page' % slug)
                if pc <= 1:
                    pc = self._pagecount(text, '/tag/%s' % slug)
                out.update({"list": items, "page": pg, "pagecount": max(pc, pg),
                            "total": max(pc, pg) * PAGE_SIZE, "limit": PAGE_SIZE})
                return out

            # 兜底：当频道处理
            path = '/%s/' % tid if pg <= 1 else '/%s/%d/' % (tid, pg)
            text = self._fetch(path, ttl=CACHE_TTL)
            items = self._cards(text, PAGE_SIZE)
            pc = self._pagecount(text, '/' + tid)
            out.update({"list": items, "page": pg, "pagecount": max(pc, pg),
                        "total": max(pc, pg) * PAGE_SIZE, "limit": PAGE_SIZE})
            return out
        except Exception:
            return out

    # ------------------------------------------------------------ 详情
    def _video_detail(self, vid):
        text = self._fetch('/video/%s/' % vid, ttl=CACHE_TTL)
        data = {}
        m = re.search(r'<script id="videoInitialData" type="application/json">(.*?)</script>',
                      text, re.S)
        if m:
            try:
                data = json.loads(m.group(1))
            except Exception:
                data = {}
        hist = {}
        m = re.search(r'data-history="([^"]*)"', text)
        if m:
            try:
                hist = json.loads(_htmlmod.unescape(m.group(1)))
            except Exception:
                hist = {}
        return text, data, hist

    def _play_url(self, vid, ep):
        """现签一条某集的播放地址"""
        j = self._get_json('/api/videos/%s/play?ep=%d' % (vid, int(ep)),
                           referer=self._host + '/video/%s/' % vid, ttl=PLAY_TTL)
        d = j.get('data') if isinstance(j, dict) else None
        if isinstance(d, dict):
            return _s(d.get('video_url') or d.get('videoUrl'))
        if isinstance(d, list) and d and isinstance(d[0], dict):
            return _s(d[0].get('video_url'))
        return ""

    def _episodes(self, vid, total, preset):
        """把 1..total 集的播放地址补齐（已有的直接用，缺的并发去签）"""
        urls = {}
        for k, v in (preset or {}).items():
            try:
                n = int(k)
            except Exception:
                continue
            if v:
                urls[n] = _s(v)
        if total <= 0:
            total = max(urls.keys()) if urls else 1
        total = min(total, self._epmax)
        need = [n for n in range(1, total + 1) if not urls.get(n)]
        if need:
            try:
                from concurrent.futures import ThreadPoolExecutor

                def one(n):
                    try:
                        return n, self._play_url(vid, n)
                    except Exception:
                        return n, ""

                with ThreadPoolExecutor(max_workers=self._epthread) as ex:
                    for n, u in ex.map(one, need):
                        if u:
                            urls[n] = u
            except Exception:
                for n in need:
                    try:
                        u = self._play_url(vid, n)
                        if u:
                            urls[n] = u
                    except Exception:
                        pass
        return total, urls

    def detailContent(self, ids):
        out = {"list": []}
        try:
            self._ensure_host()
            vid = ids[0] if isinstance(ids, (list, tuple)) and ids else ids
            vid = _s(vid).strip()
            if not vid:
                return out

            # 吃瓜帖子
            if vid.startswith("post:"):
                pid = vid.split(':', 1)[1]
                j = self._get_json('/api/posts/%s' % pid,
                                   referer=self._host + '/archives/%s/' % pid, ttl=CACHE_TTL)
                d = j.get('data') if isinstance(j, dict) else None
                if not isinstance(d, dict):
                    return out
                title = _s(d.get('title')) or ('吃瓜帖 ' + pid)
                content = _txt(d.get('content'))
                desc = _txt(d.get('desc'))
                cover = _naked(d.get('cover'))
                if not cover:
                    mm = re.search(r'data-src="([^"]+)"', _s(d.get('content')))
                    if mm:
                        cover = _naked(mm.group(1))
                author = ""
                a = d.get('author')
                if isinstance(a, dict):
                    author = _s(a.get('name'))
                tags = [_s(x) for x in (d.get('tags') or []) if _s(x)]
                play = ""
                vurl = _s(d.get('video_url'))
                if vurl:
                    play = "正片$hgp|%s" % pid
                item = {
                    "vod_id": vid,
                    "vod_name": title,
                    "vod_pic": _pic_proxy(cover),
                    "type_name": "黄果吃瓜",
                    "vod_year": _s(d.get('publish_time') or d.get('created_at'))[:4],
                    "vod_area": "",
                    "vod_remarks": (" · ".join(tags[:3]) if tags else "图文"),
                    "vod_actor": author,
                    "vod_director": "",
                    "vod_content": (desc + "\n" + content).strip(),
                    "vod_play_from": "黄果吃瓜" if play else "",
                    "vod_play_url": play,
                }
                out["list"].append(item)
                return out

            vid = re.sub(r'^v:', '', vid)
            text, data, hist = self._video_detail(vid)
            if not data:
                return out

            title = _s(data.get('title')) or vid
            cover = _naked(data.get('coverSrc') or data.get('posterSrc'))
            desc = _s(data.get('description'))
            tags = [_s(x) for x in (data.get('tags') or []) if _s(x)]
            author = _s(data.get('author'))
            pub = _s(data.get('time'))
            cate = "黄果短剧"
            bc = data.get('breadcrumb')
            if isinstance(bc, list) and bc and isinstance(bc[0], dict):
                cate = _s(bc[0].get('name')) or cate

            preset = data.get('epPlaySrcs') or {}
            if not isinstance(preset, dict):
                preset = {}
            if data.get('videoSrc'):
                preset.setdefault('1', data.get('videoSrc'))

            try:
                total = int(hist.get('total') or 0)
            except Exception:
                total = 0
            if total <= 0:
                total = len(preset) or 1

            total, urls = self._episodes(vid, total, preset)

            parts = []
            for n in range(1, total + 1):
                u = urls.get(n)
                if not u:
                    continue
                parts.append("第%02d集$hgv|%s|%d" % (n, vid, n))
            play_url = "#".join(parts)

            remarks = _s(hist.get('episode')) or ("全%d集" % total if total > 1 else "正片")
            item = {
                "vod_id": vid,
                "vod_name": title,
                "vod_pic": _pic_proxy(cover),
                "type_name": cate,
                "vod_year": pub[:4] if pub else "",
                "vod_area": "",
                "vod_remarks": remarks,
                "vod_actor": author,
                "vod_director": "",
                "vod_content": desc,
                "vod_play_from": "黄果短剧",
                "vod_play_url": play_url,
            }
            if tags:
                item["vod_content"] = (desc + "\n\n题材：" + " / ".join(tags)).strip()
            if not play_url:
                item["vod_play_from"] = ""
            out["list"].append(item)
            return out
        except Exception:
            return out

    # ------------------------------------------------------------ 搜索
    def searchContent(self, key, quick, pg="1"):
        out = {"list": [], "page": 1, "pagecount": 1, "limit": PAGE_SIZE, "total": 0}
        try:
            self._ensure_host()
            try:
                pg = max(1, int(pg or 1))
            except Exception:
                pg = 1
            kw = urllib.parse.quote(_s(key).strip())
            if not kw:
                return out
            base = '/search/video/' + kw
            path = base + '/' if pg <= 1 else base + '/%d/' % pg
            text = self._fetch(path, ttl=SEARCH_TTL)
            m = re.search(r'<div class="hg-search-results">(.*?)(?=<nav class="hg-pager"|</section>|$)',
                          text, re.S)
            seg = m.group(1) if m else text
            items = self._cards(seg, PAGE_SIZE)
            if not items:
                items = self._cards(text, PAGE_SIZE)
            pc = self._pagecount(text, base)
            out.update({"list": items, "page": pg, "pagecount": max(pc, pg),
                        "total": max(pc, pg) * PAGE_SIZE, "limit": PAGE_SIZE})
            return out
        except Exception:
            return out

    # ------------------------------------------------------------ 播放
    def playerContent(self, flag, id, vipFlags):
        res = {"parse": 0, "playUrl": "", "url": "", "header": ""}
        try:
            self._ensure_host()
            token = _s(id).strip()
            url = ""
            if token.startswith('hgv|'):
                parts = token.split('|')
                if len(parts) >= 3:
                    try:
                        url = self._play_url(parts[1], int(parts[2]))
                    except Exception:
                        url = ""
                if not url and len(parts) >= 3:
                    # 现签失败：退回详情页里那份（可能还有效）
                    try:
                        _t, data, _h = self._video_detail(parts[1])
                        preset = data.get('epPlaySrcs') or {}
                        url = _s(preset.get(parts[2])) or _s(data.get('videoSrc'))
                    except Exception:
                        url = ""
            elif token.startswith('hgp|'):
                pid = token.split('|', 1)[1]
                j = self._get_json('/api/posts/%s' % pid,
                                   referer=self._host + '/archives/%s/' % pid, ttl=60)
                d = j.get('data') if isinstance(j, dict) else None
                if isinstance(d, dict):
                    url = _s(d.get('video_url'))
            elif token.startswith('http'):
                url = token
            elif token.startswith('v:'):
                url = self._play_url(token[2:], 1)
            if not url:
                return res
            res["url"] = url
            return res
        except Exception:
            return res


# --------------------------------------------------------------------------
# 命令行自测
# --------------------------------------------------------------------------

def _selftest():
    sp = Spider().init("")
    fails = []

    def check(title, cond, extra=""):
        print(("  [OK]   " if cond else "  [FAIL] ") + title + ("  " + str(extra) if extra else ""))
        if not cond:
            fails.append(title)

    print("=" * 72)
    print("黄果短剧 TVBox 爬虫 · 自测")
    print("=" * 72)

    print("\n[1] 域名探活")
    host = sp._ensure_host()
    check("可用域名", bool(host), host)

    print("\n[2] 首页 / 分类表")
    home = sp.homeContent(True)
    check("首页分类数", len(home.get("class") or []) > 50, "%d 项" % len(home.get("class") or []))
    check("首页卡片", len(home.get("list") or []) > 5, "%d 条" % len(home.get("list") or []))
    check("筛选项", bool((home.get("filters") or {}).get("ch:ai-duanju")))

    print("\n[3] 频道列表（最新 / 翻页 / 热播面板）")
    c1 = sp.categoryContent("ch:ai-duanju", 1, True, {})
    c2 = sp.categoryContent("ch:ai-duanju", 2, True, {})
    check("AI短剧 P1", len(c1["list"]) >= 15, "%d 条 / 共 %d 页" % (len(c1["list"]), c1["pagecount"]))
    check("翻页换内容", bool(c1["list"] and c2["list"] and c1["list"][0]["vod_id"] != c2["list"][0]["vod_id"]),
          "P1=%s P2=%s" % (c1["list"][0]["vod_id"] if c1["list"] else "-",
                           c2["list"][0]["vod_id"] if c2["list"] else "-"))
    chot = sp.categoryContent("ch:ai-duanju", 1, True, {"ch:ai-duanju": "hot"})
    corg = sp.categoryContent("ch:ai-duanju", 1, True, '{"ch:ai-duanju":"original"}')
    check("热播面板", len(chot["list"]) >= 10, "%d 条" % len(chot["list"]))
    check("筛选真的换内容", bool(c1["list"] and chot["list"] and
                                c1["list"][0]["vod_id"] != chot["list"][0]["vod_id"]),
          "最新=%s 热播=%s 原创=%s" % (c1["list"][0]["vod_id"] if c1["list"] else "-",
                                     chot["list"][0]["vod_id"] if chot["list"] else "-",
                                     corg["list"][0]["vod_id"] if corg["list"] else "-"))
    cmj = sp.categoryContent("ch:ai-manju", 1, True, {})
    chl = sp.categoryContent("ch:ai-huanlian", 1, True, {})
    cmg = sp.categoryContent("ch:ai-mogai", 1, True, {})
    check("漫剧/换脸/魔改", min(len(cmj["list"]), len(chl["list"]), len(cmg["list"])) > 5,
          "%d/%d/%d" % (len(cmj["list"]), len(chl["list"]), len(cmg["list"])))

    print("\n[4] 热门推荐 / 排行榜 / 吃瓜 / 专题 / 标签")
    cr = sp.categoryContent("reco", 1, True, {})
    check("热门推荐", len(cr["list"]) > 10, "%d 条 / 共 %d 页" % (len(cr["list"]), cr["pagecount"]))
    rk = sp.categoryContent("rank:hot", 1, True, {})
    check("排行榜", len(rk["list"]) > 10, "%d 条" % len(rk["list"]))
    cg = sp.categoryContent("cg:all", 1, True, {})
    cg2 = sp.categoryContent("cg:all", 2, True, {})
    check("吃瓜列表", len(cg["list"]) > 5, "%d 条 / 共 %d 页" % (len(cg["list"]), cg["pagecount"]))
    check("吃瓜翻页", bool(cg["list"] and cg2["list"] and cg["list"][0]["vod_id"] != cg2["list"][0]["vod_id"]))
    cgrm = sp.categoryContent("cg:remen", 1, True, {})
    check("吃瓜·热门", len(cgrm["list"]) > 3, "%d 条" % len(cgrm["list"]))
    tp = sp._topics()
    check("专题表", len(tp) >= 5, "%d 个" % len(tp))
    if tp:
        ct = sp.categoryContent("topic:" + tp[0][0], 1, True, {})
        check("专题列表", len(ct["list"]) > 5, "%s → %d 条" % (tp[0][1][:12], len(ct["list"])))
    ctg = sp.categoryContent("tag:dushi", 1, True, {})
    ctg2 = sp.categoryContent("tag:dushi", 2, True, {})
    check("标签列表", len(ctg["list"]) > 5, "%d 条 / 共 %d 页" % (len(ctg["list"]), ctg["pagecount"]))
    check("标签翻页", bool(ctg["list"] and ctg2["list"] and ctg["list"][0]["vod_id"] != ctg2["list"][0]["vod_id"]))

    print("\n[5] 搜索")
    s1 = sp.searchContent("禁忌", False, "1")
    check("搜索结果", len(s1["list"]) > 0, "%d 条" % len(s1["list"]))
    check("搜索命中标题", any("禁忌" in x["vod_name"] for x in s1["list"]),
          s1["list"][0]["vod_name"][:20] if s1["list"] else "-")
    s2 = sp.searchContent("禁忌", False, "2")
    check("搜索翻页", bool(s1["list"] and s2["list"]))
    s3 = sp.searchContent("zzzqqqxxyy1234", False, "1")
    check("空关键词不炸", isinstance(s3.get("list"), list))

    print("\n[6] 详情 + 分集")
    vid = "6875"
    d = sp.detailContent([vid])
    check("详情取到", bool(d["list"]))
    if d["list"]:
        it = d["list"][0]
        eps = [x for x in (it["vod_play_url"] or "").split('#') if x]
        check("标题/封面", bool(it["vod_name"]) and bool(it["vod_pic"]),
              "%s | %s" % (it["vod_name"][:16], it["vod_pic"][:48]))
        check("分集数", len(eps) >= 6, "%d 集" % len(eps))
        check("集名带集号", eps and eps[0].startswith("第01集$"), eps[0][:40] if eps else "")
        multi = sp.detailContent(["12"])
        if multi["list"]:
            e2 = [x for x in (multi["list"][0]["vod_play_url"] or "").split('#') if x]
            check("21 集长剧", len(e2) >= 21, "%d 集" % len(e2))
        single = sp.detailContent(["4381"])
        if single["list"]:
            e3 = [x for x in (single["list"][0]["vod_play_url"] or "").split('#') if x]
            check("单集换脸", len(e3) >= 1, "%d 条" % len(e3))

    print("\n[7] 播放（现签 + 拉清单 + 拉分片）")
    if d["list"]:
        eps = [x for x in (d["list"][0]["vod_play_url"] or "").split('#') if x]
        if eps:
            token = eps[0].split('$', 1)[1]
            p = sp.playerContent("", token, "")
            check("播放地址签出", p["url"].startswith("http"), p["url"][:80])
            if p["url"]:
                try:
                    m3u8 = sp._http(p["url"], referer=_dummy_ref(sp), use_cache=False, timeout=15)
                except Exception:
                    m3u8 = ""
                check("m3u8 清单", m3u8.startswith("#EXTM3U"), "%d 字节" % len(m3u8))
                segs = [x for x in m3u8.splitlines() if x and not x.startswith('#')]
                check("分片数", len(segs) > 5, "%d 片" % len(segs))
                if segs:
                    try:
                        b = urllib.request.urlopen(urllib.request.Request(
                            segs[0], headers={'User-Agent': UA}), timeout=20).read()
                        check("分片真能拉", len(b) > 10000, "%d 字节" % len(b))
                    except Exception as e:
                        check("分片真能拉", False, e)
        # 末集
        p2 = sp.playerContent("", "hgv|6875|6", "")
        check("末集现签", p2["url"].startswith("http"), p2["url"][:70])

    print("\n[8] 吃瓜帖详情")
    pc = sp.categoryContent("cg:all", 1, True, {})
    if pc["list"]:
        pd = sp.detailContent([pc["list"][0]["vod_id"]])
        check("帖子详情", bool(pd["list"]), pd["list"][0]["vod_name"][:24] if pd["list"] else "-")
        if pd["list"] and pd["list"][0].get("vod_play_url"):
            tk = pd["list"][0]["vod_play_url"].split('$', 1)[1]
            pp = sp.playerContent("", tk, "")
            check("带片帖播放", pp["url"].startswith("http"), pp["url"][:60])

    print("\n" + "=" * 72)
    if fails:
        print("失败 %d 项：%s" % (len(fails), " / ".join(fails)))
    else:
        print("全部通过 ✔")
    print("=" * 72)
    return len(fails)


def _dummy_ref(sp):
    try:
        return sp._host + '/'
    except Exception:
        return None


if __name__ == '__main__':
    try:
        Spider._dummy_ref = staticmethod(_dummy_ref)
    except Exception:
        pass
    argv = sys.argv[1:]
    if argv:
        sp = Spider().init("")
        sp._ensure_host()
        cmd = argv[0]
        if cmd == 'play' and len(argv) > 1:
            vid = argv[1]
            final = int(argv[2]) if len(argv) > 2 else 0
            if final:
                u = sp._play_url(vid, final)
                print("第%d集：%s" % (final, u or "（签不出来）"))
            else:
                d = sp.detailContent([vid])
                if not d["list"]:
                    print("详情取不到")
                else:
                    it = d["list"][0]
                    print("%s | %s" % (it["vod_name"], it["vod_remarks"]))
                    for x in (it["vod_play_url"] or "").split('#'):
                        if not x:
                            continue
                        name, _, tok = x.partition('$')
                        p = sp.playerContent("", tok, "")
                        u = p.get("url") or ""
                        tag = "裸拉OK" if u else "失败"
                        size = ""
                        try:
                            t = sp._http(u, use_cache=False, timeout=15)
                            if t.startswith('#EXTM3U'):
                                size = "清单%d片" % len([y for y in t.splitlines() if y and not y.startswith('#')])
                            else:
                                tag = "清单异常"
                        except Exception as e:
                            tag = "拉不动"
                        print("  %s  %s  %s" % (name, tag, size))
        elif cmd == 'search' and len(argv) > 1:
            r = sp.searchContent(argv[1], False, "1")
            for x in r["list"]:
                print("%s\t%s\t%s" % (x["vod_id"], x["vod_name"][:40], x["vod_remarks"]))
            print("共 %d 页" % r["pagecount"])
        else:
            print("用法：python3 huangguo_tvbox.py [play <vid> [集号] | search <关键词> | selftest]")
    else:
        _selftest()
