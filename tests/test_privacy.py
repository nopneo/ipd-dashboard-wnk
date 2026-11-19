"""คนนอก รพ. ต้องย้อนหาผู้เสียชีวิตราย ward รายวันจากช่อง "สถานะการจำหน่าย" ไม่ได้"""
from datetime import date

import pytest

import app as app_mod
import privacy
import queries

PUBLIC = {"REMOTE_ADDR": "8.8.8.8"}
LAN = {"REMOTE_ADDR": "192.168.1.5"}


@pytest.fixture
def seen(monkeypatch, fake_db):
    """จดช่วงที่ build ถูกเรียก และคืนสถานะจำหน่ายที่มี 'ตาย 1'"""
    calls = []

    def build(d1, d2, ward):
        calls.append((d1, d2, ward))
        return {"d1": d1, "d2": d2, "ward": ward, "discharge": {
            "total": 31, "items": [{"label": "หาย/ดีขึ้น", "value": 30, "pct": 96.8, "color": "#22a06b"},
                                   {"label": "ตาย", "value": 1, "pct": 3.2, "color": "#e5484d"}]}}
    monkeypatch.setattr(queries, "build", build)
    return calls


def test_lan_keeps_exact_single_day(client, seen):
    r = client.get("/api/data.json?d1=2026-02-03&d2=2026-02-03&ward=01", environ_base=LAN)
    assert seen[-1] == ("2026-02-03", "2026-02-03", "01")
    assert any(i["label"] == "ตาย" and i["value"] == 1 for i in r.get_json()["discharge"]["items"])


def test_public_window_snapped_and_small_status_merged(client, seen):
    r = client.get("/api/data.json?d1=2026-02-03&d2=2026-02-03&ward=01", environ_base=PUBLIC)
    assert seen[-1] == ("2026-02-01", "2026-02-28", "01")
    items = r.get_json()["discharge"]["items"]
    assert all(i["label"] != "ตาย" for i in items)
    assert sum(i["value"] for i in items) == 31                          # ยอดรวมเท่าเดิม
    assert any(i["label"].startswith("สถานะอื่น") and i["value"] == 1 for i in items)


def test_cloudflare_from_non_hospital_ip_is_public(client, seen, monkeypatch):
    monkeypatch.setattr(app_mod, "LAN_NETS", app_mod._nets("183.88.214.202"))
    h = {"X-Forwarded-For": "1.2.3.4, 162.158.1.1", "CF-Connecting-IP": "1.2.3.4"}
    client.get("/api/data.json?d1=2026-02-03&d2=2026-02-03", headers=h, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert seen[-1][:2] == ("2026-02-01", "2026-02-28")
    h = {"X-Forwarded-For": "183.88.214.202, 162.158.1.1", "CF-Connecting-IP": "183.88.214.202"}
    client.get("/api/data.json?d1=2026-02-03&d2=2026-02-03", headers=h, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert seen[-1][:2] == ("2026-02-03", "2026-02-03")                 # IP จริงของ รพ. ผ่าน Cloudflare = คนใน


def test_spoofed_cf_header_from_lan_peer_is_not_trusted(client, seen):
    # เครื่องใน LAN ส่ง CF-Connecting-IP ปลอมมาตรง ๆ → ไม่ได้มาจาก Cloudflare จริง → ไม่เชื่อ และนับเป็นคนนอก (fail closed)
    client.get("/api/data.json?d1=2026-02-03&d2=2026-02-03", headers={"CF-Connecting-IP": "183.88.214.202"},
               environ_base={"REMOTE_ADDR": "192.168.1.5"})
    assert seen[-1][:2] == ("2026-02-01", "2026-02-28")


def test_public_html_notice(client):
    pub = client.get("/?d1=2026-02-03&d2=2026-02-03", environ_base=PUBLIC).get_data(as_text=True)
    lan = client.get("/?d1=2026-02-03&d2=2026-02-03", environ_base=LAN).get_data(as_text=True)
    assert "ข้อมูลสาธารณะ" in pub and "ข้อมูลสาธารณะ" not in lan


def test_public_ctx_does_not_mutate_cache():
    ctx = {"discharge": {"total": 3, "items": [{"label": "ตาย", "value": 3, "pct": 100, "color": "x"}]}}
    out = privacy.public_ctx(ctx)
    assert out["discharge"]["items"][0]["label"].startswith("สถานะอื่น")
    assert ctx["discharge"]["items"][0]["label"] == "ตาย"


def test_export_public_flag_and_separate_cache(client, seen, monkeypatch):
    urls = []

    def fake(fmt, url, out):
        urls.append(url)
        open(out, "wb").write(b"x")
    monkeypatch.setattr(app_mod.render, "render_to", fake)
    monkeypatch.setattr(app_mod, "EXPORT_ENABLED", True)
    assert client.get("/export.png?d1=2026-02-01&d2=2026-02-28", environ_base=LAN).status_code == 200
    assert client.get("/export.png?d1=2026-02-01&d2=2026-02-28", environ_base=PUBLIC).status_code == 200
    assert len(urls) == 2 and "public=1" not in urls[0] and "public=1" in urls[1]


def test_month_window():
    assert privacy.month_window("2026-02-03", "2026-02-03", today=date(2026, 6, 1)) == ("2026-02-01", "2026-02-28")
    assert privacy.month_window("2026-06-02", "2026-06-05", today=date(2026, 6, 9)) == ("2026-06-01", "2026-06-09")


def test_disabled_with_zero(client, seen, monkeypatch):
    monkeypatch.setattr(privacy, "MIN_CELL", 0)
    client.get("/api/data.json?d1=2026-02-03&d2=2026-02-03", environ_base=PUBLIC)
    assert seen[-1][:2] == ("2026-02-03", "2026-02-03")
