# ติดตั้ง IPD Dashboard สำหรับโรงพยาบาลที่ใช้ HOSxP

แดชบอร์ดผู้ป่วยในแบบเว็บ (Flask) อ่านข้อมูลสดจากฐานข้อมูล HOSxP (MariaDB/MySQL) **อ่านอย่างเดียว**
ใช้เวลาติดตั้งราว 15 นาที

> คู่มือฉบับเต็มพร้อมภาพประกอบ: `IPD_Dashboard_install_manual.pdf` (อยู่ใน zip และใน `docs/` ของ repo)

## 1. สิ่งที่ต้องมี

- เครื่องที่ต่อถึงฐานข้อมูล HOSxP ได้ (แนะนำให้ต่อ **Slave/Replica** ไม่ใช่ฐานหลัก)
- Python 3.9 ขึ้นไป · Chrome/Edge/Chromium ถ้าจะใช้ปุ่มส่งออก JPG/PNG/PDF
- บัญชีฐานข้อมูลที่ **SELECT อย่างเดียว** (แดชบอร์ดไม่เขียนข้อมูล และตั้ง session เป็น READ ONLY ให้อีกชั้น)

สร้างบัญชีอ่านอย่างเดียว (ตัวอย่าง):

```sql
CREATE USER 'ipd_ro'@'%' IDENTIFIED BY '<รหัสผ่านยาว ๆ>';
GRANT SELECT ON hos.* TO 'ipd_ro'@'%';     -- จำกัด host ให้แคบที่สุดเท่าที่ทำได้
```

## 2. ติดตั้งบน Linux

```bash
unzip ipd_dashboard-1.0.0.zip -d /opt && cd /opt/ipd_dashboard
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && chmod 600 .env && nano .env        # ใส่ DB_HOST / DB_USER / DB_PASS
.venv/bin/python app.py                                    # ทดลอง: http://127.0.0.1:5700/
```

รันเป็นบริการ (systemd) `/etc/systemd/system/ipd-dashboard.service`:

```ini
[Unit]
Description=IPD Dashboard
After=network.target

[Service]
WorkingDirectory=/opt/ipd_dashboard
ExecStart=/opt/ipd_dashboard/.venv/bin/gunicorn --bind 0.0.0.0:5700 --workers 3 --timeout 120 \
  --access-logfile - --access-logformat '%(t)s %(m)s %(U)s %(s)s %(L)ss' app:app
Restart=always
# ไม่ต้องใช้สิทธิ์ root — ถ้าใช้ปุ่มส่งออก chromium จะเปิด sandbox เองเมื่อไม่ใช่ root
User=ipd
NoNewPrivileges=true
ProtectSystem=full

[Install]
WantedBy=multi-user.target
```

```bash
useradd -r -s /sbin/nologin ipd && chown -R ipd /opt/ipd_dashboard
systemctl enable --now ipd-dashboard
```

## 3. ติดตั้งบน Windows

```powershell
python -m venv .venv ; .venv\Scripts\pip install -r requirements.txt
copy .env.example .env ; notepad .env
.venv\Scripts\waitress-serve --listen=0.0.0.0:5700 app:app
```

## 4. ตั้งค่า (.env)

| ค่า | ความหมาย |
|---|---|
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASS` | ฐานข้อมูล HOSxP (บัญชีอ่านอย่างเดียว) |
| `PORT` | พอร์ตของแอป (ค่าเริ่มต้น 5700) |
| `IPD_HOSPITAL_NAME`, `IPD_HOSPITAL_NAME_EN` | ชื่อโรงพยาบาล (ถ้าไม่ใส่อ่านจาก `opdconfig`) / ชื่อภาษาอังกฤษใต้ชื่อ |
| `IPD_CONTACT`, `IPD_DEV_CONTACT` | ช่องทางติดต่อที่แสดงท้ายหน้า |
| `IPD_TOTAL_BEDS` | เตียงตามกรอบ ใช้เป็นตัวหารอัตราครองเตียง (ไม่ใส่ = รวม `ward.bedcount`) |
| `IPD_LATE_DISCHARGE` | เวลาที่ถือว่าจำหน่ายช้า รูปแบบ HH:MM (ค่าเริ่มต้น `16:00`) นับเฉพาะแพทย์อนุญาตให้กลับ |
| `IPD_MAX_DAYS` | ช่วงวันที่ยาวสุดที่ดึงได้ต่อครั้ง (ค่าเริ่มต้น 366) |
| `IPD_CACHE_TTL` | อายุแคชข้อมูลเป็นวินาที (ค่าเริ่มต้น 120, 0 = ปิด) |
| `IPD_DB_TIMEOUT` | เวลาสูงสุดต่อคำสั่ง SQL เป็นวินาที (ค่าเริ่มต้น 60) |
| `IPD_EXPORT` | `0` = ปิดปุ่มส่งออกทั้งหมด (เครื่องที่ไม่มี chromium) |
| `IPD_EXPORT_WAIT` | ส่งออก: รอคิวได้นานสุดกี่วินาที (ค่าเริ่มต้น 0 = ไม่ว่างตอบ 503 ทันที) |
| `IPD_EXPORT_CACHE_TTL` | เก็บไฟล์ที่ส่งออกแล้วให้ผู้ขอช่วงเดียวกัน (วินาที, ค่าเริ่มต้น 300) |
| `IPD_CHROME_NO_SANDBOX` | `1` = ปิด sandbox ของ chromium (ค่าเริ่มต้นปิดเฉพาะเมื่อรันด้วย root / บน Windows) |
| `IPD_HOST` | ที่อยู่ที่ฟัง เมื่อรัน `python app.py` (ค่าเริ่มต้น 0.0.0.0) |

**โลโก้และภาพพื้นหลัง** — วาง PNG สี่เหลี่ยมจัตุรัสที่ `static/img/hospital_logo.png` (ไม่มี = ใช้โลโก้กลาง)
และภาพแนวนอนที่ `static/img/hero.jpg` เป็นพื้นแถบหัว (ไม่มี = พื้นไล่สีม่วง) แล้วรีสตาร์ตแอป

## 5. เปิดผ่าน reverse proxy (ทางเลือก)

วางใต้ sub-path เช่น `https://<โดเมน รพ.>/ipd/` (Apache) — ใส่ **ก่อน** บรรทัด catch-all `ProxyPass /` ถ้ามี:

```apache
RewriteRule ^/ipd$ /ipd/ [R=302,L]
ProxyPass /ipd/ http://127.0.0.1:5700/
ProxyPassReverse /ipd/ http://127.0.0.1:5700/
<Location /ipd/>
  RequestHeader set X-Forwarded-Prefix /ipd
  RequestHeader set X-Forwarded-Proto https
</Location>
```

แอปอ่าน `X-Forwarded-Prefix` / `X-Forwarded-Proto` สร้างลิงก์ให้ถูกเอง · แก้ค่าตั้งของ Apache ทุกครั้ง: สำรองไฟล์ →
`httpd -t` → ผ่านค่อย `systemctl reload httpd`

**ถ้าเปิดออกอินเทอร์เน็ต อ่าน [SECURITY.md](SECURITY.md) ก่อน** (rate limit, firewall, HTTPS ที่ต้องตั้งที่ proxy)

## 6. ตรวจสอบหลังติดตั้ง

- `GET /healthz` ตอบ `{"status":"ok"}`
- เปิดหน้าแรกแล้วตัวเลขตรงกับหน้า HOSxP (ผู้ป่วยในปัจจุบัน, เตียงที่ใช้)
- ถ้าตัวเลขครองเตียงเพี้ยน: ตรวจ `ward.bedcount` / `ward.ward_active` ของแต่ละตึก หรือกำหนด `IPD_TOTAL_BEDS`
- ปุ่มส่งออกไม่ทำงาน: ตรวจว่ามี Chrome/Edge/Chromium (หรือตั้ง `IPD_EXPORT=0`) และดู log ของบริการ

## 7. Docker

```bash
docker build -t ipd-dashboard .                          # ไม่มีปุ่มส่งออก (image เล็ก)
docker build --target export -t ipd-dashboard-export .   # มี chromium
docker run -p 8080:8080 --env-file .env ipd-dashboard
```

## 8. อัปเดตเป็นเวอร์ชันใหม่

แตก zip ใหม่ทับโฟลเดอร์เดิม (ไฟล์ `.env`, `static/img/hospital_logo.png`, `static/img/hero.jpg` ของคุณไม่อยู่ใน zip จึงไม่ถูกทับ)
แล้วรีสตาร์ตบริการ · ดูสิ่งที่เปลี่ยนใน [CHANGELOG.md](CHANGELOG.md)
