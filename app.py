"""IPD Dashboard — แดชบอร์ดบริหารจัดการผู้ป่วยใน (HOSxP)

รันเอง:      python app.py            → http://127.0.0.1:5700/
production:  gunicorn -b 0.0.0.0:5700 app:app   (ออกผ่าน sub-path เช่น /ipd/)
"""

import ipaddress
import os
import re
import tempfile
import threading
import time
import urllib.parse
from datetime import date, timedelta
from io import BytesIO

try:
    import fcntl                      # Linux/macOS — ล็อกไฟล์ข้ามโปรเซสของ gunicorn
except ImportError:                   # Windows
    fcntl = None

from flask import (Flask, jsonify, render_template, request, send_file,
                   url_for)

import db
import privacy
import queries
import render
from cache import TTLCache


class PrefixMiddleware:
    """รองรับการวางแอปไว้หลัง reverse proxy แบบ sub-path

    อ่าน X-Forwarded-Prefix มาตั้ง SCRIPT_NAME และ X-Forwarded-Proto มาตั้ง url_scheme
    ทำให้ url_for() สร้างลิงก์ /ipd/... ถูก และยังเข้าตรง :5700 บน LAN ได้เหมือนเดิม
    """

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        prefix = environ.get("HTTP_X_FORWARDED_PREFIX", "")
        if prefix:
            environ["SCRIPT_NAME"] = prefix.rstrip("/")
            path = environ.get("PATH_INFO", "")
            if path.startswith(prefix):
                environ["PATH_INFO"] = path[len(prefix):] or "/"
        proto = environ.get("HTTP_X_FORWARDED_PROTO")
        if proto:
            environ["wsgi.url_scheme"] = proto
        return self.wsgi_app(environ, start_response)


app = Flask(__name__)
app.wsgi_app = PrefixMiddleware(app.wsgi_app)

VERSION = "1.2.0"
ICON_VERSION = "1"
#: ผู้พัฒนา — แสดงท้ายหน้า แก้ทับได้ด้วย env IPD_DEV_CONTACT
DEV_CONTACT = "นายเผด็จชัย สิงหล โรงพยาบาลวังน้ำเขียว"

_HERE = os.path.dirname(os.path.abspath(__file__))
_hosp_cache = TTLCache(maxsize=1)


def hospital_name():
    """ชื่อโรงพยาบาล: env IPD_HOSPITAL_NAME > opdconfig.hospitalname ของ HOSxP > "โรงพยาบาล" """
    env = os.getenv("IPD_HOSPITAL_NAME")
    if env:
        return env
    hit = _hosp_cache.get("n", 3600)
    if hit:
        return hit
    try:
        name = (db.fetch_one("select hospitalname from opdconfig limit 1").get("hospitalname") or "").strip()
    except Exception:
        name = ""
    name = name or "โรงพยาบาล"
    _hosp_cache.set("n", name)
    return name


@app.context_processor
def _branding():
    """ชื่อ/โลโก้/ภาพพื้นหลังของแต่ละที่ติดตั้ง — ไม่ผูกกับโรงพยาบาลใดโรงพยาบาลหนึ่ง

    โลโก้: วาง static/img/hospital_logo.png (ไม่มี = ใช้โลโก้กลาง static/img/logo.png)
    ภาพพื้นหลังแถบหัว: วาง static/img/hero.jpg (ไม่มี = ใช้สีพื้นเรียบ)
    """
    img = lambda n: os.path.exists(os.path.join(_HERE, "static", "img", n))
    return {
        "hospital": hospital_name(),
        "hospital_en": os.getenv("IPD_HOSPITAL_NAME_EN", ""),
        "logo_url": url_for("static", filename="img/hospital_logo.png" if img("hospital_logo.png")
                            else "img/logo.png") + "?v=" + ICON_VERSION,
        "hero_url": url_for("static", filename="img/hero.jpg") if img("hero.jpg") else None,
        "version": VERSION,
        "fiscal_year": current_fiscal_year(),
        "contact": os.getenv("IPD_CONTACT", "ผู้ดูแลระบบสารสนเทศของโรงพยาบาล"),
        "dev_contact": os.getenv("IPD_DEV_CONTACT", DEV_CONTACT),
    }


@app.after_request
def _security_headers(resp):
    """ห้ามเบราว์เซอร์เดาชนิดไฟล์ และไม่ส่ง URL (ซึ่งมีช่วงวันที่/ward) ไปเว็บอื่น"""
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    return resp


#: ช่วงวันที่ยาวสุดที่ดึงได้ต่อครั้ง — กันการดึงหลายปีจนฐานข้อมูล HOSxP ช้าทั้งโรงพยาบาล
MAX_DAYS = int(os.getenv("IPD_MAX_DAYS", "366"))
_WARD_RE = re.compile(r"[0-9A-Za-z]{1,10}")


def _parse_date(v):
    try:
        return date.fromisoformat(v)
    except (TypeError, ValueError):
        return None


def current_fiscal_year(today=None):
    """ปีงบประมาณ (พ.ศ.) ของวันที่ — เริ่ม 1 ต.ค. เช่น 17 พ.ย. 2569 = ปีงบ 2570"""
    today = today or date.today()
    return today.year + 543 + (1 if today.month >= 10 else 0)


def _fiscal_year_arg(v, today):
    """fy จาก URL: รับเฉพาะ พ.ศ. 4 หลักที่ไม่เกินปีงบปัจจุบัน และย้อนได้ไม่เกิน 10 ปี"""
    try:
        fy = int(v)
    except (TypeError, ValueError):
        return None
    cur = current_fiscal_year(today)
    return fy if cur - 10 <= fy <= cur else None


def _params():
    """(d1, d2, ward) ที่ผ่านการตรวจแล้ว

    ค่าเหล่านี้ถูกต่อเข้า SQL ต่อ จึงรับเฉพาะวันที่จริง (YYYY-MM-DD) และรหัส ward ที่เป็นตัวอักษร/ตัวเลข
    ผิดรูปแบบ = ใช้ค่าเริ่มต้น (ต้นเดือน–วันนี้ / ทุก ward) ไม่ปล่อยข้อความดิบลงไปใน SQL
    """
    today = date.today()
    d1 = _parse_date(request.args.get("d1")) or today.replace(day=1)
    d2 = _parse_date(request.args.get("d2")) or today
    fy = _fiscal_year_arg(request.args.get("fy"), today)
    if fy:                                   # ปีงบประมาณ พ.ศ. = 1 ต.ค. ปีก่อน – 30 ก.ย.
        d1, d2 = date(fy - 544, 10, 1), date(fy - 543, 9, 30)
    d2 = min(d2, today)
    d1 = min(d1, d2)
    if (d2 - d1).days >= MAX_DAYS:
        d1 = d2 - timedelta(days=MAX_DAYS - 1)
    ward = request.args.get("ward") or "all"
    if ward != "all" and not _WARD_RE.fullmatch(ward):
        ward = "all"
    return d1.isoformat(), d2.isoformat(), ward


# ---------------------------------------------------------------- คนใน / คนนอก รพ. (privacy.py)

#: reverse proxy ที่เชื่อ X-Forwarded-For ได้ (Apache บนเครื่องเดียวกัน)
TRUSTED_PROXIES = {x.strip() for x in os.getenv("IPD_TRUSTED_PROXIES", "127.0.0.1,::1").split(",") if x.strip()}
_CF_DEFAULT = ("173.245.48.0/20,103.21.244.0/22,103.22.200.0/22,103.31.4.0/22,141.101.64.0/18,108.162.192.0/18,"
               "190.93.240.0/20,188.114.96.0/20,197.234.240.0/22,198.41.128.0/17,162.158.0.0/15,104.16.0.0/13,"
               "104.24.0.0/14,172.64.0.0/13,131.0.72.0/22,2400:cb00::/32,2606:4700::/32,2803:f800::/32,"
               "2405:b500::/32,2405:8100::/32,2a06:98c0::/29,2c0f:f248::/32")


def _nets(spec):
    out = []
    for x in spec.split(","):
        try:
            out.append(ipaddress.ip_network(x.strip(), strict=False))
        except ValueError:
            pass
    return out


CF_NETS = _nets(os.getenv("IPD_CF_RANGES", _CF_DEFAULT))
#: IP สาธารณะจริงของ รพ. (เข้าผ่าน Cloudflare) — ห้ามใส่ IP บ้าน · เครื่อง 192.168.x.x นับเป็นคนในอยู่แล้ว
LAN_NETS = _nets(os.getenv("IPD_LAN_NETS", ""))


def _is_cf(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(a in n for n in CF_NETS)


def _client_ip():
    """IP ผู้ใช้: ค่าท้ายสุดของ X-Forwarded-For (Apache ต่อท้ายเอง ปลอมไม่ได้) · ผ่าน Cloudflare จริงจึงเชื่อ CF-Connecting-IP"""
    peer = request.remote_addr
    if peer not in TRUSTED_PROXIES:
        return peer
    xff = [x.strip() for x in request.headers.get("X-Forwarded-For", "").split(",") if x.strip()]
    hop = xff[-1] if xff else peer
    cf = request.headers.get("CF-Connecting-IP", "").strip()
    return cf if cf and _is_cf(hop) else hop


def _from_lan():
    """เครื่องใน รพ. หรือไม่ — ผ่าน Cloudflare นับเฉพาะ IP ใน IPD_LAN_NETS"""
    try:
        ip = ipaddress.ip_address((_client_ip() or "").strip())
    except ValueError:
        return False
    if any(ip in n for n in LAN_NETS):
        return True
    if request.headers.get("CF-Connecting-IP", "").strip():
        return False
    return ip.is_private or ip.is_loopback


def _public_view():
    """คนนอก รพ. → ช่วงวันที่ปัดเป็นเดือนเต็ม และสถานะจำหน่ายช่องเล็กรวมเป็นช่องเดียว
    public=1 = บังคับโหมดสาธารณะ (ใช้ตอนส่งออกไฟล์ให้คนนอก เพราะ chromium เรียกหน้าเว็บจาก 127.0.0.1 ซึ่งนับเป็นคนใน)"""
    if not privacy.enabled():
        return False
    return request.args.get("public") == "1" or not _from_lan()


def _view_params():
    """(d1, d2, ward, public) ของหน้าแดชบอร์ด"""
    d1, d2, ward = _params()
    public = _public_view()
    if public:
        d1, d2 = privacy.month_window(d1, d2)
    return d1, d2, ward, public


def _view_data(d1, d2, ward, public):
    data = queries.build(d1, d2, ward)
    return privacy.public_ctx(data) if public else data


@app.route("/")
def dashboard():
    d1, d2, ward, public = _view_params()
    return render_template("dashboard.html", d=_view_data(d1, d2, ward, public), public_view=public,
                           print_mode=request.args.get("print") == "1")


@app.route("/api/data.json")
def api_data():
    d1, d2, ward, public = _view_params()
    return jsonify(_view_data(d1, d2, ward, public))


class _RenderLock:
    """เรนเดอร์ทีละงานทั้งเครื่อง — chromium กินแรมสูง

    gunicorn มีหลาย worker (คนละโปรเซส) threading.Lock กันได้แค่ในโปรเซสเดียว จึงล็อกไฟล์เพิ่มด้วย
    """

    def __init__(self, path):
        self._path = path
        self._thread = threading.Lock()
        self._fd = None

    def acquire(self, timeout):
        """timeout = 0 → ไม่รอเลย (ไม่ว่าง = คืน False ทันที)"""
        deadline = time.monotonic() + timeout
        got = self._thread.acquire(timeout=timeout) if timeout > 0 else self._thread.acquire(blocking=False)
        if not got:
            return False
        if fcntl is None:
            return True
        fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o600)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._fd = fd
                return True
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    self._thread.release()
                    return False
                time.sleep(0.5)

    def release(self):
        if self._fd is not None:
            fd, self._fd = self._fd, None
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        self._thread.release()


_render_lock = _RenderLock(os.path.join(tempfile.gettempdir(), "ipd_dashboard_render.lock"))

#: รอคิวส่งออกได้นานสุดกี่วินาที — ค่าเริ่มต้น 0 = ไม่รอ ตอบ "ไม่ว่าง" (503) ทันที
#: (รอนาน ๆ แล้วคนบนอินเทอร์เน็ตยิงพร้อมกันไม่กี่ครั้งก็กิน worker ของ gunicorn หมดจนเว็บค้าง)
EXPORT_WAIT = int(os.getenv("IPD_EXPORT_WAIT", "0"))
#: ไฟล์ที่ส่งออกแล้วเก็บไว้ให้คนถัดไปที่ขอชุดเดียวกัน — ไม่ต้องเปิด chromium ซ้ำ
EXPORT_CACHE_TTL = int(os.getenv("IPD_EXPORT_CACHE_TTL", "300"))
_export_cache = TTLCache(maxsize=8)
#: IPD_EXPORT=0 ปิดปุ่มส่งออกทั้งหมด (เช่นเครื่องที่ไม่มี chromium)
EXPORT_ENABLED = os.getenv("IPD_EXPORT", "1") != "0"

MIME = {"png": "image/png", "jpg": "image/jpeg", "pdf": "application/pdf"}


@app.context_processor
def _export_flag():
    return {"export_enabled": EXPORT_ENABLED}


@app.route("/export.<fmt>")
def export(fmt):
    """ส่งออกหน้าแดชบอร์ดเป็นไฟล์ PNG / JPG / PDF ด้วย chromium headless

    เรียกหน้าเดิมของตัวเองด้วย ?print=1 (ซ่อนแถบเครื่องมือ) แล้วถ่ายภาพ
    """
    if fmt not in MIME or not EXPORT_ENABLED:
        return {"error": "รองรับเฉพาะ png, jpg, pdf" if EXPORT_ENABLED else "ปิดการส่งออกไฟล์"}, 404

    d1, d2, ward, public = _view_params()
    qs = urllib.parse.urlencode({"d1": d1, "d2": d2, "ward": ward, "print": "1", **({"public": "1"} if public else {})})
    url = f"http://127.0.0.1:{os.getenv('PORT', '5700')}/?{qs}"

    name = f"ipd_{d1}_{d2}{'' if ward == 'all' else '_ward' + ward}.{fmt}"
    cache_key = name + ("|public" if public else "")            # ไฟล์ของคนนอกต้องไม่ได้ฉบับเต็มจาก cache
    blob = _export_cache.get(cache_key, EXPORT_CACHE_TTL)
    if blob is not None:
        return send_file(BytesIO(blob), mimetype=MIME[fmt], as_attachment=True, download_name=name)
    if not _render_lock.acquire(timeout=EXPORT_WAIT):
        return ({"error": "กำลังส่งออกไฟล์ของผู้ใช้อื่นอยู่ ลองใหม่ในอีกครู่"}, 503, {"Retry-After": "30"})
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, name)
            render.render_to(fmt, url, out)
            # อ่านใส่หน่วยความจำก่อนออกจาก with — send_file ยังถือ handle ค้างไว้
            # ทำให้ลบโฟลเดอร์ชั่วคราวไม่ได้ (PermissionError บน Windows)
            blob = open(out, "rb").read()
        _export_cache.set(cache_key, blob)
        return send_file(BytesIO(blob), mimetype=MIME[fmt],
                         as_attachment=True, download_name=name)
    except Exception as e:                      # chromium ไม่มี/ตาย/หมดเวลา
        app.logger.exception("export failed")
        return {"error": f"ส่งออกไม่สำเร็จ: {type(e).__name__}"}, 500
    finally:
        _render_lock.release()


@app.route("/manifest.webmanifest")
def manifest():
    """ให้เพิ่มลงหน้าจอโฮมมือถือแล้วเปิดเต็มจอพร้อมไอคอนแอป

    ใช้ url_for ทุกเส้นทาง เพื่อให้ได้ /ipd/... ถูกต้องเมื่ออยู่หลัง reverse proxy
    """
    ic = lambda f: url_for("static", filename="icon/" + f) + "?v=" + ICON_VERSION
    resp = jsonify({
        "name": "IPD Dashboard — " + hospital_name(),
        "short_name": "IPD Dashboard",
        "description": "แดชบอร์ดบริหารจัดการผู้ป่วยใน",
        "start_url": url_for("dashboard"),
        "scope": url_for("dashboard"),
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#efe9ff",
        "theme_color": "#5b3fd6",
        "icons": [
            {"src": ic("icon-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": ic("icon-512.png"), "sizes": "512x512", "type": "image/png"},
            {"src": ic("icon-192-maskable.png"), "sizes": "192x192",
             "type": "image/png", "purpose": "maskable"},
            {"src": ic("icon-512-maskable.png"), "sizes": "512x512",
             "type": "image/png", "purpose": "maskable"},
        ],
    })
    resp.mimetype = "application/manifest+json"
    return resp


@app.route("/healthz")
def healthz():
    return {"status": "ok"}


if __name__ == "__main__":
    # debug ปิดเป็นค่าเริ่มต้น — ตัว debugger ของ Werkzeug สั่งรันโค้ดบนเครื่องได้
    # เปิดด้วย FLASK_DEBUG=1 เฉพาะตอนพัฒนา และจะฟังแค่ 127.0.0.1 (เครื่องอื่นใน LAN เข้าไม่ได้)
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="127.0.0.1" if debug else os.getenv("IPD_HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "5700")), debug=debug)
