import os

from dotenv import load_dotenv
import motor.motor_asyncio
import certifi  # ✅ Atlas TLS 인증서용

load_dotenv()

MONGO_URL = os.getenv("MONGO_URL")

# MongoDB Atlas 클라이언트 생성
client = motor.motor_asyncio.AsyncIOMotorClient(
    MONGO_URL,
    tls=True,                  # ✅ TLS 사용
    tlsCAFile=certifi.where()  # ✅ 신뢰할 수 있는 CA 번들 지정
)

db = client["smart_attendance_ip"]

attendance_collection = db["attendance"]
code_collection = db["codes"]