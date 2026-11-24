"""ป้องกันการย้อนหาตัวบุคคลจากตัวเลขรวมในหน้าสาธารณะ (คนนอก รพ.) — แบบเดียวกับ anc / lr dashboard

ปัญหา: หน้าแดชบอร์ดเลือกช่วงวันที่ได้แคบถึง 1 วันและกรองราย ward ได้ — ช่อง "สถานะการจำหน่าย" แสดงจำนวนตามชื่อสถานะ
ใน HOSxP ซึ่งรวมสถานะเสียชีวิต จึงเห็นว่า ward ไหนมีผู้ป่วยเสียชีวิตวันไหน

มาตรการ (เฉพาะคนนอก รพ. — เครื่องใน รพ. / IPD_LAN_NETS เห็นตัวเลขเต็มเหมือนเดิม):
1. ช่วงวันที่ปัดเป็นเดือนเต็ม: วันเริ่ม = วันที่ 1 · วันสิ้นสุด = สิ้นเดือน (เดือนปัจจุบันถึงวันนี้)
2. สถานะการจำหน่ายที่มี 1 ถึง MIN_CELL-1 ราย รวมเป็นช่องเดียว "สถานะอื่น (น้อยกว่า 5 ต่อสถานะ)" — ยอดรวมเท่าเดิม
3. "กลุ่มโรคหลัก (Top 5)" และ "Readmission ตามกลุ่มโรค" ที่มี 1 ถึง MIN_CELL-1 ราย แสดงเป็น "<5"
   (เลือก ward เล็กรายเดือนได้ — โรคอ่อนไหว เช่น HIV / จิตเวช 1–2 รายย้อนหาตัวคนได้) · ซ่อน % และยอดรวมที่ใช้คำนวณย้อนได้

IPD_PUBLIC_MIN_CELL = ขนาดช่องขั้นต่ำ (ค่าเริ่มต้น 5) · 0 = ปิดมาตรการทั้งหมด (ไม่แนะนำ)
"""

import calendar
import copy
import os
from datetime import date

MIN_CELL = max(int(os.getenv("IPD_PUBLIC_MIN_CELL", "5") or 0), 0)
OTHER_COLOR = "#94a3b8"


def enabled():
    return MIN_CELL > 0


def month_window(d1, d2, today=None):
    """(d1, d2) แบบ ISO string → ปัดเป็นเดือนเต็ม · วันสิ้นสุดไม่เกินวันนี้"""
    today = today or date.today()
    a, b = date.fromisoformat(d1), date.fromisoformat(d2)
    end = b.replace(day=calendar.monthrange(b.year, b.month)[1])
    return a.replace(day=1).isoformat(), min(end, today).isoformat()


def _pct(v, total):
    return round(v * 100.0 / total, 1) if total else 0


def _hide_small(items):
    """สำเนารายการที่ค่า 1..MIN_CELL-1 เป็น "<MIN_CELL" · แท่งกราฟของช่องที่ซ่อนยาวเท่ากันหมด (ยาวเท่าค่า MIN_CELL)
    ไม่งั้นความยาวแท่งบอกค่าจริงได้ · คืน (items, มีช่องที่ซ่อนไหม)"""
    shown = [i.get("value") or 0 for i in items if not 0 < (i.get("value") or 0) < MIN_CELL]
    top = max(shown + [MIN_CELL])
    out, hidden = [], False
    for it in items:
        v = it.get("value") or 0
        if 0 < v < MIN_CELL:
            hidden = True
            it = dict(it, value=f"<{MIN_CELL}", pct=None, bar=_pct(MIN_CELL, top))
        elif "bar" in it:
            it = dict(it, bar=_pct(v, top))
        out.append(it)
    return out, hidden


def public_ctx(ctx):
    """สำเนาของข้อมูลหน้า (ต้นฉบับอยู่ใน cache ที่ใช้ร่วมกับเครื่องใน รพ. — ห้ามแก้) ที่รวมสถานะจำหน่ายช่องเล็กแล้ว"""
    if not enabled():
        return ctx
    c = copy.copy(ctx)
    c["public_privacy"] = True
    dch = ctx.get("discharge")
    if isinstance(dch, dict) and dch.get("items"):
        keep, small = [], 0
        for it in dch["items"]:
            v = it.get("value") or 0
            if 0 < v < MIN_CELL:
                small += v
            else:
                keep.append(it)
        if small:
            total = dch.get("total") or sum(i.get("value") or 0 for i in dch["items"])
            keep.append({"label": f"สถานะอื่น (น้อยกว่า {MIN_CELL} ต่อสถานะ)", "value": small,
                         "pct": _pct(small, total), "color": OTHER_COLOR})
        c["discharge"] = dict(dch, items=keep)
    prof = ctx.get("profile")
    if isinstance(prof, dict) and prof.get("dx"):
        c["profile"] = dict(prof, dx=_hide_small(prof["dx"])[0])
    ra = ctx.get("readmit")
    if isinstance(ra, dict) and ra.get("items"):
        items, hidden = _hide_small(ra["items"])
        c["readmit"] = dict(ra, items=items, shown=None if hidden else ra.get("shown"))
    return c
