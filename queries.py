"""รวม SQL ทุกบล็อกของ IPD Dashboard (อ้างอิงสคีมา HOSxP)

ทุกฟังก์ชันคืน dict/list ที่พร้อมส่งเข้า template ตรง ๆ
ตัวเลขทุกตัวมาจากฐานข้อมูลจริง ไม่มีค่าสมมติ
"""

import os
import re
from datetime import date, timedelta

from db import fetch, fetch_one, num

# ---------------------------------------------------------------- helpers

#: ward ที่ใช้รับผู้ป่วยในจริงและมีจำนวนเตียงกำหนดไว้
ACTIVE_WARD_COND = "w.ward_active='Y' and ifnull(w.bedcount,0)>0"

#: เตียงตามกรอบของโรงพยาบาล ใช้เป็นตัวหารอัตราครองเตียงภาพรวม
#: ไม่ตั้ง (0) = รวม ward.bedcount ของทุกตึกที่เปิดใช้ · ตั้ง env IPD_TOTAL_BEDS เมื่อกรอบจริงไม่เท่าผลรวม
#: (เช่น รพ.วังน้ำเขียว bedcount รวม 56 แต่กรอบจริงคือ 40 เตียง)
TOTAL_BEDS = int(os.getenv("IPD_TOTAL_BEDS", "0"))


def _q(v):
    """escape ค่าก่อนต่อเข้า SQL — app.py ตรวจรูปแบบมาแล้ว นี่คือชั้นสำรองถ้ามีคนเรียก queries ตรง ๆ"""
    return str(v).replace("\\", "\\\\").replace("'", "''")


def _ward_cond(ward, alias="i"):
    """เงื่อนไขกรอง ward สำหรับต่อท้าย where (ว่าง = ทุก ward)"""
    if not ward or ward == "all":
        return ""
    return f" and {alias}.ward='{_q(ward)}' "


def _pct(part, whole, digits=1):
    return round(part * 100.0 / whole, digits) if whole else 0.0


def _delta(cur, prev):
    """% เปลี่ยนแปลงเทียบช่วงก่อนหน้า"""
    if not prev:
        return None
    return round((cur - prev) * 100.0 / prev, 1)


# ---------------------------------------------------------------- 1. KPI

def kpi_summary(d1, d2, ward):
    wc = _ward_cond(ward)
    span = (date.fromisoformat(d2) - date.fromisoformat(d1)).days + 1
    p2 = (date.fromisoformat(d1) - timedelta(days=1)).isoformat()
    p1 = (date.fromisoformat(p2) - timedelta(days=span - 1)).isoformat()

    cur_in = num(fetch_one(
        f"select count(*) c from ipt i where i.dchdate is null {wc}").get("c"))

    adm = num(fetch_one(
        f"select count(*) c from ipt i where i.regdate between '{d1}' and '{d2}' {wc}").get("c"))
    adm_prev = num(fetch_one(
        f"select count(*) c from ipt i where i.regdate between '{p1}' and '{p2}' {wc}").get("c"))

    dch = num(fetch_one(
        f"select count(*) c from ipt i where i.dchdate between '{d1}' and '{d2}' {wc}").get("c"))
    dch_prev = num(fetch_one(
        f"select count(*) c from ipt i where i.dchdate between '{p1}' and '{p2}' {wc}").get("c"))

    if ward and ward != "all":
        total_beds = num(fetch_one(
            f"select w.bedcount c from ward w where w.ward='{_q(ward)}'").get("c"))
    else:
        total_beds = TOTAL_BEDS or num(fetch_one(
            f"select sum(w.bedcount) c from ward w where {ACTIVE_WARD_COND}").get("c"))

    los = fetch_one(
        "select avg(datediff(i.dchdate,i.regdate)) a from ipt i "
        f"where i.dchdate between '{d1}' and '{d2}' and i.regdate is not null {wc}")
    los_prev = fetch_one(
        "select avg(datediff(i.dchdate,i.regdate)) a from ipt i "
        f"where i.dchdate between '{p1}' and '{p2}' and i.regdate is not null {wc}")
    alos = round(num(los.get("a")), 1)
    alos_prev = round(num(los_prev.get("a")), 1)

    re_cur = _readmission(d1, d2, wc)
    re_prev = _readmission(p1, p2, wc)

    return {
        "period": {"d1": d1, "d2": d2, "prev_d1": p1, "prev_d2": p2, "days": span},
        "current_inpatients": cur_in,
        "admissions": adm,
        "admissions_delta": _delta(adm, adm_prev),
        "discharges": dch,
        "discharges_delta": _delta(dch, dch_prev),
        "total_beds": total_beds,
        "beds_used": cur_in,
        "beds_free": max(total_beds - cur_in, 0),
        "occupancy_rate": _pct(cur_in, total_beds),
        "alos": alos,
        "alos_delta": _delta(alos, alos_prev),
        "readmission_rate": re_cur,
        "readmission_delta": _delta(re_cur, re_prev),
    }


def _readmission(d1, d2, wc):
    """อัตรากลับมานอนซ้ำภายใน 30 วันหลังจำหน่าย (นับจากยอดจำหน่ายในช่วง)"""
    row = fetch_one(
        "select count(*) total, "
        " sum(exists(select 1 from ipt r where r.hn=i.hn and r.an<>i.an "
        "   and r.regdate > i.dchdate and r.regdate <= i.dchdate + interval 30 day)) re "
        f"from ipt i where i.dchdate between '{d1}' and '{d2}' {wc}")
    total, re = num(row.get("total")), num(row.get("re"))
    return _pct(re, total)


# ------------------------------------------- 2. แนวโน้มรับใหม่ / จำหน่าย

def admission_trend(d2, ward, months=9):
    """ยอดรับใหม่-จำหน่ายรายเดือน และยอดผู้ป่วยคงพยาบาล ณ สิ้นเดือน

    ใช้ GROUP BY 2 ครั้ง + ยอดยกมา 2 ครั้ง แทนการยิง subquery รายเดือน
    (แบบเดิมยิง 27 subquery ต่อการเปิดหน้า 1 ครั้ง ใช้เวลา ~3.7 วินาที)

    คงพยาบาล ณ สิ้นเดือน = (รับใหม่สะสมถึงสิ้นเดือน) - (จำหน่ายสะสมถึงสิ้นเดือน)
    """
    wc = _ward_cond(ward)
    end = date.fromisoformat(d2).replace(day=1)
    start = end
    for _ in range(months - 1):
        start = (start - timedelta(days=1)).replace(day=1)
    last = (end + timedelta(days=31)).replace(day=1) - timedelta(days=1)

    def by_month(col):
        rows = fetch(f"""
            select date_format(i.{col},'%Y-%m') ym, count(*) c from ipt i
            where i.{col} between '{start}' and '{last}' {wc}
            group by ym""")
        return {r["ym"]: num(r["c"]) for r in rows}

    adm, dch = by_month("regdate"), by_month("dchdate")
    carry = fetch_one(f"""
        select (select count(*) from ipt i where i.regdate < '{start}' {wc})
             - (select count(*) from ipt i where i.dchdate < '{start}' {wc}) c""")
    census = num(carry.get("c"))

    out, cur = [], start
    for _ in range(months):
        ym = cur.strftime("%Y-%m")
        a, d = adm.get(ym, 0), dch.get(ym, 0)
        census += a - d
        out.append({"label": TH_MONTH[cur.month - 1],
                    "admissions": a, "discharges": d, "census": census})
        cur = (cur + timedelta(days=31)).replace(day=1)
    return out


# ------------------------------------------------- 3. ครองเตียงราย ward

def bed_occupancy():
    rows = fetch(f"""
        select w.ward, w.name, w.bedcount total,
          (select count(*) from ipt i where i.dchdate is null and i.ward=w.ward) used
        from ward w where {ACTIVE_WARD_COND} order by w.ward""")
    out = []
    for r in rows:
        total, used = num(r["total"]), num(r["used"])
        out.append({
            "ward": r["ward"], "name": r["name"],
            "total": total, "used": used, "free": max(total - used, 0),
            "occupancy": _pct(used, total),
        })
    return out


# ------------------------------------------------ 4. สถานะผู้ป่วยปัจจุบัน

def patient_status(ward):
    """แบ่งสถานะผู้ป่วยในปัจจุบันจากฟิลด์ที่ HOSxP บันทึกจริง

    - รับใหม่วันนี้        : regdate = วันนี้
    - รอผลตรวจ Lab/X-ray  : lab_status='W' หรือ xray_status='W'
    - นอนเกิน 10 วัน       : ต้องติดตามเป็นพิเศษ
    - รักษาต่อเนื่อง        : ที่เหลือ
    """
    wc = _ward_cond(ward)
    r = fetch_one(f"""
        select count(*) total,
          sum(i.regdate = curdate()) new_today,
          sum(i.lab_status='W' or i.xray_status='W') waiting_result,
          sum(datediff(curdate(), i.regdate) > 10) long_stay
        from ipt i where i.dchdate is null {wc}""")
    total = num(r.get("total"))
    new_today = num(r.get("new_today"))
    waiting = num(r.get("waiting_result"))
    long_stay = num(r.get("long_stay"))
    ongoing = max(total - new_today - waiting - long_stay, 0)
    items = [
        ("รักษาต่อเนื่อง", ongoing, "#1a73e8"),
        ("รับใหม่วันนี้", new_today, "#22a06b"),
        ("รอผลตรวจ", waiting, "#f6a609"),
        ("นอนเกิน 10 วัน", long_stay, "#8b5cf6"),
    ]
    return {
        "total": total,
        "items": [{"label": l, "value": v, "pct": _pct(v, total), "color": c}
                  for l, v, c in items],
    }


# --------------------------------------------------------- 5. Length of Stay

def los_distribution(ward):
    """กระจายวันนอนของผู้ป่วยที่ยังอยู่ในหอผู้ป่วย"""
    wc = _ward_cond(ward)
    r = fetch_one(f"""
        select count(*) total,
          sum(datediff(curdate(),i.regdate) < 3) b1,
          sum(datediff(curdate(),i.regdate) between 3 and 5) b2,
          sum(datediff(curdate(),i.regdate) between 6 and 10) b3,
          sum(datediff(curdate(),i.regdate) > 10) b4
        from ipt i where i.dchdate is null {wc}""")
    total = num(r.get("total"))
    buckets = [("< 3 วัน", num(r.get("b1")), "#22a06b"),
               ("3 - 5 วัน", num(r.get("b2")), "#1a73e8"),
               ("6 - 10 วัน", num(r.get("b3")), "#f6a609"),
               ("> 10 วัน", num(r.get("b4")), "#e5484d")]
    return {"total": total,
            "items": [{"label": l, "value": v, "pct": _pct(v, total), "color": c}
                      for l, v, c in buckets]}


# ------------------------------------------------------- 6. Patient Flow

def patient_flow(ward):
    wc = _ward_cond(ward)
    r = fetch_one(f"""
        select
          (select count(*) from ipt i where i.regdate = curdate() {wc}) admitted_today,
          (select count(*) from ipt i where i.dchdate is null {wc}
             and not exists(select 1 from iptadm a where a.an=i.an and a.outdate is null)) no_bed,
          (select count(*) from ipt i where i.dchdate is null {wc}) in_treatment,
          (select count(*) from ipt i where i.dchdate is null {wc}
             and (i.lab_status='W' or i.xray_status='W')) waiting_result,
          (select count(*) from ipt i where i.dchdate = curdate() {wc}) discharged_today
        """)
    return [
        {"label": "Admit วันนี้", "sub": "(วันนี้)", "value": num(r.get("admitted_today")), "icon": "clipboard"},
        {"label": "รอเตียง", "sub": "(Bed Assign)", "value": num(r.get("no_bed")), "icon": "bed"},
        {"label": "อยู่ระหว่างการรักษา", "sub": "(In Treatment)", "value": num(r.get("in_treatment")), "icon": "nurse"},
        {"label": "รอผลตรวจ", "sub": "(Lab/X-Ray)", "value": num(r.get("waiting_result")), "icon": "flask"},
        {"label": "จำหน่ายวันนี้", "sub": "(Discharge)", "value": num(r.get("discharged_today")), "icon": "doc"},
    ]


# -------------------------------------------- 7. การจำหน่าย + เวลาจำหน่าย

#: dchtype 01 = With Approval (แพทย์อนุญาตให้กลับ) · เวลาที่ถือว่าจำหน่ายช้า (ตั้งได้ด้วย IPD_LATE_DISCHARGE)
DCH_APPROVED = "01"
_late = os.getenv("IPD_LATE_DISCHARGE", "16:00")
LATE_DISCHARGE = (_late if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", _late) else "16:00") + ":00"


def discharge_planning(d1, d2, ward):
    wc = _ward_cond(ward)
    rows = fetch(f"""
        select ifnull(s.name,'ไม่ระบุ') name, count(*) c
        from ipt i left join dchstts s on s.dchstts = i.dchstts
        where i.dchdate between '{d1}' and '{d2}' {wc}
        group by s.name order by c desc""")
    total = sum(num(r["c"]) for r in rows)
    palette = ["#22a06b", "#1a73e8", "#f6a609", "#e5484d", "#8b5cf6", "#0ea5e9"]
    items = []
    for idx, r in enumerate(rows[:5]):
        v = num(r["c"])
        items.append({"label": r["name"], "value": v, "pct": _pct(v, total),
                      "color": palette[idx % len(palette)]})
    other = total - sum(i["value"] for i in items)
    if other > 0:
        items.append({"label": "อื่น ๆ", "value": other, "pct": _pct(other, total), "color": "#94a3b8"})

    # เวลาจำหน่ายเป็นเรื่องกระบวนการ จึงนับเฉพาะ "แพทย์อนุญาตให้กลับ" (dchtype 01)
    # ไม่รวมเสียชีวิต / ส่งต่อ / หนี ที่เกิดได้ทุกเวลา · "หลัง 16:00" = เกิน 16:00:00 (16:00 ตรงไม่นับ)
    t = fetch_one(f"""
        select sec_to_time(avg(time_to_sec(i.dchtime))) avg_time,
               sum(i.dchtime > '{LATE_DISCHARGE}') late
        from ipt i where i.dchdate between '{d1}' and '{d2}'
              and i.dchtime is not null and i.dchtype = '{DCH_APPROVED}' {wc}""")
    avg_time = str(t.get("avg_time") or "")[:5]
    return {"total": total, "items": items,
            "avg_discharge_time": avg_time or "-",
            "late_discharges": num(t.get("late")),
            "late_cutoff": LATE_DISCHARGE[:5]}


# ------------------------------------------------------ 8. ผู้ป่วยรอเตียง

def waiting_for_bed(d1, d2, ward):
    """รอเตียง = admit แล้วแต่ยังไม่มีแถวเตียงเปิดอยู่ใน iptadm
    เวลารอเฉลี่ย = เวลาตั้งแต่ลงทะเบียน admit จนถึงเวลาเข้าเตียงจริง
    """
    wc = _ward_cond(ward)
    waiting = num(fetch_one(f"""
        select count(*) c from ipt i where i.dchdate is null {wc}
          and not exists(select 1 from iptadm a where a.an=i.an and a.outdate is null)""").get("c"))

    w = fetch_one(f"""
        select avg(timestampdiff(minute,
                   timestamp(i.regdate,i.regtime), a.move_in_bed_datetime)) m
        from ipt i join iptadm a on a.an=i.an
        where i.regdate between '{d1}' and '{d2}' {wc}
          and a.move_in_bed_datetime is not null and i.regtime is not null
          and a.move_in_bed_datetime >= timestamp(i.regdate,i.regtime)
          and timestampdiff(hour, timestamp(i.regdate,i.regtime), a.move_in_bed_datetime) < 48""")
    mins = num(w.get("m"))
    return {"waiting": waiting, "avg_wait_hours": round(mins / 60.0, 1) if mins else 0.0}


# ------------------------------------------------- 9. โปรไฟล์ผู้ป่วยใน

def patient_profile(d1, d2, ward):
    wc = _ward_cond(ward)
    base = f"i.regdate between '{d1}' and '{d2}' {wc}"

    rights = fetch(f"""
        select ifnull(p.name,'ไม่ระบุ') name, count(*) c
        from ipt i left join pttype p on p.pttype = i.pttype
        where {base} group by p.name order by c desc limit 5""")
    r_total = num(fetch_one(f"select count(*) c from ipt i where {base}").get("c"))
    palette = ["#1a73e8", "#22a06b", "#f6a609", "#e5484d", "#8b5cf6"]
    rights_items = [{"label": r["name"], "value": num(r["c"]),
                     "pct": _pct(num(r["c"]), r_total), "color": palette[i % 5]}
                    for i, r in enumerate(rights)]
    shown = sum(i["value"] for i in rights_items)
    if r_total - shown > 0:
        rights_items.append({"label": "อื่น ๆ", "value": r_total - shown,
                             "pct": _pct(r_total - shown, r_total), "color": "#94a3b8"})

    ages = fetch_one(f"""
        select count(*) total,
          sum(timestampdiff(year,pt.birthday,i.regdate) <= 18) a1,
          sum(timestampdiff(year,pt.birthday,i.regdate) between 19 and 40) a2,
          sum(timestampdiff(year,pt.birthday,i.regdate) between 41 and 60) a3,
          sum(timestampdiff(year,pt.birthday,i.regdate) between 61 and 80) a4,
          sum(timestampdiff(year,pt.birthday,i.regdate) > 80) a5
        from ipt i join patient pt on pt.hn = i.hn where {base} and pt.birthday is not null""")
    a_total = num(ages.get("total"))
    age_items = [
        {"label": "0-18 ปี", "value": num(ages.get("a1")), "color": "#22a06b"},
        {"label": "19-40 ปี", "value": num(ages.get("a2")), "color": "#1a73e8"},
        {"label": "41-60 ปี", "value": num(ages.get("a3")), "color": "#8b5cf6"},
        {"label": "61-80 ปี", "value": num(ages.get("a4")), "color": "#f6a609"},
        {"label": "> 80 ปี", "value": num(ages.get("a5")), "color": "#e5484d"},
    ]
    for it in age_items:
        it["pct"] = _pct(it["value"], a_total)

    dx = fetch(f"""
        select d.icd10, ifnull(nullif(c.tname,''), ifnull(c.name, d.icd10)) name, count(*) cnt
        from iptdiag d join ipt i on i.an = d.an
             left join icd101 c on c.code = d.icd10
        where d.diagtype = '1' and {base}
        group by d.icd10, name order by cnt desc limit 5""")
    dx_total = sum(num(r["cnt"]) for r in dx) or 1
    dx_items = [{"label": r["name"], "code": r["icd10"], "value": num(r["cnt"]),
                 "pct": _pct(num(r["cnt"]), r_total)} for r in dx]
    dx_max = max((i["value"] for i in dx_items), default=1)
    for it in dx_items:
        it["bar"] = _pct(it["value"], dx_max)

    return {"total": r_total, "rights": rights_items,
            "ages": age_items, "age_total": a_total, "dx": dx_items}


# ------------------------------------------- 11. คุณภาพการจำหน่าย / เวชระเบียน

#: รหัส dchtype มาตรฐานของ HOSxP (ตาราง dchtype)
DCH_REFER, DCH_AGAINST_ADVICE, DCH_ESCAPE = "04", "02", "03"


def discharge_quality(d1, d2, ward, total_beds):
    """ส่งต่อ / ไม่สมัครใจอยู่ / หนี · เวชระเบียนยังไม่ลงรหัสโรคหลัก · AdjRW / CMI · วันนอนรวม / bed turnover"""
    wc = _ward_cond(ward)
    r = fetch_one(f"""
        select count(*) total,
               sum(i.dchtype = '{DCH_REFER}') refer,
               sum(i.dchtype = '{DCH_AGAINST_ADVICE}') ama,
               sum(i.dchtype = '{DCH_ESCAPE}') escape,
               sum(not exists(select 1 from iptdiag x where x.an = i.an and x.diagtype = '1')) no_pdx,
               sum(i.adjrw > 0) rw_n, sum(if(i.adjrw > 0, i.adjrw, 0)) rw_sum
        from ipt i where i.dchdate between '{d1}' and '{d2}' {wc}""")
    total = num(r.get("total"))
    rw_n, rw_sum = num(r.get("rw_n")), num(r.get("rw_sum"))

    # วันนอนรวม = จำนวนวันที่ผู้ป่วยแต่ละรายนอนอยู่ "ภายในช่วงที่เลือก" (นับวันรับ ไม่นับวันจำหน่าย)
    days = (date.fromisoformat(d2) - date.fromisoformat(d1)).days + 1
    pd = num(fetch_one(f"""
        select sum(greatest(datediff(least(ifnull(i.dchdate, '{d2}' + interval 1 day), '{d2}' + interval 1 day),
                                     greatest(i.regdate, '{d1}')), 0)) d
        from ipt i where i.regdate <= '{d2}' and (i.dchdate is null or i.dchdate >= '{d1}') {wc}""").get("d"))

    def item(key, label, v, unit="ราย"):
        return {"key": key, "label": label, "value": v, "unit": unit, "pct": _pct(v, total)}

    refer, ama, esc, no_pdx = (num(r.get(k)) for k in ("refer", "ama", "escape", "no_pdx"))
    return {
        "total": total,
        "refer": item("refer", "ส่งต่อ (Refer out)", refer),
        "ama": item("ama", "ไม่สมัครใจอยู่", ama),
        "escape": item("escape", "หนีกลับ", esc),
        "no_pdx": item("no_pdx", "ยังไม่ลงรหัสโรคหลัก", no_pdx),
        "rw_n": rw_n, "rw_pct": _pct(rw_n, total),
        "rw_sum": round(rw_sum, 1),
        "cmi": round(rw_sum / rw_n, 2) if rw_n else 0.0,
        "patient_days": pd,
        "daily_census": round(pd / days, 1) if days else 0.0,
        "period_occupancy": _pct(pd, total_beds * days),
        "turnover": round(total / total_beds, 1) if total_beds else 0.0,
        "beds": total_beds, "days": days,
    }


# ------------------------------------------- 12. Readmission ตามกลุ่มโรค

def readmission_by_dx(d1, d2, ward, limit=5):
    """ผู้ป่วยที่จำหน่ายในช่วงแล้วกลับมานอนซ้ำภายใน 30 วัน แยกตามโรคหลักของครั้งแรก (Top 5)"""
    wc = _ward_cond(ward)
    rows = fetch(f"""
        select d.icd10, c.name, count(*) cnt
        from ipt i
             left join iptdiag d on d.an = i.an and d.diagtype = '1'
             left join icd101 c on c.code = d.icd10
        where i.dchdate between '{d1}' and '{d2}' {wc}
          and exists(select 1 from ipt r where r.hn = i.hn and r.an <> i.an
                     and r.regdate > i.dchdate and r.regdate <= i.dchdate + interval 30 day)
        group by d.icd10, c.name order by cnt desc limit {int(limit) + 1}""")
    # ค่าแทน NULL ใส่ฝั่ง Python — สตริงไทยใน SQL เพี้ยนเพราะ connection เป็น tis-620
    items = [{"label": x["name"] or ("ยังไม่ลงรหัสโรค" if not x["icd10"] else x["icd10"]),
              "code": x["icd10"] or "-", "value": num(x["cnt"])} for x in rows[:limit]]
    top = max((i["value"] for i in items), default=1) or 1
    for it in items:
        it["bar"] = _pct(it["value"], top)
    return {"items": items, "shown": sum(i["value"] for i in items), "more": len(rows) > limit}


# ------------------------------------------------------ 10. Alert & Action

def alerts(kpi, beds, los, wait, dch, dq=None, readmit=None):
    """สร้างรายการเฝ้าระวังจากเกณฑ์มาตรฐาน — คำนวณจากตัวเลขจริงทั้งหมด"""
    out = []
    for b in beds:
        if b["occupancy"] >= 90:
            out.append({"level": "high", "issue": f"อัตราครองเตียง {b['occupancy']}%",
                        "where": b["name"], "status": "เร่งดำเนินการ",
                        "action": "เพิ่มรอบจำหน่าย / ประสานเตียง"})
    if wait["waiting"] > 0:
        out.append({"level": "high", "issue": f"ผู้ป่วยรอเตียง {wait['waiting']} ราย",
                    "where": "ภาพรวม", "status": "เร่งดำเนินการ", "action": "ประสานจัดเตียง"})
    long_stay = next((i for i in los["items"] if i["label"] == "> 10 วัน"), None)
    if long_stay and long_stay["value"] > 0:
        out.append({"level": "mid", "issue": f"LOS > 10 วัน {long_stay['value']} ราย",
                    "where": "ผู้ป่วยใน", "status": "เฝ้าระวัง", "action": "ทบทวนแผนการรักษา"})
    if dch["late_discharges"] > 0:
        out.append({"level": "mid", "issue": f"จำหน่ายหลัง {dch.get('late_cutoff', '16:00')} น. {dch['late_discharges']} ราย",
                    "where": "ภาพรวม", "status": "เฝ้าระวัง", "action": "ปรับกระบวนการจำหน่าย"})
    if kpi["readmission_rate"] > 10:
        top = (readmit or {}).get("items") or []
        why = f" (มากสุด: {top[0]['code']})" if top and top[0]["code"] != "-" else ""
        out.append({"level": "high", "issue": f"Readmission 30 วัน {kpi['readmission_rate']}%{why}",
                    "where": "ผู้ป่วยใน", "status": "เร่งดำเนินการ", "action": "ทบทวนแผนจำหน่ายกลุ่มโรคที่กลับมาซ้ำ"})
    elif kpi["readmission_rate"] > 5:
        out.append({"level": "mid", "issue": f"Readmission 30 วัน {kpi['readmission_rate']}%",
                    "where": "ผู้ป่วยใน", "status": "ติดตาม", "action": "วิเคราะห์สาเหตุรายราย"})
    if dq and dq["no_pdx"]["pct"] > 10:
        out.append({"level": "mid", "issue": f"ยังไม่ลงรหัสโรคหลัก {dq['no_pdx']['value']} ราย ({dq['no_pdx']['pct']}%)",
                    "where": "เวชระเบียน", "status": "เฝ้าระวัง", "action": "เร่งสรุปชาร์ตและลงรหัส"})
    if kpi["occupancy_rate"] >= 85:
        out.append({"level": "high", "issue": f"ครองเตียงรวม {kpi['occupancy_rate']}%",
                    "where": "ทั้งโรงพยาบาล", "status": "เร่งดำเนินการ", "action": "เปิดเตียงสำรอง"})
    if not out:
        out.append({"level": "ok", "issue": "ไม่พบประเด็นเกินเกณฑ์เฝ้าระวัง",
                    "where": "-", "status": "ปกติ", "action": "ติดตามตามรอบปกติ"})
    return out[:7]


# ------------------------------------------------------------- ประกอบร่าง

def ward_list():
    return fetch(f"select w.ward, w.name from ward w where {ACTIVE_WARD_COND} order by w.ward")


#: แคชผลลัพธ์สั้น ๆ — หลายคนเปิดหน้าเดียวกันพร้อมกันจะไม่ยิง DB ซ้ำ
#: ตั้งอายุแคชด้วย env IPD_CACHE_TTL (วินาที, 0 = ปิด)
CACHE_TTL = int(os.getenv("IPD_CACHE_TTL", "120"))
_cache = {}


def build(d1=None, d2=None, ward="all"):
    today = date.today()
    d2 = d2 or today.isoformat()
    d1 = d1 or today.replace(day=1).isoformat()

    import time
    key = (d1, d2, ward)
    if CACHE_TTL:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]

    kpi = kpi_summary(d1, d2, ward)
    beds = bed_occupancy()
    los = los_distribution(ward)
    wait = waiting_for_bed(d1, d2, ward)
    dch = discharge_planning(d1, d2, ward)
    dq = discharge_quality(d1, d2, ward, kpi["total_beds"])
    readmit = readmission_by_dx(d1, d2, ward)

    data = {
        "generated_at": _thai_datetime(),
        "period_label": _thai_range(d1, d2),
        "d1": d1, "d2": d2, "ward": ward,
        "wards": ward_list(),
        "kpi": kpi,
        "trend": admission_trend(d2, ward),
        "beds": beds,
        "status": patient_status(ward),
        "los": los,
        "flow": patient_flow(ward),
        "discharge": dch,
        "wait": wait,
        "profile": patient_profile(d1, d2, ward),
        "quality": dq,
        "readmit": readmit,
        "alerts": alerts(kpi, beds, los, wait, dch, dq, readmit),
    }
    if CACHE_TTL:
        _cache[key] = (time.time(), data)
    return data


TH_MONTH = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
            "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


def _thai_date(d):
    d = date.fromisoformat(d) if isinstance(d, str) else d
    return f"{d.day} {TH_MONTH[d.month - 1]} {d.year + 543}"


def _thai_range(d1, d2):
    a, b = date.fromisoformat(d1), date.fromisoformat(d2)
    if a.month == b.month and a.year == b.year:
        return f"วันที่ {a.day} - {b.day} {TH_MONTH[b.month - 1]} {b.year + 543}"
    return f"{_thai_date(a)} - {_thai_date(b)}"


def _thai_datetime():
    from datetime import datetime

    n = datetime.now()
    return f"{_thai_date(n.date())} เวลา {n:%H:%M} น."
