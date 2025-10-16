import sys, time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# --- Cấu hình driver ---
driver_path = r"msedgedriver.exe"  # file msedgedriver cùng thư mục Flask
service = Service(driver_path)
options = webdriver.EdgeOptions()
options.add_argument("--start-maximized")

# --- Lấy selector từ tham số ---
selector = sys.argv[1] if len(sys.argv) > 1 else "//a[contains(text(),'ログイン')]"
by = "xpath"

print(f"🧩 Đang mở trang cần thao tác...")

# --- Mở trang login trực tiếp để test ---
url = "https://www.yodobashi.com/product/100000001003891482/"
driver = webdriver.Edge(service=service, options=options)
driver.get(url)

try:
    # --- Đợi nút login xuất hiện ---
    print("🔎 Đợi nút ログイン xuất hiện...")
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.XPATH, selector))
    )
    element = driver.find_element(By.XPATH, selector)

    # --- Cuộn vào vùng hiển thị ---
    driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", element)
    time.sleep(1)

    # --- Dùng script click mạnh để tránh chặn JS ---
    driver.execute_script("arguments[0].click();", element)
    print("✅ Đã click nút ログイン thành công!")

except Exception as e:
    print(f"❌ Lỗi khi click DOM: {e}")

time.sleep(5)
driver.quit()
