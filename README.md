# IPD Dashboard — แดชบอร์ดบริหารจัดการผู้ป่วยใน (HOSxP)

> **เวอร์ชัน 1.0.0** — ใช้ได้ทุกโรงพยาบาลที่ใช้ HOSxP · ติดตั้ง: [INSTALL.md](INSTALL.md) ·
> สร้างชุดติดตั้ง `python build_dist.py` → `dist/ipd_dashboard-<เวอร์ชัน>.zip` ·
> ความปลอดภัย: [SECURITY.md](SECURITY.md) · การเปลี่ยนแปลง: [CHANGELOG.md](CHANGELOG.md)

รายงานอินโฟกราฟิก 10 บล็อก ดึงข้อมูลสดจาก HOSxP (MariaDB `hos`) โทนลาเวนเดอร์/ม่วง
ใช้ได้ทั้งเป็น **เว็บ live** (เปิดจากมือถือได้ เพิ่มลงหน้าจอโฮมเป็นไอคอนแอป) และ **กดส่งออกเป็น JPG / PNG / PDF**

## วิธีใช้

```bash
pip install -r requirements.txt
cp .env.example .env          # แก้ DB_USER / DB_PASS (บัญชีอ่านอย่างเดียว)
python app.py                 # http://127.0.0.1:5700/   (debug ปิด เปิดด้วย FLASK_DEBUG=1)
python render.py --d1 2026-10-01 --d2 2026-10-15 --fmt jpg
```

เลือกช่วงวันที่และ ward จากแถบด้านบน (`?d1=YYYY-MM-DD&d2=YYYY-MM-DD&ward=รหัส`) · API ดิบ `GET /api/data.json`

## บล็อกในรายงาน

| # | บล็อก | ที่มาของข้อมูล |
|---|-------|----------------|
| 1 | IPD KPI Summary | `ipt` — ผู้ป่วยในปัจจุบัน / รับใหม่ / จำหน่าย / เตียง / ครองเตียง / ALOS / Readmission 30 วัน (เทียบช่วงก่อนหน้าที่ยาวเท่ากัน) |
| 2 | Admission & Discharge Trend | `ipt` รายเดือน 9 เดือนล่าสุด + ยอดคงพยาบาล ณ สิ้นเดือน |
| 3 | Bed Occupancy by Ward | `ward` × `ipt.ward` เฉพาะ ward ที่ `ward_active='Y'` และกำหนด `bedcount` |
| 4 | Patient Status | `ipt.lab_status` / `xray_status` / `regdate` |
| 5 | Length of Stay | `datediff(curdate(), regdate)` ของผู้ป่วยที่ยังไม่จำหน่าย |
| 6 | Patient Flow | `ipt` + `iptadm` (แถวเตียงที่ยังเปิดอยู่) |
| 7 | Discharge Planning | `ipt.dchstts` join `dchstts` + เวลาจำหน่ายเฉลี่ยจาก `dchtime` |
| 8 | Waiting for Bed | admit แล้วยังไม่มีแถวเตียงเปิดใน `iptadm` |
| 9 | Patient Profile | `pttype`, `patient.birthday`, `iptdiag` (`diagtype='1'`) join `icd101` |
| 10 | Alert & Action | คำนวณจากเกณฑ์เฝ้าระวังในโค้ด (`queries.alerts`) |

ตัวเลขทุกตัวมาจากฐานข้อมูลจริง ไม่มีค่าสมมติ — ช่องที่ HOSxP ไม่ได้บันทึกถูกแทนด้วยตัวชี้วัดที่มีข้อมูลจริง

## อัตราครองเตียง

ตัวหารคือ **เตียงตามกรอบของโรงพยาบาล** — ไม่ตั้งค่า = ผลรวม `ward.bedcount` ของตึกที่เปิดใช้
ถ้ากรอบจริงไม่เท่าผลรวม (เช่น bedcount รวม 56 แต่กรอบ 40) ตั้ง `IPD_TOTAL_BEDS=40`
ตัวตั้งคือผู้ป่วยในที่ยังไม่จำหน่ายทุก ward · เมื่อกรองเฉพาะ ward จะใช้ `bedcount` ของ ward นั้นแทน

## การเชื่อมต่อฐานข้อมูล

`db.py` มี 2 โหมด เลือกด้วย `IPD_DB_MODE` (ตั้ง `DB_PASS` = `direct` อัตโนมัติ)

- `direct` — ต่อ MariaDB ตรงด้วย PyMySQL อ่านค่าจาก `.env` (ใช้บนเซิร์ฟเวอร์จริง) ตั้ง session เป็น READ ONLY ให้เอง
- `ssh` — ยิงผ่าน `ssh $IPD_SSH_HOST mariadb hos` (ใช้ตอนพัฒนาบนเครื่องที่ไม่มีรหัส DB)

HOSxP เก็บข้อมูลเป็น **tis-620** โค้ดตั้ง charset ให้แล้ว

## ส่งออกไฟล์

กดปุ่ม ⬇ JPG / PNG / PDF บนแถบเครื่องมือ (~10 วินาที) หรือสั่งจาก command line (`python render.py --fmt pdf`)
ใช้ chromium headless ถ่ายภาพหน้าที่กว้างตายตัว 1600px แล้วห่อเป็น PDF หน้าเดียว (A3) ทั้งสามชนิดจึงตรงกันเสมอ
ต้องมี Chrome/Edge/Chromium บนเครื่อง · ไม่ต้องการ = ตั้ง `IPD_EXPORT=0` (ซ่อนปุ่มทั้งหมด)

## ความปลอดภัยและความเป็นส่วนตัว

- แสดงเฉพาะข้อมูลรวม **ไม่มี HN / AN / ชื่อผู้ป่วย** ไม่เรียกทรัพยากรภายนอก (ฟอนต์ฝังในเครื่อง)
- พารามิเตอร์ (`d1`, `d2`, `ward`) ถูกตรวจรูปแบบก่อนเข้า SQL · ช่วงวันที่จำกัด `IPD_MAX_DAYS` (366)
- ส่งออกไฟล์: ทำทีละงานทั้งเครื่อง ไม่ว่าง = ตอบ 503 ทันที + แคชไฟล์ล่าสุด + chromium เรียกได้เฉพาะ `127.0.0.1`
- debug ปิดเป็นค่าเริ่มต้น · header `nosniff`, `Referrer-Policy` · Docker รันด้วยผู้ใช้ธรรมดาบน Alpine
- รายละเอียดและสิ่งที่ต้องตั้งที่ proxy: [SECURITY.md](SECURITY.md)

## ทดสอบ

```bash
pip install -r requirements-dev.txt
python -m pytest tests
```

ใบอนุญาต MIT — ดู [LICENSE](LICENSE) และ [NOTICE.md](NOTICE.md)
