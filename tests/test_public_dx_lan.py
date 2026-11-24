"""คนนอก รพ. ไม่เห็นจำนวนโรค 1–4 ราย (กลุ่มโรคหลัก / Readmission ตามกลุ่มโรค) · header จาก proxy ปลอมไม่ได้ ·
IP ภายในระบุช่วงเอง"""
import re

import pytest

import app as app_mod
import privacy
import queries

PUBLIC = {"REMOTE_ADDR": "8.8.8.8"}
LAN = {"REMOTE_ADDR": "192.168.1.5"}


def _data(d1="2026-02-01", d2="2026-02-28", ward="all"):
    return {"d1": d1, "d2": d2, "ward": ward,
            "profile": {"total": 60, "rights": [], "ages": [], "age_total": 60, "dx": [
                {"label": "Pneumonia", "code": "J189", "value": 20, "pct": 33.3, "bar": 100.0},
                {"label": "HIV disease", "code": "B24", "value": 2, "pct": 3.3, "bar": 10.0}]},
            "readmit": {"items": [{"label": "COPD", "code": "J449", "value": 6, "bar": 100.0},
                                  {"label": "Schizophrenia", "code": "F209", "value": 1, "bar": 16.7}],
                        "shown": 7, "more": False}}


@pytest.fixture
def built(monkeypatch, fake_db):
    real = queries.build                                              # ฐานปลอมคืนค่าว่าง — ได้โครงหน้าครบ
    monkeypatch.setattr(queries, "build", lambda d1, d2, ward: {**real(d1, d2, ward), **_data(d1, d2, ward)})


def test_public_json_hides_small_dx_counts(client, built):
    d = client.get("/api/data.json", environ_base=PUBLIC).get_json()
    dx = d["profile"]["dx"]
    assert dx[0]["value"] == 20 and dx[1]["value"] == "<5" and dx[1]["pct"] is None
    ra = d["readmit"]
    assert ra["items"][0]["value"] == 6 and ra["items"][1]["value"] == "<5"
    assert ra["shown"] is None                                       # 7 - 6 = 1 คำนวณย้อนได้
    assert not re.search(r"'value': [1-4][,}]", str(d))


def test_hidden_bars_do_not_reveal_value():
    items = [{"value": 20, "bar": 100.0}, {"value": 1, "bar": 5.0}, {"value": 4, "bar": 20.0}]
    out, hidden = privacy._hide_small(items)
    assert hidden and out[1]["bar"] == out[2]["bar"] == 25.0          # ยาวเท่ากันหมด (= ค่า 5)
    assert out[0]["bar"] == 100.0
    only_small, _ = privacy._hide_small([{"value": 3, "bar": 100.0}, {"value": 1, "bar": 33.3}])
    assert only_small[0]["bar"] == only_small[1]["bar"]


def test_lan_sees_exact_dx(client, built):
    d = client.get("/api/data.json", environ_base=LAN).get_json()
    assert d["profile"]["dx"][1]["value"] == 2 and d["readmit"]["shown"] == 7


def test_public_html_shows_lt5(client, built):
    html = client.get("/", environ_base=PUBLIC).get_data(as_text=True)
    assert "&lt;5 ราย" in html


def test_public_ctx_does_not_touch_cache():
    data = _data()
    out = privacy.public_ctx(data)
    assert out["profile"]["dx"][1]["value"] == "<5"
    assert data["profile"]["dx"][1]["value"] == 2 and data["readmit"]["items"][1]["value"] == 1


def test_min_cell_zero_disables(monkeypatch):
    monkeypatch.setattr(privacy, "MIN_CELL", 0)
    assert privacy.public_ctx(_data())["profile"]["dx"][1]["value"] == 2


# ------------------------------------------------------------------ header จาก proxy

def test_prefix_only_from_trusted_proxy(client, built):
    lan_peer = client.get("/", headers={"X-Forwarded-Prefix": "/evil"}, environ_base=LAN).get_data(as_text=True)
    assert "/evil/static" not in lan_peer
    local = client.get("/", headers={"X-Forwarded-Prefix": "/ipd"},
                       environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_data(as_text=True)
    assert "/ipd/static" in local


@pytest.mark.parametrize("prefix", ["//evil.example", "/ipd\"><script>", "ipd", "/ip d"])
def test_bad_prefix_ignored(client, built, prefix):
    html = client.get("/", headers={"X-Forwarded-Prefix": prefix},
                      environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_data(as_text=True)
    assert "evil.example" not in html and "<script>/static" not in html and 'href="ipd' not in html


def test_proto_only_http_or_https(client, built):
    r = client.get("/manifest.webmanifest", headers={"X-Forwarded-Proto": "javascript"},
                   environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200


# ------------------------------------------------------------------ ช่วง IP ภายใน

@pytest.mark.parametrize("ip, lan", [
    ("192.168.11.5", True), ("10.1.2.3", True), ("172.20.0.1", True), ("127.0.0.1", True),
    ("198.18.0.1", False), ("203.0.113.7", False), ("100.64.1.1", False), ("8.8.8.8", False),
    ("::ffff:192.168.1.1", True), ("::ffff:8.8.8.8", False)])
def test_from_lan_explicit_ranges(ip, lan):
    with app_mod.app.test_request_context("/", environ_base={"REMOTE_ADDR": ip}):
        assert app_mod._from_lan() is lan
