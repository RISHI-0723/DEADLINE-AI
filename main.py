from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from groq import Groq
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from passlib.context import CryptContext
from jose import JWTError, jwt
from datetime import datetime, timedelta

import sqlite3
import os
import json
import re

load_dotenv()

# ---------------- APP ----------------

limiter = Limiter(key_func=get_remote_address)
app     = FastAPI()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- SECURITY ----------------

SECRET_KEY        = os.getenv("SECRET_KEY", "deadlineai-secret-change-in-production-xk92jd8s7f")
ALGORITHM         = "HS256"
TOKEN_EXPIRE_DAYS = 7
pwd_context       = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer            = HTTPBearer()

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def create_token(email: str) -> str:
    expire  = datetime.utcnow() + timedelta(days=TOKEN_EXPIRE_DAYS)
    payload = {"sub": email, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str) -> str:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email   = payload.get("sub")
        if not email:
            raise HTTPException(status_code=401, detail="Invalid token")
        return email
    except JWTError:
        raise HTTPException(status_code=401, detail="Token expired or invalid. Please login again.")

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer)) -> str:
    return decode_token(credentials.credentials)

def validate_email(email: str) -> bool:
    return bool(re.match(r"^[\w\.-]+@[\w\.-]+\.\w+$", email.strip()))

# ---------------- GROQ ----------------

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

def call_ai(prompt: str) -> str:
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1000
    )
    return response.choices[0].message.content.strip()

def safe_parse_json(text: str):
    try:
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end   = text.rfind("}") + 1
        if start != -1 and end > start:
            return json.loads(text[start:end])
        start = text.find("[")
        end   = text.rfind("]") + 1
        if start != -1 and end > start:
            return json.loads(text[start:end])
        raise

# ---------------- DATABASE ----------------

conn   = sqlite3.connect("deadlines.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    email      TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    password   TEXT NOT NULL,
    created_at TEXT NOT NULL
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS deadlines (
    id         TEXT PRIMARY KEY,
    subject    TEXT,
    deadline   TEXT,
    urgency    TEXT,
    email      TEXT,
    created_at TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS push_subscriptions (
    email        TEXT PRIMARY KEY,
    subscription TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS login_attempts (
    ip           TEXT,
    email        TEXT,
    attempted_at TEXT
)
""")

conn.commit()

# ---------------- SCHEDULER ----------------

scheduler = BackgroundScheduler(
    job_defaults={"misfire_grace_time": 60 * 60}
)

def auto_delete_passed_deadlines():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    cursor.execute("DELETE FROM deadlines WHERE deadline < ?", (now,))
    conn.commit()
    print(f"Auto-cleaned passed deadlines at {now}")

scheduler.add_job(auto_delete_passed_deadlines, "interval", hours=1)

def saturday_nudge():
    print("Saturday nudge firing!")
    cursor.execute("SELECT DISTINCT email FROM deadlines")
    emails = [r[0] for r in cursor.fetchall()]
    for email in emails:
        deadlines = check_conflicts(email)
        if deadlines:
            items = "\n".join([f"- {d['subject']} by {d['deadline']}" for d in deadlines])
            body  = f"Hey!\n\nYou have these deadlines coming up:\n\n{items}\n\nStay ahead of it!"
        else:
            body = "Hey!\n\nYou have no deadlines set. Open DeadlineAI and add this week's tasks!"
        send_email(email, "DeadlineAI — Weekly Check-in", body)
        cursor.execute("SELECT subscription FROM push_subscriptions WHERE email=?", (email,))
        row = cursor.fetchone()
        if row:
            send_push(row[0], "DeadlineAI", body[:100])

scheduler.add_job(saturday_nudge, "cron", day_of_week="sat", hour=9, minute=0)
scheduler.start()

# ---------------- DATA MODELS ----------------

class RegisterInput(BaseModel):
    name:     str
    email:    str
    password: str

class LoginInput(BaseModel):
    email:    str
    password: str

class ChatInput(BaseModel):
    message: str

class PushSubscription(BaseModel):
    subscription: dict

conversation_history = []

# ---------------- BRUTE FORCE ----------------

def check_brute_force(ip: str, email: str):
    window = (datetime.now() - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        "SELECT COUNT(*) FROM login_attempts WHERE ip=? AND email=? AND attempted_at > ?",
        (ip, email, window)
    )
    if cursor.fetchone()[0] >= 5:
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again in 15 minutes.")

def record_attempt(ip: str, email: str):
    cursor.execute(
        "INSERT INTO login_attempts VALUES (?, ?, ?)",
        (ip, email, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()

def clear_attempts(ip: str, email: str):
    cursor.execute("DELETE FROM login_attempts WHERE ip=? AND email=?", (ip, email))
    conn.commit()

# ---------------- EMAIL ----------------

def send_email(to: str, subject: str, body: str):
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg            = MIMEText(body)
        msg["Subject"] = subject
        msg["From"]    = os.getenv("YOUR_EMAIL")
        msg["To"]      = to
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(os.getenv("YOUR_EMAIL"), os.getenv("YOUR_EMAIL_PASSWORD"))
            server.send_message(msg)
        print(f"Email sent to {to}")
    except Exception as e:
        print("Email failed:", e)

# ---------------- PUSH ----------------

def send_push(subscription_json: str, title: str, body: str):
    try:
        from pywebpush import webpush
        subscription = json.loads(subscription_json)
        webpush(
            subscription_info=subscription,
            data=json.dumps({"title": title, "body": body}),
            vapid_private_key=os.getenv("VAPID_PRIVATE_KEY"),
            vapid_claims={"sub": f"mailto:{os.getenv('YOUR_EMAIL')}"}
        )
    except Exception as e:
        print("Push failed:", e)

# ---------------- SMART REMINDERS ----------------

def send_reminder(subject: str, email: str, time_left: str):
    print(f"Reminder triggered: {subject} — {time_left}")
    if "NOW" in time_left:
        emoji = "🚨"; urgency = "DEADLINE NOW"
    elif "HURRY" in time_left or "10 min" in time_left:
        emoji = "⚠️"; urgency = "HURRY UP"
    else:
        emoji = "⏰"; urgency = "Reminder"
    body = f"Hey!\n\n{emoji} DeadlineAI {urgency}\n\nSubject: {subject}\nStatus: {time_left}\n\n{'🚨 SUBMIT IMMEDIATELY!' if 'NOW' in time_left else 'Don t miss it!'}"
    send_email(email, f"{emoji} {urgency}: {subject}", body)
    cursor.execute("SELECT subscription FROM push_subscriptions WHERE email=?", (email,))
    row = cursor.fetchone()
    if row:
        send_push(row[0], f"{emoji} {subject}", time_left)

def schedule_smart_reminders(subject: str, email: str, deadline_str: str):
    try:
        deadline_dt = datetime.strptime(deadline_str, "%Y-%m-%d %H:%M")
        now         = datetime.now()
        diff_mins   = (deadline_dt - now).total_seconds() / 60
        if diff_mins <= 0:
            return
        if diff_mins > 30:
            for mins_before in [30, 10]:
                remind_at = deadline_dt - timedelta(minutes=mins_before)
                if remind_at > now:
                    scheduler.add_job(send_reminder, "date", run_date=remind_at,
                        args=[subject, email, f"{mins_before} minutes left!"])
            scheduler.add_job(send_reminder, "date", run_date=deadline_dt,
                args=[subject, email, "Deadline is NOW! Submit immediately!"])
        else:
            remind_at = now + timedelta(minutes=2)
            while remind_at < deadline_dt:
                mins_left = int((deadline_dt - remind_at).total_seconds() / 60)
                scheduler.add_job(send_reminder, "date", run_date=remind_at,
                    args=[subject, email, f"HURRY! Only {mins_left} minutes left!"])
                remind_at += timedelta(minutes=10)
            scheduler.add_job(send_reminder, "date", run_date=deadline_dt,
                args=[subject, email, "Deadline is NOW! Submit immediately!"])
        print(f"Smart reminders scheduled for: {subject}")
    except Exception as e:
        print(f"Smart reminder error: {e}")

# ---------------- AI FUNCTIONS ----------------

def extract_deadline(message: str):
    prompt = f"""
Extract ALL deadlines from this message: "{message}"

Return ONLY a raw JSON array:
[
  {{
    "subject": "assignment or exam name",
    "deadline": "YYYY-MM-DD HH:MM",
    "urgency": "high/medium/low"
  }}
]

Today is {datetime.now().strftime("%Y-%m-%d %H:%M")}.
Rules:
- If no time mentioned assume 23:59
- urgency: high if due within 24h, medium if within 3 days, low otherwise
- No markdown, no explanation
"""
    return safe_parse_json(call_ai(prompt))

def extract_email_details(message: str) -> dict:
    prompt = f"""
Extract email details from: "{message}"
Return ONLY raw JSON:
{{"to":"recipient email","subject":"email subject","body":"email body"}}
Write a professional friendly body. No markdown.
"""
    return safe_parse_json(call_ai(prompt))

# ---------------- TOOL FUNCTIONS ----------------

def check_conflicts(email: str):
    cursor.execute("SELECT subject, deadline FROM deadlines WHERE email=?", (email,))
    return [{"subject": r[0], "deadline": r[1]} for r in cursor.fetchall()]

def suggest_reschedule(message: str):
    prompt = f'Suggest a better deadline for: "{message}". Return ONLY raw JSON: {{"new_deadline":"YYYY-MM-DD HH:MM","reason":"..."}}'
    return safe_parse_json(call_ai(prompt))

def send_summary(email: str):
    deadlines = check_conflicts(email)
    if deadlines:
        items = "\n".join([f"- {d['subject']} by {d['deadline']}" for d in deadlines])
        body  = f"Your deadline summary:\n\n{items}\n\nStay on top of it!"
    else:
        body = "You have no upcoming deadlines. You're all clear!"
    send_email(email, "Your DeadlineAI Summary", body)
    return {"sent": True, "count": len(deadlines)}

def delete_passed_deadlines(email: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    cursor.execute("DELETE FROM deadlines WHERE email=? AND deadline < ?", (email, now))
    conn.commit()
    return {"deleted": cursor.rowcount}

def delete_deadline(email: str, message: str):
    prompt = f'''
What deadline name should be deleted based on: "{message}"?

Available deadlines:
{json.dumps(check_conflicts(email))}

Return ONLY raw JSON: {{"name": "exact subject name from the list above, or null if delete all"}}
No markdown, no explanation.
'''
    try:
        details = safe_parse_json(call_ai(prompt))
        name    = details.get("name")
        if not name or name == "null":
            cursor.execute("DELETE FROM deadlines WHERE email=?", (email,))
            conn.commit()
            return {"deleted": True, "message": "Deleted all your deadlines"}
        cursor.execute(
            "DELETE FROM deadlines WHERE email=? AND subject=?",
            (email, name)
        )
        conn.commit()
        if cursor.rowcount == 0:
            return {"deleted": False, "error": f"Could not find '{name}'"}
        return {"deleted": True, "message": f"Deleted '{name}'"}
    except Exception as e:
        return {"deleted": False, "error": str(e)}

def rename_deadline(email: str, message: str):
    prompt = f'Extract rename from: "{message}". Return ONLY raw JSON: {{"old_name":"...","new_name":"..."}}'
    try:
        details  = safe_parse_json(call_ai(prompt))
        old_name = details.get("old_name") or details.get("from")
        new_name = details.get("new_name") or details.get("to")
        if not old_name or not new_name:
            return {"renamed": False, "error": "Could not understand names"}
        cursor.execute("UPDATE deadlines SET subject=? WHERE email=? AND LOWER(subject) LIKE ?",
            (new_name, email, f"%{old_name.lower()}%"))
        conn.commit()
        if cursor.rowcount == 0:
            return {"renamed": False, "error": f"Could not find '{old_name}'"}
        return {"renamed": True, "from": old_name, "to": new_name}
    except Exception as e:
        return {"renamed": False, "error": str(e)}

def safe_send_custom_email(message: str):
    try:
        details = extract_email_details(message)
        to      = details.get("to") or details.get("to_email") or details.get("email") or details.get("recipient")
        subject = details.get("subject") or details.get("title") or "Message from DeadlineAI"
        body    = details.get("body") or details.get("message") or details.get("content") or message
        if not to:
            return {"sent": False, "error": "No recipient email found."}
        send_email(to, subject, body)
        return {"sent": True, "to": to}
    except Exception as e:
        return {"sent": False, "error": str(e)}

# ---------------- TOOL REGISTRY ----------------

def execute_tool(tool_name: str, message: str, email: str):
    tools = {
        "extract_deadline":        lambda: extract_deadline(message),
        "check_conflicts":         lambda: check_conflicts(email),
        "suggest_reschedule":      lambda: suggest_reschedule(message),
        "send_summary":            lambda: send_summary(email),
        "delete_passed_deadlines": lambda: delete_passed_deadlines(email),
        "delete_deadline":         lambda: delete_deadline(email, message),
        "rename_deadline":         lambda: rename_deadline(email, message),
        "send_custom_email":       lambda: safe_send_custom_email(message),
        "do_nothing":              lambda: {"status": "no action needed"},
    }
    fn = tools.get(tool_name)
    if not fn:
        return {"error": f"unknown tool: {tool_name}"}
    return fn()

# ---------------- PLANNER ----------------

def plan(user_message: str, history: list) -> dict:
    prompt = f"""
You are a deadline management agent. Pick the right tool.

Tools:
- extract_deadline: user giving a new deadline to save
- check_conflicts: user wants to see their deadlines
- suggest_reschedule: user wants to reschedule something
- send_summary: user wants a summary emailed to them
- delete_passed_deadlines: delete only past deadlines
- delete_deadline: delete a specific deadline by name or all
- rename_deadline: rename a deadline
- send_custom_email: send an email to someone else
- do_nothing: casual chat, greetings, no action needed

Return ONLY raw JSON: {{"tool": "...", "reason": "..."}}

History: {json.dumps(history[-6:])}
Message: "{user_message}"
"""
    return safe_parse_json(call_ai(prompt))

# ---------------- AGENT LOOP ----------------

def agent_loop(message: str, email: str) -> dict:
    conversation_history.append({"role": "user", "content": message})
    try:
        action    = plan(message, conversation_history)
        tool_name = action.get("tool", "do_nothing")
    except Exception:
        tool_name = "do_nothing"

    if tool_name == "extract_deadline":
        try:
            extracted_list = extract_deadline(message)
            saved          = []
            for extracted in extracted_list:
                deadline_id  = f"deadline_{datetime.now().timestamp()}"
                deadline_str = extracted.get("deadline", "")
                subject      = extracted.get("subject", "Unknown")
                cursor.execute(
                    "INSERT INTO deadlines VALUES (?, ?, ?, ?, ?, ?)",
                    (deadline_id, subject, deadline_str,
                     extracted.get("urgency", "medium"), email,
                     datetime.now().strftime("%Y-%m-%d %H:%M"))
                )
                conn.commit()
                saved.append(subject)
                schedule_smart_reminders(subject, email, deadline_str)
            result = {"saved": saved}
        except Exception as e:
            result = {"error": str(e)}
    else:
        try:
            result = execute_tool(tool_name, message, email)
        except Exception as e:
            result = {"error": str(e)}

    conversation_history.append({"role": "assistant", "content": f"Used {tool_name}: {result}"})
    return {"steps": [{"tool": tool_name, "result": result}], "final": result}

# ---------------- GITAM SCRAPER ----------------

def save_scraped_deadline(subject: str, deadline_str: str, email: str):
    """Save a scraped deadline directly to DB"""
    try:
        deadline_id = f"deadline_{datetime.now().timestamp()}"
        cursor.execute(
            "INSERT OR IGNORE INTO deadlines VALUES (?, ?, ?, ?, ?, ?)",
            (deadline_id, subject, deadline_str, "high", email,
             datetime.now().strftime("%Y-%m-%d %H:%M"))
        )
        conn.commit()
        schedule_smart_reminders(subject, email, deadline_str)
        return True
    except Exception as e:
        print(f"Save error: {e}")
        return False

# ================================================================
# AUTH ENDPOINTS
# ================================================================

@app.post("/register")
@limiter.limit("5/minute")
async def register(request: Request, data: RegisterInput):
    name     = data.name.strip()
    email    = data.email.strip().lower()
    password = data.password.strip()

    if len(name) < 2:
        raise HTTPException(status_code=400, detail="Name must be at least 2 characters.")
    if not validate_email(email):
        raise HTTPException(status_code=400, detail="Invalid email address.")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

    cursor.execute("SELECT email FROM users WHERE email=?", (email,))
    if cursor.fetchone():
        raise HTTPException(status_code=409, detail="Account already exists. Please login.")

    try:
        cursor.execute(
            "INSERT INTO users VALUES (?, ?, ?, ?)",
            (email, name, hash_password(password), datetime.now().strftime("%Y-%m-%d %H:%M"))
        )
        conn.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not create account: {str(e)}")

    token = create_token(email)
    return {"status": "success", "token": token, "user": {"name": name, "email": email}}


@app.post("/login")
@limiter.limit("10/minute")
async def login(request: Request, data: LoginInput):
    ip    = request.client.host
    email = data.email.strip().lower()

    if not validate_email(email):
        raise HTTPException(status_code=400, detail="Invalid email address.")

    check_brute_force(ip, email)

    cursor.execute("SELECT name, email, password FROM users WHERE email=?", (email,))
    row = cursor.fetchone()

    if not row:
        record_attempt(ip, email)
        raise HTTPException(status_code=401, detail="No account found. Please sign up first.")

    if not verify_password(data.password, row[2]):
        record_attempt(ip, email)
        raise HTTPException(status_code=401, detail="Wrong password. Please try again.")

    clear_attempts(ip, email)
    token = create_token(email)
    return {"status": "success", "token": token, "user": {"name": row[0], "email": row[1]}}


@app.get("/me")
async def get_me(email: str = Depends(get_current_user)):
    cursor.execute("SELECT name, email, created_at FROM users WHERE email=?", (email,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return {"name": row[0], "email": row[1], "created_at": row[2]}


@app.post("/chat")
@limiter.limit("20/minute")
async def chat(request: Request, data: ChatInput, email: str = Depends(get_current_user)):
    try:
        result = agent_loop(data.message, email)
        return {"status": "success", **result}
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/deadlines")
@limiter.limit("30/minute")
async def get_deadlines(request: Request, email: str = Depends(get_current_user)):
    cursor.execute(
        "SELECT id, subject, deadline, urgency, email, created_at FROM deadlines WHERE email=?",
        (email,)
    )
    rows = cursor.fetchall()
    return [
        {"id": r[0], "subject": r[1], "deadline": r[2],
         "urgency": r[3], "email": r[4], "created_at": r[5]}
        for r in rows
    ]


@app.post("/subscribe-push")
@limiter.limit("10/minute")
async def subscribe_push(request: Request, data: PushSubscription, email: str = Depends(get_current_user)):
    try:
        cursor.execute(
            "INSERT OR REPLACE INTO push_subscriptions VALUES (?, ?)",
            (email, json.dumps(data.subscription))
        )
        conn.commit()
        return {"status": "subscribed"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/sync-gitam")
@limiter.limit("3/minute")
async def sync_gitam(request: Request, email: str = Depends(get_current_user)):
    try:
        from scraper import run_scraper
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        added = run_scraper(token)
        return {"status": "success", "added": added or [], "count": len(added or [])}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/health")
async def health():
    return {"status": "ok"}