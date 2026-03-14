# DeadlineAI 🎯

An agentic AI-powered deadline reminder app that understands natural language, saves your deadlines, and sends you email reminders automatically.

---

## What it does

- 💬 **Natural language input** — just say "ML assignment due tomorrow at 5pm"
- 🧠 **Agentic AI** — automatically decides whether to save a deadline, check conflicts, reschedule, or send an email
- ⏰ **Smart reminders** — sends email reminders 30 min and 10 min before every deadline
- 📧 **Send emails** — say "send an email to john@gmail.com saying the meeting is at 3pm"
- 🗑️ **Delete deadlines** — say "delete daa assignment" or "delete all my deadlines"
- ✏️ **Rename deadlines** — say "rename dbms assignment to database project"
- 📋 **Deadline dashboard** — view all your deadlines with urgency badges and countdown timers
- 👤 **Profile page** — stats, dark/light theme toggle
- 🔔 **Browser push notifications** — get notified even when the tab is closed
- 📅 **Saturday 12pm nudge** — automatic weekly check-in email to all users
- 🧹 **Auto cleanup** — past deadlines are deleted automatically every hour

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI (Python) |
| AI | Groq (Llama 3.3 70B) — free |
| Database | SQLite |
| Scheduler | APScheduler |
| Email | Gmail SMTP |
| WhatsApp | Twilio (optional) |
| Push notifications | Web Push (pywebpush) |
| Frontend | Vanilla HTML/CSS/JS |

---

## Project Structure

```
DEADLINE-AI/
├── main.py          # FastAPI backend + agentic loop
├── index.html       # Frontend (single file app)
├── Sw.js            # Service worker for push notifications
├── requirements.txt # Python dependencies
└── .gitignore
```

---

## Setup & Installation

### 1. Clone the repo

```bash
git clone https://github.com/RISHI-0723/DEADLINE-AI.git
cd DEADLINE-AI
```

### 2. Create virtual environment

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
source .venv/bin/activate # Mac/Linux
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Create `.env` file

```env
GROQ_API_KEY=your_groq_api_key
YOUR_EMAIL=yourgmail@gmail.com
YOUR_EMAIL_PASSWORD=your_16_char_app_password
TWILIO_ACCOUNT_SID=your_twilio_sid
TWILIO_AUTH_TOKEN=your_twilio_token
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
VAPID_PRIVATE_KEY=path/to/private_key.pem
```

> **Gmail App Password**: Go to `myaccount.google.com` → Security → 2-Step Verification → App Passwords → Generate

> **Groq API Key**: Free at `console.groq.com`

### 5. Run the backend

```bash
uvicorn main:app --reload
```

### 6. Open the frontend

Just double-click `index.html` in your browser — no server needed!

---

## How to use

| You say | What happens |
|---------|-------------|
| "DAA assignment due tomorrow at 11am" | Saves deadline, schedules reminders |
| "check my deadlines" | Shows all upcoming deadlines |
| "delete daa assignment" | Deletes that specific deadline |
| "delete all my deadlines" | Clears everything |
| "rename dbms to database project" | Renames the deadline |
| "send me a summary" | Emails you all your deadlines |
| "send an email to friend@gmail.com saying hello" | Sends a custom email |
| "suggest a better time for ML assignment" | AI recommends a new deadline |

---

## Agentic Architecture

```
User message
     ↓
  Planner (Groq LLM)
     ↓ decides tool
  Tool Registry
  ├── extract_deadline
  ├── check_conflicts
  ├── suggest_reschedule
  ├── send_summary
  ├── delete_deadline
  ├── delete_passed_deadlines
  ├── rename_deadline
  ├── send_custom_email
  └── do_nothing
     ↓
  Memory (conversation history)
     ↓
  Response to user
```

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `GROQ_API_KEY` | Groq API key (free at console.groq.com) |
| `YOUR_EMAIL` | Gmail address used to send reminders |
| `YOUR_EMAIL_PASSWORD` | Gmail App Password (16 chars) |
| `TWILIO_ACCOUNT_SID` | Twilio account SID (optional) |
| `TWILIO_AUTH_TOKEN` | Twilio auth token (optional) |
| `TWILIO_WHATSAPP_FROM` | Twilio WhatsApp number (optional) |
| `VAPID_PRIVATE_KEY` | Path to VAPID private key .pem file |

---

## License

MIT License — free to use and modify.

---

Built with ❤️ by Rishi
