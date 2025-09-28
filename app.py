import os
import sys
import math
import json
import time
import atexit
import datetime as dt
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

"""
Cross‑platform, dependency‑free app with two UI modes:
1) Tkinter UI (if available) – draggable red circle that blinks when active; click toggles.
2) Web UI fallback (standard library only) – served locally and opened in the browser.

Both modes:
- Log each active session (from click ON to click OFF) as a line in logs/YYYY-MM-DD.txt
- At day end (and on clean exit), append a daily Persian summary like: "چهارشنبه ۸ مهر — ۲:۴۵ دقیقه مجموع".

Run:
  python app.py               # auto: Tkinter if present, else Web UI
  python app.py --force-web   # force Web UI
  python app.py --force-tk    # force Tk UI (fails gracefully if not installed)
  python app.py --test        # run unit tests (no UI)

Build exe (Windows):
  pip install pyinstaller
  pyinstaller --onefile --noconsole app.py

Environment:
  Set APP_LOG_DIR to change where logs are written (useful for tests/portable exe).
"""

# --------------------------- Utils: App Paths ---------------------------

def app_dir() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

LOG_DIR = os.environ.get('APP_LOG_DIR') or os.path.join(app_dir(), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

# --------------------------- Persian (Jalali) Date Helpers ---------------------------
PERSIAN_WEEKDAYS = [
    "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"
]
PERSIAN_MONTHS = [
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"
]
PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def gregorian_to_jalali(g_y, g_m, g_d):
    g_days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    j_days_in_month = [31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29]

    gy = g_y - 1600
    gm = g_m - 1
    gd = g_d - 1

    g_day_no = 365 * gy + (gy + 3) // 4 - (gy + 99) // 100 + (gy + 399) // 400
    for i in range(gm):
        g_day_no += g_days_in_month[i]
    if gm > 1 and ((g_y % 4 == 0 and g_y % 100 != 0) or (g_y % 400 == 0)):
        g_day_no += 1
    g_day_no += gd

    j_day_no = g_day_no - 79
    j_np = j_day_no // 12053
    j_day_no %= 12053

    jy = 979 + 33 * j_np + 4 * (j_day_no // 1461)
    j_day_no %= 1461

    if j_day_no >= 366:
        jy += (j_day_no - 1) // 365
        j_day_no = (j_day_no - 1) % 365

    for i in range(11):
        if j_day_no < j_days_in_month[i]:
            jm = i + 1
            jd = j_day_no + 1
            break
        j_day_no -= j_days_in_month[i]
    else:
        jm = 12
        jd = j_day_no + 1

    return jy, jm, jd


def persian_weekday_name(date_greg: dt.date) -> str:
    return PERSIAN_WEEKDAYS[date_greg.weekday()]


def persian_date_str(date_greg: dt.date, use_persian_digits: bool = True) -> str:
    jy, jm, jd = gregorian_to_jalali(date_greg.year, date_greg.month, date_greg.day)
    wd = persian_weekday_name(date_greg)
    s = f"{wd} {jd} {PERSIAN_MONTHS[jm-1]}"
    return s.translate(PERSIAN_DIGITS) if use_persian_digits else s


def format_hm(td: dt.timedelta, use_persian_digits: bool = True) -> str:
    total_minutes = int(td.total_seconds() // 60)
    h = total_minutes // 60
    m = total_minutes % 60
    s = f"{h}:{m:02d}"
    return s.translate(PERSIAN_DIGITS) if use_persian_digits else s

# --------------------------- Logging & Summary ---------------------------

def today_log_path(day: dt.date | None = None) -> str:
    day = day or dt.date.today()
    return os.path.join(LOG_DIR, day.isoformat() + '.txt')


def write_line(text: str, path: str | None = None) -> None:
    path = path or today_log_path()
    with open(path, 'a', encoding='utf-8') as f:
        f.write(text.rstrip('\n') + '\n')


def parse_minutes_from_line(line: str) -> int:
    try:
        if 'مدت:' in line:
            seg = line.split('مدت:')[1].strip()
            hm = seg.split()[0]
            h, m = hm.split(':')
            # handle Persian digits
            def to_ascii(s: str) -> str:
                out = []
                for ch in s:
                    if '۰' <= ch <= '۹':
                        out.append(str(ord(ch) - 1776))
                    else:
                        out.append(ch)
                return ''.join(out)
            h = int(to_ascii(h))
            m = int(to_ascii(m))
            return h * 60 + m
    except Exception:
        return 0
    return 0


def compute_total_minutes_for_file(path: str) -> int:
    if not os.path.exists(path):
        return 0
    total = 0
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            total += parse_minutes_from_line(line)
    return total


def _log_single_segment(start_dt: dt.datetime, end_dt: dt.datetime) -> None:
    duration = end_dt - start_dt
    path = today_log_path(start_dt.date())
    start_str = start_dt.strftime('%H:%M').translate(PERSIAN_DIGITS)
    end_str = end_dt.strftime('%H:%M').translate(PERSIAN_DIGITS)
    dur_str = format_hm(duration)
    write_line(f"از {start_str} تا {end_str} — مدت: {dur_str}", path)


def log_session_range(start_dt: dt.datetime, end_dt: dt.datetime) -> None:
    cur = start_dt
    while True:
        next_midnight = (cur + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_segment = min(end_dt, next_midnight)
        _log_single_segment(cur, end_segment)
        if end_segment >= end_dt:
            break
        cur = end_segment


def write_daily_summary_for(date_obj: dt.date) -> None:
    path = today_log_path(date_obj)
    total_minutes = compute_total_minutes_for_file(path)
    total_td = dt.timedelta(minutes=total_minutes)
    last_line = ''
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            lines = [ln.strip() for ln in f.readlines() if ln.strip()]
            if lines:
                last_line = lines[-1]
    if 'مجموع' in last_line:
        return
    date_str = persian_date_str(date_obj)
    total_str = format_hm(total_td)
    write_line(f"{date_str} — {total_str} دقیقه مجموع", path)

# --------------------------- Midnight Summary Thread ---------------------------

_stop_midnight = threading.Event()

def _midnight_worker():
    while not _stop_midnight.is_set():
        now = dt.datetime.now()
        tomorrow = (now + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        wait_s = (tomorrow - now).total_seconds()
        _stop_midnight.wait(timeout=wait_s)
        if _stop_midnight.is_set():
            break
        try:
            yday = dt.date.today() - dt.timedelta(days=1)
            write_daily_summary_for(yday)
        except Exception:
            pass


def start_midnight_summary_thread():
    t = threading.Thread(target=_midnight_worker, daemon=True)
    t.start()
    return t

# --------------------------- Tkinter UI (optional) ---------------------------

def run_tkinter_ui() -> bool:
    try:
        import tkinter as tk  # Imported lazily to avoid ModuleNotFoundError in sandbox
    except Exception:
        return False

    class BlinkingCircleApp:
        def __init__(self, root: "tk.Tk"):
            self.root = root
            self.root.title('دایرهٔ چشمک‌زن')
            self.canvas = tk.Canvas(root, width=400, height=300, bg='#f5f5f5', highlightthickness=0)
            self.canvas.pack(fill='both', expand=True)

            self.radius = 40
            self.cx, self.cy = 200, 150
            self.circle = self.canvas.create_oval(
                self.cx - self.radius, self.cy - self.radius,
                self.cx + self.radius, self.cy + self.radius,
                fill='#b0b0b0', outline='#8c8c8c', width=2
            )

            self.active = False
            self.blink_on = False
            self.blink_job = None
            self.session_start = None

            self.drag_start = None
            self.moved_enough = False

            self.canvas.tag_bind(self.circle, '<ButtonPress-1>', self.on_press)
            self.canvas.tag_bind(self.circle, '<B1-Motion>', self.on_drag)
            self.canvas.tag_bind(self.circle, '<ButtonRelease-1>', self.on_release)

            self.root.protocol('WM_DELETE_WINDOW', self.on_close)

        def on_press(self, event):
            self.drag_start = (event.x, event.y)
            self.moved_enough = False

        def on_drag(self, event):
            if not self.drag_start:
                return
            dx = event.x - self.drag_start[0]
            dy = event.y - self.drag_start[1]
            if math.hypot(dx, dy) > 3:
                self.moved_enough = True
            self.canvas.move(self.circle, dx, dy)
            self.drag_start = (event.x, event.y)

        def on_release(self, event):
            if not self.moved_enough:
                self.toggle_active()
            self.drag_start = None
            self.moved_enough = False

        def toggle_active(self):
            if not self.active:
                self.active = True
                self.session_start = dt.datetime.now()
                self.start_blinking()
            else:
                self.active = False
                self.stop_blinking()
                if self.session_start:
                    log_session_range(self.session_start, dt.datetime.now())
                    self.session_start = None

        def start_blinking(self):
            self.blink_on = False
            self._blink_step()

        def _blink_step(self):
            if not self.active:
                return
            self.blink_on = not self.blink_on
            fill = '#ff3333' if self.blink_on else '#ffb3b3'
            self.canvas.itemconfig(self.circle, fill=fill, outline='#cc0000')
            self.blink_job = self.root.after(500, self._blink_step)

        def stop_blinking(self):
            if self.blink_job is not None:
                self.root.after_cancel(self.blink_job)
                self.blink_job = None
            self.canvas.itemconfig(self.circle, fill='#bfbfbf', outline='#9e9e9e')

        def on_close(self):
            if self.active and self.session_start:
                end = dt.datetime.now()
                self.stop_blinking()
                log_session_range(self.session_start, end)
                self.active = False
                self.session_start = None
            write_daily_summary_for(dt.date.today())
            self.root.destroy()

    root = tk.Tk()
    root.minsize(360, 240)
    start_midnight_summary_thread()
    BlinkingCircleApp(root)
    root.mainloop()
    return True

# --------------------------- Web UI (standard library only) ---------------------------

HTML_PAGE = """
<!doctype html>
<html lang="fa" dir="rtl">
<meta charset="utf-8" />
<title>دایرهٔ چشمک‌زن</title>
<style>
  body { font-family: system-ui, sans-serif; background:#f5f5f5; margin:0; }
  header { padding:12px 16px; background:#ffffff; box-shadow:0 1px 4px rgba(0,0,0,.06); position:sticky; top:0; }
  #wrap { position:relative; height: calc(100vh - 64px); }
  #circle { width:80px; height:80px; border-radius:50%; position:absolute; left: calc(50% - 40px); top: calc(50% - 40px);
            background:#bfbfbf; box-shadow:0 2px 10px rgba(0,0,0,.15); cursor:grab; border:2px solid #9e9e9e; opacity:0.9; }
  #circle.active { border-color:#cc0000; }
  #circle.b-on { background:#ff3333; }
  #circle.b-off { background:#ffb3b3; }
  button { padding:8px 12px; border-radius:10px; border:1px solid #ddd; background:#fff; cursor:pointer; }
  #status { margin-inline-start:10px; font-weight:600; }
</style>
<header>
  <button id="quit">خروج</button>
  <span id="status">غیرفعال</span>
</header>
<div id="wrap">
  <div id="circle" title="کلیک: شروع/توقف • درگ: جابجایی"></div>
</div>
<script>
(function(){
  const circle = document.getElementById('circle');
  const statusEl = document.getElementById('status');
  const quitBtn = document.getElementById('quit');
  let dragging=false, startX=0, startY=0, moved=false;
  let active=false, blink=false, timer=null;

  async function getJSON(url){
    const r = await fetch(url, {cache:'no-store'}); return await r.json();
  }
  async function postToggle(to){
    const r = await getJSON('/api/toggle?state=' + to);
    setActive(r.active);
  }
  function setActive(a){
    active = a; circle.classList.toggle('active', a);
    statusEl.textContent = a ? 'فعال (در حال ثبت زمان)' : 'غیرفعال';
    if(timer){ clearInterval(timer); timer=null; }
    if(a){
      timer = setInterval(()=>{
        blink = !blink;
        circle.classList.toggle('b-on', blink);
        circle.classList.toggle('b-off', !blink);
      }, 500);
    } else {
      circle.classList.remove('b-on');
      circle.classList.add('b-off');
    }
  }

  circle.addEventListener('mousedown', e=>{
    dragging=true; moved=false;
    startX=e.clientX; startY=e.clientY; circle.style.cursor='grabbing';
  });
  window.addEventListener('mousemove', e=>{
    if(!dragging) return;
    const dx=e.clientX-startX, dy=e.clientY-startY;
    if(Math.hypot(dx,dy)>3) moved=true;
    const rect=circle.getBoundingClientRect();
    circle.style.left=(rect.left+dx)+'px';
    circle.style.top=(rect.top+dy)+'px';
    startX=e.clientX; startY=e.clientY;
  });
  window.addEventListener('mouseup', e=>{
    if(!dragging) return; dragging=false; circle.style.cursor='grab';
    if(!moved){ postToggle(active? 'off' : 'on'); }
  });

  circle.classList.add('b-off');
  getJSON('/api/state').then(s=> setActive(s.active));
  quitBtn.addEventListener('click', async ()=>{ try{ await fetch('/api/quit'); }catch(e){} setTimeout(()=>window.close(), 300); });
})();
</script>
</html>
"""

class _SharedState:
    def __init__(self):
        self.active = False
        self.session_start: dt.datetime | None = None
        self.lock = threading.Lock()

STATE = _SharedState()

class Handler(BaseHTTPRequestHandler):
    def _json(self, obj: dict, code: int = 200):
        data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/':
            page = HTML_PAGE.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(page)))
            self.end_headers()
            self.wfile.write(page)
            return
        if parsed.path == '/api/state':
            with STATE.lock:
                self._json({ 'active': STATE.active })
            return
        if parsed.path == '/api/toggle':
            qs = parse_qs(parsed.query)
            state = (qs.get('state', [''])[0]).lower()
            now = dt.datetime.now()
            with STATE.lock:
                if state == 'on' and not STATE.active:
                    STATE.active = True
                    STATE.session_start = now
                    self._json({'ok': True, 'active': True})
                    return
                if state == 'off' and STATE.active:
                    start = STATE.session_start or now
                    STATE.active = False
                    STATE.session_start = None
                    log_session_range(start, now)
                    self._json({'ok': True, 'active': False})
                    return
            self._json({'ok': True, 'active': STATE.active})
            return
        if parsed.path == '/api/quit':
            # clean shutdown: log any active session and write today's summary
            with STATE.lock:
                if STATE.active and STATE.session_start:
                    log_session_range(STATE.session_start, dt.datetime.now())
                    STATE.active = False
                    STATE.session_start = None
            write_daily_summary_for(dt.date.today())
            def _shutdown(server):
                time.sleep(0.1)
                server.shutdown()
            threading.Thread(target=_shutdown, args=(self.server,), daemon=True).start()
            self._json({'ok': True})
            return
        if parsed.path == '/api/total':
            path = today_log_path(dt.date.today())
            total = compute_total_minutes_for_file(path)
            self._json({'total_minutes': total})
            return
        # favicon and others
        if parsed.path == '/favicon.ico':
            self.send_response(204); self.end_headers(); return
        self.send_response(404); self.end_headers()


def run_web_ui(host: str = '127.0.0.1', port: int = 0):
    httpd = HTTPServer((host, port), Handler)
    actual_port = httpd.server_address[1]
    start_midnight_summary_thread()
    url = f'http://{host}:{actual_port}/'
    try:
        webbrowser.open(url, new=1)
    except Exception:
        pass
    print(f"Web UI running at: {url}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        with STATE.lock:
            if STATE.active and STATE.session_start:
                log_session_range(STATE.session_start, dt.datetime.now())
                STATE.active = False
                STATE.session_start = None
        write_daily_summary_for(dt.date.today())

# --------------------------- Tests ---------------------------

def _run_tests():
    import tempfile
    import unittest

    class T(unittest.TestCase):
        def setUp(self):
            self.tmp = tempfile.TemporaryDirectory()
            os.environ['APP_LOG_DIR'] = self.tmp.name
            global LOG_DIR
            LOG_DIR = os.environ['APP_LOG_DIR']

        def tearDown(self):
            self.tmp.cleanup()

        def test_nowruz_1402(self):
            self.assertEqual(gregorian_to_jalali(2023, 3, 21), (1402,1,1))

        def test_format_hm(self):
            self.assertEqual(format_hm(dt.timedelta(minutes=165), use_persian_digits=False), '2:45')

        def test_parse_minutes(self):
            line = 'از ۱۴:۲۲ تا ۱۵:۱۰ — مدت: ۰:۴۸'
            self.assertEqual(parse_minutes_from_line(line), 48)

        def test_log_split_midnight(self):
            start = dt.datetime(2023,3,21,23,55)
            end   = dt.datetime(2023,3,22,0,10)
            log_session_range(start, end)
            p1 = today_log_path(start.date())
            p2 = today_log_path(end.date())
            self.assertTrue(os.path.exists(p1))
            self.assertTrue(os.path.exists(p2))
            self.assertEqual(compute_total_minutes_for_file(p1), 5)
            self.assertEqual(compute_total_minutes_for_file(p2), 10)

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(T)
    runner = unittest.TextTestRunner(verbosity=2)
    res = runner.run(suite)
    if not res.wasSuccessful():
        sys.exit(1)

# --------------------------- Entry Point ---------------------------

def main():
    if '--test' in sys.argv:
        _run_tests(); return

    forced_web = '--force-web' in sys.argv
    forced_tk  = '--force-tk' in sys.argv

    started = False
    if forced_tk:
        started = run_tkinter_ui()
        if not started:
            print('Tkinter در دسترس نیست؛ به حالت Web UI می‌رویم...')
            run_web_ui()
            return
    elif not forced_web:
        started = run_tkinter_ui()
        if started:
            return
    # fallback
    run_web_ui()

if __name__ == '__main__':
    main()
