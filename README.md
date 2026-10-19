<div align="center">

<img src="docs/icon.png" alt="Pomo, a little matcha mochi with a sakura blossom" width="96">

# Pomo

**A cozy Pomodoro timer for Windows, starring Pomo the matcha mochi. It comes with a focus dashboard, little games, and an eye on your coding agents.**

<a href="../../releases/latest/download/Pomo-Setup.exe"><img src="https://img.shields.io/badge/Download%20for%20Windows-6f9d60?style=for-the-badge" alt="Download Pomo for Windows" height="40"></a>

<sub>Windows 10 or 11 (64-bit) &middot; free &middot; no admin rights needed &middot; <a href="../../releases/latest/download/Pomo-portable.zip">portable zip</a> &middot; <a href="../../releases">all releases</a></sub>

<br><br>

<img src="docs/hero.png" alt="Pomo's island at the top of the screen, with the first-launch card: Keep the island or Switch to orb" width="503">

</div>

## Install

1. Click **Download for Windows** above. You get `Pomo-Setup.exe`, and there is no need to download the source code.
2. Run it. The installer is not code-signed, so Windows SmartScreen may say *"Windows protected your PC"*. Click **More info**, then **Run anyway**.
3. Choose whether you want a desktop shortcut and whether Pomo should run on startup, then click **Finish**.

Pomo opens as a little island at the top of your screen and says hi. Prefer a round timer you can drag anywhere? Click **Switch to orb** on the welcome card, or the style button on the timer whenever you like.

- **Update:** download and run the newest installer. It replaces the old version and keeps your history.
- **Uninstall:** *Settings > Apps > Installed apps > Pomo*. You will be asked whether to keep your history.
- **No installer:** download the [portable zip](../../releases/latest/download/Pomo-portable.zip), extract it anywhere and run `Pomo.exe`.

### Coming from FocusOrb?

Pomo used to be called FocusOrb. Run `Pomo-Setup.exe` and it removes FocusOrb for you. On its first launch, Pomo brings over your history, settings, saved API key and Claude Code connection. The old `%APPDATA%\FocusOrb` folder is left in place as a backup.

## What's inside

- **Pomo, your focus buddy.** A soft matcha mochi who lives on your timer, follows your cursor and reacts to everything (see [Meet Pomo](#meet-pomo)).
- **Two timer styles, one click apart.** A slim island at the top of the screen (the default), or a round orb you can drag anywhere. The style button on the timer switches between them.
- **Buttons right on the timer.** Ask Pomo, the dashboard, the timer style and settings are on the timer itself. No right-click needed.
- **Editable time.** Double-click the time and type `30`, `45:00` or `1:30:00`, or scroll over it to add or remove minutes.
- **Pomodoro turns.** Dots show your place in the cycle before the long break, and *3/8 today* tracks your daily goal.
- **Alerts when a session ends:** a Windows notification, a sound (four built in), a pop-up card with *Start break* / *Skip*, and a soft glow on the timer.
- **Five pastel themes and eight outfits.** Pick your colors and dress Pomo up.
- **Things to play with.** Pet Pomo, feed treats you earn by focusing, and play Petal Catch on your break.
- **Dashboard.** KPIs, charts, your agents' activity, a session log and CSV export.
- **Keeps an eye on your coding agents.** See when Claude Code is working, done, or needs you, and approve its requests from Pomo.
- **Ask Pomo.** Chat with Claude, using your own API key, to plan your work into pomodoros or look back at your week.
- **Runs on startup** if you want it to: one switch in *Settings > General*.

<div align="center"><img src="docs/popup.png" alt="Pop-up card shown when a session ends" width="328"></div>

## Two timer styles

**The island** (the default) hangs from the top edge of your screen:

- **While a session runs**, it's a small pill with a mini Pomo and the countdown.
- **Hover it** and it opens up with the controls, the buttons, a progress bar and what your agents are doing.
- **When nothing is running**, it stays put. Turn on *Tuck the island away when idle* in *Settings > Appearance* to shrink it to a thin sliver instead; hover the top edge of the screen to bring it back.

<div align="center"><img src="docs/island.png" alt="The island: compact while a session runs, and open on hover with the buttons and an agent status line" width="503"></div>

**The orb** floats in the bottom-right corner, just above the taskbar. Drag it anywhere. Hover it for the buttons: dashboard, Ask Pomo, timer style and settings along the top, and play, reset and skip below the time.

<div align="center"><img src="docs/orb.png" alt="The orb while focusing, on hover with its buttons, and when a session ends" width="760"></div>

## Meet Pomo

Pomo is a soft matcha mochi with a sakura blossom who sits on your timer and keeps you company:

- Pomo's eyes follow your cursor, and Pomo blinks and breathes.
- Pomo looks determined while you focus, relaxes with happy closed eyes on breaks, and sometimes does a little dance.
- Pomo bounces when the next session is ready and jumps for joy when you finish one. Reach your daily goal and there's confetti.
- Leave the timer idle for a few minutes and Pomo dozes off.
- Poke Pomo: once is fine, a few times gets you a grumpy face, and keep going for a dizzy one.
- While your coding agents work, a little thought bubble appears over Pomo's head. When one needs you, it turns into a "!".

<div align="center"><img src="docs/pomo.png" alt="Pomo's moods: idle, focus, break, paused, ready, sleepy, happy, love, munch, ouch, grumpy, dizzy" width="760"></div>

Don't want a buddy? Turn off *Show Pomo* in *Settings > Appearance*.

## Play with Pomo

- **Pet:** rub your cursor back and forth over Pomo for hearts.
- **Twirl:** double-click Pomo.
- **Treats:** every finished focus session earns a dango (you can save up to 9). Click the dango next to Pomo to feed one.
- **Petal Catch:** click the petal next to Pomo (it shows on hover and during breaks) for a 30-second game. Move Pomo with your mouse or the arrow keys to catch falling sakura petals. Golden petals are worth 3, and your best score is saved. Open it during a focus session and Pomo will suggest saving it for your break.

<div align="center"><img src="docs/game.png" alt="Petal Catch: the start screen, and a round in progress" width="620"></div>

## Themes and outfits

Pick a theme in *Settings > Appearance* or from the right-click menu: **Matcha** (the default), **Sakura** and **Lavender** are light, **Cocoa** and **Blueberry** are dark.

<div align="center"><img src="docs/themes.png" alt="The orb in the Matcha, Sakura, Lavender, Cocoa and Blueberry themes" width="100%"></div>

Pomo has eight outfits. Choose one under *Pomo's outfit* in *Settings > Appearance* or in the right-click menu.

<div align="center"><img src="docs/wardrobe.png" alt="Pomo's outfits: sakura blossom, headphones, cozy beanie, party hat, crown, ribbon bow, round glasses, and nothing" width="100%"></div>

## Keep an eye on your coding agents

Pomo can watch your [Claude Code](https://code.claude.com) sessions, so you can stay in your focus session and still know when an agent needs you.

<div align="center"><img src="docs/agents.png" alt="Pomo with a thought bubble while an agent works, and an approval card with Allow, Deny and In terminal" width="662"></div>

**Connect it once:** open *Settings > Agents* and click **Connect Claude Code**. Pomo adds its hooks to `%USERPROFILE%\.claude\settings.json`. Your other settings are kept, and a backup is saved next to the file the first time. New Claude Code sessions pick it up.

What you get:

- **Live status.** Pomo's thought bubble while an agent works. The island and the right-click menu show each session's project and what it is doing (for example *pomo: Editing app.py*).
- **Approvals from Pomo.** When Claude Code would ask for permission, a card shows the project and the exact command or file, with **Allow**, **Deny** and **In terminal**. If you don't answer within the wait time (60 seconds by default), Claude Code asks in the terminal as usual. Nothing is ever approved without your click.
- **Heads-up when it's done.** A notification when an agent finishes a long task (longer than 30 seconds by default) or is waiting for your input.
- **Today's activity** on the dashboard: sessions, prompts, tool calls, files edited, commands and approvals.

Watching never slows Claude Code down: everything except approvals runs in the background. If Pomo isn't running, Claude Code behaves exactly as before. Agent events stay on your PC: they travel over a local named pipe that only your Windows account can open.

To stop, click **Disconnect Claude Code** in the same place. Only Pomo's own entries are removed.

## Ask Pomo

Click the chat button on the timer to chat with Claude in Pomo's voice. Pomo knows your timer, your focus stats, your current task and what your agents did today, so you can ask things like:

- *Plan my task*: splits your current task into pomodoro-sized steps.
- *How am I doing?*: observations about your week, and one suggestion.
- *Agents today*: a per-project summary of your coding agents' work.
- *Pep talk*: a nudge before your next session.

<div align="center"><img src="docs/chat.png" alt="Ask Pomo: the bring-your-own-key panel, and a chat planning a task into pomodoros" width="760"></div>

**Bring your own key.** Ask Pomo runs on your own Anthropic API key. There is no subscription: you pay Anthropic directly for what you use.

1. The first time you open Ask Pomo, it asks for a key. Click **Get a key** to open the Anthropic Console, create one, and paste it in.
2. Pomo checks the key with a free call that uses no tokens. Then you're ready to chat.

The key is stored encrypted for your Windows account and is only ever sent to Anthropic. Change or remove it with the **Key** button in the chat or in *Settings > AI*, where you can also pick the model: Claude Opus 5.5 (the default), Claude Sonnet 5.5 or Claude Haiku 5.5. If no key is saved and `ANTHROPIC_API_KEY` is set, Pomo uses that.

**Privacy:** nothing is sent until you ask something. Then Pomo sends your question plus a short summary: timer state, focus stats, task names and agent activity (project names, prompts and tool counts).

## Using the timer

| Action | How |
|---|---|
| Start / pause | Hover, then click the round play button, or press **Space** |
| Reset / skip | The buttons next to play, or **R** / **S** |
| Edit the time | Double-click the time and type a value. When the timer is idle, whole minutes become the new default for that mode. While it is running, this changes the time left. |
| Nudge the time | Scroll over the timer: 1 min per notch (Shift or Ctrl: 5 min), or **Up** / **Down** |
| Dashboard, Ask Pomo, timer style, settings | The small buttons along the top of the timer (hover to see them) |
| Move it | Drag the orb anywhere. The island stays at the top center. |
| Play | Poke, pet or double-click Pomo; click the dango to feed a treat or the petal for Petal Catch |
| Everything else | Right-click: switch mode, add time, set task, daily goal, timer style, theme, outfit, run on startup, coding agents, hide, quit |
| Tray icon | Click to bring the timer back; double-click for the dashboard |

## Dashboard

Open it with the chart button on the timer, from the right-click menu or by double-clicking the tray icon.

<img src="docs/dashboard.png" alt="The Pomo dashboard" width="100%">

- **Today:** focus time, and pomodoros against your goal.
- **KPIs:** focus time and pomodoros (with change vs the previous period), average per active day, completion rate, current and best streak, peak hour, best day, and break time.
- **Charts:** daily focus with a goal line, a consistency heatmap, top tasks, time of day and weekday rhythm.
- **Coding agents:** today's sessions, prompts, tool calls, files edited, commands and approvals, plus what is running right now.
- **Session log:** a table of every session, with CSV export.
- **Range filter:** 7 days, 30 days, 90 days or all time.

## Settings

| Tab | What you can change |
|---|---|
| Timer | Focus, short and long break lengths; long break interval; daily goal; auto-start for breaks and for focus |
| Alerts | Notifications, pop-up card, sound, volume, repeats, idle reminder |
| Appearance | Theme, timer style (island or orb), show Pomo, Pomo's outfit, tuck the island away when idle, orb size, opacity, fade while focusing, always on top, show task, colors for each mode |
| Agents | Connect or disconnect Claude Code, answer permission requests in Pomo, how long to wait for your answer, finish notifications |
| AI | Your Anthropic API key (save, check, remove), model for Ask Pomo |
| General | Run Pomo on startup, move the orb back to its corner, open the data folder, export CSV, clear history |

Your data lives in `%APPDATA%\Pomo`: `settings.json`, `history.db` (pomodoros and agent activity) and, if you saved one, `ai_key.bin` (your API key, encrypted).

## For developers

### Run from source

From the project folder, in PowerShell or Command Prompt:

```powershell
.\run.bat
```

The first run creates a `.venv` and installs the dependencies (PySide6 and the Anthropic SDK). To try the dashboard with sample history (kept in a separate data folder), run `.\run.bat --demo`.

Run the tests with `python -m unittest discover -s tests -t .`.

### Build the installer locally

Install [Inno Setup 6](https://jrsoftware.org/isinfo.php) (`winget install JRSoftware.InnoSetup`), then run:

```powershell
.\build.bat
```

This runs the tests, then writes two files:

- `dist\Pomo-Setup.exe`, the installer
- `dist\Pomo-portable.zip`, the portable build

### Publish a release

The workflow in [`.github/workflows/release.yml`](.github/workflows/release.yml) builds the installer on every push. When you push a version tag, it also publishes a GitHub Release:

1. Bump `APP_VERSION` in [`pomodoro/__init__.py`](pomodoro/__init__.py) and commit.
2. Tag and push the tag:
   ```bash
   git tag v1.2.0
   git push origin v1.2.0
   ```
3. GitHub Actions builds `Pomo-Setup.exe` and `Pomo-portable.zip` and attaches them to the release.

The download button at the top of this README always points to the latest release, so it needs no edits.

### Project layout

```
main.py                     entry point, single-instance guard, hook mode, FocusOrb data migration
pomodoro/engine.py          timer state machine (monotonic clock, cycles, logging)
pomodoro/island.py          the top-of-screen island (the default timer style)
pomodoro/orb.py             the floating round timer
pomodoro/pomo.py            Pomo: drawing, moods, outfits and animation (all in code, no image files)
pomodoro/game.py            Petal Catch
pomodoro/toast.py           pop-up cards (session alerts, welcome, agent approvals)
pomodoro/agents.py          live Claude Code sessions and approvals, over a local named pipe
pomodoro/hook.py            the hook client Claude Code runs (standard library only, starts fast)
pomodoro/claude_hooks.py    connects / disconnects Pomo in Claude Code's settings.json
pomodoro/ai.py              Ask Pomo: context, streaming replies and key checks through the Anthropic SDK
pomodoro/chat.py            the Ask Pomo window, with the bring-your-own-key panel
pomodoro/keystore.py        API key storage, encrypted with Windows DPAPI
pomodoro/dashboard.py       dashboard window
pomodoro/charts.py          hand-painted charts (no matplotlib, keeps the build small)
pomodoro/settings_dialog.py settings window
pomodoro/app.py             controller: tray, menus, notifications, wiring
pomodoro/config.py          settings, outfits and the FocusOrb to Pomo data move
pomodoro/storage.py         SQLite history (pomodoros and agent events)
pomodoro/stats.py           KPI calculations
pomodoro/sounds.py          alert sounds synthesized at runtime
pomodoro/theme.py           the five pastel themes, stylesheet
pomodoro/icons.py           button glyphs and the Pomo app icon
pomodoro/winutil.py         Windows helpers: run on startup, dark title bars
installer/Pomo.iss          Inno Setup installer script (also removes FocusOrb when upgrading)
tools/prepare_build.py      generates the .exe icon, installer artwork and version info
tests/                      unit tests
```

## Credits

The idea of a buddy that reacts to your work, a timer island at the top of the screen, and watching coding agents from it was inspired by [Coucou](https://github.com/Louis-CFM/coucou) by Louis Raillé. Pomo is an original character, and this app uses none of Coucou's code, artwork or sounds.
