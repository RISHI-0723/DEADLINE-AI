import sqlite3

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

conn.commit()