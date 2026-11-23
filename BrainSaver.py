"""
BrainSaver - eye-break reminder.

Every 20 minutes (counted from the last time the ball was closed) a small
red 20x20 px dot appears in the top-right corner of the primary monitor.

  * Click the dot  -> a 20 second countdown starts next to it.
  * Countdown ends -> the dot blows up into a large, semi-transparent ball
                      telling the user to look out the window.
  * The ball stays for a few seconds, fades away, and the timer restarts.
  * If there is no keyboard/mouse input for 5 minutes, the dot is removed
    and the timer is paused until the user returns; then the 20 minute
    timer starts again.

Right-click the dot to quit BrainSaver.
"""

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
BLINK_REPEAT_MS = 20 * 1000            # 20 s between blink rounds            # time per opacity change (fast blink)
if TEST:
    REMINDER_INTERVAL_MS = 10 * 1000   # 10 s
    COUNTDOWN_SECONDS = 3

else:
    REMINDER_INTERVAL_MS = 20 * 60 * 1000  # 20 min
    COUNTDOWN_SECONDS = 20

BALL_MAX_SIZE = 320               # px, final size of the ball
BALL_ALPHA = 0.45                 # 0 = invisible, 1 = opaque
ANIM_STEPS = 30
ANIM_FRAME_MS = 16
BALL_HOLD_MS = 4000               # how long the full ball stays before fading
FADE_STEPS = 30
FADE_FRAME_MS = 50                # ~1.5 s fade-out in total

RED = "#e03131"
BALL_COLOR = "#2f9e44"            # big ball is green
BALL_OUTLINE = "#8ce99a"
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


class BrainSaver:
    def __init__(self, root):
        self.root = root
        self.root.withdraw()  # no main window; everything is an overlay

        self.dot_win = None
        self.countdown_win = None
        self.ball_win = None
        self.ball_canvas = None

        self.reminder_job = None
        self.countdown_job = None
        self.anim_job = None
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
        for job in (self.countdown_job, self.anim_job, self.blink_job):
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except tk.TclError:
                    pass
        self.countdown_job = None
        self.anim_job = None
        self.blink_job = None
        self._destroy(self.countdown_win)
        self.countdown_win = None
        self._destroy(self.ball_win)
        self.ball_win = None
        self.ball_canvas = None
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
        """Borderless, always-on-top window with a transparent background."""
        win = tk.Toplevel(self.root)
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
        x, y = self._dot_position()
        win, canvas = self._overlay(DOT_SIZE, DOT_SIZE, x, y)
        canvas.create_oval(0, 0, DOT_SIZE - 1, DOT_SIZE - 1,
                           fill=RED, outline=RED)
        canvas.bind("<Button-1>", lambda e: self.start_countdown())
        canvas.bind("<Button-3>", lambda e: self.quit())
        self.dot_win = win
        self.status = "Dot shown - click to start"
        self.next_dot_at = None
        self._start_blink()

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
        left, top, right, bottom = self._other_monitor()
        x = left + ((right - left) - BALL_MAX_SIZE) // 2
        y = top + ((bottom - top) - BALL_MAX_SIZE) // 2
        win, canvas = self._overlay(BALL_MAX_SIZE, BALL_MAX_SIZE, x, y)
        win.attributes("-alpha", BALL_ALPHA)
        canvas.bind("<Button-1>", lambda e: self.close_ball())
        self.ball_win = win
        self.ball_canvas = canvas
        self._animate(0)

    def _animate(self, step):
        self.anim_job = None
        if self.ball_canvas is None:
            return
        t = step / ANIM_STEPS
        ease = 1 - (1 - t) ** 3              # ease-out cubic
        d = int(DOT_SIZE + (BALL_MAX_SIZE - DOT_SIZE) * ease)
        offset = (BALL_MAX_SIZE - d) // 2

        c = self.ball_canvas
        c.delete("all")
        c.create_oval(offset, offset, offset + d, offset + d,
                      fill=BALL_COLOR, outline=BALL_OUTLINE, width=4)
        if step < ANIM_STEPS:
            self.anim_job = self.root.after(ANIM_FRAME_MS,
                                            self._animate, step + 1)
        else:
            c.create_text(BALL_MAX_SIZE // 2, BALL_MAX_SIZE // 2,
                          text="Oči so spočite",
                          fill="white", font=("Segoe UI", 18, "bold"),
                          justify="center")
            self.anim_job = self.root.after(BALL_HOLD_MS, self._fade, 0)

    def _fade(self, step):
        self.anim_job = None
        if self.ball_win is None:
            return
        if step == 0:
            self.status = "Ball fading"
        if step >= FADE_STEPS:
            self.close_ball()
            return
        self.ball_win.attributes("-alpha",
                                 BALL_ALPHA * (1 - step / FADE_STEPS))
        self.anim_job = self.root.after(FADE_FRAME_MS, self._fade, step + 1)

    def close_ball(self):
        if self.anim_job is not None:
            self.root.after_cancel(self.anim_job)
            self.anim_job = None
        self._destroy(self.ball_win)
        self.ball_win = None
        self.ball_canvas = None
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
        for job in (self.reminder_job, self.countdown_job, self.anim_job,
                    self.idle_job, self.blink_job, self.tray_job):
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except tk.TclError:
                    pass
        for win in (self.dot_win, self.countdown_win, self.ball_win):
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
