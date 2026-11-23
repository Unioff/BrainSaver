# BrainSaver

BrainSaver is a small Windows app that reminds you to rest your eyes. Every
20 minutes it shows a big, friendly circle on your other screen, asks you to
look away at something far away, and then gets out of your way.

Staring at a screen for a long time makes you blink less and strains your
eyes. The usual advice is the "20-20-20" rule: every 20 minutes, look at
something about 20 feet (6 metres) away for 20 seconds. BrainSaver does the
reminding for you.

## What you can expect

BrainSaver stays out of your way. It has no main window and no sound.

1. **Every 20 minutes** a big red circle appears on your other monitor with
   the words *"Spočij si oči"* ("Rest your eyes"). It grows, stays for a few
   seconds, and fades out.
2. At the same time a **small red dot** appears in the top-right corner of
   that monitor and blinks gently. It stays until you deal with it.
3. **Click the small dot** when you are ready to rest. A 20-second countdown
   ("Poglej stran: N s", meaning "Look away: N s") appears next to it.
4. **When the countdown ends**, the dot grows into a large green circle that
   says *"Oči so spočite"* ("Your eyes are rested"). Look out the window while
   it is up. It stays for a few seconds, grows across the screen as it fades,
   and then disappears.
5. The 20-minute timer starts again.

You can click either big circle to dismiss it early.

### Breaks and idle time

- If there is **no keyboard or mouse input for 5 minutes**, BrainSaver assumes
  you have stepped away. It removes the dot and pauses the timer.
- When you come back, the 20-minute timer starts again.
- After you **unlock Windows**, the timer restarts from zero.

### Quitting

- **Right-click the small dot** to quit BrainSaver.
- Or use the **tray icon** menu (the red dot in the notification area) and
  choose *Quit*. The tray tooltip also shows the time until the next reminder
  and how long you have been idle.

## Requirements

- Windows (it uses Windows-specific calls for idle time, the lock screen and
  the monitor layout).
- Python 3.11 or newer with tkinter, which is included in the standard
  Windows installer.
- Optional, for the tray icon: `pip install pystray pillow`. Without them the
  app still works; the tray icon is simply not shown.

## Running it

```
pythonw BrainSaver.py
```

`pythonw` runs without a console window. Only one copy can run at a time; a
second copy quits silently.

### Start it automatically at logon

`BrainSaver.xml` is a Windows Task Scheduler export. It starts BrainSaver at
logon and again when the session is unlocked. Import it with Task Scheduler
(*Action → Import Task…*). It assumes Python is at
`C:\Program Files\Python\3.11.9\pythonw.exe` and the script is at
`C:\Users\jerajp\_SANDBOX\BrainSaver\BrainSaver.py`, so edit the task's
*Actions* tab if your paths are different.

## Settings

Edit the constants at the top of `BrainSaver.py`:

| Setting | Default | What it does |
| --- | --- | --- |
| `TEST` | `True` | `True` uses short test timings (10 s reminder, 3 s countdown). Set to `False` for the real timings below. |
| `REMINDER_INTERVAL_MS` | 20 min (when `TEST` is `False`) | Time between reminders. |
| `COUNTDOWN_SECONDS` | 20 (when `TEST` is `False`) | Length of the countdown after clicking the dot. |
| `IDLE_TIMEOUT_MS` | 5 min | Inactivity time before the timer pauses. |
| `BALL_HOLD_MS` | 4 s | How long a big circle stays fully visible. |
| `BALL_PEAK_ALPHA` | 0.85 | How opaque the circles get (0 = invisible, 1 = solid). |
| `START_BALL_TEXT` / `END_BALL_TEXT` | see file | Text on the red and green circles. |

> **Note:** `TEST` is currently `True`, so the app runs with the short test
> timings. Set it to `False` for normal use.

## Troubleshooting

- **Nothing happens.** Check that the app is running (look for the tray icon
  or the process in Task Manager). Running it again while it is already
  running does nothing.
- **The tray icon is missing.** Install `pystray` and `pillow`.
- **The circles appear on the wrong monitor.** BrainSaver uses the monitor
  that is *not* the primary one, and falls back to the primary monitor if you
  have only one.
- **The screen flashes.** Please report it with a description of when it
  happens; this is still being tuned.
