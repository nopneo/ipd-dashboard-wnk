# ประวัติเวอร์ชัน IPD Dashboard

## 1.0.0 — รุ่นแรกที่เผยแพร่

แดชบอร์ดผู้ป่วยใน 10 บล็อก ดึงข้อมูลสดจาก HOSxP · ใช้ได้ทุกโรงพยาบาล

**ฟีเจอร์**
1. อินโฟกราฟิก 10 บล็อก: KPI, แนวโน้มรับ/จำหน่าย, ครองเตียงรายตึก, สถานะผู้ป่วย, วันนอน, Patient Flow,
   Discharge Planning, ผู้ป่วยรอเตียง, โปรไฟล์ผู้ป่วย, Alert & Action
2. เปิดจากมือถือ + เพิ่มลงหน้าจอโฮมเป็นไอคอนแอป (manifest, maskable icon)
3. ส่งออก JPG / PNG / PDF กว้างตายตัว 1600px (chromium headless)
4. อัตราครองเตียงคิดจากเตียงตามกรอบ (`IPD_TOTAL_BEDS`) หรือผลรวม `ward.bedcount`
5. ชื่อ/โลโก้/ภาพพื้นหลังตั้งได้ต่อโรงพยาบาล (`IPD_HOSPITAL_NAME`, `static/img/hospital_logo.png`, `hero.jpg`)
6. ชุดติดตั้ง `build_dist.py`, `Dockerfile` (2 target), คู่มือ INSTALL.md

**ความปลอดภัย** (รายละเอียดดู [SECURITY.md](SECURITY.md))
1. ตรวจ `d1`/`d2`/`ward` ก่อนเข้า SQL + escape ซ้ำใน `queries.py` · จำกัดช่วงวันที่ 366 วัน
2. ต่อฐานข้อมูลแบบ READ ONLY + timeout · แคชผลในหน่วยความจำ
3. ส่งออกไฟล์ทีละงาน ไม่ว่าง = 503 ทันที, แคชไฟล์ล่าสุด, chromium เปิดเฉพาะ `127.0.0.1`, เปิด sandbox เมื่อไม่ใช่ root
4. debug ปิดเป็นค่าเริ่มต้น · header `nosniff` / `Referrer-Policy`
5. Docker: Alpine, ไม่ใช้ root, ลบ pip, access log ไม่มี query string
6. แสดงเฉพาะข้อมูลรวม ไม่มี HN/ชื่อผู้ป่วย — มี test ตรวจ
7. แก้หน้าพังเมื่อฐานข้อมูลยังไม่มีข้อมูล (ward ว่าง / ไม่มีผู้ป่วยในช่วงที่เลือก)
