"""IPD Dashboard — แดชบอร์ดบริหารจัดการผู้ป่วยใน (HOSxP)

รันเอง:      python app.py            → http://127.0.0.1:5700/
production:  gunicorn -b 0.0.0.0:5700 app:app   (ออกผ่าน sub-path เช่น /ipd/)
"""

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

VERSION = "1.0.0"
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


def _params():
    """(d1, d2, ward) ที่ผ่านการตรวจแล้ว

    ค่าเหล่านี้ถูกต่อเข้า SQL ต่อ จึงรับเฉพาะวันที่จริง (YYYY-MM-DD) และรหัส ward ที่เป็นตัวอักษร/ตัวเลข
    ผิดรูปแบบ = ใช้ค่าเริ่มต้น (ต้นเดือน–วันนี้ / ทุก ward) ไม่ปล่อยข้อความดิบลงไปใน SQL
    """
    today = date.today()
    d1 = _parse_date(request.args.get("d1")) or today.replace(day=1)
    d2 = _parse_date(request.args.get("d2")) or today
    d2 = min(d2, today)
    d1 = min(d1, d2)
    if (d2 - d1).days >= MAX_DAYS:
        d1 = d2 - timedelta(days=MAX_DAYS - 1)
    ward = request.args.get("ward") or "all"
    if ward != "all" and not _WARD_RE.fullmatch(ward):
        ward = "all"
    return d1.isoformat(), d2.isoformat(), ward


@app.route("/")
def dashboard():
    d1, d2, ward = _params()
    return render_template("dashboard.html", d=queries.build(d1, d2, ward),
                           print_mode=request.args.get("print") == "1")


@app.route("/api/data.json")
def api_data():
    d1, d2, ward = _params()
    return jsonify(queries.build(d1, d2, ward))


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

    d1, d2, ward = _params()
    qs = urllib.parse.urlencode({"d1": d1, "d2": d2, "ward": ward, "print": "1"})
    url = f"http://127.0.0.1:{os.getenv('PORT', '5700')}/?{qs}"

    name = f"ipd_{d1}_{d2}{'' if ward == 'all' else '_ward' + ward}.{fmt}"
    blob = _export_cache.get(name, EXPORT_CACHE_TTL)
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
        _export_cache.set(name, blob)
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
