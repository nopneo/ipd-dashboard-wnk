"""ตั้งค่าร่วมของ test — ไม่ต่อฐานข้อมูลจริง (แทน fetch ด้วยตัวปลอมที่จดคำสั่ง SQL ไว้)"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("IPD_HOSPITAL_NAME", "โรงพยาบาลทดสอบ")

import app as app_mod  # noqa: E402
import queries  # noqa: E402


@pytest.fixture
def fake_db(monkeypatch):
    """แทน fetch ของ queries: คืนผลว่างและจด SQL ทุกคำสั่งให้ test ตรวจ"""
    calls = []

    def fetch(sql):
        calls.append(sql)
        return []

    def fetch_one(sql):
        calls.append(sql)
        return {}

    monkeypatch.setattr(queries, "fetch", fetch)
    monkeypatch.setattr(queries, "fetch_one", fetch_one)
    monkeypatch.setattr(queries, "CACHE_TTL", 0)
    return calls


@pytest.fixture
def client(fake_db):
    app_mod._export_cache._data.clear()
    app_mod.app.config["TESTING"] = True
    return app_mod.app.test_client()
