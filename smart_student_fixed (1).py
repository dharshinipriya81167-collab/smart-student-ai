from flask import Flask, request, jsonify, render_template_string, send_from_directory
import sqlite3
import os
import random
import re
import json
import ast
import operator
import urllib.request
import urllib.error
from datetime import datetime, date

try:
    from PyPDF2 import PdfReader
except Exception:
    PdfReader = None

try:
    import pytesseract
    from PIL import Image
except Exception:
    pytesseract = None
    Image = None

app = Flask(__name__)

# Real AI configuration. Keep keys in environment variables; never put them in HTML.
# AI_PROVIDER can be "openai", "gemini", or "offline".
AI_PROVIDER = os.environ.get("AI_PROVIDER", "offline").strip().lower()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip()


def call_json_api(url, payload, headers=None, timeout=45):
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers or {}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError("AI provider error (%s): %s" % (exc.code, detail[:500]))
    except urllib.error.URLError as exc:
        raise RuntimeError("Could not connect to AI provider: %s" % exc.reason)


def ask_openai(question):
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    payload = {
        "model": OPENAI_MODEL,
        "instructions": "You are Smart Student AI, a helpful tutor. Explain clearly and briefly. If the user asks an academic question, include an example when useful.",
        "input": question,
        "max_output_tokens": 800
    }
    data = call_json_api(
        "https://api.openai.com/v1/responses",
        payload,
        {"Authorization": "Bearer " + OPENAI_API_KEY, "Content-Type": "application/json"}
    )
    answer = data.get("output_text", "").strip()
    if not answer:
        for item in data.get("output", []):
            for content in item.get("content", []):
                if content.get("type") in ("output_text", "text"):
                    answer += str(content.get("text", ""))
    return answer.strip() or "The AI returned an empty answer."


def ask_gemini(question):
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    payload = {
        "systemInstruction": {"parts": [{"text": "You are Smart Student AI, a helpful tutor. Explain clearly and briefly. If the user asks an academic question, include an example when useful."}]},
        "contents": [{"role": "user", "parts": [{"text": question}]}],
        "generationConfig": {"maxOutputTokens": 800, "temperature": 0.4}
    }
    url = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent?key=%s" % (GEMINI_MODEL, GEMINI_API_KEY)
    data = call_json_api(url, payload, {"Content-Type": "application/json"})
    candidates = data.get("candidates", [])
    if candidates:
        parts = candidates[0].get("content", {}).get("parts", [])
        answer = "".join(str(part.get("text", "")) for part in parts).strip()
        if answer:
            return answer
    raise RuntimeError("Gemini returned an empty answer.")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "smart_student_data")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
DB_FILE = os.path.join(DATA_DIR, "smart_student.db")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)


def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_column(cur, table, column, definition):
    cols = {row[1] for row in cur.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS student (
            id INTEGER PRIMARY KEY,
            name TEXT DEFAULT 'Student',
            regno TEXT DEFAULT '',
            department TEXT DEFAULT 'CSE',
            year TEXT DEFAULT '2nd Year',
            xp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            streak INTEGER DEFAULT 0,
            last_study TEXT DEFAULT '',
            study_minutes INTEGER DEFAULT 0
        )
    """)

    # Migration: add missing columns to an existing database without deleting data.
    ensure_column(cur, "student", "department", "TEXT DEFAULT 'CSE'")
    ensure_column(cur, "student", "year", "TEXT DEFAULT '2nd Year'")
    ensure_column(cur, "student", "xp", "INTEGER DEFAULT 0")
    ensure_column(cur, "student", "level", "INTEGER DEFAULT 1")
    ensure_column(cur, "student", "streak", "INTEGER DEFAULT 0")
    ensure_column(cur, "student", "last_study", "TEXT DEFAULT ''")
    ensure_column(cur, "student", "study_minutes", "INTEGER DEFAULT 0")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS marks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT,
            mark REAL,
            created_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT,
            status TEXT,
            created_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task TEXT,
            completed INTEGER DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            content TEXT,
            created_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            filepath TEXT,
            filetype TEXT,
            extracted_text TEXT,
            created_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS quiz_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            score INTEGER,
            total INTEGER,
            created_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS leaderboard (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            xp INTEGER DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS exam (
            id INTEGER PRIMARY KEY,
            exam_date TEXT DEFAULT ''
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS calendar_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            event_date TEXT NOT NULL,
            event_time TEXT DEFAULT '',
            event_type TEXT DEFAULT 'Study',
            created_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            subject TEXT DEFAULT '',
            due_date TEXT NOT NULL,
            priority TEXT DEFAULT 'Medium',
            completed INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS flashcards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT DEFAULT 'General',
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            created_at TEXT
        )
    """)

    row = cur.execute("SELECT * FROM student WHERE id=1").fetchone()
    if row is None:
        cur.execute("""
            INSERT INTO student
            (id, name, regno, department, year, xp, level, streak, last_study, study_minutes)
            VALUES (1, 'Student', '', 'CSE', '2nd Year', 0, 1, 0, '', 0)
        """)
    else:
        # Fill newly-added columns only when they are NULL; existing data is preserved.
        cur.execute("UPDATE student SET department=COALESCE(department,'CSE'), year=COALESCE(year,'2nd Year') WHERE id=1")

    if cur.execute("SELECT COUNT(*) FROM exam").fetchone()[0] == 0:
        cur.execute("INSERT INTO exam (id, exam_date) VALUES (1, '')")

    conn.commit()
    conn.close()


init_db()


SUBJECTS = {
    "CSE": ["Python", "Java", "C Programming", "Data Structures", "DBMS",
            "Operating Systems", "Computer Networks", "Artificial Intelligence",
            "Software Engineering", "Computer Architecture"],
    "EEE": ["Basic Electrical Engineering", "Circuit Theory", "Electrical Machines",
            "Power Systems", "Control Systems", "Digital Electronics"],
    "ECE": ["Electronic Devices", "Digital Electronics", "Analog Electronics",
            "Signals and Systems", "Communication Systems", "Microprocessors"],
    "MECH": ["Engineering Mechanics", "Thermodynamics", "Fluid Mechanics",
             "Manufacturing Technology", "Machine Design", "Engineering Drawing"],
    "CIVIL": ["Engineering Mechanics", "Structural Engineering", "Surveying",
              "Fluid Mechanics", "Concrete Technology", "Geotechnical Engineering"]
}


NOTES = {
    "Python": """Python is a high-level programming language.

Important Topics:
1. Variables
2. Data Types
3. Operators
4. Conditions
5. Loops
6. Functions
7. Lists
8. Tuples
9. Dictionaries
10. Classes and Objects

Example:
def add(a, b):
    return a + b

Python is widely used in AI, automation, web development and data science.""",

    "Java": """Java is an object-oriented programming language.

Important Topics:
1. Classes
2. Objects
3. Constructors
4. Inheritance
5. Polymorphism
6. Encapsulation
7. Abstraction
8. Exception Handling
9. Arrays
10. Interfaces""",

    "C Programming": """C is a procedural programming language.

Important Topics:
1. Variables
2. Data Types
3. Operators
4. Conditions
5. Loops
6. Arrays
7. Functions
8. Pointers
9. Structures
10. Files""",

    "Data Structures": """Data Structures are methods used to organize data.

Important Topics:
1. Array
2. Linked List
3. Stack
4. Queue
5. Tree
6. Graph
7. Searching
8. Sorting
9. Hashing""",

    "DBMS": """DBMS stands for Database Management System.

Important Topics:
1. Database
2. Tables
3. Primary Key
4. Foreign Key
5. SQL
6. Normalization
7. Transactions
8. Joins
9. ER Diagram""",

    "Operating Systems": """Operating System manages hardware and software.

Important Topics:
1. Process
2. Thread
3. Scheduling
4. Deadlock
5. Memory Management
6. Virtual Memory
7. File System
8. Context Switching""",

    "Computer Networks": """Computer Network allows computers to communicate.

Important Topics:
1. OSI Model
2. TCP/IP
3. IP Address
4. Router
5. Switch
6. DNS
7. HTTP
8. TCP
9. UDP""",

    "Artificial Intelligence": """Artificial Intelligence enables machines to perform tasks that normally require human intelligence.

Important Topics:
1. Machine Learning
2. Deep Learning
3. Neural Networks
4. NLP
5. Computer Vision
6. Generative AI""",

    "Software Engineering": """Software Engineering is the systematic development and maintenance of software.

Important Topics:
1. SDLC
2. Waterfall Model
3. Agile Model
4. Spiral Model
5. V Model
6. Testing
7. Maintenance""",

    "Computer Architecture": """Computer Architecture describes how a computer system is organized and how its components communicate.

Important Topics:
1. CPU
2. ALU
3. Control Unit
4. Memory
5. Registers
6. Cache
7. Input/Output"""
}


QUIZ = [
    {"q": "Python function definition uses which keyword?", "options": ["func", "def", "function", "define"], "answer": "def"},
    {"q": "Which data structure follows LIFO?", "options": ["Queue", "Stack", "Tree", "Graph"], "answer": "Stack"},
    {"q": "Which data structure follows FIFO?", "options": ["Stack", "Queue", "Tree", "Array"], "answer": "Queue"},
    {"q": "What does CPU stand for?", "options": ["Central Processing Unit", "Computer Processing Unit", "Central Program Unit", "Computer Program Utility"], "answer": "Central Processing Unit"},
    {"q": "Which SQL command retrieves data?", "options": ["INSERT", "UPDATE", "SELECT", "DELETE"], "answer": "SELECT"},
    {"q": "AI stands for?", "options": ["Artificial Intelligence", "Automatic Internet", "Advanced Input", "Artificial Information"], "answer": "Artificial Intelligence"},
    {"q": "Which one is an operating system?", "options": ["Python", "Linux", "HTML", "SQL"], "answer": "Linux"},
    {"q": "Which protocol is commonly used for web pages?", "options": ["HTTP", "SMTP", "FTP", "POP3"], "answer": "HTTP"},
    {"q": "Which language is object oriented?", "options": ["Java", "HTML", "CSS", "SQL"], "answer": "Java"},
    {"q": "Which is used to store key-value pairs in Python?", "options": ["List", "Tuple", "Dictionary", "Set"], "answer": "Dictionary"}
]


def get_student():
    conn = db()
    row = conn.execute("SELECT * FROM student WHERE id=1").fetchone()
    conn.close()
    return dict(row)


def add_xp(points):
    try:
        points = int(points)
    except (TypeError, ValueError):
        return
    conn = db()
    student = conn.execute("SELECT xp FROM student WHERE id=1").fetchone()
    if student:
        xp = int(student["xp"] or 0) + points
        level = (xp // 100) + 1
        conn.execute("UPDATE student SET xp=?, level=? WHERE id=1", (xp, level))
        conn.commit()
    conn.close()


def update_streak():
    conn = db()
    student = conn.execute("SELECT streak,last_study FROM student WHERE id=1").fetchone()
    today = date.today()
    last = student["last_study"] or ""
    streak = int(student["streak"] or 0)

    if last != str(today):
        if last:
            try:
                old = datetime.strptime(last, "%Y-%m-%d").date()
                difference = (today - old).days
                streak = streak + 1 if difference == 1 else 1
            except Exception:
                streak = 1
        else:
            streak = 1

        conn.execute("UPDATE student SET streak=?,last_study=? WHERE id=1", (streak, str(today)))
        conn.commit()
    conn.close()


def safe_calculate(expression):
    allowed = set("0123456789+-*/().% ")
    if not expression:
        return "Enter a calculation."
    if len(expression) > 100 or any(ch not in allowed for ch in expression):
        return "Invalid calculation."

    try:
        tree = ast.parse(expression, mode="eval")
        operators = {
            ast.Add: operator.add,
            ast.Sub: operator.sub,
            ast.Mult: operator.mul,
            ast.Div: operator.truediv,
            ast.Mod: operator.mod,
            ast.USub: operator.neg
        }

        def evaluate(node):
            if isinstance(node, ast.Expression):
                return evaluate(node.body)
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                if abs(node.value) > 1_000_000_000:
                    raise ValueError()
                return node.value
            if isinstance(node, ast.BinOp) and type(node.op) in operators:
                left, right = evaluate(node.left), evaluate(node.right)
                value = operators[type(node.op)](left, right)
                if abs(value) > 1_000_000_000:
                    raise ValueError()
                return value
            if isinstance(node, ast.UnaryOp) and type(node.op) in operators:
                return operators[type(node.op)](evaluate(node.operand))
            raise ValueError()

        return str(evaluate(tree))
    except Exception:
        return "Unable to calculate."


@app.route("/")
def home():
    return render_template_string(HTML)


@app.route("/api/dashboard")
def dashboard():
    student = get_student()
    conn = db()
    marks = conn.execute("SELECT mark FROM marks ORDER BY id DESC").fetchall()
    total_marks = sum(float(x["mark"]) for x in marks)
    average = round(total_marks / len(marks), 2) if marks else 0
    conn.close()
    return jsonify({
        "name": student["name"], "regno": student["regno"],
        "department": student.get("department", "CSE"),
        "year": student.get("year", "2nd Year"),
        "xp": student["xp"], "level": student["level"],
        "streak": student["streak"], "study_minutes": student["study_minutes"],
        "average": average
    })


@app.route("/api/profile", methods=["POST"])
def profile():
    body = request.get_json(silent=True) or {}
    name = str(body.get("name", "Student")).strip() or "Student"
    regno = str(body.get("regno", "")).strip()
    department = str(body.get("department", "CSE")).strip() or "CSE"
    year = str(body.get("year", "2nd Year")).strip() or "2nd Year"

    conn = db()
    conn.execute("UPDATE student SET name=?,regno=?,department=?,year=? WHERE id=1",
                 (name, regno, department, year))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "Profile saved."})


@app.route("/api/subjects")
def subjects():
    return jsonify(SUBJECTS)


@app.route("/api/notes")
def all_notes():
    return jsonify(NOTES)


@app.route("/api/notes/<subject>")
def subject_note(subject):
    content = NOTES.get(subject, "Notes not available for this subject.")
    add_xp(3)
    return jsonify({"subject": subject, "content": content})


@app.route("/api/ai", methods=["POST"])
def ai():
    body = request.get_json(silent=True) or {}
    question = str(body.get("question", "")).strip()
    if not question:
        return jsonify({"answer": "Please enter your question."})
    try:
        if AI_PROVIDER == "openai":
            answer = ask_openai(question)
        elif AI_PROVIDER == "gemini":
            answer = ask_gemini(question)
        else:
            # Safe offline fallback when no paid/external API is configured.
            knowledge = {
                "python": "Python is a high-level programming language used for AI, automation, web development and data science.",
                "java": "Java is an object-oriented programming language based on classes and objects.",
                "stack": "Stack is a linear data structure that follows LIFO: Last In First Out.",
                "queue": "Queue is a linear data structure that follows FIFO: First In First Out.",
                "dbms": "DBMS is software used to create, store, organize and retrieve data from databases.",
                "artificial intelligence": "AI is the field of creating systems that can perform tasks requiring intelligent decision making."
            }
            lower_question = question.lower()
            answer = next((value for key, value in knowledge.items() if key in lower_question),
                          "Offline AI is enabled. Set AI_PROVIDER and an API key to ask any question.")
        add_xp(5)
        return jsonify({"answer": answer, "provider": AI_PROVIDER})
    except RuntimeError as exc:
        return jsonify({"answer": str(exc), "provider": AI_PROVIDER, "configured": False}), 503
    except Exception:
        return jsonify({"answer": "AI request failed. Check your provider settings and internet connection."}), 502


@app.route("/api/question-generator", methods=["POST"])
def question_generator():
    body = request.get_json(silent=True) or {}
    subject = str(body.get("subject", "Python")).strip() or "Python"
    try:
        count = max(1, min(int(body.get("count", 5)), 20))
    except (TypeError, ValueError):
        count = 5

    templates = [
        f"What is {subject}?",
        f"Explain the important features of {subject}.",
        f"What are the advantages of {subject}?",
        f"Write a short note on {subject}.",
        f"Explain the applications of {subject}.",
        f"Differentiate important concepts in {subject}.",
        f"Explain {subject} with an example."
    ]
    questions = [random.choice(templates) for _ in range(count)]
    add_xp(10)
    return jsonify({"subject": subject, "questions": questions})


@app.route("/api/quiz")
def quiz():
    selected = random.sample(list(enumerate(QUIZ)), min(10, len(QUIZ)))
    return jsonify([
        {"index": index, "q": item["q"], "options": item["options"]}
        for index, item in selected
    ])


@app.route("/api/quiz/submit", methods=["POST"])
def submit_quiz():
    body = request.get_json(silent=True) or {}
    answers = body.get("answers", [])
    if not isinstance(answers, list):
        answers = []

    score = 0
    for item in answers:
        if not isinstance(item, dict):
            continue
        q_index = item.get("index", -1)
        answer = item.get("answer", "")
        if isinstance(q_index, int) and 0 <= q_index < len(QUIZ) and answer == QUIZ[q_index]["answer"]:
            score += 1

    total = len(answers)
    conn = db()
    conn.execute("INSERT INTO quiz_results (score,total,created_at) VALUES (?,?,?)",
                 (score, total, str(datetime.now())))
    conn.commit()
    conn.close()
    xp = score * 20
    add_xp(xp)
    return jsonify({"score": score, "total": total, "xp": xp})


@app.route("/api/final-test")
def final_test():
    questions = []
    for _ in range(50):
        index = random.randrange(len(QUIZ))
        item = QUIZ[index]
        questions.append({"index": index, "q": item["q"], "options": item["options"]})
    return jsonify({"questions": questions})


@app.route("/api/final-test/submit", methods=["POST"])
def submit_final_test():
    body = request.get_json(silent=True) or {}
    answers = body.get("answers", [])
    if not isinstance(answers, list):
        answers = []
    score = 0
    for item in answers:
        if not isinstance(item, dict):
            continue
        index = item.get("index", -1)
        answer = item.get("answer", "")
        if isinstance(index, int) and 0 <= index < len(QUIZ) and answer == QUIZ[index]["answer"]:
            score += 1
    total = len(answers)
    xp = score * 5
    add_xp(xp)
    return jsonify({"score": score, "total": total, "xp": xp})


@app.route("/api/marks", methods=["GET", "POST"])
def marks():
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        subject = str(body.get("subject", "")).strip()
        try:
            mark = float(body.get("mark", 0))
        except (TypeError, ValueError):
            return jsonify({"error": "Mark must be a number."}), 400
        if not subject:
            return jsonify({"error": "Enter a subject."}), 400
        if not 0 <= mark <= 100:
            return jsonify({"error": "Mark must be between 0 and 100."}), 400

        conn = db()
        conn.execute("INSERT INTO marks (subject,mark,created_at) VALUES (?,?,?)",
                     (subject, mark, str(datetime.now())))
        conn.commit()
        conn.close()
        add_xp(10)

    conn = db()
    rows = conn.execute("SELECT * FROM marks ORDER BY id DESC").fetchall()
    conn.close()
    result = [dict(x) for x in rows]
    total = sum(float(x["mark"]) for x in result)
    average = round(total / len(result), 2) if result else 0
    return jsonify({"marks": result, "total": total, "average": average})


@app.route("/api/performance")
def performance():
    conn = db()
    rows = conn.execute("SELECT subject,mark FROM marks").fetchall()
    conn.close()
    data = {}
    for row in rows:
        data.setdefault(row["subject"], []).append(float(row["mark"]))
    result = []
    for subject, values in data.items():
        avg = sum(values) / len(values)
        status = "Weak" if avg < 40 else "Needs Improvement" if avg < 60 else "Good" if avg < 80 else "Excellent"
        result.append({"subject": subject, "average": round(avg, 2), "status": status})
    return jsonify(result)


@app.route("/api/weak-topic")
def weak_topic():
    conn = db()
    rows = conn.execute("SELECT subject,mark FROM marks").fetchall()
    conn.close()
    return jsonify([
        {"subject": row["subject"], "mark": row["mark"],
         "recommendation": "Revise basics and practice more questions."}
        for row in rows if float(row["mark"]) < 60
    ])


@app.route("/api/study-plan", methods=["POST"])
def study_plan():
    body = request.get_json(silent=True) or {}
    subjects = body.get("subjects", [])
    if not isinstance(subjects, list):
        subjects = []
    subjects = [str(x).strip() for x in subjects if str(x).strip()]
    try:
        hours = float(body.get("hours", 2))
    except (TypeError, ValueError):
        return jsonify({"error": "Hours must be a number."}), 400
    if hours <= 0 or hours > 24:
        return jsonify({"error": "Hours must be between 0 and 24."}), 400
    if not subjects:
        subjects = ["Python", "Data Structures"]

    minutes = max(20, int(hours * 60 / len(subjects)))
    plan = [{"subject": subject, "minutes": minutes,
             "tasks": ["Read concepts", "Write short notes", "Practice questions", "Quick revision"]}
            for subject in subjects]
    add_xp(10)
    return jsonify({"hours": hours, "plan": plan})


@app.route("/api/study", methods=["POST"])
def study():
    body = request.get_json(silent=True) or {}
    try:
        minutes = int(body.get("minutes", 30))
    except (TypeError, ValueError):
        minutes = 30
    minutes = max(1, min(minutes, 720))
    update_streak()

    conn = db()
    conn.execute("UPDATE student SET study_minutes=study_minutes+? WHERE id=1", (minutes,))
    conn.commit()
    conn.close()
    add_xp(30)
    student = get_student()
    return jsonify({"streak": student["streak"], "study_minutes": student["study_minutes"]})


@app.route("/api/xp", methods=["POST"])
def award_xp():
    body = request.get_json(silent=True) or {}
    try:
        points = int(body.get("points", 0))
    except (TypeError, ValueError):
        points = 0
    points = max(0, min(points, 1000))
    add_xp(points)
    student = get_student()
    return jsonify({"success": True, "xp": student["xp"], "level": student["level"], "added": points})


@app.route("/api/achievements")
def achievements():
    student = get_student()
    xp, streak, minutes = student["xp"], student["streak"], student["study_minutes"]
    badges = []
    if xp >= 100: badges.append("⭐ Beginner")
    if xp >= 500: badges.append("🏆 XP Master")
    if streak >= 3: badges.append("🔥 3 Day Streak")
    if streak >= 7: badges.append("🔥 7 Day Streak")
    if minutes >= 60: badges.append("📚 Study Starter")
    if minutes >= 300: badges.append("🎓 Study Champion")
    if not badges: badges.append("🌱 Start studying to unlock badges")
    return jsonify({"badges": badges})


@app.route("/api/leaderboard")
def leaderboard():
    student = get_student()
    conn = db()
    exists = conn.execute("SELECT id FROM leaderboard WHERE name=?", (student["name"],)).fetchone()
    if exists:
        conn.execute("UPDATE leaderboard SET xp=? WHERE id=?", (student["xp"], exists["id"]))
    else:
        conn.execute("INSERT INTO leaderboard (name,xp) VALUES (?,?)", (student["name"], student["xp"]))
    conn.commit()
    rows = conn.execute("SELECT name,xp FROM leaderboard ORDER BY xp DESC LIMIT 20").fetchall()
    conn.close()
    return jsonify([dict(x) for x in rows])


@app.route("/api/exam", methods=["GET", "POST"])
def exam():
    conn = db()
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        exam_date = str(body.get("exam_date", "")).strip()
        if exam_date:
            try:
                datetime.strptime(exam_date, "%Y-%m-%d")
            except ValueError:
                conn.close()
                return jsonify({"error": "Invalid date."}), 400
        conn.execute("UPDATE exam SET exam_date=? WHERE id=1", (exam_date,))
        conn.commit()

    row = conn.execute("SELECT exam_date FROM exam WHERE id=1").fetchone()
    conn.close()
    exam_date = row["exam_date"]
    days = None
    if exam_date:
        try:
            target = datetime.strptime(exam_date, "%Y-%m-%d").date()
            days = (target - date.today()).days
        except Exception:
            days = None
    return jsonify({"exam_date": exam_date, "days_left": days})


@app.route("/api/attendance", methods=["GET", "POST"])
def attendance():
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        subject = str(body.get("subject", "General")).strip() or "General"
        status = str(body.get("status", "Present")).strip()
        if status not in ("Present", "Absent"):
            return jsonify({"error": "Invalid attendance status."}), 400
        conn = db()
        conn.execute("INSERT INTO attendance (subject,status,created_at) VALUES (?,?,?)",
                     (subject, status, str(datetime.now())))
        conn.commit()
        conn.close()
        add_xp(5)

    conn = db()
    rows = conn.execute("SELECT * FROM attendance").fetchall()
    conn.close()
    total = len(rows)
    present = sum(1 for x in rows if x["status"] == "Present")
    percentage = round(present / total * 100, 2) if total else 0
    return jsonify({"total": total, "present": present, "percentage": percentage})


@app.route("/api/tasks", methods=["GET", "POST"])
def tasks():
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        task = str(body.get("task", "")).strip()
        if not task:
            return jsonify({"error": "Enter a task."}), 400
        conn = db()
        conn.execute("INSERT INTO tasks (task,completed) VALUES (?,0)", (task,))
        conn.commit()
        conn.close()

    conn = db()
    rows = conn.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()
    conn.close()
    return jsonify([dict(x) for x in rows])


@app.route("/api/tasks/<int:task_id>", methods=["PUT"])
def complete_task(task_id):
    conn = db()
    cur = conn.execute("UPDATE tasks SET completed=1 WHERE id=?", (task_id,))
    conn.commit()
    conn.close()
    if cur.rowcount:
        add_xp(10)
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Task not found."}), 404


@app.route("/api/calculate", methods=["POST"])
def calculate():
    body = request.get_json(silent=True) or {}
    result = safe_calculate(str(body.get("expression", "")))
    add_xp(2)
    return jsonify({"result": result})


@app.route("/api/game/number")
def number_game():
    return jsonify({"message": "Guess a number between 1 and 10.", "number": random.randint(1, 10)})


@app.route("/api/game/math")
def math_game():
    a, b = random.randint(1, 20), random.randint(1, 20)
    return jsonify({"question": f"{a} + {b} = ?", "answer": a + b})


@app.route("/api/smart-notes", methods=["POST"])
def smart_notes():
    body = request.get_json(silent=True) or {}
    text = str(body.get("text", ""))
    if not text.strip():
        return jsonify({"notes": "Please enter text."})
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", text.strip()) if x.strip()]
    words = re.findall(r"[A-Za-z]{5,}", text.lower())
    keywords = list(dict.fromkeys(words))[:10]
    result = "SMART NOTES\n\n" + "\n".join("• " + x for x in sentences[:8]) + "\n\nKEYWORDS\n" + ", ".join(keywords)
    add_xp(10)
    return jsonify({"notes": result})


@app.route("/api/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "No file selected."}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "Invalid file."}), 400

    original = os.path.basename(file.filename)
    stem, extension = os.path.splitext(original)
    extension = extension.lower()
    filename = original
    counter = 1
    while os.path.exists(os.path.join(UPLOAD_DIR, filename)):
        filename = f"{stem}_{counter}{extension}"
        counter += 1

    save_path = os.path.join(UPLOAD_DIR, filename)
    file.save(save_path)
    extracted = ""

    if extension == ".pdf":
        if PdfReader:
            try:
                reader = PdfReader(save_path)
                extracted = "\n".join((page.extract_text() or "") for page in reader.pages)
            except Exception:
                extracted = "PDF uploaded, but text extraction failed."
        else:
            extracted = "PDF uploaded. Install PyPDF2 for text extraction."
    elif extension in [".png", ".jpg", ".jpeg", ".webp"]:
        if pytesseract and Image:
            try:
                extracted = pytesseract.image_to_string(Image.open(save_path))
            except Exception:
                extracted = "Image uploaded, but OCR failed."
        else:
            extracted = "Image uploaded. Install Pillow + pytesseract and Tesseract OCR for OCR."
    else:
        try:
            with open(save_path, "r", encoding="utf-8", errors="ignore") as f:
                extracted = f.read(10000)
        except Exception:
            extracted = "File uploaded successfully."

    conn = db()
    conn.execute("""INSERT INTO uploads
                    (filename,filepath,filetype,extracted_text,created_at)
                    VALUES (?,?,?,?,?)""",
                 (filename, save_path, extension, extracted, str(datetime.now())))
    conn.commit()
    conn.close()
    add_xp(10)
    return jsonify({"success": True, "filename": filename, "text": extracted[:12000]})


@app.route("/api/uploads")
def uploads():
    conn = db()
    rows = conn.execute("SELECT id,filename,filetype,created_at FROM uploads ORDER BY id DESC").fetchall()
    conn.close()
    return jsonify([dict(x) for x in rows])


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


@app.route("/api/ocr-question", methods=["POST"])
def ocr_question():
    body = request.get_json(silent=True) or {}
    text = str(body.get("text", "")).strip()
    if not text:
        return jsonify({"answer": "No question text found."})
    explanation = ("📷 QUESTION SCANNER\n\nDetected Question:\n" + text +
                   "\n\n🤖 Study Helper:\nBreak the question into keywords, identify the topic and then solve each step carefully.")
    add_xp(10)
    return jsonify({"answer": explanation})


TRANSLATIONS = {
    "english": {"welcome": "Welcome to Smart Student"},
    "tamil": {"welcome": "Smart Student-க்கு வரவேற்கிறோம்"},
    "hindi": {"welcome": "Smart Student में आपका स्वागत है"}
}


@app.route("/api/language/<lang>")
def language(lang):
    return jsonify(TRANSLATIONS.get(lang.lower(), TRANSLATIONS["english"]))


@app.route("/api/calendar", methods=["GET", "POST"])
def calendar_events():
    conn = db()
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        title = str(body.get("title", "")).strip()
        event_date = str(body.get("event_date", "")).strip()
        event_time = str(body.get("event_time", "")).strip()
        event_type = str(body.get("event_type", "Study")).strip() or "Study"
        if not title or not event_date:
            conn.close()
            return jsonify({"error": "Title and date are required."}), 400
        try:
            datetime.strptime(event_date, "%Y-%m-%d")
        except ValueError:
            conn.close()
            return jsonify({"error": "Invalid date."}), 400
        conn.execute("INSERT INTO calendar_events (title,event_date,event_time,event_type,created_at) VALUES (?,?,?,?,?)",
                     (title, event_date, event_time, event_type, str(datetime.now())))
        conn.commit()
        add_xp(5)
    rows = conn.execute("SELECT * FROM calendar_events ORDER BY event_date,event_time,id").fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])


@app.route("/api/calendar/<int:event_id>", methods=["DELETE"])
def delete_calendar_event(event_id):
    conn = db()
    cur = conn.execute("DELETE FROM calendar_events WHERE id=?", (event_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": bool(cur.rowcount)})


@app.route("/api/assignments", methods=["GET", "POST"])
def assignments():
    conn = db()
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        title = str(body.get("title", "")).strip()
        subject = str(body.get("subject", "")).strip()
        due_date = str(body.get("due_date", "")).strip()
        priority = str(body.get("priority", "Medium")).strip()
        if not title or not due_date:
            conn.close()
            return jsonify({"error": "Title and due date are required."}), 400
        if priority not in ("Low", "Medium", "High"):
            priority = "Medium"
        try:
            datetime.strptime(due_date, "%Y-%m-%d")
        except ValueError:
            conn.close()
            return jsonify({"error": "Invalid due date."}), 400
        conn.execute("INSERT INTO assignments (title,subject,due_date,priority,created_at) VALUES (?,?,?,?,?)",
                     (title, subject, due_date, priority, str(datetime.now())))
        conn.commit()
        add_xp(5)
    rows = conn.execute("SELECT * FROM assignments ORDER BY completed,due_date,id").fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])


@app.route("/api/assignments/<int:assignment_id>", methods=["PUT", "DELETE"])
def assignment_item(assignment_id):
    conn = db()
    if request.method == "DELETE":
        cur = conn.execute("DELETE FROM assignments WHERE id=?", (assignment_id,))
    else:
        cur = conn.execute("UPDATE assignments SET completed=1 WHERE id=?", (assignment_id,))
    conn.commit()
    conn.close()
    if cur.rowcount and request.method == "PUT":
        add_xp(10)
    return jsonify({"success": bool(cur.rowcount)})


@app.route("/api/analytics")
def analytics():
    conn = db()
    marks = conn.execute("SELECT subject,mark,created_at FROM marks ORDER BY id").fetchall()
    attendance_rows = conn.execute("SELECT subject,status FROM attendance").fetchall()
    study = conn.execute("SELECT study_minutes,streak,xp FROM student WHERE id=1").fetchone()
    quiz_rows = conn.execute("SELECT score,total,created_at FROM quiz_results ORDER BY id DESC LIMIT 10").fetchall()
    conn.close()
    subject_map = {}
    for row in marks:
        subject_map.setdefault(row["subject"], []).append(float(row["mark"]))
    subjects = [{"subject": k, "average": round(sum(v) / len(v), 2), "attempts": len(v)} for k, v in subject_map.items()]
    attendance_map = {}
    for row in attendance_rows:
        item = attendance_map.setdefault(row["subject"], {"present": 0, "total": 0})
        item["total"] += 1
        item["present"] += int(row["status"] == "Present")
    attendance = [{"subject": k, "percentage": round(v["present"] / v["total"] * 100, 2)} for k, v in attendance_map.items()]
    quiz = [dict(row) for row in quiz_rows]
    return jsonify({"subjects": subjects, "attendance": attendance, "study_minutes": study["study_minutes"],
                    "streak": study["streak"], "xp": study["xp"], "quiz": quiz})


@app.route("/api/flashcards", methods=["GET", "POST"])
def flashcards():
    conn = db()
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        subject = str(body.get("subject", "General")).strip() or "General"
        question = str(body.get("question", "")).strip()
        answer = str(body.get("answer", "")).strip()
        if not question or not answer:
            conn.close()
            return jsonify({"error": "Question and answer are required."}), 400
        conn.execute("INSERT INTO flashcards (subject,question,answer,created_at) VALUES (?,?,?,?)",
                     (subject, question, answer, str(datetime.now())))
        conn.commit()
        add_xp(5)
    rows = conn.execute("SELECT * FROM flashcards ORDER BY id DESC").fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])


@app.route("/api/flashcards/<int:card_id>", methods=["DELETE"])
def delete_flashcard(card_id):
    conn = db()
    cur = conn.execute("DELETE FROM flashcards WHERE id=?", (card_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": bool(cur.rowcount)})


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no">
<title>Smart Student AI</title>
<style>
:root{--purple:#22d3ee;--purple2:#38bdf8;--ink:#e7f6ff;--muted:#92a9bb;--bg:#08111f;--card:#101e31;--line:#20354c;--green:#34d399;--orange:#fbbf24;--shadow:0 16px 36px rgba(0,0,0,.26)}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 15% 0%,#123653 0%,transparent 36%),var(--bg);color:var(--ink);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}button,input,select,textarea{font:inherit}button{border:0;cursor:pointer}.hidden{display:none!important}.app{min-height:100vh;max-width:720px;margin:auto;background:var(--bg);padding-bottom:92px}.topbar{padding:28px 20px 12px;display:flex;justify-content:space-between;align-items:center}.eyebrow{font-size:12px;color:var(--muted);margin:0 0 5px}.topbar h1{font-size:25px;margin:0;letter-spacing:-.6px}.avatar{width:44px;height:44px;border-radius:15px;background:linear-gradient(135deg,#0891b2,#2563eb);color:white;display:grid;place-items:center;font-weight:800;box-shadow:0 8px 18px #7657e833}.page{display:none;padding:8px 20px}.page.active{display:block}.welcome{background:linear-gradient(135deg,#0e7490,#155e75);color:#fff;border-radius:26px;padding:22px;margin:8px 0 18px;box-shadow:0 15px 30px #7657e82b}.welcome h2{margin:0 0 7px;font-size:24px}.welcome p{margin:0;color:#eeeaff;font-size:13px}.section-title{display:flex;justify-content:space-between;align-items:center;margin:22px 0 12px}.section-title h2{font-size:17px;margin:0}.section-title span{font-size:12px;color:var(--purple)}.card{background:var(--card);border:1px solid var(--line);border-radius:22px;padding:17px;box-shadow:var(--shadow);margin-bottom:14px}.card h3{font-size:16px;margin:0 0 12px}.stats{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}.stat{background:rgba(16,30,49,.92);border:1px solid var(--line);border-radius:19px;padding:15px}.stat .label{font-size:12px;color:var(--muted)}.stat strong{display:block;font-size:24px;margin-top:6px;color:var(--purple)}.quick-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}.quick{border-radius:20px;padding:17px;background:rgba(16,30,49,.92);border:1px solid var(--line);box-shadow:var(--shadow);text-align:left}.quick .icon{width:39px;height:39px;border-radius:13px;display:grid;place-items:center;margin-bottom:12px;font-size:20px;background:#12344b}.quick b{display:block;font-size:14px}.quick small{color:var(--muted);font-size:11px}.pill{display:inline-flex;align-items:center;gap:5px;padding:7px 11px;border-radius:99px;background:#12344b;color:var(--purple);font-size:12px;font-weight:700}.progress{height:8px;background:#1c3045;border-radius:20px;overflow:hidden;margin:12px 0}.progress i{display:block;height:100%;background:linear-gradient(90deg,var(--purple),var(--purple2));border-radius:20px}.row{display:flex;gap:10px;align-items:center}.row>*{flex:1}.input,textarea,select{width:100%;border:1px solid var(--line);background:#f7fbff;border-radius:14px;padding:12px 13px;outline:none;color:#132338;margin:5px 0}.input:focus,textarea:focus,select:focus{border-color:var(--purple)}textarea{min-height:125px;resize:vertical}.btn{background:var(--purple);color:#fff;border-radius:13px;padding:11px 15px;font-weight:700;margin:5px 3px 5px 0}.btn.secondary{background:#12344b;color:var(--purple)}.btn.ghost{background:transparent;color:var(--purple);border:1px solid #2c5872}.output{white-space:pre-wrap;background:#fafaff;border-radius:14px;padding:13px;margin-top:10px;min-height:36px;font-size:13px;color:#4b4c64}.subject-list{display:flex;flex-wrap:wrap;gap:7px;margin-top:10px}.subject-list button{border:1px solid #e3defd;background:#faf9ff;color:var(--purple);border-radius:99px;padding:8px 10px;font-size:12px}.question{padding:13px;border:1px solid var(--line);border-radius:15px;margin:9px 0;background:#fcfcff;font-size:13px}.question label{display:block;padding-top:7px;color:#5d5e73}.taskRow{display:flex;justify-content:space-between;gap:8px;align-items:center;padding:10px 0;border-bottom:1px solid var(--line);font-size:13px}.taskRow button{background:#eafaf4;color:#15946e;border-radius:9px;padding:6px 9px;font-size:11px}.timer{text-align:center;font-size:46px;font-weight:800;letter-spacing:2px;color:var(--purple);margin:15px}.focus{position:fixed;inset:0;background:#1d1934;color:#fff;z-index:20;display:none;align-items:center;justify-content:center;flex-direction:column;text-align:center}.focus h1{font-size:32px}.focus .timer{color:#fff}.bottom-nav{position:fixed;bottom:0;left:50%;transform:translateX(-50%);width:min(720px,100%);height:78px;background:rgba(8,17,31,.94);border-top:1px solid var(--line);display:flex;justify-content:space-around;align-items:center;z-index:10;backdrop-filter:blur(12px)}.nav-item{background:none;color:#7891a5;font-size:10px;display:flex;flex-direction:column;align-items:center;gap:4px;padding:8px 12px}.nav-item .nav-icon{font-size:20px}.nav-item.active{color:var(--purple);font-weight:800}.login{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;background:radial-gradient(circle at 15% 0%,#164e63 0%,transparent 42%),linear-gradient(145deg,#07101d,#102a43)}.login-box{width:100%;max-width:410px;background:#101e31;border-radius:30px;padding:28px 22px;box-shadow:0 18px 50px #5d49b125}.brand-mark{width:68px;height:68px;border-radius:23px;background:linear-gradient(135deg,#06b6d4,#2563eb);display:grid;place-items:center;color:#fff;font-size:31px;margin-bottom:20px}.login h1{font-size:29px;margin:0 0 8px}.login p{color:var(--muted);font-size:14px;line-height:1.5;margin-bottom:23px}.login .btn{width:100%;padding:14px}.small{font-size:12px;color:var(--muted)}.back{background:none;color:var(--muted);font-size:13px;padding:0;margin-bottom:12px}.hero-icon{font-size:25px;margin-bottom:10px}.list-line{padding:12px 0;border-bottom:1px solid var(--line);font-size:13px}.empty{color:var(--muted);font-size:13px;padding:10px 0}.danger{color:#e65d70}.success{color:#15946e}.dark{--ink:#f1efff;--muted:#aaa7c5;--bg:#17152a;--card:#24213d;--line:#3a3559;--shadow:0 12px 35px rgba(0,0,0,.22)}.dark .stat,.dark .quick,.dark .bottom-nav{background:#24213d}.dark .input,.dark textarea,.dark select,.dark .output,.dark .question{background:#1e1b34;color:var(--ink);border-color:var(--line)}.login-box .input{background:#f7fbff;color:#132338}.login-box .input::placeholder{color:#60768a}.dark .subject-list button{background:#2b2650;border-color:#554b8a}.dark .bottom-nav{background:rgba(36,33,61,.96)}@media(min-width:650px){.quick-grid{grid-template-columns:repeat(4,1fr)}.stats{grid-template-columns:repeat(5,1fr)}.page{padding-left:28px;padding-right:28px}.topbar{padding-left:28px;padding-right:28px}}
</style>
</head>
<body>
<div id="loginScreen" class="login"><div class="login-box"><div class="brand-mark">✦</div><h1>Smart Student</h1><p>Your calm, focused space for learning, planning and improving every day.</p><input id="loginName" class="input" placeholder="Your name" autocomplete="name"><input id="loginReg" class="input" placeholder="Register number"><button class="btn" onclick="enterApp()">Continue to learning →</button><div class="small" style="margin-top:14px">Offline-friendly student learning platform</div></div></div>
<div id="appShell" class="app hidden"><header class="topbar"><div><p class="eyebrow">GOOD MORNING, <span id="topName">STUDENT</span></p><h1 id="pageTitle">Home</h1></div><button class="avatar" onclick="go('profile')" id="avatar">S</button></header>
<main>
<section id="homePage" class="page active"><div class="welcome"><p id="welcomeSmall">Your learning journey</p><h2>Ready to make progress?</h2><p>Small steps today become big results tomorrow.</p></div><div class="stats"><div class="stat"><span class="label">XP Points</span><strong id="xp">0</strong></div><div class="stat"><span class="label">Level</span><strong id="level">1</strong></div><div class="stat"><span class="label">Streak</span><strong id="streak">0</strong></div><div class="stat"><span class="label">Study mins</span><strong id="minutes">0</strong></div><div class="stat"><span class="label">Average</span><strong id="average">0</strong></div></div><div class="section-title"><h2>Quick actions</h2><span>Explore</span></div><div class="quick-grid"><button class="quick" onclick="go('ai')"><span class="icon">🤖</span><b>AI Assistant</b><small>Ask and learn</small></button><button class="quick" onclick="go('voice')"><span class="icon">🎤</span><b>Voice Tutor</b><small>Listen & speak</small></button><button class="quick" onclick="go('notes')"><span class="icon">📚</span><b>Notes</b><small>Subject library</small></button><button class="quick" onclick="go('quiz')"><span class="icon">🧠</span><b>Quiz</b><small>Test yourself</small></button><button class="quick" onclick="go('upload')"><span class="icon">📄</span><b>PDF Reader</b><small>Upload & read</small></button></div><div class="section-title"><h2>Today's focus</h2><span onclick="go('planner')">Planner →</span></div><div class="card"><div class="row"><div><b>Keep your study streak alive</b><p class="small">Complete a focused session today.</p></div><span class="pill">🔥 <span id="homeStreak">0</span> days</span></div><button class="btn" onclick="studyToday()">I studied today</button><div id="streakOut" class="output hidden"></div></div></section>
<section id="alertsPage" class="page"><button class="back" onclick="go('home')">← Back to Home</button><div class="section-title"><h2>Alerts & reminders</h2><span class="pill">Updates</span></div><div class="card"><h3>⏳ Exam countdown</h3><input type="date" id="examDate" class="input"><button class="btn" onclick="saveExam()">Save exam date</button><div id="examOut" class="output">No exam date set.</div></div><div class="card"><h3>📌 Your tasks</h3><div class="row"><input id="task" class="input" placeholder="Add a study task"><button class="btn" onclick="addTask()">Add</button></div><div id="taskOut"></div></div><div class="card"><h3>🌐 Language</h3><button class="btn secondary" onclick="language('english')">English</button><button class="btn secondary" onclick="language('tamil')">தமிழ்</button><button class="btn secondary" onclick="language('hindi')">हिन्दी</button><div id="languageOut" class="output"></div></div></section>
<section id="dashboardPage" class="page"><div class="section-title"><h2>Dashboard</h2><span class="pill">Overview</span></div><div class="card"><div class="row"><div><p class="eyebrow">CURRENT LEVEL</p><h2 style="margin:0">Level <span id="dashLevel">1</span></h2></div><span class="pill">⭐ <span id="dashXp">0</span> XP</span></div><div class="progress"><i id="xpBar" style="width:0%"></i></div><p class="small">Keep learning to unlock the next level.</p></div><div class="stats"><div class="stat"><span class="label">Average mark</span><strong id="dashAverage">0</strong></div><div class="stat"><span class="label">Attendance</span><strong id="dashAttendance">0%</strong></div><div class="stat"><span class="label">Study time</span><strong id="dashMinutes">0</strong></div><div class="stat"><span class="label">Streak</span><strong id="dashStreak">0</strong></div></div><div class="section-title"><h2>Progress tools</h2></div><div class="quick-grid"><button class="quick" onclick="go('marks')"><span class="icon">📊</span><b>Marks</b><small>Track results</small></button><button class="quick" onclick="go('attendance')"><span class="icon">✅</span><b>Attendance</b><small>Stay eligible</small></button><button class="quick" onclick="go('planner')"><span class="icon">🗓️</span><b>Planner</b><small>Plan your hours</small></button><button class="quick" onclick="go('focus')"><span class="icon">🎯</span><b>Focus</b><small>Pomodoro mode</small></button><button class="quick" onclick="go('calendar')"><span class="icon">📅</span><b>Calendar</b><small>Plan events</small></button><button class="quick" onclick="go('assignments')"><span class="icon">📌</span><b>Assignments</b><small>Track deadlines</small></button><button class="quick" onclick="go('analytics')"><span class="icon">📈</span><b>Analytics</b><small>See trends</small></button><button class="quick" onclick="go('flashcards')"><span class="icon">🃏</span><b>Flashcards</b><small>Revise faster</small></button></div><div class="card"><h3>🏅 Achievements</h3><button class="btn secondary" onclick="badges()">View badges</button><div id="badgeOut" class="output"></div></div></section>
<section id="aiPage" class="page"><button class="back" onclick="go('home')">← Back to Home</button><div class="section-title"><h2>AI Assistant</h2><span class="pill">Offline AI</span></div><div class="card"><div class="hero-icon">🤖</div><h3>Ask your study helper</h3><input id="aiQuestion" class="input" placeholder="Ask about Python, DBMS, AI..."><button class="btn" onclick="askAI()">Ask AI</button><div id="aiOut" class="output">Your answer will appear here.</div></div><div class="card"><h3>🧠 Question generator</h3><input id="qSubject" class="input" placeholder="Subject / topic"><select id="qCount"><option value="5">5 Questions</option><option value="10">10 Questions</option><option value="15">15 Questions</option></select><button class="btn secondary" onclick="generateQuestions()">Generate questions</button><div id="questionOut" class="output"></div></div></section>
<section id="voicePage" class="page"><button class="back" onclick="go('home')">← Back to Home</button><div class="section-title"><h2>Voice Tutor</h2><span class="pill">Speak & listen</span></div><div class="card"><div class="hero-icon">🎤</div><h3>Learn out loud</h3><textarea id="voiceText" placeholder="Type a question or use your voice..."></textarea><button class="btn" onclick="voiceInput()">🎤 Speak</button><button class="btn secondary" onclick="voiceRead()">🔊 Read aloud</button><div id="voiceOut" class="output"></div></div></section>
<section id="notesPage" class="page"><button class="back" onclick="go('home')">← Back to Home</button><div class="section-title"><h2>Notes library</h2><span class="pill">Subjects</span></div><div class="card"><select id="department" onchange="loadSubjects()"><option>CSE</option><option>EEE</option><option>ECE</option><option>MECH</option><option>CIVIL</option></select><div id="subjectList" class="subject-list"></div><div id="notesOut" class="output">Select a subject to view notes.</div></div><div class="card"><h3>✨ Smart Notes Maker</h3><textarea id="smartText" placeholder="Paste long notes here..."></textarea><button class="btn" onclick="makeSmartNotes()">Make smart notes</button><div id="smartOut" class="output"></div></div></section>
<section id="quizPage" class="page"><button class="back" onclick="go('home')">← Back to Home</button><div class="section-title"><h2>Quiz zone</h2><span class="pill">Practice</span></div><div class="card"><h3>📝 Quick quiz</h3><div id="quizOut"></div><button class="btn" onclick="startQuiz()">Start quiz</button><button class="btn secondary" onclick="submitQuiz()">Submit</button><div id="quizResult" class="output"></div></div><div class="card"><h3>🎓 Final 50-question test</h3><button class="btn secondary" onclick="startFinalTest()">Start final test</button><button class="btn ghost" onclick="submitFinalTest()">Submit final test</button><div id="finalTestOut"></div><div id="finalTestResult" class="output"></div></div></section>
<section id="uploadPage" class="page"><button class="back" onclick="go('home')">← Back to Home</button><div class="section-title"><h2>PDF Reader & Scanner</h2><span class="pill">Files</span></div><div class="card"><h3>📄 Upload and read PDF</h3><p class="small">Upload a PDF to extract its text and open it in the built-in reader.</p><input type="file" id="fileInput" class="input" accept=".pdf,.txt,.doc,.docx"><button class="btn" onclick="uploadFile()">Upload PDF</button><div id="uploadOut" class="output"></div><div id="pdfReader" class="hidden" style="margin-top:12px"><p class="small">PDF preview</p><iframe id="pdfFrame" title="PDF Reader" style="width:100%;height:520px;border:1px solid var(--line);border-radius:14px;background:#fff"></iframe></div></div><div class="card"><h3>📷 Question image scanner</h3><p class="small">Select a clear image containing a question. OCR will read it and the study helper will explain the next steps.</p><input type="file" id="ocrFile" accept="image/*" class="input"><button class="btn secondary" onclick="uploadOCR()">Scan question</button><div id="ocrOut" class="output"></div></div><div class="card"><h3>🗂️ Uploaded files</h3><button class="btn ghost" onclick="loadUploads()">Refresh files</button><div id="uploadList"></div></div></section>
<section id="plannerPage" class="page"><button class="back" onclick="go('home')">← Back to Home</button><div class="section-title"><h2>Study planner</h2><span class="pill">Organise</span></div><div class="card"><h3>🎯 Personalised plan</h3><input id="planSubjects" class="input" placeholder="Python, DBMS, Java"><input id="planHours" class="input" type="number" value="2" placeholder="Hours"><button class="btn" onclick="createPlan()">Create plan</button><div id="planOut" class="output"></div></div><div class="card"><h3>🧮 Calculator</h3><input id="calc" class="input" placeholder="Example: 25+10*2"><button class="btn secondary" onclick="calculate()">Calculate</button><div id="calcOut" class="output"></div></div></section>
<section id="marksPage" class="page"><button class="back" onclick="go('dashboard')">← Back to Dashboard</button><div class="section-title"><h2>Marks & performance</h2><span class="pill">Analytics</span></div><div class="card"><h3>📈 Add marks</h3><input id="markSubject" class="input" placeholder="Subject"><input id="mark" class="input" type="number" min="0" max="100" placeholder="Mark out of 100"><button class="btn" onclick="addMark()">Add mark</button><div id="marksOut" class="output"></div></div><div class="card"><h3>📊 Performance analysis</h3><button class="btn secondary" onclick="performance()">Analyse performance</button><div id="performanceOut" class="output"></div></div><div class="card"><h3>⚠️ Weak topic detector</h3><button class="btn ghost" onclick="weakTopics()">Find weak topics</button><div id="weakOut" class="output"></div></div></section>
<section id="attendancePage" class="page"><button class="back" onclick="go('dashboard')">← Back to Dashboard</button><div class="section-title"><h2>Attendance</h2><span class="pill">Stay on track</span></div><div class="card"><h3>✅ Record attendance</h3><input id="attSubject" class="input" placeholder="Subject"><select id="attStatus"><option>Present</option><option>Absent</option></select><button class="btn" onclick="attendance()">Save attendance</button><div id="attOut" class="output"></div></div></section>
<section id="focusPage" class="page"><button class="back" onclick="go('dashboard')">← Back to Dashboard</button><div class="section-title"><h2>Focus mode</h2><span class="pill">25 minutes</span></div><div class="card" style="text-align:center"><div id="timer" class="timer">25:00</div><p class="small">A distraction-free Pomodoro session.</p><button class="btn" onclick="startTimer()">Start</button><button class="btn secondary" onclick="pauseTimer()">Pause</button><button class="btn ghost" onclick="resetTimer()">Reset</button><br><button class="btn" onclick="openFocus()">Enter full-screen focus</button></div></section>
<section id="gamesPage" class="page"><button class="back" onclick="go('home')">← Back to Home</button><div class="section-title"><h2>Educational games</h2><span class="pill">Play & learn</span></div><div class="card"><h3>🎮 Quick games</h3><button class="btn" onclick="numberGame()">🔢 Number game</button><button class="btn secondary" onclick="mathGame()">➕ Math game</button><div id="gameOut" class="output"></div></div><div class="card"><h3>🏆 Leaderboard</h3><button class="btn ghost" onclick="leaderboard()">Refresh leaderboard</button><div id="leaderOut" class="output"></div></div></section>
<section id="calendarPage" class="page"><button class="back" onclick="go('dashboard')">← Back to Dashboard</button><div class="section-title"><h2>Calendar</h2><span class="pill">Plan your week</span></div><div class="card"><h3>📅 Add calendar event</h3><input id="eventTitle" class="input" placeholder="Event title"><div class="row"><input id="eventDate" class="input" type="date"><input id="eventTime" class="input" type="time"></div><select id="eventType"><option>Study</option><option>Exam</option><option>Assignment</option><option>Personal</option></select><button class="btn" onclick="addEvent()">Add event</button><div id="calendarOut" class="output"></div></div><div class="card"><h3>Upcoming events</h3><div id="eventList"></div></div></section>
<section id="assignmentsPage" class="page"><button class="back" onclick="go('dashboard')">← Back to Dashboard</button><div class="section-title"><h2>Assignment tracker</h2><span class="pill">Deadlines</span></div><div class="card"><h3>📌 Add assignment</h3><input id="assignmentTitle" class="input" placeholder="Assignment title"><input id="assignmentSubject" class="input" placeholder="Subject"><input id="assignmentDate" class="input" type="date"><select id="assignmentPriority"><option>High</option><option selected>Medium</option><option>Low</option></select><button class="btn" onclick="addAssignment()">Save assignment</button><div id="assignmentOut" class="output"></div></div><div class="card"><h3>Your assignments</h3><div id="assignmentList"></div></div></section>
<section id="analyticsPage" class="page"><button class="back" onclick="go('dashboard')">← Back to Dashboard</button><div class="section-title"><h2>Advanced analytics</h2><span class="pill">Your trends</span></div><div class="card"><h3>📈 Performance snapshot</h3><button class="btn" onclick="loadAnalytics()">Refresh analytics</button><div id="analyticsOut" class="output"></div></div><div class="card"><h3>Subject averages</h3><div id="subjectAnalytics"></div></div><div class="card"><h3>Attendance by subject</h3><div id="attendanceAnalytics"></div></div></section>
<section id="flashcardsPage" class="page"><button class="back" onclick="go('home')">← Back to Home</button><div class="section-title"><h2>Flashcards</h2><span class="pill">Active recall</span></div><div class="card"><h3>🃏 Create a flashcard</h3><input id="cardSubject" class="input" placeholder="Subject"><textarea id="cardQuestion" placeholder="Question / front"></textarea><textarea id="cardAnswer" placeholder="Answer / back"></textarea><button class="btn" onclick="addFlashcard()">Add flashcard</button><div id="flashcardOut" class="output"></div></div><div class="card"><h3>Revision deck</h3><button class="btn secondary" onclick="loadFlashcards()">Load cards</button><div id="flashcardList"></div></div></section>
<section id="profilePage" class="page"><div class="section-title"><h2>Profile</h2><span class="pill">Student</span></div><div class="card"><h3>👤 Your details</h3><input id="name" class="input" placeholder="Student name"><input id="regno" class="input" placeholder="Register number"><select id="profileDepartment"><option>CSE</option><option>EEE</option><option>ECE</option><option>MECH</option><option>CIVIL</option></select><input id="profileYear" class="input" value="2nd Year" placeholder="Year"><button class="btn" onclick="saveProfile()">Save profile</button><div id="profileOut" class="output"></div></div><div class="card"><h3>Appearance</h3><p class="small">Choose a comfortable study theme.</p><button class="btn secondary" onclick="toggleDark()">Toggle dark mode</button></div><div class="card"><h3>Account</h3><button class="btn ghost" onclick="logout()">Log out</button></div></section>
</main><div id="focus" class="focus"><h1>Focus mode</h1><p>Study time. Stay focused.</p><div id="focusTimer" class="timer">25:00</div><button class="btn" style="background:#fff;color:#2a2448" onclick="closeFocus()">Exit focus mode</button></div>
<nav class="bottom-nav"><button class="nav-item active" data-page="home" onclick="go('home')"><span class="nav-icon">⌂</span>Home</button><button class="nav-item" data-page="alerts" onclick="go('alerts')"><span class="nav-icon">♢</span>Alerts</button><button class="nav-item" data-page="dashboard" onclick="go('dashboard')"><span class="nav-icon">▦</span>Dashboard</button><button class="nav-item" data-page="profile" onclick="go('profile')"><span class="nav-icon">◯</span>Profile</button></nav></div>
<script>
const $=id=>document.getElementById(id);let currentPage='home',quizData=[],finalData=[],timerSeconds=1500,timerInterval=null;
async function api(url,options={}){const r=await fetch(url,options),text=await r.text();let d;try{d=JSON.parse(text)}catch(e){throw Error('Server returned an invalid response.')}if(!r.ok)throw Error(d.error||'Request failed.');return d}
function showError(id,e){$(id).innerText='❌ '+e.message}
function go(page){currentPage=page;document.querySelectorAll('.page').forEach(x=>x.classList.remove('active'));const target=$(page+'Page');if(target)target.classList.add('active');$('pageTitle').innerText=({home:'Home',alerts:'Alerts',dashboard:'Dashboard',profile:'Profile',ai:'AI Assistant',voice:'Voice Tutor',notes:'Notes',quiz:'Quiz',upload:'Upload & PDF',planner:'Study Planner',marks:'Marks',attendance:'Attendance',focus:'Focus',games:'Games',calendar:'Calendar',assignments:'Assignments',analytics:'Analytics',flashcards:'Flashcards'})[page]||'Smart Student';document.querySelectorAll('.nav-item').forEach(x=>x.classList.toggle('active',x.dataset.page===page));window.scrollTo({top:0,behavior:'smooth'});if(page==='dashboard')refreshDashboard()}
function enterApp(){const name=$('loginName').value.trim()||'Student',reg=$('loginReg').value.trim();localStorage.setItem('ss_logged_v2','1');if(name){api('/api/profile',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,regno:reg,department:'CSE',year:'2nd Year'})}).catch(()=>{})}showApp()}
function showApp(){$('loginScreen').classList.add('hidden');$('appShell').classList.remove('hidden');refreshDashboard();loadSubjects();loadMarks();loadTasks();api('/api/exam').then(showExam).catch(()=>{});updateTimer()}
function logout(){localStorage.removeItem('ss_logged_v2');localStorage.removeItem('ss_logged');$('appShell').classList.add('hidden');$('loginScreen').classList.remove('hidden')}
function refreshDashboard(){api('/api/dashboard').then(d=>{$('xp').innerText=d.xp;$('level').innerText=d.level;$('streak').innerText=d.streak;$('minutes').innerText=d.study_minutes;$('average').innerText=d.average;$('dashXp').innerText=d.xp;$('dashLevel').innerText=d.level;$('dashAverage').innerText=d.average;$('dashMinutes').innerText=d.study_minutes;$('dashStreak').innerText=d.streak;$('homeStreak').innerText=d.streak;$('topName').innerText=(d.name||'Student').toUpperCase();$('avatar').innerText=(d.name||'S')[0].toUpperCase();$('name').value=d.name;$('regno').value=d.regno;$('profileDepartment').value=d.department||'CSE';$('profileYear').value=d.year||'2nd Year';$('xpBar').style.width=(d.xp%100)+'%';api('/api/attendance').then(a=>$('dashAttendance').innerText=a.percentage+'%').catch(()=>{})}).catch(()=>{})}
function saveProfile(){api('/api/profile',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:$('name').value,regno:$('regno').value,department:$('profileDepartment').value,year:$('profileYear').value})}).then(d=>{$('profileOut').innerText=d.message;refreshDashboard()}).catch(e=>showError('profileOut',e))}
function askAI(){api('/api/ai',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:$('aiQuestion').value})}).then(d=>{$('aiOut').innerText=d.answer;refreshDashboard()}).catch(e=>showError('aiOut',e))}
function voiceInput(){if(!('webkitSpeechRecognition'in window)){ $('voiceOut').innerText='Voice input is not supported in this browser.';return}let r=new webkitSpeechRecognition();r.lang='en-IN';r.start();r.onresult=e=>{$('voiceText').value=e.results[0][0].transcript;$('voiceOut').innerText=$('voiceText').value}}
function voiceRead(){let text=$('voiceText').value||$('aiOut').innerText;if('speechSynthesis'in window)speechSynthesis.speak(new SpeechSynthesisUtterance(text))}
function loadSubjects(){api('/api/subjects').then(data=>{$('subjectList').innerHTML='';(data[$('department').value]||[]).forEach(s=>{let b=document.createElement('button');b.innerText=s;b.onclick=()=>loadNote(s);$('subjectList').appendChild(b)})})}
function loadNote(s){api('/api/notes/'+encodeURIComponent(s)).then(d=>$('notesOut').innerText=d.content).catch(e=>showError('notesOut',e))}
function generateQuestions(){api('/api/question-generator',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({subject:$('qSubject').value,count:Number($('qCount').value)})}).then(d=>$('questionOut').innerText=d.questions.map((x,i)=>(i+1)+'. '+x).join('\n')).catch(e=>showError('questionOut',e))}
function makeSmartNotes(){api('/api/smart-notes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:$('smartText').value})}).then(d=>$('smartOut').innerText=d.notes).catch(e=>showError('smartOut',e))}
function uploadFile(){let f=$('fileInput').files[0];if(!f){$('uploadOut').innerText='Please select a PDF or text file.';return}let form=new FormData();form.append('file',f);fetch('/api/upload',{method:'POST',body:form}).then(r=>r.json()).then(d=>{if(d.error)throw Error(d.error);$('uploadOut').innerText='Uploaded: '+d.filename+'\n\n'+(d.text||'No text extracted.');if(f.name.toLowerCase().endsWith('.pdf')){$('pdfReader').classList.remove('hidden');$('pdfFrame').src='/uploads/'+encodeURIComponent(d.filename)}loadUploads();refreshDashboard()}).catch(e=>showError('uploadOut',e))}
function uploadOCR(){let f=$('ocrFile').files[0];if(!f){$('ocrOut').innerText='Please select an image.';return}let form=new FormData();form.append('file',f);fetch('/api/upload',{method:'POST',body:form}).then(r=>r.json()).then(d=>{if(d.error)throw Error(d.error);return api('/api/ocr-question',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:d.text||''})})}).then(d=>$('ocrOut').innerText=d.answer).catch(e=>showError('ocrOut',e))}
function loadUploads(){api('/api/uploads').then(data=>{$('uploadList').innerHTML=data.length?data.map(x=>'<div class="list-line"><b>'+x.filename+'</b><br><span class="small">'+x.filetype+' · '+x.created_at+'</span> <a class="btn ghost" href="/uploads/'+encodeURIComponent(x.filename)+'" target="_blank">Open</a></div>').join(''):'<div class="empty">No uploaded files yet.</div>'}).catch(e=>showError('uploadList',e))}
function createPlan(){let subjects=$('planSubjects').value.split(',').map(x=>x.trim()).filter(Boolean);api('/api/study-plan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({subjects,hours:Number($('planHours').value)})}).then(d=>$('planOut').innerText=d.plan.map(x=>'📚 '+x.subject+' - '+x.minutes+' minutes\n  • '+x.tasks.join('\n  • ')).join('\n\n')).catch(e=>showError('planOut',e))}
function startQuiz(){api('/api/quiz').then(data=>{quizData=data;$('quizOut').innerHTML=data.map((q,i)=>'<div class="question"><b>'+(i+1)+'. '+q.q+'</b><br>'+q.options.map(o=>'<label><input type="radio" name="quiz'+i+'" value="'+o.replace(/"/g,'&quot;')+'"> '+o+'</label>').join('')+'</div>').join('');$('quizResult').innerText=''}).catch(e=>showError('quizResult',e))}
function submitQuiz(){if(!quizData.length){$('quizResult').innerText='Start the quiz first.';return}let answers=quizData.map((q,i)=>{let s=document.querySelector('input[name="quiz'+i+'"]:checked');return{index:q.index,answer:s?s.value:''}});api('/api/quiz/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({answers})}).then(d=>{$('quizResult').innerText='🎉 Score: '+d.score+'/'+d.total+'\n⭐ XP Earned: '+d.xp;refreshDashboard()}).catch(e=>showError('quizResult',e))}
function startFinalTest(){api('/api/final-test').then(d=>{finalData=d.questions;$('finalTestOut').innerHTML=finalData.map((q,i)=>'<div class="question"><b>'+(i+1)+'. '+q.q+'</b><br>'+q.options.map(o=>'<label><input type="radio" name="final'+i+'" value="'+o.replace(/"/g,'&quot;')+'"> '+o+'</label>').join('')+'</div>').join('');$('finalTestResult').innerText='' }).catch(e=>showError('finalTestResult',e))}
function submitFinalTest(){if(!finalData.length){$('finalTestResult').innerText='Start the final test first.';return}let answers=finalData.map((q,i)=>{let s=document.querySelector('input[name="final'+i+'"]:checked');return{index:q.index,answer:s?s.value:''}});api('/api/final-test/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({answers})}).then(d=>{$('finalTestResult').innerText='🎉 Score: '+d.score+'/'+d.total+'\n⭐ XP Earned: '+d.xp;refreshDashboard()}).catch(e=>showError('finalTestResult',e))}
function performance(){api('/api/performance').then(data=>$('performanceOut').innerText=data.length?data.map(x=>x.subject+' : '+x.average+' → '+x.status).join('\n'):'No marks available.').catch(e=>showError('performanceOut',e))}
function addMark(){api('/api/marks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({subject:$('markSubject').value,mark:Number($('mark').value)})}).then(()=>{loadMarks();refreshDashboard()}).catch(e=>showError('marksOut',e))}
function loadMarks(){api('/api/marks').then(d=>$('marksOut').innerText='Total: '+d.total+'\nAverage: '+d.average+'\n\n'+d.marks.map(x=>x.subject+' : '+x.mark).join('\n')).catch(e=>showError('marksOut',e))}
function weakTopics(){api('/api/weak-topic').then(data=>$('weakOut').innerText=data.length?data.map(x=>'⚠️ '+x.subject+' : '+x.mark+'\n'+x.recommendation).join('\n\n'):'🎉 No weak topics detected.').catch(e=>showError('weakOut',e))}
function attendance(){api('/api/attendance',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({subject:$('attSubject').value,status:$('attStatus').value})}).then(d=>{$('attOut').innerText='Present: '+d.present+'\nTotal: '+d.total+'\nAttendance: '+d.percentage+'%';refreshDashboard()}).catch(e=>showError('attOut',e))}
function studyToday(){api('/api/study',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({minutes:30})}).then(d=>{$('streakOut').classList.remove('hidden');$('streakOut').innerText='🔥 '+d.streak+' day streak\n📚 '+d.study_minutes+' total minutes';refreshDashboard()}).catch(e=>showError('streakOut',e))}
function badges(){api('/api/achievements').then(d=>$('badgeOut').innerText=d.badges.join('\n')).catch(e=>showError('badgeOut',e))}
function saveExam(){api('/api/exam',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({exam_date:$('examDate').value})}).then(showExam).catch(e=>showError('examOut',e))}
function showExam(d){$('examOut').innerText=d.days_left===null?'No exam date set.':'⏳ '+d.days_left+' days remaining'}
function leaderboard(){api('/api/leaderboard').then(data=>$('leaderOut').innerText=data.map((x,i)=>(i+1)+'. '+x.name+' — '+x.xp+' XP').join('\n')||'No students yet.').catch(e=>showError('leaderOut',e))}
function calculate(){api('/api/calculate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expression:$('calc').value})}).then(d=>$('calcOut').innerText=d.result).catch(e=>showError('calcOut',e))}
function numberGame(){api('/api/game/number').then(d=>{let guess=prompt(d.message);if(Number(guess)===Number(d.number)){$('gameOut').innerText='🎉 Correct! +20 XP';api('/api/xp',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({points:20})}).then(refreshDashboard)}else $('gameOut').innerText='❌ Try again!'})}
function mathGame(){api('/api/game/math').then(d=>{let answer=prompt(d.question);if(Number(answer)===Number(d.answer)){$('gameOut').innerText='🎉 Correct! +15 XP';api('/api/xp',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({points:15})}).then(refreshDashboard)}else $('gameOut').innerText='❌ Incorrect.'})}
function language(lang){api('/api/language/'+lang).then(d=>$('languageOut').innerText=d.welcome)}
function addTask(){api('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task:$('task').value})}).then(()=>{$('task').value='';loadTasks()}).catch(e=>showError('taskOut',e))}
function loadTasks(){api('/api/tasks').then(data=>{$('taskOut').innerHTML='';data.forEach(x=>{let row=document.createElement('div');row.className='taskRow';let span=document.createElement('span');span.innerText=(x.completed?'✅ ':'⬜ ')+x.task;row.appendChild(span);if(!x.completed){let b=document.createElement('button');b.innerText='Done';b.onclick=()=>api('/api/tasks/'+x.id,{method:'PUT'}).then(loadTasks).then(refreshDashboard);row.appendChild(b)}$('taskOut').appendChild(row)})})}
function updateTimer(){let text=String(Math.floor(timerSeconds/60)).padStart(2,'0')+':'+String(timerSeconds%60).padStart(2,'0');$('timer').innerText=text;$('focusTimer').innerText=text}
function startTimer(){if(timerInterval)return;timerInterval=setInterval(()=>{if(timerSeconds>0){timerSeconds--;updateTimer()}else{pauseTimer();api('/api/study',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({minutes:25})});alert('Pomodoro completed!');refreshDashboard()}},1000)}
function pauseTimer(){clearInterval(timerInterval);timerInterval=null}function resetTimer(){pauseTimer();timerSeconds=1500;updateTimer()}function openFocus(){$('focus').style.display='flex'}function closeFocus(){$('focus').style.display='none'}
function toggleDark(){document.body.classList.toggle('dark');localStorage.setItem('ss_dark',document.body.classList.contains('dark')?'1':'0')}
function addEvent(){api('/api/calendar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:$('eventTitle').value,event_date:$('eventDate').value,event_time:$('eventTime').value,event_type:$('eventType').value})}).then(()=>{ $('calendarOut').innerText='Event added.';loadEvents() }).catch(e=>showError('calendarOut',e))}
function loadEvents(){api('/api/calendar').then(data=>{$('eventList').innerHTML=data.length?data.map(x=>'<div class="list-line"><b>'+x.title+'</b><br><span class="small">'+x.event_date+(x.event_time?' · '+x.event_time:'')+' · '+x.event_type+'</span> <button class="btn ghost" onclick="deleteEvent('+x.id+')">Delete</button></div>').join(''):'<div class="empty">No events yet.</div>'}).catch(e=>showError('eventList',e))}
function deleteEvent(id){api('/api/calendar/'+id,{method:'DELETE'}).then(loadEvents)}
function addAssignment(){api('/api/assignments',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:$('assignmentTitle').value,subject:$('assignmentSubject').value,due_date:$('assignmentDate').value,priority:$('assignmentPriority').value})}).then(()=>{ $('assignmentOut').innerText='Assignment saved.';loadAssignments() }).catch(e=>showError('assignmentOut',e))}
function loadAssignments(){api('/api/assignments').then(data=>{$('assignmentList').innerHTML=data.length?data.map(x=>'<div class="list-line"><b class="'+(x.completed?'success':'')+'">'+(x.completed?'✅ ':'📌 ')+x.title+'</b><br><span class="small">'+(x.subject||'General')+' · Due '+x.due_date+' · '+x.priority+'</span> '+(x.completed?'':'<button class="btn ghost" onclick="completeAssignment('+x.id+')">Done</button>')+'<button class="btn ghost" onclick="deleteAssignment('+x.id+')">Delete</button></div>').join(''):'<div class="empty">No assignments yet.</div>'}).catch(e=>showError('assignmentList',e))}
function completeAssignment(id){api('/api/assignments/'+id,{method:'PUT'}).then(()=>{loadAssignments();refreshDashboard()})}function deleteAssignment(id){api('/api/assignments/'+id,{method:'DELETE'}).then(loadAssignments)}
function loadAnalytics(){api('/api/analytics').then(d=>{$('analyticsOut').innerText='Study time: '+d.study_minutes+' minutes\nCurrent streak: '+d.streak+' days\nXP: '+d.xp+'\nQuiz attempts: '+d.quiz.length;$('subjectAnalytics').innerHTML=d.subjects.length?d.subjects.map(x=>'<div class="list-line"><b>'+x.subject+'</b><br><span class="small">Average: '+x.average+' · Attempts: '+x.attempts+'</span><div class="progress"><i style="width:'+Math.min(100,x.average)+'%"></i></div></div>').join(''):'<div class="empty">Add marks to see subject trends.</div>';$('attendanceAnalytics').innerHTML=d.attendance.length?d.attendance.map(x=>'<div class="list-line"><b>'+x.subject+'</b><span class="pill" style="float:right">'+x.percentage+'%</span><div class="progress"><i style="width:'+x.percentage+'%"></i></div></div>').join(''):'<div class="empty">Record attendance to see trends.</div>'}).catch(e=>showError('analyticsOut',e))}
function addFlashcard(){api('/api/flashcards',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({subject:$('cardSubject').value,question:$('cardQuestion').value,answer:$('cardAnswer').value})}).then(()=>{ $('flashcardOut').innerText='Flashcard added.';$('cardQuestion').value='';$('cardAnswer').value='';loadFlashcards() }).catch(e=>showError('flashcardOut',e))}
function loadFlashcards(){api('/api/flashcards').then(data=>{$('flashcardList').innerHTML=data.length?data.map(x=>'<div class="question"><span class="pill">'+x.subject+'</span><p><b>Q:</b> '+x.question+'</p><button class="btn secondary" onclick="this.nextElementSibling.classList.toggle(\'hidden\')">Show answer</button><p class="output hidden"><b>A:</b> '+x.answer+'</p><button class="btn ghost" onclick="deleteFlashcard('+x.id+')">Delete</button></div>').join(''):'<div class="empty">Create your first flashcard.</div>'}).catch(e=>showError('flashcardList',e))}
function deleteFlashcard(id){api('/api/flashcards/'+id,{method:'DELETE'}).then(loadFlashcards)}

if(localStorage.getItem('ss_dark')==='1')document.body.classList.add('dark');
// Versioned login key makes older sessions show the login screen once after an app update.
if(localStorage.getItem('ss_logged_v2'))showApp();
loadEvents();loadAssignments();loadFlashcards();loadUploads();
</script></body></html>
"""


if __name__ == "__main__":
    print("=" * 60)
    print("🤖 SMART STUDENT AI")
    print("=" * 60)
    print("Server started!")
    print("Open: http://127.0.0.1:5000")
    print("=" * 60)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
