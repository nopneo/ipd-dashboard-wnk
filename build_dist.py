"""สร้างชุดติดตั้งสำหรับโรงพยาบาลอื่น → dist/ipd_dashboard-<version>.zip

ใส่เฉพาะไฟล์กลาง ไม่มี .env / โลโก้และภาพพื้นหลังของ รพ.ผู้พัฒนา / สคริปต์ deploy เฉพาะเซิร์ฟเวอร์ของเรา
"""

import os
import re
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ["app.py", "cache.py", "db.py", "queries.py", "render.py",
         "requirements.txt", ".env.example", "INSTALL.md", "SECURITY.md", "CHANGELOG.md",
         "Dockerfile", ".dockerignore", "LICENSE", "NOTICE.md"]
DIRS = ["templates", "static"]
SKIP = {os.path.join("static", "img", "hospital_logo.png"), os.path.join("static", "img", "hero.jpg")}


def main():
    version = re.search(r'VERSION = "([^"]+)"', open(os.path.join(HERE, "app.py"), encoding="utf-8").read())[1]
    os.makedirs(os.path.join(HERE, "dist"), exist_ok=True)
    out = os.path.join(HERE, "dist", f"ipd_dashboard-{version}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in FILES:
            z.write(os.path.join(HERE, f), f"ipd_dashboard/{f}")
        for d in DIRS:
            for root, _, names in os.walk(os.path.join(HERE, d)):
                for n in sorted(names):
                    full = os.path.join(root, n)
                    rel = os.path.relpath(full, HERE)
                    if rel in SKIP or "__pycache__" in rel:
                        continue
                    z.write(full, "ipd_dashboard/" + rel.replace(os.sep, "/"))
    print(out, os.path.getsize(out), "bytes")
    return out


if __name__ == "__main__":
    main()
