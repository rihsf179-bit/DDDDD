โฮสต์ฟรีสำหรับ Luca X Shop Bot
================================

แนะนำให้ลอง Oracle Cloud Always Free VM ถ้าต้องการเครื่อง Linux เปิดต่อเนื่อง โดยเป็น VM ฟรีตามโควตาที่กำหนด แต่ความพร้อมใช้งานขึ้นกับ Region/Capacity ของ Oracle และการสมัครบัญชี ไม่สามารถรับประกัน uptime 100% ได้
เอกสารทางการ: https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm

ภาพรวมวิธีลงบน Oracle Cloud
1. สร้าง Compute VM ที่อยู่ใน Always Free allowance
2. เปิด SSH และเชื่อมต่อเครื่องด้วย SSH
3. ติดตั้ง Python 3.11+ และ Git หรืออัปโหลด ZIP
4. แตกไฟล์ไปที่ /opt/lucaxshop
5. รัน bash install_linux.sh แล้วกรอกไฟล์ .env
6. ตั้ง systemd ด้วยไฟล์ lucaxshop.service.example (แก้ User และ path ให้ตรงเครื่อง)
7. ใช้ sudo systemctl enable --now lucaxshop แล้วตรวจด้วย sudo systemctl status lucaxshop

อย่าเปิดพอร์ตเว็บเพิ่มเพื่อให้ Discord Bot ทำงาน เพราะบอทนี้เชื่อมต่อ Discord Gateway ออกไปเอง

ทางเลือกอื่น
- aaPanel: อัปโหลดไฟล์ แล้วสร้าง Python Project/Startup Command หรือใช้ systemd
- Pterodactyl: ใช้ Startup `python -u bot.py` และเก็บโฟลเดอร์ data ไว้ใน persistent storage
- Docker host: `docker compose up -d --build`
- Render Free Web Service บางประเภทพักเครื่องเมื่อไม่มี request จึงไม่เหมาะกับ Discord Gateway bot ที่ต้องออนไลน์ตลอด

ไม่มีบริการฟรีไหนรับประกัน 24/7 เสมอไป หากบอทใช้รับเงินหรือคีย์ของลูกค้าจริง ให้เก็บฐานข้อมูลสำรองและพิจารณา VPS ที่มี SLA
