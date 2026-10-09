# ULTRON v1.0 — Personal AI Voice Assistant

Ultron is a lightweight desktop voice assistant for Windows 10/11, written in Python with CustomTkinter.
You can talk to it or type to it. It answers questions with an AI model, speaks its replies aloud,
opens approved websites and applications, and keeps simple notes.

**Local commands (time, notes, apps, websites, maths, jokes…) work without any API key.**

## Features

| Area | What you can say or type |
|------|--------------------------|
| Basics | `hello`, `who are you`, `what time is it`, `what's the date`, `how are you`, `tell me a joke`, `random fact`, `help` |
| Control | `stop` (stop speaking), `clear conversation`, `exit` |
| Web | `open Google` / `YouTube` / `GitHub` / `Gmail`, `open example.com`, `search Google for black holes` |
| Apps | `open Notepad`, `open Calculator`, `open File Explorer`, `open Command Prompt`, `open VS Code` (if installed), plus apps you add in `.env` |
| Notes | `take a note buy milk`, `take a note` (then say the text), `list my notes`, `read note 2`, `read my last note`, `read my notes`, `delete note 2` (asks for confirmation) |
| Maths | `calculate 12 times 8`, `what is 15 percent of 200`, `square root of 81` |
| AI | Anything else: general questions, coding help, study help, "summarize: …", follow-up questions (recent history is remembered) |
| Optional | `look up <topic>` gives a quick answer from DuckDuckGo when `WEB_SEARCH_PROVIDER=duckduckgo` |

The window has a Start/Stop Listening (microphone) button, a status indicator (Ready / Listening /
Processing / Speaking / Error), a conversation panel, text input with Send, Mute/Unmute,
Stop Speaking, Clear Conversation, a Continuous listening switch, and a Settings panel.
Keyboard: **Enter** sends, **Esc** stops speaking.

## Requirements

- Windows 10 or 11
- **Python 3.12 or 3.13 recommended** (3.11 also works). Python 3.14 works for everything *except voice
  input*, because PyAudio does not yet publish a Windows installer for 3.14.
- A microphone and speakers for voice features (optional — typing always works)
- An internet connection for AI answers and speech recognition
- An OpenAI API key for AI answers (optional)

## Setup (Windows PowerShell)

1. **Install Python** from <https://www.python.org/downloads/> (3.12 or 3.13). In the installer, tick
   **"Add python.exe to PATH"** and keep **"tcl/tk and IDLE"** selected.

2. **Open PowerShell in the project folder**:

   ```powershell
   cd C:\path\to\Ultron
   ```

3. **Create and activate a virtual environment**:

   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

   If PowerShell says running scripts is disabled, run this once and try again:

   ```powershell
   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
   ```

4. **Install the dependencies**:

   ```powershell
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```

5. **Create your configuration file**:

   ```powershell
   Copy-Item .env.example .env
   notepad .env
   ```

   Paste your key after `OPENAI_API_KEY=` (or leave it empty to use local commands only) and save.
   Every setting is documented inside `.env.example`.

6. **Check the setup** (optional, recommended):

   ```powershell
   python main.py --check
   ```

## Launching

With the virtual environment activated:

```powershell
python main.py
```

Or double-click **`run.bat`** — it uses `.venv` automatically when it exists and creates `.env` from
`.env.example` if you don't have one yet.

### Browser version (localhost link)

```powershell
python main.py --web
```

Then open **<http://127.0.0.1:8000>** (it opens automatically; add `--no-browser` to skip that, or
`--port 9000` / `WEB_PORT` to change the port). You can also double-click **`run_web.bat`**. Use Chrome or
Edge for voice input: the browser handles the microphone and spoken replies, while the Python server
handles commands, notes, apps and AI. The server only listens on your own computer (127.0.0.1) and
requires a per-session token, so other websites can't control it. Press **Ctrl+C** in the terminal to stop.

Other modes:

```powershell
python main.py --cli     # text chat in the terminal, no window
python main.py --mute    # start with spoken replies muted
python main.py --check   # dependency and configuration report
```

## Running the tests

```powershell
python -m pytest
```

The tests use fakes for the microphone, speech engine, AI service, browser and app launcher, so they
need no microphone, no API key, no internet and never touch your real notes.

## Configuration

All settings live in `.env` (see `.env.example` for the full, commented list). The most important:

| Setting | Default | Meaning |
|---------|---------|---------|
| `AI_PROVIDER` | `openai` | `openai`, or `none` to disable AI answers |
| `OPENAI_API_KEY` | *(empty)* | Your API key. Never logged or shown in the UI |
| `OPENAI_MODEL` | `gpt-4o-mini` | Any chat model your account can use |
| `OPENAI_BASE_URL` | *(empty)* | For OpenAI-compatible services |
| `TTS_RATE` / `TTS_VOLUME` / `TTS_VOICE` | `175` / `0.9` / *(default)* | Voice output speed, volume, and voice name (e.g. `Zira`) |
| `VOICE_LANGUAGE` | `en` | Speech recognition language (`en`, `en-GB`, `hi-IN`, …) |
| `ENABLE_CONTINUOUS_MODE` | `false` | Keep listening after each reply |
| `WEB_SEARCH_PROVIDER` | `none` | `duckduckgo` enables `look up …` quick answers |
| `ULTRON_APPS` | *(empty)* | Extra apps, e.g. `paint=C:\Windows\System32\mspaint.exe;spotify=C:\Users\you\AppData\Roaming\Spotify\Spotify.exe` |
| `ULTRON_DATA_DIR` | `data` | Where notes are stored (`notes.json`) |

Most AI and voice options can also be changed from the **Settings** panel. **Apply** affects the
current session only; **Save to .env** also writes them to your `.env` file.

## Safety and privacy

- The microphone is opened **only** after you press Start Listening (or while continuous mode is on), and the
  status shows *Listening* whenever it is active. Speech is sent to Google's free Web Speech API for
  transcription and is never saved to disk.
- Ultron only launches the built-in app list and apps you explicitly add to `ULTRON_APPS`
  (`.exe`/`.lnk` only). It never runs shell commands, and it never executes anything suggested by the AI.
- Only `http`/`https` URLs with a valid host are opened. `javascript:`, `file:`, and addresses containing
  `user@host` credentials are rejected.
- Deleting a note requires saying or typing **yes** to confirm. Ultron has no commands for purchases,
  sending messages, or system changes, and the AI is instructed never to claim it performed actions.
- Your API key is redacted from logs (`logs/ultron.log`). `.env`, `logs/`, and your notes in `data/` are
  excluded from git by `.gitignore`.

## Project structure

```text
Ultron/
├── main.py                 # Entry point: GUI, --cli, --check
├── config.py               # Settings from .env, validation, .env writer
├── run.bat                 # Windows launcher (uses .venv if present)
├── core/
│   ├── assistant.py        # Listen → transcribe → process → respond → speak flow, threading
│   ├── command_handler.py  # Extensible command registry, confirmations, safe calculator
│   ├── conversation.py     # Bounded, thread-safe chat history
│   └── logging_utils.py    # Rotating log file with secret redaction
├── services/
│   ├── ai_service.py       # OpenAI(-compatible) chat with friendly error messages
│   ├── speech_recognition_service.py
│   ├── text_to_speech_service.py  # Interruptible speech (runs tts_worker.py per utterance)
│   ├── tts_worker.py
│   ├── web_service.py      # URL validation, websites, Google search, optional DuckDuckGo lookup
│   ├── application_service.py     # Allow-listed app launching
│   └── notes_service.py    # JSON notes with timestamps
├── ui/
│   ├── main_window.py      # Main window; receives background events through a queue
│   ├── settings_window.py  # AI and voice preferences
│   └── components.py       # Chat panel, status indicator, mic button
├── tests/                  # pytest suite (no hardware or network needed)
└── data/                   # Your notes (not committed)
```

### Adding a command

Register a pattern and a handler in `CommandHandler._register_defaults` (`core/command_handler.py`):

```python
r.add("coin_flip", [r"^flip a coin$"],
      lambda m, t: CommandResult(self.rng.choice(["Heads!", "Tails!"])))
```

Commands are checked in registration order; anything unmatched goes to the AI.

## Troubleshooting

**"Voice input needs PyAudio" / microphone unavailable**
- Run `pip install pyaudio` inside the activated `.venv`. If it fails on Python 3.14, recreate the
  virtual environment with Python 3.12 or 3.13 (`py -3.12 -m venv .venv`).
- Windows **Settings → Privacy & security → Microphone**: turn on *Microphone access* and
  *Let desktop apps access your microphone*.
- Check **Settings → System → Sound → Input** that the right microphone is selected and its level moves.

**"I didn't hear anything" / "couldn't understand that"**
Speak shortly after the status shows *Listening*, reduce background noise, or raise `LISTEN_TIMEOUT`.

**"The speech recognition service is unreachable"**
Speech-to-text needs internet access. Typing still works offline.

**No spoken replies**
Make sure the app isn't muted, your speakers work, and `TTS_VOLUME` is above 0. Leave `TTS_VOICE`
empty to use the default voice. Extra voices can be added in *Settings → Time & language → Speech*.
`pip install --force-reinstall pyttsx3 pywin32 comtypes` fixes most SAPI5 errors.

**"AI answers aren't set up yet"**
Add `OPENAI_API_KEY=...` to `.env` (or paste it in Settings) and restart. Local commands keep working.

**"API key was rejected" / "run out of credits" / "rate limiting"**
Check the key at <https://platform.openai.com/api-keys>, your billing/quota, or wait a minute.

**"The AI model … isn't available"**
Set `OPENAI_MODEL` to a model your account can use.

**"Vs Code doesn't appear to be installed"**
Ultron looks in the standard install folders. For other locations add it to `ULTRON_APPS`, e.g.
`ULTRON_APPS=vs code=D:\Tools\VSCode\Code.exe`.

**`ModuleNotFoundError: No module named 'tkinter'`**
Reinstall Python from python.org with the *tcl/tk and IDLE* option enabled.

**Something else**
Run `python main.py --check` and look at `logs\ultron.log` (secrets are redacted).

## Known limitations

- Speech recognition uses Google's free Web Speech API: it needs internet and isn't meant for heavy use.
- Stop Listening takes effect at the end of the current listening window (up to `LISTEN_TIMEOUT` seconds).
- Each spoken reply starts a short helper process, adding a small delay before speech; this is what makes
  instant "stop speaking" reliable.
- Web lookups (`look up …`) return short instant answers only; Ultron does not browse the web.
