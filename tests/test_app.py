"""ความปลอดภัยและพฤติกรรมหลักของ IPD Dashboard"""

import json
import time
from datetime import date, timedelta

import pytest

import app as app_mod
import render


# ------------------------------------------------------------ พารามิเตอร์ → SQL

@pytest.mark.parametrize("ward", [
    "1' or '1'='1", "x'; drop table ipt; --", "01' union select 1,2,3 -- ", "a b", "0" * 11,
])
def test_ward_injection_never_reaches_sql(client, fake_db, ward):
    r = client.get("/", query_string={"ward": ward})
    assert r.status_code == 200
    assert fake_db, "ต้องมีการยิง SQL"
    for sql in fake_db:
        assert "drop table" not in sql and "union select" not in sql and "'1'='1" not in sql


@pytest.mark.parametrize("d", ["x", "2026-13-45", "2026-01-01' or 1=1 --", "01/10/2026", ""])
def test_bad_dates_fall_back_to_default(client, fake_db, d):
    assert client.get("/", query_string={"d1": d, "d2": d}).status_code == 200
    assert client.get("/api/data.json", query_string={"d1": d}).status_code == 200
    for sql in fake_db:
        assert "or 1=1" not in sql


def test_valid_ward_is_used(client, fake_db):
    client.get("/", query_string={"ward": "01"})
    assert any("ward='01'" in sql for sql in fake_db)


def test_future_date_is_clamped_to_today(client):
    with app_mod.app.test_request_context("/?d1=2999-01-01&d2=2999-12-31"):
        d1, d2, _ = app_mod._params()
    assert d2 == date.today().isoformat() and d1 <= d2


def test_range_is_limited(client):
    with app_mod.app.test_request_context("/?d1=2000-01-01&d2=2099-01-01"):
        d1, d2, _ = app_mod._params()
    assert (date.fromisoformat(d2) - date.fromisoformat(d1)).days == app_mod.MAX_DAYS - 1


def test_reversed_dates_are_swapped_safely(client):
    a, b = (date.today() - timedelta(days=5)).isoformat(), (date.today() - timedelta(days=9)).isoformat()
    with app_mod.app.test_request_context(f"/?d1={a}&d2={b}"):
        d1, d2, _ = app_mod._params()
    assert d1 <= d2


def test_queries_escape_ward_even_if_called_directly():
    import queries
    cond = queries._ward_cond("a'b")
    assert "''" in cond and "a'b" not in cond.replace("''", "")


# ------------------------------------------------------------ header / ข้อมูลส่วนบุคคล

def test_security_headers(client):
    r = client.get("/")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "same-origin"


def test_api_has_no_patient_identifiers(client):
    """ผลที่ออกเว็บเป็นตัวเลขรวม — ไม่มีชื่อ/HN/AN เป็นคีย์ไหนเลย"""
    data = json.dumps(client.get("/api/data.json").get_json())
    for bad in ('"hn"', '"an"', '"fname"', '"lname"', '"pname"', '"cid"'):
        assert bad not in data


def test_hospital_name_comes_from_env(client):
    assert "โรงพยาบาลทดสอบ" in client.get("/").get_data(as_text=True)


def test_manifest_and_health(client):
    r = client.get("/manifest.webmanifest")
    assert r.mimetype == "application/manifest+json" and r.get_json()["icons"]
    assert client.get("/healthz").get_json() == {"status": "ok"}


def test_prefix_middleware_builds_subpath_links(client):
    html = client.get("/", headers={"X-Forwarded-Prefix": "/ipd"}).get_data(as_text=True)
    assert 'href="/ipd/static/style.css"' in html


# ------------------------------------------------------------ ส่งออกไฟล์

def test_export_busy_answers_immediately(client, monkeypatch):
    """งานส่งออกค้างอยู่ — คำขอใหม่ต้องได้ 503 ทันที ไม่นั่งรอกิน worker"""
    monkeypatch.setattr(app_mod, "EXPORT_WAIT", 0)
    assert app_mod._render_lock.acquire(timeout=1)
    try:
        t = time.monotonic()
        r = client.get("/export.png")
        assert r.status_code == 503 and r.headers["Retry-After"]
        assert time.monotonic() - t < 1
    finally:
        app_mod._render_lock.release()


def test_export_reuses_recent_file(client, monkeypatch):
    calls = []

    def fake_render(fmt, url, out):
        calls.append(url)
        open(out, "wb").write(b"PNG")

    monkeypatch.setattr(app_mod.render, "render_to", fake_render)
    for _ in range(3):
        r = client.get("/export.png?d1=2026-10-01&d2=2026-10-15")
        assert r.status_code == 200 and r.data == b"PNG"
    assert len(calls) == 1
    client.get("/export.png?d1=2026-10-02&d2=2026-10-15")          # ช่วงอื่น = เรนเดอร์ใหม่
    assert len(calls) == 2


def test_export_only_calls_itself(client, monkeypatch):
    """chromium ถูกสั่งให้เปิดแค่ 127.0.0.1 — ไม่รับ URL จากผู้ใช้"""
    seen = []
    monkeypatch.setattr(app_mod.render, "render_to", lambda f, url, out: (seen.append(url), open(out, "wb").write(b"x")))
    client.get("/export.png?url=http://evil.example/&d1=2026-10-01&d2=2026-10-02")
    assert seen and all(u.startswith("http://127.0.0.1:") for u in seen)
    assert "evil" not in seen[0]


def test_export_rejects_unknown_format(client):
    assert client.get("/export.exe").status_code == 404


def test_export_can_be_disabled(client, monkeypatch):
    monkeypatch.setattr(app_mod, "EXPORT_ENABLED", False)
    assert client.get("/export.png").status_code == 404


def test_chromium_sandbox_policy(monkeypatch):
    monkeypatch.setenv("IPD_CHROME_NO_SANDBOX", "1")
    assert render._no_sandbox() is True
    monkeypatch.delenv("IPD_CHROME_NO_SANDBOX")
    monkeypatch.setattr(render.os, "geteuid", lambda: 1000, raising=False)
    assert render._no_sandbox() is False
    monkeypatch.setattr(render.os, "geteuid", lambda: 0, raising=False)
    assert render._no_sandbox() is True


def test_debug_is_off_by_default():
    src = open(app_mod.__file__, encoding="utf-8").read()
    assert 'os.getenv("FLASK_DEBUG", "0") == "1"' in src and "debug=True" not in src
