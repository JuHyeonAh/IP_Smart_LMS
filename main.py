from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from datetime import datetime, timedelta
import random
import string

from bson import ObjectId  # ✅ 코드 상세 조회용

from db import attendance_collection, code_collection


def get_client_ip(request: Request) -> str:
    # 클라우드(프록시 뒤)에서는 X-Forwarded-For에 실제 클라이언트 IP가 들어옴
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    # 로컬/테스트 환경
    return request.client.host or "unknown"

app = FastAPI()

templates = Jinja2Templates(directory="templates")
app.mount(
    "/static",
    StaticFiles(directory="templates/static"),
    name="static"
)


# --------- 유틸 함수들 --------- #

def generate_code(length: int = 6) -> str:
    return "".join(random.choices(string.digits, k=length))

CAMPUS_PREFIXES = ["210.108.18."]  # 교내 IP 대역

def classify_ip(ip: str) -> tuple[str, str]:
    # 개발 중 로컬 접속
    if ip.startswith("127.") or ip == "::1":
        return "DEV", "💻 로컬 개발 환경 (교내 여부 판단 안 함)"

    # 교내 WiFi (예: 210.108.18.88, 210.108.18.71, 같은 대역)
    if any(ip.startswith(prefix) for prefix in CAMPUS_PREFIXES):
        return "NORMAL", "✅ 교내 WiFi (신뢰도 높음)"

    # 사설망 / 인접 강의실 공유 AP 등
    if ip.startswith("10.") or ip.startswith("192.168."):
        return "WARNING", "⚠ 인접 강의실/내부망 (확인 필요)"

    # 나머지는 외부망(LTE, 집 와이파이 등) 의심
    return "SUSPICIOUS", "❗ 외부망 (LTE 등) 의심됨"


async def get_active_sessions():
    """
    현재 유효한 출석 코드가 있는 수업(날짜) 목록을 만든다.
    - 같은 날짜(session_date)는 한 번만 표시
    - 각 항목에 유효 종료 시간 문자열도 같이 전달
    """
    now = datetime.now()
    cursor = code_collection.find(
        {"valid_until": {"$gt": now}}
    ).sort("valid_until", 1)

    codes = await cursor.to_list(length=100)

    sessions = []
    seen_dates = set()

    for c in codes:
        sd = c.get("session_date")
        if sd in seen_dates:
            continue
        seen_dates.add(sd)

        sessions.append(
            {
                "session_date": sd,
                "end_str": c["valid_until"].strftime("%m/%d %H:%M"),
            }
        )

    return sessions

# --------- 학생용 --------- #

@app.get("/student")
async def student_page(request: Request):
    sessions = await get_active_sessions()

    return templates.TemplateResponse(
        "student.html",
        {
            "request": request,
            "result": None,
            "ip_status_message": None,
            "sessions": sessions,   # ✅ 유효한 수업 목록
        }
    )
@app.post("/student/attend")
async def student_attend(
    request: Request,
    student_name: str = Form(...),
    session_date: str = Form(...),
    attendance_code: str = Form(...)
):
    client_ip = get_client_ip(request)
    ip_status, ip_status_message = classify_ip(client_ip)
    now = datetime.now()  # 로컬(KST) 기준

    code_doc = await code_collection.find_one(
        {
            "session_date": session_date,
            "attendance_code": attendance_code,
            "valid_until": {"$gt": now}
        }
    )

    if not code_doc:
        result = "출석 코드가 유효하지 않거나 시간이 만료되었습니다."
    else:
        existing = await attendance_collection.find_one(
            {
                "session_date": session_date,
                "attendance_code": attendance_code,
                "student_name": student_name
            }
        )

        if existing:
            result = "이미 출석이 처리되었습니다."
        else:
            attend_doc = {
                "student_name": student_name,
                "session_date": session_date,
                "attendance_code": attendance_code,
                "ip": client_ip,
                "ip_status": ip_status,
                "ip_status_message": ip_status_message,
                "timestamp": now
            }
            await attendance_collection.insert_one(attend_doc)
            result = "출석이 정상적으로 처리되었습니다."

    # ✅ 다시 유효한 수업 목록 가져오기
    sessions = await get_active_sessions()

    return templates.TemplateResponse(
        "student.html",
        {
            "request": request,
            "result": result,
            "ip_status_message": ip_status_message,
            "sessions": sessions,
        }
    )

# --------- 교수용 메인 화면 --------- #

@app.get("/teacher")
async def teacher_page(request: Request):
    """
    - 현재 유효한 출석 코드 목록(클릭하면 상세로 이동)
    - 지난 출석 코드 목록
    """
    now = datetime.now()

    # 아직 유효한 코드들
    active_codes_cursor = code_collection.find(
        {"valid_until": {"$gt": now}}
    ).sort("valid_until", 1)
    active_codes = await active_codes_cursor.to_list(length=20)

    # 지난 출석(만료된 코드들)
    past_codes_cursor = code_collection.find(
        {"valid_until": {"$lte": now}}
    ).sort("valid_until", -1)
    past_codes = await past_codes_cursor.to_list(length=30)

    return templates.TemplateResponse(
        "teacher.html",
        {
            "request": request,
            "active_codes": active_codes,
            "past_codes": past_codes,
        }
    )


@app.post("/teacher/create-code")
async def create_code(
    session_date: str = Form(...),
    minutes_valid: int = Form(10)
):
    """
    하나의 과목에서 날짜(session_date)별로 출결.
    """
    code = generate_code(6)

    now = datetime.now()
    valid_until = now + timedelta(minutes=minutes_valid)

    doc = {
        "session_date": session_date,
        "attendance_code": code,
        "created_at": now,
        "valid_until": valid_until,
    }
    await code_collection.insert_one(doc)

    return RedirectResponse(url="/teacher", status_code=303)


# --------- 코드별 상세 화면 --------- #

@app.get("/teacher/code/{code_id}")
async def teacher_code_detail(request: Request, code_id: str):
    """
    특정 출석 코드에 대한:
    - 상태(진행중/만료)
    - 타이머
    - 출석 현황
    - IP 주소 확인 필요 목록
    """
    try:
        oid = ObjectId(code_id)
    except Exception:
        raise HTTPException(status_code=404, detail="잘못된 코드 ID 입니다.")

    code_doc = await code_collection.find_one({"_id": oid})
    if not code_doc:
        raise HTTPException(status_code=404, detail="출석 코드 정보를 찾을 수 없습니다.")

    now = datetime.now()
    is_active = code_doc["valid_until"] > now

    # 이 코드/날짜에 대한 모든 출석 기록
    attendance_cursor = attendance_collection.find(
        {
            "session_date": code_doc["session_date"],
            "attendance_code": code_doc["attendance_code"],
        }
    ).sort("timestamp", -1)
    attendance_list = await attendance_cursor.to_list(length=500)

    # IP 확인 필요 (정상 아닌 것들)
    suspicious_cursor = attendance_collection.find(
        {
            "session_date": code_doc["session_date"],
            "attendance_code": code_doc["attendance_code"],
            "ip_status": {"$ne": "NORMAL"},
        }
    ).sort("timestamp", -1)
    suspicious_list = await suspicious_cursor.to_list(length=500)

    return templates.TemplateResponse(
        "teacher_detail.html",
        {
            "request": request,
            "code": code_doc,
            "is_active": is_active,
            "attendance_list": attendance_list,
            "suspicious_list": suspicious_list,
        }
    )