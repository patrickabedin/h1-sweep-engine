function money(n) {
  if (n == null || Number.isNaN(Number(n))) return "—";
  const v = Number(n);
  const sign = v > 0 ? "+" : "";
  return sign + v.toFixed(2);
}
function tone(n) {
  if (n == null) return "muted";
  if (Number(n) > 0) return "pos";
  if (Number(n) < 0) return "neg";
  return "muted";
}
function px(n) {
  if (n == null) return "—";
  const v = Number(n);
  if (v >= 100) return v.toFixed(2);
  if (v >= 1) return v.toFixed(4);
  return v.toPrecision(4);
}
async function loadSnap() {
  const res = await fetch("/snapshot.json", { cache: "no-store" });
  if (!res.ok) throw new Error("snapshot " + res.status);
  return res.json();
}
function nav(page) {
  return `<nav>
    <a class="${page === "home" ? "active" : ""}" href="/">Home</a>
    <a class="${page === "calendar" ? "active" : ""}" href="/calendar/">Calendar</a>
    <a class="${page === "tickets" ? "active" : ""}" href="/tickets/">Tickets</a>
  </nav>`;
}
function header(data, page) {
  return `<div class="top">
    <div>
      <span class="badge">Paper · H1 sweep</span>
      <h1>H1 sweep desk</h1>
      <p class="sub">${data.session} · $100 × 10x = $1,000 · fee $1.20 RT · ${data.min_r}R–${data.max_r}R</p>
    </div>
    ${nav(page)}
  </div>`;
}
function tradeRow(t, kind) {
  const pnl = kind === "open" ? null : t.pnl_usd;
  const when = kind === "open" ? (t.opened_at_ny || t.opened_at_utc || "") : (t.closed_at_ny || t.closed_at_utc || t.exit_reason || "");
  return `<tr>
    <td>${t.symbol}</td>
    <td>${t.direction}</td>
    <td class="${tone(pnl)}">${kind === "open" ? "open" : money(pnl)}</td>
    <td>${px(t.entry_price)}</td>
    <td>${px(t.stop_price)}</td>
    <td>${px(t.target_price)}</td>
    <td class="muted">${t.rr != null ? Number(t.rr).toFixed(2) + "R" : "—"}</td>
    <td class="muted">${kind === "open" ? when : (t.exit_reason || when)}</td>
  </tr>`;
}
function table(rows, kind, empty) {
  if (!rows.length) return `<p class="empty">${empty}</p>`;
  return `<div class="panel" style="overflow:auto"><table>
    <thead><tr>
      <th>Symbol</th><th>Side</th><th>$</th><th>Entry</th><th>Stop</th><th>Target</th><th>R</th><th>${kind === "open" ? "Opened" : "Exit"}</th>
    </tr></thead>
    <tbody>${rows.map((t) => tradeRow(t, kind)).join("")}</tbody>
  </table></div>`;
}
function leadingBlanks(month) {
  const [y, m] = month.split("-").map(Number);
  const dow = new Date(Date.UTC(y, m - 1, 1)).getUTCDay();
  return (dow + 6) % 7;
}
function renderCalendar(root, data) {
  const days = data.days || [];
  const pad = leadingBlanks(data.month);
  const cells = Array(pad).fill(null).concat(days);
  while (cells.length % 7) cells.push(null);
  const wins = (data.closed || []).filter((t) => Number(t.pnl_usd) > 0).length;
  const losses = (data.closed || []).filter((t) => Number(t.pnl_usd) < 0).length;
  let selected = data.today;
  const chosen = () => days.find((d) => d.date === selected) || null;
  function paint() {
    const day = chosen();
    root.innerHTML = `${header(data, "calendar")}
      <div class="panel">
        <div class="month-head">
          <div>
            <h2>${data.month}</h2>
            <p class="kicker">NY session day · ${data.label}</p>
          </div>
          <div>
            <div class="tot ${tone(data.month_net)}">${money(data.month_net)}</div>
            <div class="kicker">${data.n} closes · ${wins}W / ${losses}L</div>
          </div>
        </div>
        <div class="week">${["Mon","Tue","Wed","Thu","Fri","Sat","Sun"].map((d) => `<span>${d}</span>`).join("")}</div>
        <div class="grid" role="grid">
          ${cells.map((c, i) => {
            if (!c) return `<div class="pad" aria-hidden></div>`;
            const on = c.date === selected ? " on" : "";
            const cls = c.n_full ? tone(c.net_full) : "muted";
            return `<button class="cell${on}" data-date="${c.date}" type="button">
              <span class="d">${c.dom}</span>
              <span class="p ${cls}">${c.n_full ? money(c.net_full) : "—"}</span>
            </button>`;
          }).join("")}
        </div>
      </div>
      <p class="note">Paper book. Stop beyond the sweep wick (max 2% of price). Target 2R–4R. Session time-stop 09:11 ET. Not Trend Continuation.</p>
      <h3>${day ? `${day.date} · ${day.n_full} close${day.n_full === 1 ? "" : "s"}` : "Pick a day"}</h3>
      ${table(day ? day.trades : [], "closed", "No closes this day.")}`;
    root.querySelectorAll(".cell").forEach((btn) => {
      btn.addEventListener("click", () => {
        selected = btn.getAttribute("data-date");
        paint();
      });
    });
  }
  paint();
}
function renderTickets(root, data) {
  root.innerHTML = `${header(data, "tickets")}
    <h3>Open</h3>
    ${table(data.open || [], "open", "No open paper tickets.")}
    <h3>Closed</h3>
    ${table(data.closed || [], "closed", "No closed paper tickets yet.")}
    <p class="note">Generated ${data.generated_at || ""} · last scan ${data.last_scan && data.last_scan.time_utc ? data.last_scan.time_utc : "—"}</p>`;
}
function renderHome(root, data) {
  root.innerHTML = `${header(data, "home")}
    <div class="panel" style="padding:16px">
      <p class="note">This is a <strong>paper</strong> desk for the H1 sweep + M1 FVG model. No Cornix, no Telegram, no live orders.</p>
      <p class="note">Month ${data.month}: <span class="${tone(data.month_net)}">${money(data.month_net)}</span> on ${data.n} closes · ${data.open_count} open.</p>
      <p class="note"><a href="/calendar/">Open the PnL calendar</a> · <a href="/tickets/">Open / closed tickets</a></p>
    </div>`;
}
async function boot(page) {
  const root = document.getElementById("app");
  try {
    const data = await loadSnap();
    if (page === "calendar") renderCalendar(root, data);
    else if (page === "tickets") renderTickets(root, data);
    else renderHome(root, data);
  } catch (err) {
    root.innerHTML = `<p class="empty">Could not load snapshot. ${err}</p>`;
  }
}
window.H1Desk = { boot };
