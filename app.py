import os
import sys
import math
import json
import time
import datetime as dt
import threading
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from typing import Optional

"""
Tiny, dependency‑free app. Two UI modes:
1) Tkinter (if available) — NOW tiny 50×50 window (always-on-top), a single blinking circle.
   • Window opacity: 100% on hover, ~10% when mouse leaves.
   • Left‑click toggles ON/OFF (blinking red / grey), Right‑click opens Exit menu.
2) Web fallback (stdlib only) — a tiny 50×50 page with the same behavior.

Logging FIX:
- Durations are now logged with seconds precision as H:MM:SS (no more 0 for short sessions).
- Daily summary also shows H:MM:SS.
- Backward compatibility: if old lines contain فقط H:MM, parser still reads them.

Run:
  python app.py               # auto UI (Tk if present, else Web)
  python app.py --force-web   # force Web UI
  python app.py --force-tk    # force Tk UI
  python app.py --test        # run tests

Build exe (Windows):
  pip install pyinstaller
  pyinstaller --onefile --noconsole app.py

Env:
  APP_LOG_DIR to change logs folder.
"""

# --------------------------- Paths ---------------------------

def app_dir() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

LOG_DIR = os.environ.get('APP_LOG_DIR') or os.path.join(app_dir(), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

# --------------------------- Persian (Jalali) ---------------------------
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
    gy = g_y - 1600; gm = g_m - 1; gd = g_d - 1
    g_day_no = 365 * gy + (gy + 3) // 4 - (gy + 99) // 100 + (gy + 399) // 400
    for i in range(gm): g_day_no += g_days_in_month[i]
    if gm > 1 and ((g_y % 4 == 0 and g_y % 100 != 0) or (g_y % 400 == 0)): g_day_no += 1
    g_day_no += gd
    j_day_no = g_day_no - 79
    j_np = j_day_no // 12053; j_day_no %= 12053
    jy = 979 + 33 * j_np + 4 * (j_day_no // 1461); j_day_no %= 1461
    if j_day_no >= 366:
        jy += (j_day_no - 1) // 365; j_day_no = (j_day_no - 1) % 365
    for i in range(11):
        if j_day_no < j_days_in_month[i]: jm = i + 1; jd = j_day_no + 1; break
        j_day_no -= j_days_in_month[i]
    else:
        jm = 12; jd = j_day_no + 1
    return jy, jm, jd


def persian_weekday_name(date_greg: dt.date) -> str:
    return PERSIAN_WEEKDAYS[date_greg.weekday()]


def persian_date_str(date_greg: dt.date, use_persian_digits: bool = True) -> str:
    jy, jm, jd = gregorian_to_jalali(date_greg.year, date_greg.month, date_greg.day)
    s = f"{persian_weekday_name(date_greg)} {jd} {PERSIAN_MONTHS[jm-1]}"
    return s.translate(PERSIAN_DIGITS) if use_persian_digits else s


def fmt_hm(td: dt.timedelta, fa: bool = True) -> str:
    total_minutes = int(td.total_seconds() // 60)
    h, m = divmod(total_minutes, 60)
    s = f"{h}:{m:02d}"; return s.translate(PERSIAN_DIGITS) if fa else s


def fmt_hms(td: dt.timedelta, fa: bool = True) -> str:
    total_seconds = int(td.total_seconds())
    h = total_seconds // 3600; m = (total_seconds % 3600) // 60; s = total_seconds % 60
    out = f"{h}:{m:02d}:{s:02d}"; return out.translate(PERSIAN_DIGITS) if fa else out

# --------------------------- Logging (seconds precision) ---------------------------

def today_log_path(day: Optional[dt.date] = None) -> str:
    day = day or dt.date.today()
    return os.path.join(LOG_DIR, day.isoformat() + '.txt')


def write_line(text: str, path: Optional[str] = None) -> None:
    path = path or today_log_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a', encoding='utf-8') as f:
        f.write(text.rstrip('\n') + '\n')


def _to_ascii_digits(s: str) -> str:
    out = []
    for ch in s:
        if '۰' <= ch <= '۹': out.append(str(ord(ch) - 1776))
        else: out.append(ch)
    return ''.join(out)


def parse_duration_to_seconds(line: str) -> int:
    """Parses 'مدت: H:MM:SS' (new) or 'مدت: H:MM' (legacy) to seconds."""
    if 'مدت:' not in line: return 0
    try:
        seg = line.split('مدت:')[1].strip()
        token = seg.split()[0]
        token = _to_ascii_digits(token)
        parts = token.split(':')
        if len(parts) == 3:
            h, m, s = map(int, parts)
            return h * 3600 + m * 60 + s
        if len(parts) == 2:
            h, m = map(int, parts)
            return h * 3600 + m * 60
    except Exception:
        return 0
    return 0


def compute_total_seconds_for_file(path: str) -> int:
    if not os.path.exists(path): return 0
    total = 0
    with open(path, 'r', encoding='utf-8') as f:
        for line in f: total += parse_duration_to_seconds(line)
    return total


def _log_single_segment(start_dt: dt.datetime, end_dt: dt.datetime) -> None:
    duration = end_dt - start_dt
    # round up to 1s to avoid zero for ultra-short
    if duration.total_seconds() < 1: duration = dt.timedelta(seconds=1)
    path = today_log_path(start_dt.date())
    start_str = start_dt.strftime('%H:%M:%S').translate(PERSIAN_DIGITS)
    end_str = end_dt.strftime('%H:%M:%S').translate(PERSIAN_DIGITS)
    dur_str = fmt_hms(duration)
    write_line(f"از {start_str} تا {end_str} — مدت: {dur_str}", path)


def log_session_range(start_dt: dt.datetime, end_dt: dt.datetime) -> None:
    cur = start_dt
    while True:
        next_midnight = (cur + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_segment = min(end_dt, next_midnight)
        _log_single_segment(cur, end_segment)
        if end_segment >= end_dt: break
        cur = end_segment


def write_daily_summary_for(date_obj: dt.date) -> None:
    path = today_log_path(date_obj)
    total_seconds = compute_total_seconds_for_file(path)
    total_td = dt.timedelta(seconds=total_seconds)
    last_line = ''
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            lines = [ln.strip() for ln in f.readlines() if ln.strip()]
            if lines: last_line = lines[-1]
    if 'مجموع' in last_line: return
    date_str = persian_date_str(date_obj)
    total_str = fmt_hms(total_td)
    write_line(f"{date_str} — {total_str} مجموع", path)

# --------------------------- Midnight thread ---------------------------
_stop_midnight = threading.Event()

def _midnight_worker():
    while not _stop_midnight.is_set():
        now = dt.datetime.now()
        tomorrow = (now + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        _stop_midnight.wait(timeout=(tomorrow - now).total_seconds())
        if _stop_midnight.is_set(): break
        try:
            write_daily_summary_for(dt.date.today() - dt.timedelta(days=1))
        except Exception: pass

def start_midnight_summary_thread():
    t = threading.Thread(target=_midnight_worker, daemon=True); t.start(); return t

# --------------------------- Tkinter UI (transparent tiny 50×64) ---------------------------

def run_tkinter_ui() -> bool:
    try:
        import tkinter as tk
    except Exception:
        return False

    class TinyTransparent:
        MASK = '#00FF00'  # magic transparent key color (Windows)
        def __init__(self, root: "tk.Tk"):
            self.root = root
            self.size_w, self.size_h = 50, 64  # 50×50 circle + 14px for timer text
            self.r = 20  # circle radius

            # Borderless, on top, transparent background
            self.root.overrideredirect(True)
            self.root.wm_attributes('-topmost', True)
            try:
                # use color key transparency when supported (Windows)
                self.root.configure(bg=self.MASK)
                self.root.wm_attributes('-transparentcolor', self.MASK)
            except Exception:
                # fallback: alpha based transparency
                try: self.root.wm_attributes('-alpha', 0.10)
                except Exception: pass

            self.root.geometry(f"{self.size_w}x{self.size_h}+120+120")

            # Canvas with transparent key as background
            self.cn = tk.Canvas(self.root, width=self.size_w, height=self.size_h,
                                bg=self.MASK, highlightthickness=0, bd=0)
            self.cn.pack(fill='both', expand=True)

            # Draw red circle centered at top area
            cx, cy = self.size_w/2, 24
            self.circle = self.cn.create_oval(cx-self.r, cy-self.r, cx+self.r, cy+self.r,
                                              fill='#ff3333', outline='', width=0)
            # Close dot (top-right tiny black point)
            self.close_dot = self.cn.create_oval(self.size_w-10, 4, self.size_w-4, 10,
                                                 fill='#000000', outline='')
            # Timer text (below circle, mm:ss)
            self.text = self.cn.create_text(self.size_w/2, 52, text='۰۰:۰۰',
                                            fill='#111111', font=('Segoe UI', 8, 'bold'))

            # State
            self.active = False
            self.blink_on = True
            self.blink_job = None
            self.timer_job = None
            self.session_start: Optional[dt.datetime] = None

            # Bindings: click toggles, drag window (move threshold), leave/enter opacity, close
            self.cn.tag_bind(self.circle, '<ButtonPress-1>', self.on_press)
            self.cn.tag_bind(self.circle, '<B1-Motion>', self.on_drag)
            self.cn.tag_bind(self.circle, '<ButtonRelease-1>', self.on_release)
            # allow dragging by grabbing anywhere
            self.cn.bind('<ButtonPress-1>', self.on_press)
            self.cn.bind('<B1-Motion>', self.on_drag)
            self.cn.bind('<ButtonRelease-1>', self.on_release)

            self.cn.tag_bind(self.close_dot, '<Button-1>', lambda e: self.exit_app())

            self.cn.bind('<Enter>', lambda e: self.set_alpha(1.0))
            self.cn.bind('<Leave>', lambda e: self.set_alpha(0.10))
            self.set_alpha(0.10)

            self._tick_timer()  # start timer label update

        # opacity helper
        def set_alpha(self, a: float):
            try:
                # when transparentcolor is used, keep window solid; use slight alpha change for hint only
                self.root.wm_attributes('-alpha', a)
            except Exception:
                pass

        # drag helpers
        def on_press(self, e):
            self.start_xy = (e.x_root, e.y_root)
            self.win_xy = (self.root.winfo_x(), self.root.winfo_y())
            self.dragged = False

        def on_drag(self, e):
            dx = e.x_root - self.start_xy[0]; dy = e.y_root - self.start_xy[1]
            if abs(dx) + abs(dy) > 2: self.dragged = True
            self.root.geometry(f"+{self.win_xy[0]+dx}+{self.win_xy[1]+dy}")

        def on_release(self, e):
            if not self.dragged:
                self.toggle_active()
            self.dragged = False

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
            self.blink_on = True
            self._blink_step()

        def _blink_step(self):
            if not self.active:
                return
            # toggle red/soft red
            self.blink_on = not self.blink_on
            self.cn.itemconfig(self.circle, fill=('#ff3333' if self.blink_on else '#ffb3b3'))
            self.blink_job = self.root.after(500, self._blink_step)

        def stop_blinking(self):
            if self.blink_job is not None:
                self.root.after_cancel(self.blink_job); self.blink_job = None
            self.cn.itemconfig(self.circle, fill='#bfbfbf')

        def _tick_timer(self):
            # Update mm:ss below the circle
            if self.active and self.session_start:
                elapsed = dt.datetime.now() - self.session_start
            else:
                elapsed = dt.timedelta(0)
            mmss = fmt_hms(elapsed).split(':')[-2:]  # MM:SS
            label = f"{mmss[0]}:{mmss[1]}".translate(PERSIAN_DIGITS)
            self.cn.itemconfig(self.text, text=label)
            self.timer_job = self.root.after(1000, self._tick_timer)

        def exit_app(self):
            if self.active and self.session_start:
                log_session_range(self.session_start, dt.datetime.now())
            write_daily_summary_for(dt.date.today())
            self.root.destroy()

    root = tk.Tk()
    start_midnight_summary_thread()
    TinyTransparent(root)
    root.mainloop()
    return True

# --------------------------- Web UI (tiny 50×50) ---------------------------

HTML_PAGE = """
<!doctype html>
<html lang=\"fa\" dir=\"rtl\">
<meta charset=\"utf-8\" />
<title>دایرهٔ چشمک‌زن</title>
<style>
  html,body{margin:0;height:100%;}
  body { display:flex; align-items:center; justify-content:center; background:transparent; }
  #circle { width:46px; height:46px; border-radius:50%;
            background:#bfbfbf; border:1px solid #8c8c8c; box-shadow:0 1px 4px rgba(0,0,0,.25);
            cursor:default; transition:opacity .15s; }
  body.dim { opacity:.10; }
  .active { border-color:#cc0000; }
  .b-on { background:#ff3333; }
  .b-off { background:#ffb3b3; }
</style>
<div id=\"circle\" title=\"کلیک: شروع/توقف\"></div>
<script>
(function(){
  const c = document.getElementById('circle');
  let active=false, blink=false, t=null, inside=false;
  function setActive(a){
    active=a; c.classList.toggle('active', a);
    if(t){clearInterval(t); t=null;}
    if(a){
      t=setInterval(()=>{ blink=!blink; c.classList.toggle('b-on', blink); c.classList.toggle('b-off', !blink); }, 500);
    } else { c.classList.remove('b-on'); c.classList.add('b-off'); }
  }
  c.addEventListener('click', ()=> setActive(!active));
  document.body.addEventListener('mouseenter', ()=>{inside=true; document.body.classList.remove('dim');});
  document.body.addEventListener('mouseleave', ()=>{inside=false; document.body.classList.add('dim');});
  document.body.classList.add('dim');
})();
</script>
</html>
"""

class _SharedState:
    def __init__(self):
        self.active = False
        self.session_start: Optional[dt.datetime] = None
        self.lock = threading.Lock()

STATE = _SharedState()

class Handler(BaseHTTPRequestHandler):
    def _json(self, obj: dict, code: int = 200):
        data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers(); self.wfile.write(data)

    def do_GET(self):
        p = urlparse(self.path)
        if p.path == '/':
            page = HTML_PAGE.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(page)))
            self.end_headers(); self.wfile.write(page); return
        if p.path == '/api/toggle':
            qs = parse_qs(p.query); state = (qs.get('state', [''])[0]).lower()
            now = dt.datetime.now()
            with STATE.lock:
                if state == 'on' and not STATE.active:
                    STATE.active = True; STATE.session_start = now
                elif state == 'off' and STATE.active:
                    start = STATE.session_start or now
                    STATE.active = False; STATE.session_start = None
                    log_session_range(start, now)
            self._json({'ok': True}); return
        if p.path == '/api/quit':
            with STATE.lock:
                if STATE.active and STATE.session_start:
                    log_session_range(STATE.session_start, dt.datetime.now())
                    STATE.active = False; STATE.session_start = None
            write_daily_summary_for(dt.date.today())
            def _shutdown(server): time.sleep(0.1); server.shutdown()
            threading.Thread(target=_shutdown, args=(self.server,), daemon=True).start()
            self._json({'ok': True}); return
        self.send_response(404); self.end_headers()


def run_web_ui(host: str = '127.0.0.1', port: int = 0):
    httpd = HTTPServer((host, port), Handler)
    url = f'http://{host}:{httpd.server_address[1]}/'
    start_midnight_summary_thread()
    try: webbrowser.open(url, new=1)
    except Exception: pass
    print(f"Web UI running at: {url}")
    try: httpd.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        with STATE.lock:
            if STATE.active and STATE.session_start:
                log_session_range(STATE.session_start, dt.datetime.now())
                STATE.active = False; STATE.session_start = None
        write_daily_summary_for(dt.date.today())

# --------------------------- Tests ---------------------------

def _run_tests():
    import tempfile, unittest
    class T(unittest.TestCase):
        def setUp(self):
            self.tmp = tempfile.TemporaryDirectory()
            os.environ['APP_LOG_DIR'] = self.tmp.name
            global LOG_DIR; LOG_DIR = os.environ['APP_LOG_DIR']
            os.makedirs(LOG_DIR, exist_ok=True)
        def tearDown(self): self.tmp.cleanup()
        def test_nowruz_1402(self): self.assertEqual(gregorian_to_jalali(2023,3,21),(1402,1,1))
        def test_fmt(self):
            self.assertEqual(fmt_hm(dt.timedelta(minutes=165), fa=False),'2:45')
            self.assertEqual(fmt_hms(dt.timedelta(seconds=5), fa=False),'0:00:05')
        def test_parse_legacy_and_new(self):
            self.assertEqual(parse_duration_to_seconds('… مدت: ۰:۴۸'), 48*60)
            self.assertEqual(parse_duration_to_seconds('… مدت: ۰:۰۰:۰۵'), 5)
        def test_log_split_midnight_seconds(self):
            s = dt.datetime(2023,3,21,23,59,50); e = dt.datetime(2023,3,22,0,0,5)
            log_session_range(s,e)
            p1 = today_log_path(s.date()); p2 = today_log_path(e.date())
            self.assertEqual(compute_total_seconds_for_file(p1), 10)
            self.assertEqual(compute_total_seconds_for_file(p2), 5)
        def test_summary_seconds(self):
            d = dt.date(2023,3,21)
            log_session_range(dt.datetime(2023,3,21,8,0,0), dt.datetime(2023,3,21,8,0,30))
            write_daily_summary_for(d)
            with open(today_log_path(d),'r',encoding='utf-8') as f:
                self.assertIn('مجموع', f.read())
    res = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(T))
    if not res.wasSuccessful(): sys.exit(1)

# --------------------------- Entry ---------------------------

def main():
    if '--test' in sys.argv: _run_tests(); return
    forced_web = '--force-web' in sys.argv; forced_tk = '--force-tk' in sys.argv
    started = False
    if forced_tk:
        started = run_tkinter_ui()
        if not started: run_web_ui(); return
    elif not forced_web:
        started = run_tkinter_ui()
        if started: return
    run_web_ui()

if __name__ == '__main__':
    main()
