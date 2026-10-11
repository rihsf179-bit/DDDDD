Luca X Shop Bot
===============

ฟังก์ชัน
- บอทโพสต์/อัปเดตแผงจัดการคีย์และแผง Redeem ให้อัตโนมัติเมื่อออนไลน์
- Generate Key 1-100 ต่อครั้ง, เลือก Random / LUCAXSHOP / LUCAXSHOP + ชื่อโปรแกรม
- อายุคีย์เลือกได้ 2 แบบ: เลือกจากเมนู (7 วัน - Lifetime*) หรือ Custom ตั้งเอง พิมพ์เช่น 45 (=45 วัน), 2w, 3m, 1y, 3เดือน (1-36,500 วัน)
- รูปแบบคีย์ลงท้ายด้วยจำนวนวัน เช่น LUCAXSHOP-ABC123-30D
- ผลคีย์ส่งให้คนสร้างแบบส่วนตัว มี Copy page และดาวน์โหลด TXT / CSV / PDF A4
- PDF มีแต่คีย์ทีละบรรทัด ไม่มีเลขลำดับหรือหัวข้อรก ๆ
- Reset HWID, Delete Key, Ban Key, Unban Key, Key Info, Key List
- ลบ/แบน/ปลดแบนได้ 1-25 คีย์ต่อครั้ง
- Key Info แสดงสถานะ, Level, วันที่สร้าง, วันที่เริ่มใช้ และวันหมดอายุเมื่อ API ส่งข้อมูลพอให้คำนวณ
- Redeem room: ตรวจ License Key กับ KeyAuth แล้วให้ Discord role ตาม Level
- คีย์หนึ่งใช้รับยศได้หนึ่งบัญชี Discord; เก็บแค่ SHA-256 ของคีย์ในฐานข้อมูล ไม่เก็บคีย์เต็ม
- Log ผู้ดำเนินการไปห้อง Log และฐานข้อมูล โดยซ่อนคีย์เต็ม
- กำหนด Role ID / User ID ของ GenKey และ Superadmin, ห้อง, รูป, Level-to-Role ได้
- รองรับ Windows, Linux, aaPanel, Pterodactyl และ Docker

ติดตั้งครั้งแรก
1. แตก ZIP
2. คัดลอก .env.example เป็น .env
3. ใส่ DISCORD_BOT_TOKEN, KEYAUTH_SELLER_KEY และ Channel ID ที่ต้องการ
4. ใส่ Role/User ID ใน .env หรือใช้ /setup หรือ /adminconfig หลังบอทออนไลน์
5. ติดตั้ง Python 3.11 ขึ้นไป
6. เปิด Terminal ในโฟลเดอร์บอท แล้วรัน:
   python -m pip install -r requirements.txt
   python -u bot.py

Windows
- ดับเบิลคลิก start_windows.bat
- ปิดหน้าต่างแล้วบอทหยุด
- หากอยากให้ทำงาน 24/7 ต้องใช้เครื่อง/VPS ที่เปิดอยู่เสมอ หรือใช้ Task Scheduler ตั้งให้เริ่มเมื่อเปิดเครื่อง

Linux / VPS / aaPanel
1. อัปโหลดและแตกไฟล์ เช่น /opt/lucaxshop
2. ติดตั้ง Python 3.11+, python3-venv และ python3-pip
3. รัน: bash install_linux.sh
4. กรอก .env แล้วเปิดด้วย: .venv/bin/python -u bot.py
5. สำหรับเปิด 24/7 ให้ตั้ง systemd ตามไฟล์ lucaxshop.service.example

Docker
1. คัดลอก .env.example เป็น .env แล้วตั้งค่า
2. รัน: docker compose up -d --build
3. ดู log: docker compose logs -f
ข้อมูลฐานข้อมูลเก็บใน ./data และต้องเก็บโฟลเดอร์นี้ไว้เวลาย้าย/อัปเดตบอท

Pterodactyl
- อัปโหลดไฟล์โปรเจกต์และตั้ง Startup เป็น: python -u bot.py
- ตั้ง Runtime เป็น Python 3.11 ขึ้นไป
- ติดตั้ง dependency: python -m pip install -r requirements.txt
- ถ้าหน้า Panel มีช่อง Environment Variables จะใส่ค่าแทน .env ได้
- อย่าลบ ./data ตอนอัปเดต เพราะมี Log, ตั้งค่า และรายการ Redeem

การตั้งค่าใน Discord
- บอทโพสต์/อัปเดตแผงใน KEY_CHANNEL_ID และ REDEEM_CHANNEL_ID เมื่อออนไลน์
- /setup: เปิดเมนูตั้งค่าและลองส่งแผงทันที พร้อมบอกสถานะ .env และแต่ละห้อง
- /adminconfig: ตั้งค่าห้อง รูป ยศ และรูปแบบคีย์
- /botstatus: ดูสถานะบอท
- /auditlogs: ดู Log
- คำสั่งแอดมินถูกซ่อนไว้ตามสิทธิ์ Administrator เริ่มต้น หากจะให้ Superadmin role ที่ไม่ใช่ Discord Administrator เห็นคำสั่ง ให้เปิด Server Settings > Integrations > Luca X Shop Bot > Commands และอนุญาตคำสั่งเฉพาะยศนั้น
- ให้บอทมี Manage Roles และย้ายยศบอทให้อยู่เหนือยศที่ต้องแจก
- จำกัดสิทธิ์การมองเห็น KEY_CHANNEL_ID ให้เฉพาะทีมงาน ไม่อย่างนั้นสมาชิกที่เห็นช่องจะเห็นปุ่ม (แต่ยังใช้ไม่ได้หากไม่มีสิทธิ์)
- เปิด Developer Mode ใน Discord เพื่อ Copy ID: Settings > Advanced > Developer Mode แล้วคลิกขวาห้อง/ยศ/สมาชิก > Copy ID

กำหนด Level ยศ Redeem
ใน /adminconfig > Permissions หรือ Redeem Roles ใส่ตัวอย่าง:
1:123456789012345678,2:234567890123456789
เลขหน้าเครื่องหมาย : คือ KeyAuth Level ส่วนเลขหลังคือ Discord Role ID
บอทจะตรวจคีย์ด้วย Seller API ก่อนให้ยศ และป้องกันคีย์เดิมถูกใช้กับบัญชีอื่นในฐานข้อมูลของบอท

รูป/โลโก้
ใช้ /adminconfig > Images แล้วใส่ Direct URL ที่ขึ้นต้นด้วย https:// จะใส่ URL รูป GIF ได้ด้วย
- Logo URL = โลโก้มุมขวาบน
- Banner URL = รูปใหญ่ของ Panel
- รูปใหญ่เพิ่มเติม = รูปที่สองใต้ Panel

รูปแบบคีย์
- Random: ใช้ KEYAUTH_MASK; * คืออักขระสุ่ม และ {days} แทนจำนวนวัน
- Luca X Shop: LUCAXSHOP-สุ่ม-จำนวนวันD
- Luca X Shop + Program: LUCAXSHOP-ชื่อโปรแกรม-สุ่ม-จำนวนวันD
เช่น LUCAXSHOP-A1B2C3-30D หรือ LUCAXSHOP-FIVEM-Z9Y8X7-90D
หมายเหตุ: API ต้องยอมรับตัวอักษรคงที่ใน mask; ถ้า KeyAuth ปฏิเสธ mask ให้ใช้ Random หรือแก้รูปแบบตามข้อความตอบจาก API

ความเสถียร/ความปลอดภัย
- คำสั่งสร้าง/ลบ/แบน/รีเซ็ตจะไม่ลองซ้ำอัตโนมัติเมื่อ timeout เพราะ KeyAuth อาจทำรายการไปแล้ว
- ถ้า Timeout หลังสร้างคีย์ ให้ตรวจ Dashboard ก่อนกดสร้างซ้ำ
- อย่าแชร์ .env, Discord Token หรือ Seller Key
- Seller Key ที่เคยโพสต์ในแชตควรเพิกถอน/หมุนเป็นคีย์ใหม่ก่อนใช้งานจริง
- PDF/TXT/CSV มี License Key จริง เก็บและส่งเฉพาะคนที่ควรได้รับเท่านั้น
- Lifetime* ในเมนูคือ 36,500 วันตาม API ไม่ใช่การรับประกันว่าไม่มีวันหมดอายุ
- Key Info คำนวณวันหมดอายุเมื่อข้อมูล API มีวันเริ่มใช้และระยะเวลาพอ หาก API ไม่ส่งข้อมูลจะระบุว่าไม่ทราบ แทนการเดา
- สถานะ API และสิทธิ์ขึ้นกับบัญชี KeyAuth จริง; หาก Seller API ปฏิเสธ บอทจะแจ้งผล ไม่แสดงว่าสำเร็จปลอม

คำเตือน
ตัวโปรแกรมผ่านการตรวจ Syntax และทดสอบฟังก์ชันทั่วไปในเครื่องสร้างไฟล์ แต่ยังต้องทดสอบการเชื่อม Discord/KeyAuth กับ Token และ Seller Key ของคุณเอง


แก้ปัญหา “ไม่พบ Seller Key”
- ไฟล์ `.env` ต้องอยู่ในโฟลเดอร์เดียวกับ `bot.py`
- ใช้บรรทัด `KEYAUTH_SELLER_KEY=ใส่คีย์ของคุณ` (ไม่ต้องใส่เครื่องหมาย <> )
- ตรวจว่าไฟล์ชื่อ `.env` จริง ไม่ใช่ `.env.txt` ใน Windows ให้เปิด View > File name extensions
- บันทึกไฟล์ แล้วปิด/เปิดบอทใหม่หนึ่งครั้ง
- เวอร์ชันนี้อ่านค่า Seller Key จากไฟล์ `.env` ใหม่ทุกครั้งที่เรียก API และรองรับชื่อเก่า `SELLER_KEY` / `KEYAUTH_SECRET` ด้วย โดยไม่แสดงค่าคีย์ใน Log

แก้ปัญหาเวอร์ชันนี้
- ข้อความ Error จะไม่แทรก [hidden] ระหว่างตัวอักษรเมื่อ Seller Key ว่าง
- /setup เป็นคำสั่งที่มีอยู่จริง และซิงก์ Slash Commands ไปยังเซิร์ฟเวอร์ที่บอทอยู่เพื่อให้เห็นเร็วขึ้น
- หากแผงไม่ส่ง บอทจะแจ้งว่าห้องยังไม่ตั้งค่า/บอทขาดสิทธิ์/Discord ปฏิเสธ และบันทึกสาเหตุใน Log
- /botstatus แสดงว่าเจอไฟล์ .env หรือไม่ และ Seller Key มีค่าหรือไม่ โดยไม่แสดงคีย์จริง

วิธีอัปเดตจากเวอร์ชันก่อน
1. ปิดบอทก่อน
2. สำรองไฟล์ .env และโฟลเดอร์ data ไว้ก่อนทุกครั้ง
3. แตก ZIP เวอร์ชันแก้ไขทับโฟลเดอร์บอทเดิม (ZIP นี้ไม่มีไฟล์ .env หรือฐานข้อมูล data จึงไม่ควรเขียนทับข้อมูลของคุณ)
4. เปิด start_windows.bat อีกครั้ง แล้วใช้ /setup
5. ใน /setup ดูบรรทัดไฟล์ตั้งค่า, Seller Key และสถานะห้องจัดการคีย์ หากห้องยังไม่ตั้งค่า กด Channels แล้วใส่ ID ห้อง
