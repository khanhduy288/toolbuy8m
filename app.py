from flask import Flask, request, jsonify, render_template
import json, os, time, threading, random, requests, zipfile, tempfile, shutil, subprocess
from datetime import datetime
from zipfile import ZipFile
from threading import Lock
from selenium import webdriver
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.edge.service import Service
from selenium.webdriver.edge.options import Options as EdgeOptions

app = Flask(__name__)

# paths / files
driver_path = r"msedgedriver.exe"
ACCOUNTS_FILE = "accounts.json"
WORKFLOW_FILE = "workflow.json"
LOG_FILE = "logs.txt"
running_accounts = {}  # username -> start_time (timestamp)
LOCK_DURATION = 2 * 60  # 2 phút
TELEGRAM_BOT_TOKEN = "8250041358:AAFXomknlgg2-oq9pztHZqaewlFbZPZ2wS4"
TELEGRAM_CHAT_ID = "-1003136584516"


# lock cho ghi log (tránh race condition)
_log_lock = Lock()

# đảm bảo file tồn tại
for file in [ACCOUNTS_FILE, WORKFLOW_FILE]:
    if not os.path.exists(file):
        with open(file, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)

def can_run_account(username):
    """Kiểm tra account có thể chạy không"""
    now = time.time()
    start_time = running_accounts.get(username)
    if start_time and now - start_time < LOCK_DURATION:
        return False
    return True

def mark_running(username):
    running_accounts[username] = time.time()

def proxy_works_http(proxy_raw):
    """Kiểm tra HTTP proxy auth bằng requests"""
    parts = proxy_raw.strip().split(":")
    if len(parts) != 4:
        log_action(f"❌ Proxy HTTP format sai: {proxy_raw}")
        return False
    ip, port, user, pwd = parts
    proxy_url = f"http://{user}:{pwd}@{ip}:{port}"
    proxies = {"http": proxy_url, "https": proxy_url}
    try:
        r = requests.get("https://api.ipify.org", proxies=proxies, timeout=5)
        log_action(f"✅ HTTP proxy sống: {proxy_raw} => IP: {r.text}")
        return True
    except:
        log_action(f"❌ HTTP proxy chết: {proxy_raw}")
        return False



def send_telegram_message(text):
    """Gửi thông báo Telegram."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
        requests.post(url, json=payload, timeout=5)
        log_action(f"📩 Đã gửi Telegram: {text}")
    except Exception as e:
        log_action(f"⚠️ Lỗi gửi Telegram: {e}")

def log_action(message):
    ts = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    line = f"{ts} {message}\n"
    # in ra console
    print(line.strip())
    # ghi file an toàn
    with _log_lock:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
def load_accounts():
    if not os.path.exists(ACCOUNTS_FILE):
        return []
    with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []

def save_accounts(data):
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)



def open_edge_with_http_proxy(url, proxy=None):
    """
    Mở Edge WebDriver dùng HTTP proxy với user:pass thông qua extension tạm.
    proxy = "ip:port:user:pass"
    """
    service = Service(driver_path)
    options = EdgeOptions()
    options.add_argument("--start-maximized")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--disable-blink-features=AutomationControlled")

    temp_dir = None
    ext_path = None

    if proxy:
        parts = proxy.strip().split(":")
        if len(parts) == 4:
            ip, port, user, pwd = parts
            try:
                # Tạo folder tạm
                temp_dir = tempfile.mkdtemp()
                ext_name = f"http_proxy_auth_{int(time.time())}.zip"
                ext_path = os.path.join(temp_dir, ext_name)

                manifest_json = {
                    "version": "1.0.0",
                    "manifest_version": 2,
                    "name": "HTTP Proxy Auth Extension",
                    "permissions": ["proxy", "tabs", "unlimitedStorage", "storage", "<all_urls>", "webRequest", "webRequestBlocking"],
                    "background": {"scripts": ["background.js"]}
                }

                background_js = f"""
                var config = {{
                    mode: "fixed_servers",
                    rules: {{
                        singleProxy: {{
                            scheme: "http",
                            host: "{ip}",
                            port: parseInt({port})
                        }},
                        bypassList: ["localhost"]
                    }}
                }};
                chrome.proxy.settings.set({{value: config, scope: "regular"}}, function(){{}});
                function callbackFn(details) {{
                    return {{
                        authCredentials: {{
                            username: "{user}",
                            password: "{pwd}"
                        }}
                    }};
                }}
                chrome.webRequest.onAuthRequired.addListener(
                    callbackFn,
                    {{urls: ["<all_urls>"]}},
                    ['blocking']
                );
                """

                with ZipFile(ext_path, 'w') as zp:
                    zp.writestr("manifest.json", json.dumps(manifest_json))
                    zp.writestr("background.js", background_js)

                options.add_extension(ext_path)
                log_action(f"🔌 Load HTTP proxy auth extension: {ext_path}")

            except Exception as e:
                log_action(f"❌ Lỗi tạo extension proxy: {e}")
                if temp_dir and os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)

        else:
            log_action(f"❌ Proxy format sai: {proxy}")

    # Khởi tạo EdgeDriver
    try:
        driver = webdriver.Edge(service=service, options=options)
    except Exception as e:
        log_action(f"❌ Không thể khởi tạo Edge WebDriver: {e}")
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        return None

    # CDP tweak chống detect
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                window.navigator.chrome = { runtime: {} };
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4]});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
            """
        })
    except Exception as e:
        log_action(f"⚠️ CDP tweak không chạy: {e}")

    # Mở trang
    try:
        driver.set_page_load_timeout(30)
        driver.get(url)
        log_action(f"🌍 Mở trang: {url}")
    except Exception as e:
        log_action(f"⚠️ Lỗi khi load trang {url}: {e}")

    # Thêm hàm cleanup tạm
    def cleanup_extension():
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
                log_action("🗑️ Đã cleanup extension tạm")
            except Exception as e:
                log_action(f"⚠️ Lỗi cleanup extension tạm: {e}")

    driver.cleanup_extension = cleanup_extension

    return driver

# ---------- helper: human typing ----------
def human_type(driver, element, text, min_delay=0.06, max_delay=0.18):
    """
    Gõ từng ký tự với delay ngẫu nhiên để giống người thật.
    Sau khi gõ xong, phát event 'input' để JS trên trang nhận.
    """
    try:
        element.click()
    except Exception:
        pass

    for ch in text:
        element.send_keys(ch)
        time.sleep(random.uniform(min_delay, max_delay))

    try:
        driver.execute_script(
            "var el = arguments[0]; el.dispatchEvent(new Event('input', {bubbles: true}));",
            element,
        )
    except Exception:
        pass


def substitute_vars(text, acc):
    if not isinstance(text, str):
        return text
    for k, v in acc.items():
        text = text.replace(f"{{{{selected_account.{k}}}}}", str(v))
    return text


def load_workflow():
    try:
        with open(WORKFLOW_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log_action(f"❌ Không thể đọc workflow.json: {e}")
        return []


def run_workflow_for_account(acc, workflow_override=None):
    """Chạy workflow cho 1 tài khoản (dùng chung cho cả workflow và tracking)"""
    if workflow_override is not None:
        workflow = workflow_override
    else:
        workflow = load_workflow()

    if not workflow:
        log_action("❌ Không tìm thấy workflow.json hoặc rỗng!")
        return

    username = acc.get("username")
    log_action(f"\n--- 🔸 Bắt đầu xử lý tài khoản {username} ---")

    # Kiểm tra proxy
    proxy_val = acc.get("proxy", "").strip() if acc.get("proxy") else None
    proxy = None
    if proxy_val:
        if proxy_works_http(proxy_val):
            proxy = proxy_val
            log_action(f"🌐 Sử dụng proxy HTTP/HTTPS: {proxy}")
        else:
            log_action(f"⚠️ Proxy {proxy_val} không khả dụng → fallback dùng IP thật của máy.")
            proxy = None
    else:
        log_action("ℹ️ Không có proxy trong account → dùng IP thật mặc định.")

    driver = None

    try:
        # Hàm tiện ích click bằng WebDriverWait
        def wait_and_click(xpath, timeout=8):
            try:
                el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((By.XPATH, xpath)))
                driver.execute_script("arguments[0].scrollIntoView({behavior:'auto',block:'center'});", el)
                el.click()
                return True
            except Exception as e:
                log_action(f"⚠️ Click element {xpath} thất bại: {e}")
                return False

        # Hàm tiện ích điền input bằng WebDriverWait
        def wait_and_fill(xpath, value, prefer_send_keys=False):
            try:
                el = WebDriverWait(driver, 8).until(EC.visibility_of_element_located((By.XPATH, xpath)))
                if prefer_send_keys:
                    try:
                        el.clear()
                        human_type(driver, el, str(value), min_delay=0.03, max_delay=0.09)
                        log_action(f"✅ (send_keys) Điền {xpath}: {value}")
                        return True
                    except Exception as e:
                        log_action(f"⚠️ send_keys thất bại cho {xpath}: {e} — fallback JS setter")
                # fallback JS
                driver.execute_script("""
                    var el = arguments[0], val = arguments[1];
                    var nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    if(nativeSetter) nativeSetter.call(el, val); else el.value = val;
                    el.dispatchEvent(new Event('input', {bubbles:true}));
                    el.dispatchEvent(new Event('change', {bubbles:true}));
                """, el, str(value))
                log_action(f"✅ (JS) Điền {xpath}: {value}")
                return True
            except Exception as e:
                log_action(f"❌ Điền {xpath} thất bại: {e}")
                return False

        for step in workflow:
            action = step.get("action")
            desc = substitute_vars(step.get("desc", ""), acc)
            log_action(f"🔹 {desc}")

            if action == "open_url":
                url = substitute_vars(step.get("url", ""), acc)
                driver = open_edge_with_http_proxy(url, proxy if proxy else None)
                time.sleep(random.uniform(0.8, 1.4))

            elif action == "click_dom":
                selector = substitute_vars(step.get("selector", ""), acc)
                log_action(f"🖱️ Click DOM: {selector}")
                if driver:
                    wait_and_click(selector)
                    time.sleep(random.uniform(0.8, 1.6))
                else:
                    log_action("⚠️ Không có phiên Edge nào đang chạy để click.")

            elif action == "sleep":
                seconds = step.get("seconds", 1)
                log_action(f"⏳ Chờ {seconds} giây...")
                time.sleep(seconds)

            elif action == "fill_login_form":
                if not driver:
                    log_action("⚠️ Không có driver để nhập form.")
                    continue

                user_selector = substitute_vars(step.get("user_selector", ""), acc)
                pass_selector = substitute_vars(step.get("pass_selector", ""), acc)
                submit_selector = substitute_vars(step.get("submit_selector", ""), acc)

                log_action("✏️ Nhập nhanh (fast) username/password bằng JS native setter, không gõ thủ công.")
                try:
                    wait_and_fill(user_selector, acc.get("username", ""))
                    wait_and_fill(pass_selector, acc.get("password", ""))
                    wait_and_click(submit_selector)
                    time.sleep(2)

                    # Check success heuristics
                    success = False
                    possible_success_xpaths = [
                        "//button[contains(., 'Đăng xuất')]",
                        "//a[contains(., 'ログアウト')]",
                        "//div[contains(text(),'Xin chào')]",
                        "//img[contains(@class,'avatar')]",
                    ]
                    for sx in possible_success_xpaths:
                        if driver.find_elements(By.XPATH, sx):
                            log_action(f"✅ Phát hiện phần tử xác nhận login: {sx}")
                            success = True
                            break

                    if not success:
                        curr = driver.current_url
                        try:
                            WebDriverWait(driver, 4).until(lambda d: d.current_url != curr)
                            log_action(f"✅ URL đổi sau submit -> {driver.current_url}")
                            success = True
                        except:
                            log_action("🔍 URL không đổi (fast check).")

                    if not success:
                        screenshot_path = f"debug_fast_login_{username}_{int(time.time())}.png"
                        driver.save_screenshot(screenshot_path)
                        log_action(f"🖼️ Lưu screenshot debug: {screenshot_path}")
                        log_action("❌ Fast login không xác nhận thành công — có thể site cần event tương tác 'thật' hoặc có anti-bot/captcha.")
                    else:
                        log_action("🎉 Fast login thành công (theo heuristics).")
                except Exception as e:
                    log_action(f"❌ Lỗi khi thực hiện fast fill_login_form: {e}")

            elif action == "wait_until_time":
                target_hour = step.get("hour", 0)
                target_minute = step.get("minute", 0)
                log_action(f"⏳ Bắt đầu đợi đến {target_hour:02d}:{target_minute:02d} JST trước khi tiếp tục...")
                while True:
                    now = datetime.utcnow()
                    jst_hour = (now.hour + 9) % 24
                    jst_minute = now.minute
                    if (jst_hour > target_hour) or (jst_hour == target_hour and jst_minute >= target_minute):
                        log_action(f"✅ Đã đến giờ {target_hour:02d}:{target_minute:02d} JST, tiếp tục workflow.")
                        break
                    time.sleep(1)
                if driver:
                    try:
                        driver.refresh()
                        log_action("🔄 Đã refresh trang sau khi đợi giờ JST.")
                        time.sleep(1)
                    except Exception as e:
                        log_action(f"⚠️ Không refresh được trang: {e}")

            elif action == "click_image":
                img = substitute_vars(step.get("image", ""), acc)
                log_action(f"🖱️ (Mô phỏng click) Ảnh: {img}")
                time.sleep(1)

            elif action == "fill_form":
                fields = step.get("fields", {})
                for k, v in fields.items():
                    val = substitute_vars(v, acc)
                    log_action(f"✏️ Điền {k}: {val}")
                    time.sleep(0.5)

            elif action == "fill_payment_form":
                if not driver:
                    log_action("⚠️ Không có driver để nhập form thanh toán.")
                    continue
                selectors = step.get("selectors", {}) or {}
                is_new = acc.get("is_new", False)
                try:
                    log_action(f"💳 Bắt đầu điền thông tin thanh toán (is_new={is_new})")

                    def safe_find(xpath, timeout=6):
                        try:
                            return WebDriverWait(driver, timeout).until(
                                EC.presence_of_element_located((By.XPATH, xpath))
                            )
                        except Exception:
                            return None

                    def fill_input(selector_key, value, prefer_send_keys=False):
                        sel = selectors.get(selector_key)
                        if not sel or value in (None, ""):
                            log_action(f"⚠️ Bỏ qua {selector_key} (thiếu selector hoặc value trống)")
                            return False
                        try:
                            try:
                                el = WebDriverWait(driver, 8).until(
                                    EC.visibility_of_element_located((By.XPATH, sel))
                                )
                            except Exception:
                                el = safe_find(sel, timeout=4)

                            if not el:
                                iframes = driver.find_elements(By.TAG_NAME, "iframe")
                                found = False
                                for fr in iframes:
                                    try:
                                        driver.switch_to.frame(fr)
                                        found_el = driver.find_elements(By.XPATH, sel)
                                        if found_el:
                                            el = found_el[0]
                                            found = True
                                            log_action("ℹ️ Tìm thấy element trong iframe, đã switch vào iframe.")
                                            break
                                        driver.switch_to.default_content()
                                    except Exception:
                                        driver.switch_to.default_content()
                                if not found:
                                    log_action(f"❌ Không tìm thấy element cho {selector_key} bằng xpath: {sel}")
                                    return False
                            try:
                                driver.execute_script(
                                    "arguments[0].scrollIntoView({behavior:'auto',block:'center'});", el
                                )
                            except Exception:
                                pass

                            if prefer_send_keys:
                                try:
                                    el.clear()
                                except:
                                    pass
                                try:
                                    human_type(driver, el, str(value), min_delay=0.03, max_delay=0.09)
                                    log_action(f"✅ (send_keys) Điền {selector_key}: {value}")
                                    time.sleep(0.3)
                                    driver.switch_to.default_content()
                                    return True
                                except Exception as e:
                                    log_action(f"⚠️ send_keys thất bại cho {selector_key}: {e} — fallback JS setter")
                            try:
                                driver.execute_script("""
                                    var el = arguments[0], val = arguments[1];
                                    var nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                                    if(nativeSetter) nativeSetter.call(el, val); else el.value = val;
                                    el.dispatchEvent(new Event('input', {bubbles:true}));
                                    el.dispatchEvent(new Event('change', {bubbles:true}));
                                """, el, str(value))
                                log_action(f"✅ (JS) Điền {selector_key}: {value}")
                                time.sleep(0.3)
                                driver.switch_to.default_content()
                                return True
                            except Exception as e:
                                log_action(f"❌ JS setter thất bại cho {selector_key}: {e}")
                                try:
                                    driver.switch_to.default_content()
                                except:
                                    pass
                                return False

                        except Exception as e:
                            log_action(f"❌ Lỗi khi điền {selector_key}: {e}")
                            try:
                                driver.switch_to.default_content()
                            except:
                                pass
                            return False

                    def fill_select(selector_key, value):
                        sel = selectors.get(selector_key)
                        if not sel or value in (None, ""):
                            log_action(f"⚠️ Bỏ qua {selector_key} (thiếu selector hoặc value trống)")
                            return False
                        try:
                            WebDriverWait(driver, 8).until(
                                EC.visibility_of_element_located((By.XPATH, sel))
                            )
                            el = driver.find_element(By.XPATH, sel)
                            driver.execute_script("""
                                var sel = arguments[0], val = arguments[1];
                                var option = Array.from(sel.options).find(o => o.value === val || o.text === val);
                                if(option) sel.value = option.value;
                                sel.dispatchEvent(new Event('change', {bubbles:true}));
                            """, el, str(value))
                            log_action(f"✅ Chọn {selector_key}: {value}")
                            time.sleep(0.3)
                            return True
                        except Exception as e:
                            log_action(f"❌ Lỗi khi chọn {selector_key}: {e}")
                            return False

                    # --- Điền thẻ mới hoặc CVV ---
                    if is_new:
                        fill_input("card_number", acc.get("card_number"), prefer_send_keys=True)
                        fill_select("card_exp_month", acc.get("card_exp_month"))
                        fill_select("card_exp_year", acc.get("card_exp_year"))
                        log_action("🎉 Hoàn tất nhập thông tin thẻ mới.")
                        # Click radio paymentTypeCode (cứng fallback)
                        radio_selector = selectors.get("payment_radio") or "//input[@id='a03']"
                        try:
                            radio = WebDriverWait(driver, 8).until(
                                EC.element_to_be_clickable((By.XPATH, radio_selector))
                            )
                            driver.execute_script("arguments[0].click();", radio)
                            log_action("✅ Đã click radio paymentTypeCode bằng JS.")
                            time.sleep(0.5)
                        except Exception as e:
                            log_action(f"⚠️ Không click được radio paymentTypeCode: {e}")

                        # Click nút Kế tiếp (fallback cứng)
                        next_btn_selector = selectors.get("next_button") or "/html/body/div[1]/div/div[2]/form/div[2]/div[1]/div[1]/div[2]/ul/li/div/a"
                        clicked = False
                        for attempt in range(3):
                            try:
                                next_btn = WebDriverWait(driver, 8).until(
                                    EC.element_to_be_clickable((By.XPATH, next_btn_selector))
                                )
                                driver.execute_script(
                                    "arguments[0].scrollIntoView({behavior:'auto',block:'center'});", next_btn
                                )
                                driver.execute_script("arguments[0].click();", next_btn)
                                log_action(f"✅ Đã click nút Kế tiếp (attempt {attempt+1})")
                                clicked = True
                                time.sleep(1)
                                break
                            except Exception as e:
                                log_action(f"⚠️ Click Kế tiếp attempt {attempt+1} thất bại: {e}")
                                time.sleep(0.6)
                        if not clicked:
                            log_action("❌ Không click được nút Kế tiếp sau 3 lần thử.")
                    else:
                        # Điền CVV
                        cvv_filled = False
                        if fill_input("card_cvv", acc.get("card_cvv"), prefer_send_keys=True):
                            cvv_filled = True
                        else:
                            try:
                                els = driver.find_elements(By.NAME, "creditCard.securityCode")
                                if els:
                                    el = els[0]
                                    human_type(driver, el, str(acc.get("card_cvv", "")), min_delay=0.03, max_delay=0.09)
                                    log_action("✅ Điền CVV bằng selector name=creditCard.securityCode")
                                    cvv_filled = True
                                else:
                                    els2 = driver.find_elements(By.CSS_SELECTOR, "input.js_c_securityCode")
                                    if els2:
                                        el = els2[0]
                                        human_type(driver, el, str(acc.get("card_cvv", "")), min_delay=0.03, max_delay=0.09)
                                        log_action("✅ Điền CVV bằng class js_c_securityCode")
                                        cvv_filled = True
                            except Exception as e:
                                log_action(f"⚠️ Fallback điền CVV lỗi: {e}")

                        if not cvv_filled:
                            log_action("❌ Không thể điền CVV — có thể element nằm trong iframe hoặc selector sai.")
                        else:
                            log_action("🎉 Hoàn tất nhập CVV.")

                        # Click nút Thanh toán (fallback cứng)
                        pay_btn_selector = "/html/body/div[1]/div/div[2]/form/div[2]/div/table/tbody/tr/td[2]/div[1]/div[1]/div/a"
                        clicked = False
                        for attempt in range(4):
                            try:
                                pay_btn = WebDriverWait(driver, 8).until(
                                    EC.element_to_be_clickable((By.XPATH, pay_btn_selector))
                                )
                                driver.execute_script(
                                    "arguments[0].scrollIntoView({behavior:'auto',block:'center'});", pay_btn
                                )
                                driver.execute_script("arguments[0].click();", pay_btn)
                                log_action(f"✅ Đã click nút Thanh toán (attempt {attempt+1})")
                                clicked = True
                                time.sleep(1)
                                break
                            except Exception as e:
                                log_action(f"⚠️ Click Thanh toán attempt {attempt+1} thất bại: {e}")
                                time.sleep(0.6)
                        if not clicked:
                            log_action("❌ Không click được nút Thanh toán sau 4 lần thử.")

                except Exception as e:
                    log_action(f"❌ Lỗi khi thực hiện fill_payment_form: {e}")


            else:
                log_action(f"⚠️ Action chưa được hỗ trợ: {action}")

        log_action(f"✅ Hoàn tất tài khoản {username}")
        send_telegram_message(f"✅ Mua hàng thành công cho tài khoản <b>{acc.get('username')}</b>")

    except Exception as e:
        log_action(f"❌ Lỗi khi xử lý {username}: {e}")
    finally:
        if driver:
            try:
                if hasattr(driver, "cleanup_extension"):
                    driver.cleanup_extension()
                driver.quit()
            except:
                pass

# =========================
# ROUTES (phần còn lại giữ nguyên)
# =========================
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/accounts")
def get_accounts():
    if os.path.exists(ACCOUNTS_FILE):
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    return jsonify([])

@app.route("/save_one", methods=["POST"])
def save_one():
    try:
        incoming = request.get_json(silent=True)  # trả None nếu không phải JSON
        print("📩 /save_one payload:", incoming)  # debug
        if not incoming:
            return jsonify({"result": "❌ Không nhận được JSON. Hãy gửi Content-Type: application/json"}), 400
        email = incoming.get("email") or incoming.get("username")
        password = incoming.get("password") or incoming.get("pass") or incoming.get("pwd")
        if not email or not password:
            return jsonify({"result": "❌ Thiếu thông tin tài khoản! Cần 'email/username' và 'password'."}), 400
        accounts = load_accounts()
        existing = next((acc for acc in accounts if acc.get("username") == email or acc.get("email") == email), None)
        if existing:
            existing.update(incoming)
            msg = f"🔄 Đã cập nhật tài khoản: {email}"
        else:
            if "id" not in incoming:
                incoming["id"] = int(time.time() * 1000)
            if "username" not in incoming and "email" in incoming:
                incoming["username"] = incoming["email"]
            accounts.append(incoming)
            msg = f"🆕 Đã thêm tài khoản mới: {email}"

        save_accounts(accounts)
        return jsonify({"result": "✅ Thành công", "message": msg})
    except Exception as e:
        log_action(f"❌ Lỗi /save_one: {e}")
        return jsonify({"result": "❌ Lỗi server", "error": str(e)}), 500

@app.route("/save", methods=["POST"])
def save_all():
    data = request.get_json()
    accounts = data.get("accounts", [])
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump(accounts, f, ensure_ascii=False, indent=2)
    log_action(f"Lưu {len(accounts)} tài khoản.")
    return jsonify({"message": f"✅ Đã lưu {len(accounts)} tài khoản!"})

@app.route("/delete_account/<int:acc_id>", methods=["DELETE"])
def delete_account(acc_id):
    accounts = []
    if os.path.exists(ACCOUNTS_FILE):
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            accounts = json.load(f)
    new_accounts = [a for a in accounts if a.get("id") != acc_id]
    if len(new_accounts) == len(accounts):
        log_action(f"⚠️ Không tìm thấy tài khoản có id={acc_id} để xóa.")
        return jsonify({"message": f"❌ Không tìm thấy tài khoản có id={acc_id}!"}), 404
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump(new_accounts, f, ensure_ascii=False, indent=2)
    log_action(f"🗑️ Đã xóa tài khoản id={acc_id}")
    return jsonify({"message": f"✅ Đã xóa tài khoản id={acc_id}!"})

@app.route("/logs")
def get_logs():
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            content = f.read()
    else:
        content = "⚠️ Chưa có log nào!"
    return jsonify({"logs": content})

@app.route("/start", methods=["POST"])
def start_workflow():
    data = request.get_json()
    selected_accounts = data.get("accounts", [])
    if not selected_accounts:
        return jsonify({"result": "❌ Không có tài khoản nào được chọn!"})

    # Lọc account chưa chạy hoặc đã hết 2 phút
    accounts_to_run = []
    for acc in selected_accounts:
        username = acc.get("username")
        if username and can_run_account(username):
            accounts_to_run.append(acc)
            mark_running(username)

    if not accounts_to_run:
        return jsonify({"result": "⚠️ Tất cả các tài khoản đang chạy hoặc bị lock 2 phút."})

    log_action(f"▶️ Bắt đầu khởi tạo {len(accounts_to_run)} threads (mỗi thread start cách nhau 3s)...")
    for idx, acc in enumerate(accounts_to_run):
        t = threading.Thread(target=run_workflow_for_account, args=(acc,), daemon=True)
        t.start()
        log_action(f"🟢 Đã start thread cho {acc.get('username')} (idx {idx})")
        if idx < len(accounts_to_run) - 1:
            time.sleep(3)
    return jsonify({"result": f"🚀 Đã bắt đầu {len(accounts_to_run)} tài khoản (staggered 3s)!"})

@app.route("/run_tracking", methods=["POST"])
def run_tracking():
    try:
        # cố gắng parse JSON (trả 400 nếu không có JSON)
        data = request.get_json(silent=True)
        if data is None:
            log_action("❌ /run_tracking nhận request nhưng không phải JSON hoặc thiếu Content-Type: application/json")
            return jsonify({"result": "❌ Thiếu hoặc không hợp lệ JSON. Hãy gửi Content-Type: application/json"}), 400

        # Hỗ trợ 2 dạng payload:
        # 1) {"accounts": [ ... ]}  OR  2) [ ... ]
        if isinstance(data, dict) and "accounts" in data:
            selected_accounts = data.get("accounts", [])
        elif isinstance(data, list):
            selected_accounts = data
        else:
            # có thể người dùng gửi {"selected_accounts": [...]}
            if isinstance(data, dict) and "selected_accounts" in data:
                selected_accounts = data.get("selected_accounts", [])
            else:
                selected_accounts = []

        # debug: log payload ngắn gọn
        log_action(f"ℹ️ /run_tracking payload received (count={len(selected_accounts)}). Preview: {str(selected_accounts)[:800]}")

        if not selected_accounts:
            return jsonify({"result": "⚠️ Không có tài khoản nào được chọn"}), 400

        # đọc tracking.json
        tracking_path = os.path.join(os.getcwd(), "tracking.json")
        if not os.path.exists(tracking_path):
            log_action("❌ Không tìm thấy tracking.json")
            return jsonify({"result": "⚠️ Không tìm thấy file tracking.json"}), 400

        with open(tracking_path, "r", encoding="utf-8") as f:
            tracking_data = json.load(f)

        log_action(f"🚀 Bắt đầu tracking cho {len(selected_accounts)} tài khoản...")

        # start thread cho từng account (giữ hành vi staggered 3s như /start)
        for idx, acc in enumerate(selected_accounts):
            # đảm bảo acc là dict
            if not isinstance(acc, dict):
                log_action(f"⚠️ Bỏ qua entry không hợp lệ tại index {idx}: {acc}")
                continue

            t = threading.Thread(
                target=run_workflow_for_account,
                args=(acc, tracking_data),
                daemon=True
            )
            t.start()
            log_action(f"🧵 Đã start thread tracking cho {acc.get('username', 'NoName')} (idx {idx})")
            if idx < len(selected_accounts) - 1:
                time.sleep(3)

        return jsonify({"result": f"✅ Đã bắt đầu tracking cho {len(selected_accounts)} tài khoản (staggered 3s)"}), 200

    except Exception as e:
        log_action(f"❌ Lỗi khi chạy /run_tracking: {e}")
        return jsonify({"result": f"❌ Lỗi server khi chạy tracking: {e}"}), 500

if __name__ == "__main__":
    app.run(debug=True, port=5000)
