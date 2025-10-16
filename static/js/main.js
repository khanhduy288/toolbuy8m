// ---- tạo option tháng và năm ----
function makeMonthOptions() {
  let html = '<option value="">MM</option>';
  for (let m = 1; m <= 12; m++) html += `<option value="${m}">${m}</option>`;
  return html;
}

function makeYearOptions() {
  const now = new Date();
  const start = now.getFullYear();
  let html = '<option value="">YYYY</option>';
  for (let y = start; y <= start + 10; y++) html += `<option value="${y}">${y}</option>`;
  return html;
}

// ---- tạo option phút ----
function makeMinuteOptions() {
  let html = '';
  for (let m = 0; m < 60; m++) html += `<option value="${m}">${m.toString().padStart(2,'0')}</option>`;
  return html;
}

// ---- auto refresh log ----
function startLogAutoRefresh() {
  setInterval(async () => {
    try {
      const res = await fetch("/logs");
      const data = await res.json();
      const logArea = document.getElementById("logArea");
      logArea.innerText = data.logs;
      logArea.scrollTop = logArea.scrollHeight;
    } catch (err) {
      console.error("Lỗi khi tải log:", err);
    }
  }, 2000);
}

// ---- lấy giờ + phút hiện tại JST ----
function getJapanTime() {
  const now = new Date();
  const hour = (now.getUTCHours() + 9) % 24;
  const minute = now.getUTCMinutes();
  return { hour, minute };
}

// ---- tạo 1 dòng tài khoản ----
function createRow(account = {}) {
  const tbody = document.getElementById("accountTableBody");
  const row = document.createElement("tr");

  row.innerHTML = `
    <td><input type="checkbox" class="run-check" ${account.run ? "checked" : ""}></td>
    <td><input type="text" name="username" value="${account.username || ""}"></td>
    <td><input type="password" name="password" value="${account.password || ""}"></td>
    <td><input type="text" name="proxy" value="${account.proxy || ""}"></td>
    <td><input type="checkbox" class="is-new" ${account.is_new ? "checked" : ""}></td>

    <td><input type="text" name="card_number" class="card-number" value="${account.card_number || ""}" style="display:${account.is_new ? '' : 'none'}"></td>
    <td><select name="card_exp_month" class="card-exp-month" style="display:${account.is_new ? '' : 'none'}">${makeMonthOptions()}</select></td>
    <td><select name="card_exp_year" class="card-exp-year" style="display:${account.is_new ? '' : 'none'}">${makeYearOptions()}</select></td>
    <td><input type="text" name="card_cvv" class="card-cvv" value="${account.card_cvv || ""}"></td>

    <td><input type="text" name="url" value="${account.url || ""}"></td>
    <td><input type="number" name="quantity" value="${account.quantity || 1}"></td>

    <td>
      <select class="run-hour">
        ${Array.from({length:24}, (_,h) => `<option value="${h}" ${account.run_hour==h?"selected":""}>${h}</option>`).join('')}
      </select> :
      <select class="run-minute">${makeMinuteOptions()}</select>
    </td>

    <td>
      <button type="button" class="save-one">💾 Save</button>
      <button type="button" class="remove-row">❌</button>
    </td>
  `;

  tbody.appendChild(row);

  if (account.card_exp_month) row.querySelector('.card-exp-month').value = account.card_exp_month;
  if (account.card_exp_year) row.querySelector('.card-exp-year').value = account.card_exp_year;
  if (account.run_minute != null) row.querySelector('.run-minute').value = account.run_minute;

  attachRowHandlers(row, account);
}

// ---- xử lý sự kiện ----
function attachRowHandlers(row, account) {
  const isNew = row.querySelector('.is-new');
  const fields = [
    row.querySelector('.card-number'),
    row.querySelector('.card-exp-month'),
    row.querySelector('.card-exp-year')
  ];
  isNew.addEventListener('change', () => {
    const show = isNew.checked;
    fields.forEach(f => f.style.display = show ? '' : 'none');
  });

  row.querySelector('.remove-row').addEventListener('click', async () => {
    const id = account?.id;
    if (id) {
      if (!confirm("Bạn có chắc muốn xóa tài khoản này không?")) return;
      try {
        const res = await fetch(`/delete_account/${id}`, { method: "DELETE" });
        const data = await res.json();
        alert(data.message || "✅ Đã xóa!");
        if (res.ok) row.remove();
      } catch (err) {
        alert("❌ Lỗi khi gọi API xóa!");
        console.error(err);
      }
    } else row.remove();
  });

  row.querySelector('.save-one').addEventListener('click', async () => {
    const acc = extractAccountFromRow(row);
    const res = await fetch("/save_one", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(acc)
    });
    const data = await res.json();
    document.getElementById("result").innerText = data.message || "✅ Đã lưu dòng!";
  });
}

// ---- lấy data từ dòng ----
function extractAccountFromRow(row) {
  const acc = {
    username: row.querySelector('[name="username"]').value.trim(),
    password: row.querySelector('[name="password"]').value.trim(),
    proxy: row.querySelector('[name="proxy"]').value.trim(),
    url: row.querySelector('[name="url"]').value.trim(),
    quantity: parseInt(row.querySelector('[name="quantity"]').value.trim() || 1),
    is_new: row.querySelector('.is-new').checked,
    run: row.querySelector('.run-check').checked,
    card_cvv: row.querySelector('.card-cvv').value.trim(),
    run_hour: parseInt(row.querySelector('.run-hour').value, 10),
    run_minute: parseInt(row.querySelector('.run-minute').value, 10)
  };

  if (acc.is_new) {
    acc.card_number = row.querySelector('.card-number').value.trim();
    acc.card_exp_month = row.querySelector('.card-exp-month').value;
    acc.card_exp_year = row.querySelector('.card-exp-year').value;
  }
  return acc;
}

// ---- load tài khoản ----
async function loadAccounts() {
  const res = await fetch("/accounts");
  if (!res.ok) return alert("Không thể tải danh sách tài khoản!");
  const data = await res.json();
  document.getElementById("accountTableBody").innerHTML = "";
  data.forEach(acc => createRow(acc));
}

function addRow() { createRow(); }

async function saveAccounts() {
  const rows = document.querySelectorAll("#accountTableBody tr");
  const accounts = Array.from(rows).map(r => extractAccountFromRow(r));
  const res = await fetch("/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ accounts })
  });
  const data = await res.json();
  document.getElementById("result").innerText = data.message || "✅ Đã lưu tất cả!";
}

// ---- chạy workflow ----
async function startWorkflow(runNow = false) {
  const { hour: currentHour, minute: currentMinute } = getJapanTime();
  const rows = document.querySelectorAll("#accountTableBody tr");

  let selected = Array.from(rows)
    .filter(r => r.querySelector('.run-check').checked)
    .map(r => extractAccountFromRow(r));

  if (!runNow) {
    // Lọc theo giờ/phút
    selected = selected.filter(acc => acc.run_hour === currentHour && acc.run_minute === currentMinute);
    if (!selected.length) {
      console.log(`⏱️ Không có tài khoản nào chạy giờ ${currentHour}:${currentMinute} JST`);
      return;
    }
  }

  console.log("✅ Chạy tài khoản:", selected); // Debug danh sách tài khoản được chọn

  const res = await fetch("/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ accounts: selected })
  });
  const data = await res.json();
  document.getElementById("result").innerText = data.result || `✅ Đã gửi yêu cầu cho ${selected.length} tài khoản!`;
}


// ---- tự động kiểm tra mỗi 1 phút ----
setInterval(() => startWorkflow(false), 60000);

// ---- khởi động ----
document.addEventListener("DOMContentLoaded", () => {
  loadAccounts();
  startLogAutoRefresh();

  // gắn nút "Chạy ngay" nếu có
  const runNowBtn = document.getElementById("runNowBtn");
  if (runNowBtn) {
    runNowBtn.addEventListener("click", () => startWorkflow(true));
  }
});
