from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt
from cryptography.fernet import Fernet
import sqlite3
import os
import pandas as pd

app = Flask(__name__)
app.secret_key = "1b60d7a91dfe780698e1fe05fe6b61d2" # Drošības atslēga sesijām

bcrypt = Bcrypt(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

DATABASE = "job_logs.db"
ENCRYPTION_KEY_FILE = "encryption_key.key"

if not os.path.exists(ENCRYPTION_KEY_FILE):
    encryption_key = Fernet.generate_key()
    with open(ENCRYPTION_KEY_FILE, "wb") as key_file:
        key_file.write(encryption_key)
else:
    with open(ENCRYPTION_KEY_FILE, "rb") as key_file:
        encryption_key = key_file.read()

cipher_suite = Fernet(encryption_key)

class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

@login_manager.user_loader
def load_user(user_id):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, username FROM users WHERE id=?", (user_id,))
    user_data = cursor.fetchone()
    conn.close()
    
    if user_data:
        return User(user_data[0], user_data[1])
    return None

# Datubāzes inicializēšana
def init_db():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            secret_data BLOB
        )
    ''')

    conn.commit()
    conn.close()

init_db()

# Funkcija, lai pieslēgtos datubāzei
def connect_db():
    return sqlite3.connect(DATABASE)

def load_jobs():
    conn = connect_db()
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    df_list = []

    for month in months:
        try:
            query = f"SELECT *, '{month}' AS month FROM {month}"
            df = pd.read_sql(query, conn)

            df.columns = df.columns.str.replace('"', '', regex=True)

            if 'ah_runtime' in df.columns:
                df.rename(columns={
                    'ah_runtime': 'AH_RUNTIME',
                    'ah_ert': 'AH_ERT',
                    'ah_msgnr': 'AH_MSGNR',
                    'ah_stype': 'AH_STYPE',
                    'ah_otype': 'AH_OTYPE'
                }, inplace=True)

            df["month"] = month
            df_list.append(df)

        except Exception as e:
            print(f"Skipping {month}: {e}")

    conn.close()

    return pd.concat(df_list, ignore_index=True) if df_list else pd.DataFrame()


@app.route("/")
@login_required
def dashboard():
    return render_template("dashboard.html", username=current_user.username)

@app.route("/chart-data")
@login_required
def chart_data():
    df = load_jobs()
    if df.empty:
        print("No job data available!")
        return jsonify({"error": "No data available"})

    df["AH_RUNTIME"] = pd.to_numeric(df["AH_RUNTIME"], errors="coerce").fillna(0)
    df["AH_ERT"] = pd.to_numeric(df["AH_ERT"], errors="coerce").fillna(0)
    df["AH_MSGNR"] = pd.to_numeric(df["AH_MSGNR"], errors="coerce").fillna(-1).astype(int)
    df["Job_Status"] = df["AH_MSGNR"].apply(lambda x: "Done" if x == 0 else "Error")

    month_order = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    if "month" not in df.columns:
        print("Error: 'month' column is missing in DataFrame!")
        return jsonify({"error": "Month column is missing in job data."})

    pie_charts = {month: [["Status", "Count"]] for month in month_order}

    for month in month_order:
        if month in df["month"].values:
            month_df = df[df["month"] == month]
            status_counts = month_df["Job_Status"].value_counts().reset_index()
            status_counts.columns = ["Status", "Count"]
            pie_charts[month] += status_counts.values.tolist()

    avg_runtime_per_month = df.groupby("month")["AH_RUNTIME"].mean().reindex(month_order, fill_value=0).reset_index()
    avg_ert_per_month = df.groupby("month")["AH_ERT"].mean().reindex(month_order, fill_value=0).reset_index()

    execution_time_data = [["Month", "Execution Time", "Estimated Time"]]
    for i in range(len(month_order)):
        execution_time_data.append([
            month_order[i],
            avg_runtime_per_month.iloc[i, 1],
            avg_ert_per_month.iloc[i, 1]
        ])

    return jsonify({
        "pie_charts": pie_charts,
        "execution_time": execution_time_data
    })

@app.route("/job-summary")
@login_required
def job_summary():
    df = load_jobs()

    if df.empty:
        print("No job summary data available!")
        return jsonify({"error": "No data available"})

    df.columns = df.columns.str.upper()

    if "MONTH" not in df.columns:
        df["MONTH"] = df["month"]

    df["AH_STYPE"] = (
        df["AH_STYPE"]
        .fillna("")
        .str.strip()
        .str.strip('"')
        .str.upper()
    )

    df["AH_OTYPE"] = df["AH_OTYPE"].astype(str).str.replace('"', '').str.strip()

    df_filtered = df.loc[~df["AH_STYPE"].isin(["", "JAVA API", "OBJ", "<ONCE>", "<PERIOD>"])]

    summary = df_filtered.groupby(["MONTH", "AH_STYPE", "AH_OTYPE"]).size().reset_index(name="count")

    month_order = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    summary["MONTH"] = pd.Categorical(summary["MONTH"], categories=month_order, ordered=True)
    summary = summary.sort_values(["MONTH", "AH_OTYPE"], ascending=[True, True])

    tables = {month: [["Starter Type", "Job Type", "Total Jobs"]] for month in month_order}
    for month in month_order:
        month_df = summary[summary["MONTH"] == month]
        if not month_df.empty:
            tables[month] += month_df[["AH_STYPE", "AH_OTYPE", "count"]].values.tolist()

    return jsonify({"tables": tables})

# Reģistrācijas lapa
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        secret_data = request.form["secret_data"]

        hashed_password = bcrypt.generate_password_hash(password).decode("utf-8")

        encrypted_data = cipher_suite.encrypt(secret_data.encode())

        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO users (username, password, secret_data) VALUES (?, ?, ?)", 
                           (username, hashed_password, encrypted_data))
            conn.commit()
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            return "Username already exists!"
        finally:
            conn.close()

    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        cursor.execute("SELECT id, password FROM users WHERE username=?", (username,))
        user_data = cursor.fetchone()
        conn.close()

        if user_data and bcrypt.check_password_hash(user_data[1], password):
            user = User(user_data[0], username)
            login_user(user)
            return redirect(url_for("dashboard"))

        return render_template("login.html", error="Invalid credentials!")

    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

if __name__ == "__main__":
    app.run(debug=True)