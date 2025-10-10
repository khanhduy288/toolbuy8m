from flask import Flask, request, jsonify, render_template
import json, os, time, subprocess
from datetime import datetime

app = Flask(__name__)

ACCOUNTS_FILE = "accounts.json"
WORKFLOW_FILE = "workflow.json"
LOG_FILE = "logs.txt"

# --- đảm bảo file tồn tại ---
for file in [ACCOUNTS_FILE, WORKFLOW_FILE]:
    if not os.path.exists(file):
        with open(file, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)

# --- Hàm mở Edge riêng ---
def open_edge_window(url):
    edge_path = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    try:
        subprocess.Popen([edge_path, "--new-window", url])
        log_action(f"🌐 Đã mở cửa sổ Edge mới: {url}")
    except Exception as e:
        log_action(f"⚠️ Lỗi mở Edge: {e}")

# --- helpers ---
def load_accounts():
    with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    normalized = []
    for a in data:
        acc = {
            "id": a.get("id", int(time.time() * 1000)),
            "username": a.get("username") or a.get("name", ""),
            "password": a.get("password", ""),
            "proxy": a.get("proxy", ""),
            "url": a.get("url", ""),
            "quantity": a.get("quantity", 1),
            "is_new": a.get("is_new", a.get("new_account", False)),
            "card_number": a.get("card_number", ""),
            "exp_month": a.get("card_exp_month", a.get("exp_month", "")),
            "exp_year": a.get("card_exp_year", a.get("exp_year", "")),
            "card_cvv": a.get("card_cvv", "")
        }
        normalized.append(acc)
    return normalized

def load_workflow():
    try:
        with open(WORKFLOW_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log_action(f"❌ Không thể đọc workflow.json: {e}")
        return []

def save_accounts(data):
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def log_action(message):
    ts = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    line = f"{ts} {message}\n"
    print(line.strip())
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line)

def substitute_vars(text, acc):
    """Thay thế {{selected_account.xxx}} trong workflow"""
    if not isinstance(text, str):
        return text
    for k, v in acc.items():
        text = text.replace(f"{{{{selected_account.{k}}}}}", str(v))
    return text

# --- routes ---
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/accounts")
def get_accounts():
    return jsonify(load_accounts())

@app.route("/save", methods=["POST"])
def save_all():
    data = request.get_json()
    accounts = data.get("accounts", [])
    save_accounts(accounts)
    log_action(f"Lưu {len(accounts)} tài khoản.")
    return jsonify({"message": f"✅ Đã lưu {len(accounts)} tài khoản!"})

@app.route("/save_one", methods=["POST"])
def save_one():
    acc = request.get_json()
    accounts = load_accounts()

    existing = next((a for a in accounts if a.get("username") == acc.get("username")), None)
    if existing:
        existing.update(acc)
    else:
        acc["id"] = int(time.time() * 1000)
        accounts.append(acc)

    save_accounts(accounts)
    log_action(f"💾 Lưu tài khoản {acc.get('username')}")
    return jsonify({"message": f"✅ Đã lưu tài khoản {acc.get('username')}!"})


@app.route("/start", methods=["POST"])
def start_workflow():
    data = request.get_json()
    selected_accounts = data.get("accounts", [])
    if not selected_accounts:
        return jsonify({"result": "❌ Không có tài khoản nào được chọn!"})

    workflow = load_workflow()
    if not workflow:
        return jsonify({"result": "❌ Không tìm thấy workflow.json hoặc rỗng!"})

    log_action(f"▶️ Bắt đầu chạy workflow cho {len(selected_accounts)} tài khoản...")

    for acc in selected_accounts:
        username = acc.get("username")
        log_action(f"\n--- 🔸 Xử lý tài khoản {username} ---")

        for step in workflow:
            action = step.get("action")
            desc = substitute_vars(step.get("desc", ""), acc)
            log_action(f"🔹 {desc}")

            if action == "open_url":
                url = substitute_vars(step.get("url", ""), acc)
                open_edge_window(url)

            elif action == "sleep":
                seconds = step.get("seconds", 1)
                log_action(f"⏳ Chờ {seconds} giây...")
                time.sleep(seconds)

            elif action == "click_image":
                img = substitute_vars(step.get("image", ""), acc)
                log_action(f"🖱️ (Mô phỏng click) Ảnh: {img}")

            elif action == "fill_form":
                fields = step.get("fields", {})
                for k, v in fields.items():
                    val = substitute_vars(v, acc)
                    log_action(f"✏️ Điền {k}: {val}")

            else:
                log_action(f"⚠️ Action chưa được hỗ trợ: {action}")

        log_action(f"✅ Hoàn tất tài khoản {username}")

    log_action("🎉 Workflow hoàn tất!")
    return jsonify({"result": "✅ Workflow chạy thành công!"})

@app.route("/logs")
def get_logs():
    if not os.path.exists(LOG_FILE):
        return jsonify([])
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    return jsonify(lines[-200:])

if __name__ == "__main__":
    app.run(debug=True, port=5000)
