import os
import sqlite3
from datetime import datetime, date
from functools import wraps

from flask import (
    Flask, g, render_template, request, redirect, url_for,
    flash, session, send_file, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
from dateutil.relativedelta import relativedelta
from io import StringIO
import csv

# -----------------------------
# Config
# -----------------------------
APP_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(APP_DIR, "attendance.db")
SCHEMA_PATH = os.path.join(APP_DIR, "schema.sql")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-in-production")
app.config["DATABASE"] = DB_PATH


# -----------------------------
# DB helpers
# -----------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(err):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    with app.open_resource("schema.sql", mode="r") as f:
        db.executescript(f.read())
    db.commit()

    # Create default admin if none exists
    cur = db.execute("SELECT COUNT(*) as c FROM users")
    if cur.fetchone()["c"] == 0:
        db.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
            ("admin", generate_password_hash("admin"), "teacher"),
        )
        db.commit()


# -----------------------------
# Auth
# -----------------------------
def login_required(view):
    @wraps(view)
    def wrapped_view(**kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(**kwargs)
    return wrapped_view


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            flash("Logged in successfully.", "success")
            return redirect(url_for("dashboard"))
        flash("Invalid credentials.", "error")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("login"))


# -----------------------------
# Routes
# -----------------------------
@app.route("/")
@login_required
def dashboard():
    db = get_db()

    # Basic stats for today
    today = date.today().isoformat()
    total_students = db.execute("SELECT COUNT(*) as c FROM students").fetchone()["c"]
    counts = db.execute("""
        SELECT status, COUNT(*) as c
        FROM attendance WHERE date = ?
        GROUP BY status
    """, (today,)).fetchall()
    by_status = {r["status"]: r["c"] for r in counts}
    present = by_status.get("Present", 0)
    absent = by_status.get("Absent", 0)
    late = by_status.get("Late", 0)

    classes = [r["class"] for r in db.execute("SELECT DISTINCT class FROM students ORDER BY class").fetchall()]

    return render_template(
        "dashboard.html",
        total_students=total_students,
        today=today,
        present=present,
        absent=absent,
        late=late,
        classes=classes
    )


# ---- Students ----
@app.route("/students")
@login_required
def students():
    db = get_db()
    q = request.args.get("q", "").strip()
    cls = request.args.get("class", "").strip()
    sql = "SELECT * FROM students WHERE 1=1"
    params = []
    if q:
        sql += " AND (student_id LIKE ? OR name LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    if cls:
        sql += " AND class = ?"
        params += [cls]
    sql += " ORDER BY class, name"
    rows = db.execute(sql, tuple(params)).fetchall()

    classes = [r["class"] for r in db.execute("SELECT DISTINCT class FROM students ORDER BY class").fetchall()]
    return render_template("students.html", students=rows, classes=classes, q=q, selected_class=cls)


@app.route("/students/add", methods=["GET", "POST"])
@login_required
def add_student():
    if request.method == "POST":
        student_id = request.form.get("student_id", "").strip()
        name = request.form.get("name", "").strip()
        cls = request.form.get("class", "").strip()

        if not student_id or not name or not cls:
            flash("All fields are required.", "error")
            return render_template("student_form.html", mode="add")

        db = get_db()
        try:
            db.execute(
                "INSERT INTO students (student_id, name, class) VALUES (?,?,?)",
                (student_id, name, cls)
            )
            db.commit()
            flash("Student added.", "success")
            return redirect(url_for("students"))
        except sqlite3.IntegrityError:
            flash("Student ID already exists.", "error")

    return render_template("student_form.html", mode="add")


@app.route("/students/<student_id>/edit", methods=["GET", "POST"])
@login_required
def edit_student(student_id):
    db = get_db()
    row = db.execute("SELECT * FROM students WHERE student_id = ?", (student_id,)).fetchone()
    if not row:
        flash("Student not found.", "error")
        return redirect(url_for("students"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        cls = request.form.get("class", "").strip()
        if not name or not cls:
            flash("All fields are required.", "error")
            return render_template("student_form.html", mode="edit", student=row)

        db.execute("UPDATE students SET name = ?, class = ? WHERE student_id = ?", (name, cls, student_id))
        db.commit()
        flash("Student updated.", "success")
        return redirect(url_for("students"))

    return render_template("student_form.html", mode="edit", student=row)


@app.route("/students/<student_id>/delete", methods=["POST"])
@login_required
def delete_student(student_id):
    db = get_db()
    db.execute("DELETE FROM students WHERE student_id = ?", (student_id,))
    db.commit()
    flash("Student deleted.", "info")
    return redirect(url_for("students"))


# ---- Attendance ----
@app.route("/attendance/mark", methods=["GET", "POST"])
@login_required
def attendance_mark():
    db = get_db()
    selected_date = request.values.get("date") or date.today().isoformat()
    selected_class = request.values.get("class", "")
    classes = [r["class"] for r in db.execute("SELECT DISTINCT class FROM students ORDER BY class").fetchall()]

    students = []
    if selected_class:
        students = db.execute(
            "SELECT * FROM students WHERE class = ? ORDER BY name",
            (selected_class,)
        ).fetchall()

    if request.method == "POST" and students:
        # Save form radio selections
        for s in students:
            key = f"status_{s['student_id']}"
            status = request.form.get(key)
            if status in ("Present", "Absent", "Late"):
                # Upsert attendance for (student_id, date)
                ts = datetime.now().isoformat(timespec="seconds")
                existing = db.execute(
                    "SELECT id FROM attendance WHERE student_id = ? AND date = ?",
                    (s["student_id"], selected_date)
                ).fetchone()

                if existing:
                    db.execute(
                        "UPDATE attendance SET status = ?, timestamp = ? WHERE id = ?",
                        (status, ts, existing["id"])
                    )
                else:
                    db.execute(
                        "INSERT INTO attendance (student_id, date, status, timestamp) VALUES (?,?,?,?)",
                        (s["student_id"], selected_date, status, ts)
                    )
        db.commit()
        flash("Attendance saved.", "success")
        return redirect(url_for("attendance_mark") + f"?date={selected_date}&class={selected_class}")

    # Fetch existing statuses to pre-select radios
    existing = {}
    if selected_class:
        rows = db.execute(
            "SELECT student_id, status FROM attendance WHERE date = ?",
            (selected_date,)
        ).fetchall()
        existing = {r["student_id"]: r["status"] for r in rows}

    return render_template(
        "attendance_mark.html",
        classes=classes,
        selected_class=selected_class,
        selected_date=selected_date,
        students=students,
        existing=existing
    )


@app.route("/attendance/scan", methods=["POST"])
@login_required
def attendance_scan():
    """
    Endpoint for keyboard-wedge QR/RFID scanners:
    Scanner inputs student_id and hits Enter -> we mark 'Present' for today by default,
    unless 'status' is provided in the form.
    """
    data = request.get_json(silent=True) or request.form
    sid = (data.get("student_id") or "").strip()
    status = (data.get("status") or "Present").strip()
    att_date = (data.get("date") or date.today().isoformat()).strip()

    if status not in ("Present", "Absent", "Late"):
        status = "Present"

    db = get_db()
    student = db.execute("SELECT * FROM students WHERE student_id = ?", (sid,)).fetchone()
    if not student:
        return jsonify({"ok": False, "message": "Student not found."}), 404

    ts = datetime.now().isoformat(timespec="seconds")
    existing = db.execute(
        "SELECT id FROM attendance WHERE student_id = ? AND date = ?",
        (sid, att_date)
    ).fetchone()
    if existing:
        db.execute(
            "UPDATE attendance SET status = ?, timestamp = ? WHERE id = ?",
            (status, ts, existing["id"])
        )
    else:
        db.execute(
            "INSERT INTO attendance (student_id, date, status, timestamp) VALUES (?,?,?,?)",
            (sid, att_date, status, ts)
        )
    db.commit()
    return jsonify({"ok": True, "message": f"{sid} -> {status} recorded."})


# ---- Reports ----
@app.route("/reports", methods=["GET", "POST"])
@login_required
def reports():
    db = get_db()
    classes = [r["class"] for r in db.execute("SELECT DISTINCT class FROM students ORDER BY class").fetchall()]

    view = request.values.get("view", "daily")
    selected_date = request.values.get("date", date.today().isoformat())
    selected_class = request.values.get("class", "")
    month = request.values.get("month")  # 'YYYY-MM'

    daily_rows = []
    daily_counts = {"Present": 0, "Absent": 0, "Late": 0}
    if view == "daily":
        base_sql = """
            SELECT s.student_id, s.name, s.class,
                   COALESCE(a.status, 'Absent') as status
            FROM students s
            LEFT JOIN attendance a
              ON s.student_id = a.student_id AND a.date = ?
        """
        params = [selected_date]
        if selected_class:
            base_sql += " WHERE s.class = ?"
            params.append(selected_class)
        base_sql += " ORDER BY s.class, s.name"

        daily_rows = db.execute(base_sql, tuple(params)).fetchall()
        for r in daily_rows:
            daily_counts[r["status"]] = daily_counts.get(r["status"], 0) + 1

    monthly_table = []
    monthly_meta = {"month": month}
    if view == "monthly" and month:
        # month format: YYYY-MM
        year, mm = map(int, month.split("-"))
        first_day = date(year, mm, 1)
        next_month = first_day + relativedelta(months=1)
        last_day = next_month - relativedelta(days=1)

        sql = """
            SELECT s.student_id, s.name, s.class,
                SUM(CASE WHEN a.status = 'Present' THEN 1 ELSE 0 END) as present_days,
                SUM(CASE WHEN a.status = 'Late' THEN 1 ELSE 0 END) as late_days,
                COUNT(a.id) as total_marked
            FROM students s
            LEFT JOIN attendance a
              ON s.student_id = a.student_id
             AND a.date >= ? AND a.date <= ?
        """
        params = [first_day.isoformat(), last_day.isoformat()]
        if selected_class:
            sql += " WHERE s.class = ?"
            params.append(selected_class)
        sql += " GROUP BY s.student_id, s.name, s.class ORDER BY s.class, s.name"

        monthly_table = db.execute(sql, tuple(params)).fetchall()
        # Compute percentage assuming school days = days with any mark (or treat unmarked as Absent).
        # Simpler: denominator = number of days between first_day..last_day where the school was open.
        # For demo: use 'total_marked' + implicit absents if needed. We'll compute percent on total_marked
        # to avoid assuming calendar holidays. Maintainers can adjust if required.

    return render_template(
        "reports.html",
        classes=classes,
        selected_class=selected_class,
        selected_date=selected_date,
        daily_rows=daily_rows,
        daily_counts=daily_counts,
        view=view,
        month=month,
        monthly_table=monthly_table,
        monthly_meta=monthly_meta
    )


@app.route("/export/daily.csv")
@login_required
def export_daily_csv():
    db = get_db()
    selected_date = request.args.get("date", date.today().isoformat())
    selected_class = request.args.get("class", "")

    sql = """
        SELECT s.student_id, s.name, s.class,
               COALESCE(a.status, 'Absent') as status
        FROM students s
        LEFT JOIN attendance a
          ON s.student_id = a.student_id AND a.date = ?
    """
    params = [selected_date]
    if selected_class:
        sql += " WHERE s.class = ?"
        params.append(selected_class)
    sql += " ORDER BY s.class, s.name"

    rows = db.execute(sql, tuple(params)).fetchall()

    # Create CSV in-memory
    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(["student_id", "name", "class", "date", "status"])
    for r in rows:
        cw.writerow([r["student_id"], r["name"], r["class"], selected_date, r["status"]])
    si.seek(0)
    return send_file(
        StringIOToBytes(si),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"daily_{selected_date}.csv"
    )


# Helper to convert StringIO -> Bytes for send_file
def StringIOToBytes(sio):
    from io import BytesIO
    bio = BytesIO()
    bio.write(sio.getvalue().encode("utf-8"))
    bio.seek(0)
    return bio


# -----------------------------
# App entrypoint
# -----------------------------
if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        # Auto-initialize DB on first run
        with app.app_context():
            init_db()
        print("Database created with default admin (username: admin, password: admin).")

    # Host 0.0.0.0 so other devices on LAN can open it (optional).
    app.run(host="0.0.0.0", port=5000)