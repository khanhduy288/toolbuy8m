import requests, concurrent.futures, time

URL = "https://www.yodobashi.com"  # Trang để test truy cập qua proxy
TIMEOUT = 7  # timeout cao hơn một chút để tránh false negative
MAX_WORKERS = 40  # số luồng chạy song song

def test_proxy(proxy):
    proxies = {
        "http": f"socks5://{proxy}",
        "https": f"socks5://{proxy}"
    }
    try:
        start = time.time()
        r = requests.get(URL, proxies=proxies, timeout=TIMEOUT)
        if r.status_code == 200:
            latency = round((time.time() - start) * 1000)
            print(f"✅ LIVE {proxy} ({latency}ms)")
            return proxy
        else:
            print(f"⚠️ FAIL {proxy} (HTTP {r.status_code})")
    except Exception:
        print(f"❌ DEAD {proxy}")
    return None


def main():
    with open("proxies.txt", "r", encoding="utf-8") as f:
        proxies = [line.strip() for line in f if line.strip()]

    print(f"[+] Tổng proxy cần kiểm tra: {len(proxies)}\n")

    valid = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for result in executor.map(test_proxy, proxies):
            if result:
                valid.append(result)

    with open("valid_socks5.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(valid))

    print(f"\n[+] ✅ Tổng proxy sống: {len(valid)}")
    print("[+] Đã lưu vào valid_socks5.txt")


if __name__ == "__main__":
    main()
