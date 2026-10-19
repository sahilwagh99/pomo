<div align="center">

<img src="docs/icon.png" alt="FocusOrb logo" width="96">

# FocusOrb

**A floating, circular Pomodoro timer for Windows, with a focus dashboard.**

<a href="../../releases/latest/download/FocusOrb-Setup.exe"><img src="https://img.shields.io/badge/Download%20for%20Windows-d95926?style=for-the-badge" alt="Download FocusOrb for Windows" height="40"></a>

<sub>Windows 10 or 11 (64-bit) &middot; free &middot; no admin rights needed &middot; <a href="../../releases/latest/download/FocusOrb-portable.zip">portable zip</a> &middot; <a href="../../releases">all releases</a></sub>

<br><br>

<img src="docs/orb.png" alt="The FocusOrb timer while focusing, on hover, and when a session ends" width="760">

</div>

## Install

1. Click **Download for Windows** above. You get `FocusOrb-Setup.exe`, and there is no need to download the source code.
2. Run it. The installer is not code-signed, so Windows SmartScreen may say *"Windows protected your PC"*. Click **More info**, then **Run anyway**.
3. Choose whether to add a desktop shortcut and to start FocusOrb when you sign in, then click **Finish**.

The timer appears in the bottom-right corner, just above the taskbar.

To update, download and run the newest installer; it replaces the old version and keeps your history. To uninstall, go to *Settings > Apps > Installed apps > FocusOrb*. You will be asked whether to keep your history.

Prefer no installer? Download the [portable zip](../../releases/latest/download/FocusOrb-portable.zip), extract it anywhere and run `FocusOrb.exe`.

## Features

- **Floating round timer.** It is always on top and can be dragged anywhere. It snaps to screen edges and remembers its position. It can also fade while you focus.
- **Editable time.** Double-click the time and type `30`, `45:00` or `1:30:00`, or scroll over it to add or remove minutes.
- **Pomodoro turns.** Dots show your place in the cycle before the long break, and *3/8 today* tracks your daily goal.
- **Alerts when a session ends:**
  - a Windows notification
  - a sound (four built-in, with volume and repeat settings)
  - a pop-up card with *Start break* / *Skip* buttons
  - a glowing pulse on the timer
- **Tasks.** Tag focus sessions with what you are working on, and see where your time went.
- **Dashboard.** KPIs, charts, a session log and CSV export (see below).
- **Customizable.** Durations, cycle length, daily goal, auto-start, dark or light theme, size, opacity, and a color for each mode.
- **Idle reminder.** An optional nudge when no timer has run for a while.

<div align="center"><img src="docs/popup.png" alt="Pop-up card shown when a session ends" width="380"></div>

## Using the timer

| Action | How |
|---|---|
| Start / pause | Hover, then click the round play button, or press **Space** |
| Reset / skip | Hover buttons, or **R** / **S** |
| Edit the time | Double-click the time and type a value. When the timer is idle, whole minutes become the new default for that mode. While it is running, this changes the time left. |
| Nudge the time | Scroll over the timer: 1 min per notch (Shift or Ctrl: 5 min), or **Up** / **Down** |
| Move it | Drag anywhere on the circle |
| Everything else | Right-click: switch mode, add time, set task, daily goal, dashboard, settings, hide, quit |
| Tray icon | Click to bring the timer back; double-click for the dashboard |

## Dashboard

Open it from the chart button on the timer, the right-click menu or the tray icon.

<img src="docs/dashboard.png" alt="FocusOrb dashboard" width="100%">

- **Today:** focus time, and pomodoros against your goal.
- **KPIs:** focus time and pomodoros (with change vs the previous period), average per active day, completion rate, current and best streak, peak hour, best day, and break time.
- **Charts:** daily focus with a goal line, a consistency heatmap, top tasks, time of day and weekday rhythm.
- **Session log:** a table of every session, with CSV export.
- **Range filter:** 7 days, 30 days, 90 days or all time.

## Settings

| Tab | What you can change |
|---|---|
| Timer | Focus, short and long break lengths; long break interval; daily goal; auto-start for breaks and for focus |
| Alerts | Notifications, pop-up card, sound, volume, repeats, idle reminder |
| Appearance | Dark or light theme, timer size, opacity, fade while focusing, always on top, show task, colors for each mode |
| General | Start with Windows, reset position, open data folder, export CSV, clear history |

Your settings and history are stored in `%APPDATA%\FocusOrb` (`settings.json` and `history.db`).

## For developers

### Run from source

```bat
run.bat            :: creates .venv on first run, then starts the app
run.bat --demo     :: separate data folder pre-filled with sample history
```

Run the tests with `python -m unittest discover -s tests -t .`.

### Build the installer locally

Install [Inno Setup 6](https://jrsoftware.org/isinfo.php) (`winget install JRSoftware.InnoSetup`), then run:

```bat
build.bat
```

This runs the tests, then writes two files:

- `dist\FocusOrb-Setup.exe`, the installer
- `dist\FocusOrb-portable.zip`, the portable build

### Publish a release

The workflow in [`.github/workflows/release.yml`](.github/workflows/release.yml) builds the installer on every push. When you push a version tag, it also publishes a GitHub Release:

1. Bump `APP_VERSION` in [`pomodoro/__init__.py`](pomodoro/__init__.py) and commit.
2. Tag and push the tag:
   ```bash
   git tag v1.0.1
   git push origin v1.0.1
   ```
3. GitHub Actions builds `FocusOrb-Setup.exe` and `FocusOrb-portable.zip` and attaches them to the release.

The download button at the top of this README always points to the latest release, so it needs no edits.

### Project layout

```
main.py                     entry point, single-instance guard
pomodoro/engine.py          timer state machine (monotonic clock, cycles, logging)
pomodoro/orb.py             the floating circular widget
pomodoro/toast.py           pop-up card beside the timer
pomodoro/dashboard.py       dashboard window
pomodoro/charts.py          hand-painted charts (no matplotlib, keeps the build small)
pomodoro/settings_dialog.py settings window
pomodoro/app.py             controller: tray, menus, notifications, wiring
pomodoro/storage.py         SQLite history
pomodoro/stats.py           KPI calculations
pomodoro/sounds.py          alert sounds synthesized at runtime
pomodoro/theme.py           color tokens and stylesheet
installer/FocusOrb.iss      Inno Setup installer script
tools/prepare_build.py      generates the .exe icon and version info
tests/                      unit tests
```
