# IPD Dashboard — container (Docker ทั่วไป / Kubernetes)
#
# มี 2 แบบ เลือกด้วย --target (ไม่ระบุ = web ซึ่งเป็น stage สุดท้าย):
#   web (ค่าเริ่มต้น) — ไม่ลง chromium ปุ่มส่งออก PNG/JPG/PDF ปิด (image เล็ก ช่องโหว่น้อย)
#       docker build -t ipd-dashboard .
#       docker run -p 8080:8080 --env-file .env ipd-dashboard
#   export — เพิ่ม chromium ให้ใช้ปุ่มส่งออกได้
#       docker build --target export -t ipd-dashboard-export .
#       docker run -p 8080:8080 --env-file .env ipd-dashboard-export
#       ถ้าปุ่มส่งออกใช้ไม่ได้เพราะ container ไม่รองรับ sandbox ของ chromium ให้เพิ่ม -e IPD_CHROME_NO_SANDBOX=1
#
# ค่าฐานข้อมูล (DB_HOST/DB_USER/DB_PASS ...) ส่งเป็น env ตอนรัน — ห้ามใส่ใน image
#
# ลดช่องโหว่ใน image (สแกนด้วย Trivy ได้):
#   - ใช้ Alpine แทน Debian slim — ไลบรารีระบบน้อยกว่ามาก
#   - apk upgrade ตอนบิลด์ = ได้แพตช์ล่าสุดทุกครั้งที่บิลด์ใหม่
#   - ลบ pip ออกหลังติดตั้งเสร็จ (แอปไม่ใช้ตอนรัน)
#   - รันด้วยผู้ใช้ธรรมดา (uid 10001) ไม่ใช่ root
FROM python:3.12-alpine AS base

# TZ: ช่วง "วันนี้" ใช้เวลาเครื่อง ต้องเป็นเวลาไทย
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    IPD_DB_MODE=direct \
    IPD_EXPORT=0 \
    TZ=Asia/Bangkok \
    PORT=8080
RUN apk upgrade --no-cache && apk add --no-cache tzdata

WORKDIR /app
COPY requirements.txt .
# gunicorn ใช้บน Linux (waitress ในไฟล์เดียวกันมีเงื่อนไขเฉพาะ Windows จึงไม่ถูกติดตั้ง)
RUN pip install -r requirements.txt \
    && pip uninstall -y pip
COPY . .

RUN adduser -D -u 10001 app && chown -R app /app
USER app

EXPOSE 8080
# gthread: request หนึ่งรอฐานข้อมูลหลายวินาที — ใช้ thread ไม่ให้ผู้ใช้คนอื่นต้องรอ
# access log บันทึกแค่ path (%(U)s) ไม่มี query string
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT} --workers 2 --threads 8 --timeout 120 --access-logfile - --access-logformat '%(t)s %(m)s %(U)s %(s)s %(L)ss' app:app"]


# ---- export: เพิ่ม chromium สำหรับปุ่มส่งออก PNG/JPG/PDF (render.py)
FROM base AS export
USER root
# font-noto-thai: ฟอนต์ไทยสำรองของ Chromium
RUN apk add --no-cache chromium font-noto-thai
ENV IPD_EXPORT=1
USER app


# ---- web: ค่าเริ่มต้น (stage สุดท้าย)
FROM base AS web
