# Pomo privacy policy

*Last updated: November 28, 2026*

Pomo is a Pomodoro timer for Windows, made by an independent developer. This policy covers Pomo from the Microsoft Store and from GitHub.

## The short version

- Pomo has **no account, no servers of its own, no analytics and no ads**. Nobody, including the developer, receives anything about you or how you use Pomo.
- Everything Pomo keeps stays **on your PC**.
- Something leaves your PC only when you use a feature that needs it: the AI features (sent to the AI provider *you* chose, with *your* key), speech recognition by OpenAI or Gemini if you pick one, the weather once you set a city, and the update check in the GitHub version.

## What Pomo keeps on your PC

- **Your settings and progress:** timer settings, tasks and plan, quests, badges, outfits, your room, your study path, XP, review cards, habits and your rank.
- **Your history:** focus sessions and how they felt, parked thoughts, your study log, urges ridden out, and your coding agents' activity (project names, prompts, tool counts and files changed).
- **Minutes per app,** only if you turn on the distraction check-in: which app was in front, and for how long. Pomo never looks at what's inside a window.
- **Your AI key,** if you save one, encrypted with Windows' own data protection so only your Windows account can read it.
- **Your notes,** as Markdown files in the notes folder you use (*Documents\Pomo Notes* unless you pick another), and your **sticky notes** with Pomo's other data. Neither is ever sent anywhere.

Window titles (for "Where was I?" and naming sessions after your project) are only ever held in memory, never written down. Voice recordings and spoken replies only pass through a temporary file that is deleted as soon as Windows is done with it.

In the GitHub version this data is in `%APPDATA%\Pomo`. In the Microsoft Store version Windows keeps it in Pomo's own app data, and removes it when you uninstall Pomo.

## What leaves your PC, and when

### AI features (only with your own key)

Ask Pomo, My path, the big picture, Teach Pomo, the English notes on your study log and the other AI features use the AI provider you pick: Anthropic (Claude), OpenAI (ChatGPT), Google (Gemini), xAI (Grok) or OpenRouter. Pomo sends your request straight from your PC to that provider with your key. That provider's own privacy policy applies to what it receives.

- **When you ask something,** Pomo sends your question plus a short summary so the answer fits your day: timer state, focus stats, task names, your plan, parked thoughts, how your sessions felt, your focus score, your habit rank and today's habits (names and which are done), and agent activity (project names, prompts and tool counts).
- **The learning loop,** if it's on and you have a key: starting a focus session on a step of your path sends your goal and the step's name, so Pomo can ask you a question about it. Turn it off in *Settings > Focus*.
- **Study features:** what you write is sent only when you click the button that asks for it, such as **Teach**, **Save and check my English** or **Redraw** on the big picture (your goal and the names of your milestones and steps).

Without a key, nothing is sent anywhere.

### Talk to Pomo

The microphone is used only while you hold the talk shortcut or the mic button. In *Settings > AI* you choose who turns your voice into text: **This PC** keeps it on your PC; **OpenAI** or **Gemini** sends the recording to that provider with your key. Replies are read aloud by a Windows voice on your PC.

### Weather (only once you set a city)

The panel can show the weather from [Open-Meteo](https://open-meteo.com), a free service with no account. Nothing is sent until you type your city in the Calendar tab. Then Pomo sends the name you typed to Open-Meteo's place search once, and your city's latitude and longitude to its forecast service about every half hour while the panel shows the weather. Choose *No weather* to stop.

### Update checks (GitHub version only)

The version from GitHub asks GitHub's public API for the latest release about once an hour, and downloads the installer when there's a new one. Nothing about you is sent with it. The Microsoft Store version never does this: the Store updates it.

### Links

Links you click (such as *Get a key* or *What's new*) open in your web browser.

## Things Pomo reads on your PC

To work, Pomo reads a few things on your PC and keeps them there: which app is in front (for the check-in, app lock and Ultra focus), the song your media player reports (for the music controls), the project open in your editor (to name sessions), and the events your coding agents send it through the hooks you connect in *Settings > Agents*. None of it is sent anywhere, except as part of the AI summary described above when you ask Pomo something.

The panel adds a few more, all kept on your PC:

- **What you copy**, for the Clipboard tab: the last few texts and pictures, in memory only. They're never written to disk and are gone when Pomo quits. Anything a password manager marks private is skipped, and you can turn this off in *Settings > General*.
- **Your recent documents**, for the Docs tab: read from Windows' own list of recent files when you open the tab.
- **The Shelf's files and the Launcher's apps**: only their paths, in Pomo's settings.
- **Your PC's sound, brightness and Bluetooth devices**, for the System tab, read and changed only when you use it.
- **Gaming mode's readings**: which app is in front, CPU and GPU use and temperatures, memory and the game's frame rate, read on your PC and never saved or sent. For FPS, Pomo reads RivaTuner's figures if it runs, or uses Intel's PresentMon if you click *Turn on FPS*; that asks Windows (with one administrator prompt) to add you to its *Performance Log Users* group, which lets programs you run read graphics timing events.

## Children

Pomo doesn't collect personal information from anyone, children included.

## Changes

If this policy changes, the new version is published here with a new date. Its history is in this repository.

## Contact

Questions about privacy: open an issue at [github.com/sahilwagh99/pomo/issues](https://github.com/sahilwagh99/pomo/issues).
