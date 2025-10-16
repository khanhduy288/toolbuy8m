from flask import Flask, request, jsonify, render_template
import json, os, time, threading, random, requests
from selenium.webdriver.common.keys import Keys
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.edge.service import Service
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from threading import Lock

app = Flask(__name__)

# paths / files
driver_path = r"msedgedriver.exe"
ACCOUNTS_FILE = "accounts.json"
WORKFLOW_FILE = "workflow.json"
LOG_FILE = "logs.txt"

# lock cho ghi log (tránh race condition)
_log_lock = Lock()

# đảm bảo file tồn tại
for file in [ACCOUNTS_FILE, WORKFLOW_FILE]:
    if not os.path.exists(file):
        with open(file, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)


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

# ---------- proxy detection ----------
def proxy_works(proxy):
    """
    Thử detect proxy: trả về 'http' nếu HTTP CONNECT hoạt động,
    trả về 'socks5' nếu SOCKS5 hoạt động,
    trả về None nếu cả 2 đều fail.
    """
    proxy = proxy.strip()
    # thử HTTP CONNECT
    try:
        proxies = {"http": f"http://{proxy}", "https": f"http://{proxy}"}
        r = requests.get("https://httpbin.org/ip", proxies=proxies, timeout=6)
        if r.status_code == 200:
            ip = r.json().get("origin") if r.headers.get("Content-Type", "").startswith("application/json") else None
            log_action(f"🟢 Proxy {proxy} hoạt động (HTTP CONNECT).")
            return "http"
        else:
            log_action(f"🔴 Proxy {proxy} (HTTP) trả mã {r.status_code}")
    except Exception as e:
        log_action(f"🔴 Proxy {proxy} (HTTP) lỗi: {e}")

    # thử SOCKS5
    try:
        proxies = {"http": f"socks5h://{proxy}", "https": f"socks5h://{proxy}"}
        r = requests.get("https://httpbin.org/ip", proxies=proxies, timeout=6)
        if r.status_code == 200:
            ip = r.json().get("origin") if r.headers.get("Content-Type", "").startswith("application/json") else None
            log_action(f"🟢 Proxy {proxy} hoạt động (SOCKS5).")
            return "socks5"
        else:
            log_action(f"🔴 Proxy {proxy} (SOCKS5) trả mã {r.status_code}")
    except Exception as e:
        log_action(f"🔴 Proxy {proxy} (SOCKS5) lỗi: {e}")

    log_action(f"❌ Proxy {proxy} không khả dụng (HTTP/SOCKS5).")
    return None


def open_edge_window_new_instance(url, proxy=None):
    """
    Mở Edge WebDriver; nếu proxy có thì detect scheme (http | socks5)
    và cấu hình đúng --proxy-server. Sau khi mở, kiểm tra nhanh bằng httpbin.
    Nếu proxy không cho driver ra ngoài, fallback mở driver không proxy.
    """
    def _build_driver(proxy_arg=None):
        service = Service(driver_path)
        options = EdgeOptions()
        options.add_argument("--start-maximized")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        options.add_argument("--disable-blink-features=AutomationControlled")
        if proxy_arg:
            options.add_argument(f"--proxy-server={proxy_arg}")
            log_action(f"🌐 Cấu hình proxy cho driver: {proxy_arg}")
        try:
            driver = webdriver.Edge(service=service, options=options)
        except Exception as e:
            log_action(f"❌ Không thể khởi tạo Edge WebDriver: {e}")
            return None

        # CDP tweak giảm khả năng detect
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

        return driver

    try:
        proxy_arg = None
        detected = None
        if proxy:
            detected = proxy_works(proxy)
            if detected == "http":
                proxy_arg = f"http://{proxy}"
            elif detected == "socks5":
                proxy_arg = f"socks5://{proxy}"
            else:
                # không detect được -> log và sẽ mở driver không proxy
                log_action(f"⚠️ Không xác định được scheme proxy {proxy}; sẽ mở driver không proxy.")
                proxy_arg = None

        # 1) Mở driver (với proxy_arg nếu có)
        driver = _build_driver(proxy_arg)
        if not driver:
            return None

        driver.set_page_load_timeout(20)

        # 2) Mở target trang
        try:
            driver.get(url)
            log_action(f"🌍 Mở trang: {url}")
        except Exception as e:
            log_action(f"⚠️ Lỗi khi load target {url}: {e}")

        # 3) Nếu dùng proxy -> kiểm tra driver thực sự ra ngoài bằng httpbin
        if proxy_arg:
            try:
                driver.get("https://httpbin.org/ip")
                time.sleep(1)
                ps = driver.page_source.lower()
                if "origin" in ps or "origin" in driver.find_element(By.TAG_NAME, "body").text.lower():
                    log_action("✅ Driver thông báo đã ra ngoài (httpbin check).")
                else:
                    log_action("⚠️ httpbin không trả origin trong page_source -> proxy có thể không hoạt động cho driver.")
                    # fallback: restart without proxy
                    try:
                        fname = f"proxy_fail_{proxy.replace(':','_')}_{int(time.time())}.png"
                        driver.save_screenshot(fname)
                        log_action(f"🖼️ Đã lưu screenshot lỗi: {fname}")
                    except Exception:
                        pass
                    try:
                        driver.quit()
                    except Exception:
                        pass
                    log_action("🔁 Fallback: mở lại driver KHÔNG dùng proxy.")
                    driver = _build_driver(None)
                    if driver:
                        try:
                            driver.get(url)
                        except Exception as e:
                            log_action(f"⚠️ Lỗi khi mở lại trang sau fallback: {e}")
            except Exception as e:
                log_action(f"⚠️ Lỗi khi kiểm tra httpbin bằng driver: {e}")
                # fallback to no-proxy
                try:
                    driver.quit()
                except:
                    pass
                log_action("🔁 Fallback: mở lại driver không dùng proxy.")
                driver = _build_driver(None)
                if driver:
                    try:
                        driver.get(url)
                    except Exception as e:
                        log_action(f"⚠️ Lỗi khi mở trang sau fallback: {e}")

        return driver

    except Exception as e:
        log_action(f"❌ Lỗi mở Edge WebDriver: {e}")
        return None


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

    # dispatch input event để chắc chắn JS bắt được change
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


def run_workflow_for_account(acc):
    """Chạy workflow cho 1 tài khoản"""
    workflow = load_workflow()
    if not workflow:
        log_action("❌ Không tìm thấy workflow.json hoặc rỗng!")
        return

    username = acc.get("username")
    log_action(f"\n--- 🔸 Bắt đầu xử lý tài khoản {username} ---")

    # ✅ Kiểm tra proxy trước khi chạy (có fallback IP thật)
    proxy_val = acc.get("proxy", "").strip() if acc.get("proxy") else None
    proxy = None
    if proxy_val:
        scheme = proxy_works(proxy_val)
        if scheme:
            proxy = f"{scheme}://{proxy_val}"
            log_action(f"🌐 Sử dụng proxy: {proxy}")
        else:
            log_action(f"⚠️ Proxy {proxy_val} không khả dụng → fallback dùng IP thật của máy.")
            proxy = None
    else:
        log_action("ℹ️ Không có proxy trong account → dùng IP thật mặc định.")

    driver = None
    try:
        for step in workflow:
            action = step.get("action")
            desc = substitute_vars(step.get("desc", ""), acc)
            log_action(f"🔹 {desc}")

            if action == "open_url":
                url = substitute_vars(step.get("url", ""), acc)
                # pass proxy WITHOUT scheme to open_edge_window_new_instance so it can re-detect if needed,
                # but here we pass proxy_val (raw ip:port) if we had one, else None
                driver = open_edge_window_new_instance(url, proxy_val if proxy else None)
                time.sleep(random.uniform(0.8, 1.4))

            elif action == "click_dom":
                selector = substitute_vars(step.get("selector", ""), acc)
                log_action(f"🖱️ Click DOM: {selector}")
                if driver:
                    try:
                        WebDriverWait(driver, 15).until(EC.element_to_be_clickable((By.XPATH, selector)))
                        element = driver.find_element(By.XPATH, selector)
                        driver.execute_script("arguments[0].scrollIntoView({behavior:'smooth',block:'center'});", element)
                        # dùng JS click vì đôi khi element là <a> với JS handler
                        driver.execute_script("arguments[0].click();", element)
                        log_action("✅ Đã click DOM thành công!")
                        time.sleep(random.uniform(0.8, 1.6))
                    except Exception as e:
                        log_action(f"❌ Lỗi khi click_dom: {e}")
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
                    # chờ element hiện
                    WebDriverWait(driver, 12).until(EC.visibility_of_element_located((By.XPATH, user_selector)))
                    WebDriverWait(driver, 12).until(EC.visibility_of_element_located((By.XPATH, pass_selector)))

                    user_input = driver.find_element(By.XPATH, user_selector)
                    pass_input = driver.find_element(By.XPATH, pass_selector)

                    # JS snippet: dùng native setter (tốt với React) rồi dispatch nhiều event nhanh
                    set_and_fire = """
                        var el = arguments[0], val = arguments[1];
                        var nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                        if(nativeSetter) {
                            nativeSetter.call(el, val);
                        } else {
                            el.value = val;
                        }
                        el.dispatchEvent(new Event('input', {bubbles:true}));
                        el.dispatchEvent(new Event('change', {bubbles:true}));
                        try { el.dispatchEvent(new KeyboardEvent('keydown', {bubbles:true})); } catch(e){}
                        try { el.dispatchEvent(new KeyboardEvent('keyup', {bubbles:true})); } catch(e){}
                        try { el.blur(); } catch(e){}
                    """

                    # set username & password quickly
                    driver.execute_script(set_and_fire, user_input, acc.get("username", ""))
                    driver.execute_script(set_and_fire, pass_input, acc.get("password", ""))

                    # nhỏ delay để JS xử lý (nhỏ, vì bạn muốn nhanh)
                    time.sleep(0.5)

                    # click submit bằng JS (thường reliable & nhanh)
                    try:
                        WebDriverWait(driver, 6).until(EC.element_to_be_clickable((By.XPATH, submit_selector)))
                        btn = driver.find_element(By.XPATH, submit_selector)
                        driver.execute_script("arguments[0].click();", btn)
                        log_action("➡️ Đã click nút login bằng JS (fast).")
                    except Exception as e:
                        log_action(f"⚠️ Không click được nút submit: {e}")

                    # chờ ngắn để xem kết quả (vì bạn cần nhanh, giữ ngắn)
                    time.sleep(2)

                    # kiểm tra thành công bằng heuristics: element logout/avatar hoặc URL thay đổi
                    success = False
                    possible_success_xpaths = [
                        "//button[contains(., 'Đăng xuất')]",
                        "//a[contains(., 'ログアウト')]",
                        "//div[contains(text(),'Xin chào')]",
                        "//img[contains(@class,'avatar')]",
                    ]
                    for sx in possible_success_xpaths:
                        try:
                            if driver.find_elements(By.XPATH, sx):
                                log_action(f"✅ Phát hiện phần tử xác nhận login (fast): {sx}")
                                success = True
                                break
                        except:
                            pass

                    if not success:
                        curr = driver.current_url
                        try:
                            WebDriverWait(driver, 4).until(lambda d: d.current_url != curr)
                            log_action(f"✅ URL đổi sau submit -> {driver.current_url}")
                            success = True
                        except:
                            log_action("🔍 URL không đổi (fast check).")

                    if not success:
                        # lưu debug: screenshot + page_source (gần như immediate)
                        try:
                            screenshot_path = f"debug_fast_login_{username}_{int(time.time())}.png"
                            driver.save_screenshot(screenshot_path)
                            log_action(f"🖼️ Lưu screenshot debug: {screenshot_path}")
                        except Exception as e:
                            log_action(f"⚠️ Không thể lưu screenshot debug: {e}")

                        try:
                            ps = driver.page_source
                            snippet = ps[:1600]
                            log_action("🔎 Snippet page_source (fast) 1600 ký tự đầu:")
                            log_action(snippet)
                        except Exception as e:
                            log_action(f"⚠️ Không thể đọc page_source: {e}")

                        log_action("❌ Fast login không xác nhận thành công — có thể site cần event tương tác 'thật' hoặc có anti-bot/captcha.")
                    else:
                        log_action("🎉 Fast login thành công (theo heuristics).")

                except Exception as e:
                    log_action(f"❌ Lỗi khi thực hiện fast fill_login_form: {e}")

            elif action == "click_image":
                img = substitute_vars(step.get("image", ""), acc)
                log_action(f"🖱️ (Mô phỏng click) Ảnh: {img}")
                time.sleep(1)

            elif action == "fill_form":
                fields = step.get("fields", {})
                for k, v in fields.items():
                    val = substitute_vars(v, acc)
                    log_action(f"✏️ Điền {k}: {val}")
                    # nếu cần gõ human-like cho các input, có thể mở rộng sau
                    time.sleep(0.5)

                        # ---- NEW: xử lý điền thông tin thanh toán thông minh ----
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
                            return WebDriverWait(driver, timeout).until(EC.presence_of_element_located((By.XPATH, xpath)))
                        except Exception:
                            return None

                    def fill_input(selector_key, value, prefer_send_keys=False):
                        """Điền input text như card_number hoặc CVV.
                        Nếu prefer_send_keys=True cố gắng send_keys (human_type) trước, sau đó fallback JS setter."""
                        sel = selectors.get(selector_key)
                        if not sel or value in (None, ""):
                            log_action(f"⚠️ Bỏ qua {selector_key} (thiếu selector hoặc value trống)")
                            return False
                        try:
                            # 1) try visibility
                            try:
                                el = WebDriverWait(driver, 8).until(EC.visibility_of_element_located((By.XPATH, sel)))
                            except Exception:
                                el = safe_find(sel, timeout=4)
                            if not el:
                                # có thể element nằm trong iframe — thử dò iframe
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

                            # Nếu element bị che phủ hoặc không interactable, scroll into view
                            try:
                                driver.execute_script("arguments[0].scrollIntoView({behavior:'auto',block:'center'});", el)
                            except Exception:
                                pass

                            # Nếu chỉ thích send_keys (ưu tiên cho CVV)
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

                            # Fallback: JS setter (good for React)
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
                            WebDriverWait(driver, 8).until(EC.visibility_of_element_located((By.XPATH, sel)))
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

                    # ----- thực hiện fill -----
                    if is_new:
                        # 1) số thẻ + exp month/year
                        fill_input("card_number", acc.get("card_number"), prefer_send_keys=True)
                        fill_select("card_exp_month", acc.get("card_exp_month"))
                        fill_select("card_exp_year", acc.get("card_exp_year"))
                        log_action("🎉 Hoàn tất nhập thông tin thẻ mới.")

                        # 2) chọn radio (nếu có)
                        radio_selector = selectors.get("payment_radio") or "//input[@id='a03']"
                        try:
                            radio = WebDriverWait(driver, 8).until(EC.element_to_be_clickable((By.XPATH, radio_selector)))
                            driver.execute_script("arguments[0].click();", radio)
                            log_action("✅ Đã click radio paymentTypeCode bằng JS.")
                            time.sleep(0.5)
                        except Exception as e:
                            log_action(f"⚠️ Không click được radio paymentTypeCode: {e}")

                        # 3) Click nút Kế tiếp (retry 3 lần)
                        next_btn_selector = selectors.get("next_button") or "/html/body/div[1]/div/div[2]/form/div[2]/div[1]/div[1]/div[2]/ul/li/div/a"
                        clicked = False
                        for attempt in range(3):
                            try:
                                next_btn = WebDriverWait(driver, 8).until(EC.element_to_be_clickable((By.XPATH, next_btn_selector)))
                                driver.execute_script("arguments[0].scrollIntoView({behavior:'auto',block:'center'});", next_btn)
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
                        # chỉ điền CVV
                        cvv_filled = False
                        # 1) thử selector từ workflow
                        if fill_input("card_cvv", acc.get("card_cvv"), prefer_send_keys=True):
                            cvv_filled = True
                        else:
                            # 2) fallback: tìm bằng name attribute
                            try:
                                els = driver.find_elements(By.NAME, "creditCard.securityCode")
                                if els:
                                    el = els[0]
                                    human_type(driver, el, str(acc.get("card_cvv", "")), min_delay=0.03, max_delay=0.09)
                                    log_action("✅ Điền CVV bằng selector name=creditCard.securityCode")
                                    cvv_filled = True
                                else:
                                    # 3) fallback class
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

                        # Click nút Thanh toán (dùng XPath bạn cung cấp, retry)
                        pay_btn_selector = selectors.get("pay_button") or "/html/body/div[1]/div/div[2]/form/div[2]/div/table/tbody/tr/td[2]/div[1]/div[1]/div/a"
                        clicked = False
                        for attempt in range(4):
                            try:
                                pay_btn = WebDriverWait(driver, 8).until(EC.element_to_be_clickable((By.XPATH, pay_btn_selector)))
                                driver.execute_script("arguments[0].scrollIntoView({behavior:'auto',block:'center'});", pay_btn)
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
    except Exception as e:
        log_action(f"❌ Lỗi khi xử lý {username}: {e}")
    finally:
        if driver:
            try:
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

        # chấp nhận cả email hoặc username
        email = incoming.get("email") or incoming.get("username")
        password = incoming.get("password") or incoming.get("pass") or incoming.get("pwd")

        if not email or not password:
            return jsonify({"result": "❌ Thiếu thông tin tài khoản! Cần 'email/username' và 'password'."}), 400

        # load existing accounts
        accounts = load_accounts()

        # định danh account bằng trường email/username; bạn có thể đổi thành 'username' nếu muốn
        existing = next((acc for acc in accounts if acc.get("username") == email or acc.get("email") == email), None)

        if existing:
            existing.update(incoming)
            msg = f"🔄 Đã cập nhật tài khoản: {email}"
        else:
            # nếu muốn tự tạo id, thêm id
            if "id" not in incoming:
                # tạo id đơn giản (millis)
                incoming["id"] = int(time.time() * 1000)
            # chuẩn hoá lưu: giữ cả username và email trường username nếu trước đó dùng username
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

    log_action(f"▶️ Bắt đầu khởi tạo {len(selected_accounts)} threads (mỗi thread start cách nhau 3s)...")

    # start mỗi tài khoản trong 1 thread riêng BUT khởi tạo threads cách nhau 3s
    for idx, acc in enumerate(selected_accounts):
        t = threading.Thread(target=run_workflow_for_account, args=(acc,), daemon=True)
        t.start()
        log_action(f"🟢 Đã start thread cho {acc.get('username')} (idx {idx})")
        # chờ 3s trước khi start thread tiếp theo (không chặn các thread đã start)
        if idx < len(selected_accounts) - 1:
            time.sleep(3)

    return jsonify({"result": f"🚀 Đã bắt đầu {len(selected_accounts)} tài khoản (staggered 3s)!"})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
