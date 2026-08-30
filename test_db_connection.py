"""
ทดสอบว่า .env ตั้งค่าถูกต้องและเชื่อมต่อ MySQL ได้จริงหรือไม่
รันด้วยคำสั่ง: python test_db_connection.py
"""
import os
from dotenv import load_dotenv
import mysql.connector

load_dotenv()

db_password = os.environ.get('DB_PASSWORD')
print("ค่าที่อ่านได้จาก .env:")
print("  DB_HOST     =", repr(os.environ.get('DB_HOST')))
print("  DB_USER     =", repr(os.environ.get('DB_USER')))
print("  DB_PASSWORD =", repr(db_password))
print("  DB_NAME     =", repr(os.environ.get('DB_NAME')))
print("  DB_PORT     =", repr(os.environ.get('DB_PORT')))
print()

if db_password is None:
    print("❌ อ่านไฟล์ .env ไม่เจอเลย (DB_PASSWORD เป็น None)")
    print("   เช็คว่า .env อยู่โฟลเดอร์เดียวกับไฟล์นี้ และลง python-dotenv แล้ว")
    raise SystemExit(1)

try:
    conn = mysql.connector.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        user=os.environ.get('DB_USER', 'root'),
        password=db_password,
        database=os.environ.get('DB_NAME', 'blur_db'),
        port=int(os.environ.get('DB_PORT', 3306)),
    )
    print("✅ เชื่อมต่อ MySQL สำเร็จ! รหัสผ่านและค่าตั้งค่าถูกต้องทั้งหมด")
    conn.close()
except mysql.connector.Error as e:
    print("❌ เชื่อมต่อไม่สำเร็จ:")
    print("  ", e)
