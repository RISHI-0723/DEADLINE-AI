from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from groq import Groq

import sqlite3
import os
import json
from datetime import datetime

load_dotenv()

app = FastAPI()

# ---------------- CORS ----------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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

conn = sqlite3.connect("deadlines.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS deadlines (
    id TEXT PRIMARY KEY,
    subject TEXT,
    deadline TEXT,
    urgency TEXT,
    email TEXT,
    phone TEXT,
    created_at TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS push_subscriptions (
    email TEXT PRIMARY KEY,
    subscription TEXT
)
""")

conn.commit()

# ---------------- SCHEDULER ----------------

scheduler = BackgroundScheduler(
    job_defaults={
        'misfire_grace_time': 60 * 60
    }
)

def auto_delete_passed_deadlines():
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    cursor.execute("DELETE FROM deadlines WHERE deadline < ?", (now,))
    conn.commit()
    print(f"Auto-cleaned passed deadlines at {now}")

scheduler.add_job(
    auto_delete_passed_deadlines,
    "interval",
    hours=1
)

def saturday_nudge():
    print("Saturday nudge firing!")
    cursor.execute("SELECT DISTINCT email FROM deadlines")
    emails = [r[0] for r in cursor.fetchall()]
    for email in emails:
        deadlines = check_conflicts(email)
        if deadlines:
            items = "\n".join([f"- {d['subject']} by {d['deadline']}" for d in deadlines])
            body = f"Hey!\n\nYou have these deadlines coming up:\n\n{items}\n\nStay ahead of it!"
        else:
            body = "Hey!\n\nYou have no deadlines set. Open DeadlineAI and add this week's tasks before you forget!"
        send_email(email, "DeadlineAI — Saturday Check-in", body)
        cursor.execute("SELECT subscription FROM push_subscriptions WHERE email=?", (email,))
        row = cursor.fetchone()
        if row:
            send_push(row[0], "DeadlineAI Reminder", body)

scheduler.add_job(
    saturday_nudge,
    "cron",
    day_of_week="sat",
    hour=12,
    minute=0
)

scheduler.start()

# ---------------- DATA MODELS ----------------

class DeadlineInput(BaseModel):
    message: str
    email: str
    phone: str

class ChatInput(BaseModel):
    message: str
    email: str
    phone: str

class PushSubscription(BaseModel):
    email: str
    subscription: dict

conversation_history = []

# ---------------- PUSH NOTIFICATION ----------------

def send_push(subscription_json: str, title: str, body: str):
    try:
        from pywebpush import webpush, WebPushException
        subscription = json.loads(subscription_json)
        webpush(
            subscription_info=subscription,
            data=json.dumps({"title": title, "body": body}),
            vapid_private_key=os.getenv("VAPID_PRIVATE_KEY"),
            vapid_claims={"sub": f"mailto:{os.getenv('YOUR_EMAIL')}"}
        )
        print(f"Push sent to {subscription['endpoint'][:40]}...")
    except Exception as e:
        print("Push failed:", e)

# ---------------- EMAIL ----------------

def send_email(email: str, subject: str, body: str):
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"]    = os.getenv("YOUR_EMAIL")
        msg["To"]      = email
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(os.getenv("YOUR_EMAIL"), os.getenv("YOUR_EMAIL_PASSWORD"))
            server.send_message(msg)
        print(f"Email sent to {email}")
    except Exception as e:
        print("Email failed:", e)

# ---------------- NOTIFICATIONS ----------------

def send_reminder(subject: str, email: str, phone: str, time_left: str):
    print(f"Reminder triggered: {subject}")
    body = f"Hey!\n\nDeadlineAI Reminder\n\nSubject: {subject}\nTime: {time_left}\n\nDon't miss it!"
    send_email(email, f"⏰ Reminder: {subject}", body)
    cursor.execute("SELECT subscription FROM push_subscriptions WHERE email=?", (email,))
    row = cursor.fetchone()
    if row:
        send_push(row[0], f"Deadline: {subject}", f"Due at {time_left}")

# ---------------- AI EXTRACTION ----------------

def extract_deadline(message: str):
    prompt = f"""
Extract ALL deadlines from this message: "{message}"

Return ONLY raw JSON array like this:

[
  {{
    "subject": "assignment or exam name",
    "deadline": "YYYY-MM-DD HH:MM",
    "urgency": "high/medium/low",
    "reminder_times": ["YYYY-MM-DD HH:MM", "YYYY-MM-DD HH:MM"]
  }}
]

Today is {datetime.now().strftime("%Y-%m-%d %H:%M")}.

Rules:
- If no time mentioned assume 23:59
- For reminder_times:
  * If deadline is more than 1 hour away: remind 30 min before and 10 min before
  * If deadline is 30-60 min away: remind 15 min before and 5 min before
  * If deadline is less than 30 min away: remind 2 min from now and at the deadline time
- reminder_times must ALWAYS be in the future from now
- No markdown, no explanation
"""
    return safe_parse_json(call_ai(prompt))

def extract_email_details(message: str) -> dict:
    prompt = f"""
Extract email details from this message: "{message}"

Return ONLY raw JSON:
{{
  "to": "recipient email address",
  "subject": "email subject line",
  "body": "full email body content"
}}

Rules:
- Write a professional friendly email body based on what the user said
- If subject not mentioned create a suitable one
- No markdown, no explanation
"""
    return safe_parse_json(call_ai(prompt))

# ---------------- TOOL FUNCTIONS ----------------

def check_conflicts(email: str):
    cursor.execute("SELECT subject, deadline FROM deadlines WHERE email=?", (email,))
    rows = cursor.fetchall()
    return [{"subject": r[0], "deadline": r[1]} for r in rows]

def suggest_reschedule(message: str):
    prompt = f'Suggest a better deadline based on: "{message}". Return ONLY raw JSON: {{"new_deadline":"YYYY-MM-DD HH:MM","reason":"..."}}'
    return safe_parse_json(call_ai(prompt))

def send_summary(email: str, phone: str):
    deadlines = check_conflicts(email)
    if deadlines:
        summary = "\n".join([f"- {d['subject']} by {d['deadline']}" for d in deadlines])
        body = f"Hey!\n\nYour deadline summary:\n\n{summary}\n\nStay on top of it!"
    else:
        body = "Hey!\n\nYou have no upcoming deadlines. You're all clear!"
    send_email(email, "Your DeadlineAI Summary", body)
    return {"sent": True, "count": len(deadlines)}

def delete_passed_deadlines(email: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    cursor.execute("DELETE FROM deadlines WHERE email=? AND deadline < ?", (email, now))
    conn.commit()
    return {"deleted": cursor.rowcount}

def safe_send_custom_email(message: str):
    try:
        details = extract_email_details(message)

        # safely handle any key the AI might return
        to      = (details.get("to")
                or details.get("to_email")
                or details.get("email")
                or details.get("recipient")
                or None)

        subject = (details.get("subject")
                or details.get("title")
                or details.get("email_subject")
                or "Message from DeadlineAI")

        body    = (details.get("body")
                or details.get("message")
                or details.get("content")
                or details.get("email_body")
                or message)

        if not to:
            return {
                "sent": False,
                "error": "Could not find recipient email. Please include an email address like someone@gmail.com"
            }

        send_email(to, subject, body)
        return {"sent": True, "to": to}

    except json.JSONDecodeError:
        return {"sent": False, "error": "Could not understand the email details. Please try again."}
    except Exception as e:
        return {"sent": False, "error": str(e)}
def delete_deadline(email: str, message: str) -> dict:
    prompt = f"""
Extract the deadline name to delete from: "{message}"

Return ONLY raw JSON:
{{
  "name": "name of the deadline to delete"
}}

No markdown, no explanation.
"""
    try:
        details = safe_parse_json(call_ai(prompt))
        name    = (details.get("name")
                or details.get("subject")
                or details.get("deadline")
                or None)

        if not name:
            # if cant extract name, delete all for this user
            cursor.execute("DELETE FROM deadlines WHERE email=?", (email,))
            conn.commit()
            return {"deleted": True, "message": "Deleted all your deadlines"}

        cursor.execute(
            "DELETE FROM deadlines WHERE email=? AND LOWER(subject) LIKE ?",
            (email, f"%{name.lower()}%")
        )
        conn.commit()

        if cursor.rowcount == 0:
            return {"deleted": False, "error": f"Could not find deadline matching '{name}'"}

        return {"deleted": True, "message": f"Deleted '{name}' deadline"}

    except Exception as e:
        return {"deleted": False, "error": str(e)}

# ---------------- TOOL REGISTRY ----------------

TOOLS = {
    "extract_deadline":        lambda data: extract_deadline(data.message),
    "check_conflicts":         lambda data: check_conflicts(data.email),
    "suggest_reschedule":      lambda data: suggest_reschedule(data.message),
    "send_summary":            lambda data: send_summary(data.email, data.phone),
    "delete_deadline":         lambda data: delete_deadline(data.email, data.message),
    "delete_passed_deadlines": lambda data: delete_passed_deadlines(data.email),
    "send_custom_email":       lambda data: safe_send_custom_email(data.message),
    "do_nothing":              lambda data: {"status": "no action needed"},
}

def execute_tool(tool_name: str, data):
    fn = TOOLS.get(tool_name)
    if not fn:
        return {"error": f"unknown tool: {tool_name}"}
    return fn(data)

# ---------------- PLANNER ----------------

def plan(user_message: str, history: list) -> dict:
    prompt = f"""
You are a deadline management agent. Decide which tool to call.

Tools:
- extract_deadline: user is giving a new deadline to save
- check_conflicts: user wants to see their deadlines
- suggest_reschedule: user wants to reschedule something
- send_summary: user wants a summary sent to their email
- delete_passed_deadlines: user wants to delete completed or past deadlines
- send_custom_email: user wants to send an email to someone else
- do_nothing: message is casual or greeting, no action needed
- delete_deadline: user wants to delete a specific deadline by name, or delete all deadlines

Return ONLY raw JSON: {{"tool": "...", "reason": "..."}}

History: {json.dumps(history[-6:])}
Message: "{user_message}"
"""
    return safe_parse_json(call_ai(prompt))

# ---------------- AGENT LOOP ----------------

def agent_loop(data: ChatInput) -> dict:
    conversation_history.append({"role": "user", "content": data.message})

    steps = []
    for _ in range(5):
        try:
            action    = plan(data.message, conversation_history)
            tool_name = action.get("tool", "do_nothing")
        except Exception:
            tool_name = "do_nothing"

        if tool_name == "extract_deadline":
            try:
                extracted_list = extract_deadline(data.message)
                for extracted in extracted_list:
                    deadline_id = f"deadline_{datetime.now().timestamp()}"
                    cursor.execute(
                        "INSERT INTO deadlines VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            deadline_id,
                            extracted.get("subject", "Unknown"),
                            extracted.get("deadline", ""),
                            extracted.get("urgency", "medium"),
                            data.email,
                            data.phone,
                            datetime.now().strftime("%Y-%m-%d %H:%M")
                        )
                    )
                    conn.commit()
                    for reminder_time in extracted.get("reminder_times", []):
                        try:
                            scheduler.add_job(
                                send_reminder,
                                "date",
                                run_date=datetime.strptime(reminder_time, "%Y-%m-%d %H:%M"),
                                args=[extracted.get("subject", ""), data.email, data.phone,
                                      f"Reminder time: {reminder_time}"]
                            )
                        except Exception as e:
                            print(f"Scheduler error: {e}")
                result = {"saved": [e.get("subject", "deadline") for e in extracted_list]}
            except Exception as e:
                result = {"error": str(e)}
        else:
            try:
                result = execute_tool(tool_name, data)
            except Exception as e:
                result = {"error": str(e)}

        steps.append({"tool": tool_name, "result": result})
        conversation_history.append({
            "role": "assistant",
            "content": f"Used {tool_name}: {result}"
        })
        break
        "delete_deadline",

    return {"steps": steps, "final": steps[-1]["result"]}

# ---------------- ENDPOINTS ----------------

@app.post("/subscribe-push")
async def subscribe_push(data: PushSubscription):
    try:
        cursor.execute(
            "INSERT OR REPLACE INTO push_subscriptions VALUES (?, ?)",
            (data.email, json.dumps(data.subscription))
        )
        conn.commit()
        return {"status": "subscribed"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/add-deadline")
async def add_deadline(data: DeadlineInput):
    try:
        extracted_list = extract_deadline(data.message)
        saved_deadlines = []
        for extracted in extracted_list:
            deadline_id = f"deadline_{datetime.now().timestamp()}"
            cursor.execute(
                "INSERT INTO deadlines VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    deadline_id,
                    extracted.get("subject", "Unknown"),
                    extracted.get("deadline", ""),
                    extracted.get("urgency", "medium"),
                    data.email,
                    data.phone,
                    datetime.now().strftime("%Y-%m-%d %H:%M")
                )
            )
            conn.commit()
            saved_deadlines.append(extracted.get("subject", "deadline"))
            for reminder_time in extracted.get("reminder_times", []):
                try:
                    scheduler.add_job(
                        send_reminder,
                        "date",
                        run_date=datetime.strptime(reminder_time, "%Y-%m-%d %H:%M"),
                        args=[extracted.get("subject", ""), data.email, data.phone,
                              f"Reminder time: {reminder_time}"]
                    )
                except Exception as e:
                    print(f"Scheduler error: {e}")
        return {"status": "success", "saved": saved_deadlines}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/deadlines")
def get_deadlines():
    cursor.execute("SELECT * FROM deadlines")
    rows = cursor.fetchall()
    result = []
    for r in rows:
        result.append({
            "id":         r[0],
            "subject":    r[1],
            "deadline":   r[2],
            "urgency":    r[3],
            "email":      r[4],
            "phone":      r[5],
            "created_at": r[6]
        })
    return result

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/chat")
async def chat(data: ChatInput):
    try:
        result = agent_loop(data)
        return {"status": "success", **result}
    except Exception as e:
        return {"status": "error", "message": str(e)}