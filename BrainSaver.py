"""
BrainSaver - eye-break reminder.

Every 20 minutes (counted from the last time the ball was closed) a small
red 20x20 px dot appears in the top-right corner of the primary monitor.

  * Dot appears    -> a large red ball saying "Spočij si oči" grows, holds
                      and fades; the small dot keeps blinking.
  * Click the dot  -> a 20 second countdown starts next to it.
  * Countdown ends -> the dot blows up into a large, semi-transparent ball
                      telling the user to look out the window.
  * The ball stays for a few seconds, fades away, and the timer restarts.
  * If there is no keyboard/mouse input for 5 minutes, the dot is removed
    and the timer is paused until the user returns; then the 20 minute
    timer starts again.

Right-click the dot to quit BrainSaver.
"""

import math
import sys
import threading
import time
import tkinter as tk

try:  # tray icon is optional: pip install pystray pillow
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False

TEST = False                      # True = short timings for testing

DOT_SIZE = 50                     # px
DOT_MARGIN = 0                    # px from the top-right corner
IDLE_CHECK_MS = 1000              # how often to check for user activity
BLINK_COUNT = 3                   # blinks per round
BLINK_MIN_ALPHA = 0.3             # dot opacity at the bottom of a blink
BLINK_STEP_MS = 150   
IDLE_TIMEOUT_MS = 5 * 60 * 1000        # 5 min without keyboard/mous
BLINK_REPEAT_MS = 5 * 1000            # 5 s between blink rounds            # time per opacity change (fast blink)
if TEST:
    REMINDER_INTERVAL_MS = 10 * 1000   # 10 s
    COUNTDOWN_SECONDS = 3

else:
    REMINDER_INTERVAL_MS = 20 * 60 * 1000  # 20 min
    COUNTDOWN_SECONDS = 20

BALL_MAX_SIZE = 320               # px, final size of the ball
BALL_PEAK_ALPHA = 0.85            # 0 = invisible, 1 = opaque; reached while holding
ANIM_STEPS = 30
ANIM_FRAME_MS = 16
BALL_HOLD_MS = 4000               # how long the full ball stays before fading
FADE_STEPS = 30
FADE_FRAME_MS = 50                # ~1.5 s fade-out in total
FADE_ALPHA_FRACTION = 0.4         # opacity hits 0 after 40% of the fade

RED = "#e03131"
BALL_COLOR = "#2f9e44"            # big ball is green
BALL_OUTLINE = "#8ce99a"
START_BALL_COLOR = RED           # big red ball shown when the dot appears
START_BALL_OUTLINE = "#ffa8a8"
START_BALL_TEXT = "Spočij si oči"
END_BALL_TEXT = "Oči so spočite"
KEY_COLOR = "#010203"             # transparent key colour (Windows)
LABEL_BG = "#222222"
LABEL_FG = "#ffffff"


def make_dpi_aware():
    """Make Windows report physical pixels so 20 px really is 20 px."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def _make_tray_image(size=64):
    """Red dot icon for the system tray."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((4, 4, size - 4, size - 4), fill=RED)
    return img


def get_idle_ms():
    """Milliseconds since the last keyboard/mouse input (0 if unknown)."""
    if sys.platform != "win32":
        return 0
    try:
        import ctypes
        from ctypes import wintypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0
        tick = ctypes.windll.kernel32.GetTickCount()
        return (tick - info.dwTime) & 0xFFFFFFFF
    except Exception:
        return 0


def is_workstation_locked():
    """True while the Windows lock screen is shown.

    While locked, the interactive input desktop cannot be opened.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.OpenInputDesktop.restype = ctypes.c_void_p
        user32.CloseDesktop.argtypes = [ctypes.c_void_p]
        DESKTOP_SWITCHDESKTOP = 0x0100
        hdesk = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
        if not hdesk:
            return True
        user32.CloseDesktop(hdesk)
        return False
    except Exception:
        return False


def get_other_monitor_rect():
    """(left, top, right, bottom) of the first non-primary monitor, or None."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class RECT(ctypes.Structure):
            _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                        ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                        ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]

        user32 = ctypes.windll.user32
        proc_type = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR,
                                       wintypes.HDC, ctypes.POINTER(RECT),
                                       wintypes.LPARAM)
        found = []

        def _cb(hmon, hdc, lprc, data):
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            user32.GetMonitorInfoW(hmon, ctypes.byref(info))
            r = info.rcMonitor
            is_primary = bool(info.dwFlags & 1)  # MONITORINFOF_PRIMARY
            if not is_primary:
                found.append((r.left, r.top, r.right, r.bottom))
            return 1

        user32.EnumDisplayMonitors(None, None, proc_type(_cb), 0)
        return found[0] if found else None
    except Exception:
        return None


class Ball:
    """A big circle that grows from dot size, shows a text, holds, then fades
    out. Used for the red start ball and the green end ball."""

    def __init__(self, app, color, outline, text, on_close=None):
        self.app = app
        self.color = color
        self.outline = outline
        self.text = text
        self.on_close = on_close
        self.win = None
        self.canvas = None
        self.job = None
        self.closed = False

    def start(self):
        # One window covering the whole monitor, created once and never
        # resized: the ball is drawn centred in it, so no window changes size
        # or moves while it grows and fades.
        self.rect = self.app._other_monitor()
        left, top, right, bottom = self.rect
        self.w, self.h = right - left, bottom - top
        self.win, self.canvas = self.app._overlay(self.w, self.h, left, top)
        self.win.attributes("-alpha", 0.0)  # fade in from invisible
        self.canvas.bind("<Button-1>", lambda e: self.close())
        self._animate(0)
        self.win.deiconify()  # first frame is already drawn at zero opacity

    def _draw(self, cx, cy, radius, with_text):
        c = self.canvas
        c.delete("all")
        c.create_oval(cx - radius, cy - radius, cx + radius, cy + radius,
                      fill=self.color, outline=self.outline, width=4)
        if with_text:
            c.create_text(cx, cy, text=self.text,
                          fill="white", font=("Segoe UI", 18, "bold"),
                          justify="center")

    def _animate(self, step):
        self.job = None
        if self.canvas is None:
            return
        t = step / ANIM_STEPS
        ease = 1 - (1 - t) ** 3              # ease-out cubic
        d = int(DOT_SIZE + (BALL_MAX_SIZE - DOT_SIZE) * ease)
        self._draw(self.w / 2, self.h / 2, d / 2,
                   with_text=step >= ANIM_STEPS)
        # fade in while growing: opacity ramps from 0 up to BALL_PEAK_ALPHA
        self.win.attributes("-alpha", BALL_PEAK_ALPHA * ease)
        if step < ANIM_STEPS:
            self.job = self.app.root.after(ANIM_FRAME_MS,
                                           self._animate, step + 1)
        else:
            self.job = self.app.root.after(BALL_HOLD_MS, self._fade, 0)

    def _fade(self, step):
        """Grow the ball across the whole monitor while it fades out."""
        self.job = None
        if self.win is None:
            return
        if step >= FADE_STEPS:
            self.close()
            return
        t = (step + 1) / FADE_STEPS
        ease = 1 - (1 - t) ** 3              # ease-out cubic
        start_r = BALL_MAX_SIZE / 2
        end_r = math.hypot(self.w, self.h) / 2   # reaches the screen corners
        radius = start_r + (end_r - start_r) * ease

        self._draw(self.w / 2, self.h / 2, radius, with_text=True)
        # opacity reaches zero early (FADE_ALPHA_FRACTION) while growth continues
        alpha = BALL_PEAK_ALPHA * max(0.0, 1 - t / FADE_ALPHA_FRACTION)
        self.win.attributes("-alpha", alpha)
        self.job = self.app.root.after(FADE_FRAME_MS, self._fade, step + 1)

    def discard(self):
        """Remove the ball immediately, without calling on_close."""
        if self.job is not None:
            try:
                self.app.root.after_cancel(self.job)
            except tk.TclError:
                pass
            self.job = None
        self.app._destroy(self.win)
        self.win = None
        self.canvas = None

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.discard()
        if self.on_close is not None:
            self.on_close()


class BrainSaver:
    def __init__(self, root):
        self.root = root
        self.root.withdraw()  # no main window; everything is an overlay

        self.dot_win = None
        self.countdown_win = None
        self.start_ball = None       # red ball shown when the dot appears
        self.ball = None             # green ball shown after the countdown

        self.reminder_job = None
        self.countdown_job = None
        self.idle_job = None
        self.blink_job = None
        self.idle = False
        self.locked = False
        self.seconds_left = 0

        self.status = "Starting"
        self.next_dot_at = None      # time.monotonic() when the next dot is due
        self.tray = None
        self.tray_job = None
        self._quit_requested = False

        self.schedule_reminder(REMINDER_INTERVAL_MS)
        self._check_idle()
        self._start_tray()

    def restart_timers(self):
        """After a Windows unlock: clear the current reminder and start the
        20 minute timer again from now."""
        for job in (self.countdown_job, self.blink_job):
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except tk.TclError:
                    pass
        self.countdown_job = None
        self.blink_job = None
        self._destroy(self.countdown_win)
        self.countdown_win = None
        for ball in (self.start_ball, self.ball):
            if ball is not None:
                ball.discard()
        self.start_ball = None
        self.ball = None
        self.hide_dot()
        self.idle = False
        self.schedule_reminder(REMINDER_INTERVAL_MS)

    def _check_idle(self):
        """Pause the reminder while the PC is idle; restart it on return.
        Also restarts the timers when the Windows lock screen is dismissed."""
        locked = is_workstation_locked()
        if self.locked and not locked:
            self.restart_timers()
        self.locked = locked

        idle_ms = get_idle_ms()
        if not self.idle and idle_ms >= IDLE_TIMEOUT_MS:
            self.idle = True
            self.status = "Paused - no input"
            self.next_dot_at = None
            self.hide_dot()
            if self.reminder_job is not None:
                self.root.after_cancel(self.reminder_job)
                self.reminder_job = None
        elif self.idle and idle_ms < IDLE_TIMEOUT_MS:
            self.idle = False
            self.schedule_reminder(REMINDER_INTERVAL_MS)
        self.idle_job = self.root.after(IDLE_CHECK_MS, self._check_idle)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _overlay(self, width, height, x, y):
        """Borderless, always-on-top window with a transparent background.
        Returned hidden; the caller calls deiconify() once it is drawn."""
        win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=KEY_COLOR)
        try:
            win.attributes("-transparentcolor", KEY_COLOR)
        except tk.TclError:
            pass  # not supported on this platform; background shows instead
        win.geometry(f"{width}x{height}+{x}+{y}")
        canvas = tk.Canvas(win, width=width, height=height, bg=KEY_COLOR,
                           highlightthickness=0, bd=0)
        canvas.pack()
        return win, canvas

    def _destroy(self, win):
        if win is not None:
            try:
                win.destroy()
            except tk.TclError:
                pass

    # ------------------------------------------------------------------
    # Stage 1: the red dot
    # ------------------------------------------------------------------
    def schedule_reminder(self, delay_ms):
        if self.reminder_job is not None:
            self.root.after_cancel(self.reminder_job)
        self.reminder_job = self.root.after(delay_ms, self.show_dot)
        self.next_dot_at = time.monotonic() + delay_ms / 1000
        self.status = "Waiting for next reminder"

    def _other_monitor(self):
        """(left, top, right, bottom) of the other monitor
        (falls back to the primary monitor if there is only one)."""
        rect = get_other_monitor_rect()
        if rect is None:
            rect = (0, 0, self.root.winfo_screenwidth(),
                    self.root.winfo_screenheight())
        return rect

    def _dot_position(self):
        """Top-left corner of the dot: top-right of the other monitor."""
        left, top, right, bottom = self._other_monitor()
        return right - DOT_SIZE - DOT_MARGIN, top + DOT_MARGIN

    def show_dot(self):
        self.reminder_job = None
        if self.dot_win is not None:
            return
        self._show_start_ball()  # created first so the small dot stays on top
        x, y = self._dot_position()
        win, canvas = self._overlay(DOT_SIZE, DOT_SIZE, x, y)
        canvas.create_oval(0, 0, DOT_SIZE - 1, DOT_SIZE - 1,
                           fill=RED, outline=RED)
        canvas.bind("<Button-1>", lambda e: self.start_countdown())
        canvas.bind("<Button-3>", lambda e: self.quit())
        win.deiconify()  # show only now that the dot is drawn
        self.dot_win = win
        self.status = "Dot shown - click to start"
        self.next_dot_at = None
        self._start_blink()

    def _show_start_ball(self):
        if self.start_ball is not None:
            return
        self.start_ball = Ball(self, START_BALL_COLOR, START_BALL_OUTLINE,
                               START_BALL_TEXT,
                               on_close=self._start_ball_closed)
        self.start_ball.start()

    def _start_ball_closed(self):
        self.start_ball = None

    def hide_dot(self):
        if self.blink_job is not None:
            self.root.after_cancel(self.blink_job)
            self.blink_job = None
        self._destroy(self.dot_win)
        self.dot_win = None

    def _start_blink(self):
        """Blink the dot BLINK_COUNT times, wait BLINK_REPEAT_MS, repeat."""
        self.blink_job = None
        if self.dot_win is None:
            return
        steps = []
        for _ in range(BLINK_COUNT):
            steps += [BLINK_MIN_ALPHA, 1.0]
        self._blink_step(steps, 0)

    def _blink_step(self, steps, i):
        self.blink_job = None
        if self.dot_win is None:
            return
        if i < len(steps):
            self.dot_win.attributes("-alpha", steps[i])
            self.blink_job = self.root.after(BLINK_STEP_MS,
                                             self._blink_step, steps, i + 1)
        else:
            self.dot_win.attributes("-alpha", 1.0)
            self.blink_job = self.root.after(BLINK_REPEAT_MS,
                                             self._start_blink)

    # ------------------------------------------------------------------
    # Stage 2: 20 second countdown next to the dot
    # ------------------------------------------------------------------
    def start_countdown(self):
        if self.countdown_job is not None or self.countdown_win is not None:
            return
        dot_x, dot_y = self._dot_position()
        self.hide_dot()
        self.status = "Countdown running"
        self.next_dot_at = None

        width, height = 170, 26
        x = dot_x - width - 6 - 200   # 200 px further left than before
        y = dot_y
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.geometry(f"{width}x{height}+{x}+{y}")
        self.countdown_label = tk.Label(win, bg=LABEL_BG, fg=LABEL_FG,
                                        font=("Segoe UI", 10, "bold"))
        self.countdown_label.pack(fill="both", expand=True)
        self.countdown_win = win

        self.seconds_left = COUNTDOWN_SECONDS
        self._tick()

    def _tick(self):
        if self.seconds_left > 0:
            self.countdown_label.config(
                text=f"Poglej stran: {self.seconds_left} s")
            self.seconds_left -= 1
            self.countdown_job = self.root.after(1000, self._tick)
        else:
            self.countdown_job = None
            self._destroy(self.countdown_win)
            self.countdown_win = None
            self.blow_up()

    # ------------------------------------------------------------------
    # Stage 3: the dot blows up into a big transparent ball
    # ------------------------------------------------------------------
    def blow_up(self):
        self.status = "Ball showing"
        self.ball = Ball(self, BALL_COLOR, BALL_OUTLINE, END_BALL_TEXT,
                         on_close=self._ball_closed)
        self.ball.start()

    def _ball_closed(self):
        self.ball = None
        if not self.idle:  # if idle, the timer restarts when the user returns
            self.schedule_reminder(REMINDER_INTERVAL_MS)
        else:
            self.status = "Paused - no input"

    # ------------------------------------------------------------------
    # Tray icon: status and countdown to the next reminder
    # ------------------------------------------------------------------
    def _start_tray(self):
        if not TRAY_AVAILABLE:
            return
        menu = pystray.Menu(
            pystray.MenuItem(lambda item: self._tray_status_text(), None,
                             enabled=False),
            pystray.MenuItem("Quit", lambda icon, item: self._request_quit()),
        )
        self.tray = pystray.Icon("BrainSaver", _make_tray_image(),
                                 "BrainSaver", menu)
        threading.Thread(target=self.tray.run, daemon=True).start()
        self._update_tray()

    def _request_quit(self):
        # Runs on the tray thread: only set a flag, Tk is not thread-safe.
        self._quit_requested = True

    def _tray_status_text(self):
        text = self.status
        if self.next_dot_at is not None:
            left = max(0, int(self.next_dot_at - time.monotonic()))
            text += f" - next dot in {left // 60:02d}:{left % 60:02d}"
        idle_s = get_idle_ms() // 1000
        text += f" - user idle {idle_s // 60:02d}:{idle_s % 60:02d}"
        return text

    def _update_tray(self):
        self.tray_job = None
        if self._quit_requested:
            self.quit()
            return
        if self.tray is not None:
            try:
                self.tray.title = "BrainSaver: " + self._tray_status_text()
            except Exception:
                pass
        self.tray_job = self.root.after(1000, self._update_tray)

    # ------------------------------------------------------------------
    def quit(self):
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:
                pass
        for job in (self.reminder_job, self.countdown_job,
                    self.idle_job, self.blink_job, self.tray_job):
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except tk.TclError:
                    pass
        for ball in (self.start_ball, self.ball):
            if ball is not None:
                ball.discard()
        for win in (self.dot_win, self.countdown_win):
            self._destroy(win)
        self.root.destroy()


_single_instance_mutex = None  # keep a reference so the mutex lives as long as the app


def acquire_single_instance():
    """True if this is the only running BrainSaver (per Windows session).

    Uses a named mutex. Windows releases it automatically when the process
    exits, even after a crash, so it can never get stuck.
    """
    global _single_instance_mutex
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        ERROR_ALREADY_EXISTS = 183
        _single_instance_mutex = kernel32.CreateMutexW(
            None, False, "Local\\BrainSaverSingleInstance")
        return kernel32.GetLastError() != ERROR_ALREADY_EXISTS
    except Exception:
        return True


def main():
    make_dpi_aware()
    if not acquire_single_instance():
        return  # another copy is already running: quit silently
    root = tk.Tk()
    BrainSaver(root)
    root.mainloop()


if __name__ == "__main__":
    main()
