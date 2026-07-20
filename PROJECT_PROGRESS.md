# Project Progress

Last updated: 2026-07-20

## Goal

Build a professional local-first Windows assistant for ESEO that connects Google workflows, existing tools, development projects, Codex, tasks, approvals, escalation state, and voice interaction.

Assistant identity:

- Full name: `Khadija Noor`
- Nickname: `Noor`

## Completed

- Created standalone project at `E:\ESEO\standalone-windows-assistant`.
- Set the assistant identity to `Khadija Noor`, nickname `Noor`.
- Added PySide6 desktop app with local SQLite persistence.
- Added assistant-style cockpit UI with central animated avatar, dark command console styling, module cards, command bar, and quick actions.
- Added connection checks for Google Workspace, existing tools, default ESEO projects, local Codex CLI, and installed Windows voices.
- Added default project registration from `config\projects.json`.
- Added `Google Workspace` connector to `config\tools.json`.
- Added deterministic local assistant commands: `open tools`, `open projects`, `open codex`, `open tasks`, `open settings`, `show approvals`, `read summary`, `test connections`, `pause escalations`, and `add task ...`.
- Added a lightweight deterministic assistant brain for basic chat, local status questions, tool/project lookup, Google/Codex/WhatsApp status, weather, news, and web research.
- Added web research helpers without adding a heavy browser dependency:
  - weather through `wttr.in`;
  - news through Google News RSS;
  - research links through DuckDuckGo Lite with relevance and explicit-result filtering.
- Added local voice output without Gemini or AI through PowerShell `System.Speech`.
- Added Settings controls for voice selection, speed, volume, and listen timeout.
- Added first-pass push-to-talk voice command recognition through Windows speech recognition.
- Fixed voice command handling after live testing:
  - stopped speaking "Listening" into the microphone before recognition starts;
  - changed default listening to hybrid productivity mode instead of open dictation;
  - added a minimum recognition confidence setting;
  - rejects low-confidence noisy recognitions instead of answering them;
  - shows the heard phrase and confidence on the assistant cockpit;
  - keeps the assistant answer visible after page refreshes.
- Added more human TTS support:
  - optional Edge Neural TTS through `edge-tts`;
  - default voice set to `en-US-JennyNeural`;
  - fallback to Windows desktop voices remains available.
- Added settings-by-command support:
  - `use edge voice`;
  - `use windows voice`;
  - `make voice faster`;
  - `make voice slower`;
  - `set voice confidence to fifty`;
  - `switch to command mode`;
  - `switch to hybrid mode`;
  - `switch to dictation mode`.
- Added Google productivity integration:
  - Google Tasks creation for `create todo`, `add todo`, `create task`, and `add task`;
  - Google Calendar event creation for `schedule event`, `schedule meeting`, and `create event`;
  - Google Calendar reminder creation for `remind me to`;
  - upcoming calendar reading through `what's on my calendar`;
  - one-time OAuth command: `connect google productivity`;
  - separate ignored token file at `data\google_productivity_token.json`.
- Updated Google productivity UI:
  - dashboard now shows Tasks/Calendar as `authorized` when the token is usable;
  - Tasks page reads live Google Tasks and can mark selected Google tasks done;
  - Calendar page reads live Google Calendar events and shows reminder metadata;
  - Tasks and Calendar pages show API-disabled errors instead of asking to reconnect;
  - added `Open Google API Setup` buttons for Google Tasks API and Google Calendar API.
- Added automatic web research fallback when Noor has no reliable local answer.
- Added a shared low-cost AI brain pipeline for assistant chat and WhatsApp replies: deterministic/local answer first, cached answer reuse, lightweight source-backed research, Gemini CLI fallback, then Codex CLI fallback with `gpt-5-mini` and low reasoning.
- Added hybrid voice grammar for spoken productivity commands without using Gemini or another AI provider.
- Added Connected Tools `Test All`.
- Replaced the selector-driven WhatsApp bridge with an event-driven `whatsapp-web.js` bridge using an isolated local authentication session, duplicate fingerprints, privacy-safe capture, audit logging, quiet hours, per-chat cooldowns, and hourly reply limits.
- Added a replaceable WhatsApp selector adapter in `config\whatsapp_web_selectors.json`; the bridge sends only a verified rule or a valid shared-brain reply to one unread direct chat at a time.
- Added optional Gemini CLI draft/answer support using `where gemini`, non-interactive `--prompt` plus `--output-format json`, strict timeouts, safe JSON parsing, bounded context, and no `--yolo` mode.
- Added optional Codex CLI answer fallback in read-only, ephemeral mode with explicit `gpt-5-mini` and low reasoning overrides so it does not inherit a high-cost default Codex config.
- Upgraded `whatsapp-web.js` to `1.34.7`, bound it to the installed Google Chrome executable, and verified the dedicated event bridge reaches `CONNECTED` after QR authentication.
- Confirmed Gemini CLI `0.51.0` and Codex CLI `0.128.0` are both detectable on this machine; answer generation remains gated by Settings, caching, hourly limits, and provider cooldowns.
- Replaced the selected `Khodeja Poly` test with direct-message auto replies for any unread contact: rules first, Gemini fallback only on a valid response, per-chat cooldown, hourly cap, group exclusion, duplicate protection, send-time chat/message verification, and audit records.
- Fixed launcher/startup stability:
  - `run_app.bat` now delegates to `run_noor_silent.vbs` instead of reinstalling dependencies on every launch;
  - `run_noor_silent.vbs` uses `pythonw.exe` when available;
  - startup helper checks now use hidden Windows subprocess creation;
  - Windows voice enumeration is deferred until Settings -> Load Windows Voices;
  - Noor now uses a Windows named mutex so duplicate launches exit before building the UI or starting timers.
- Kept existing tools separate and did not modify their source.

## Verified

- `.\.venv\Scripts\python.exe -m compileall -q main.py src`
- `.\.venv\Scripts\python.exe main.py --check`
- `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\list_voices.ps1`
- `scripts\listen_once.ps1` parses successfully and hybrid mode returns clean JSON with no speech instead of random text.
- Headless PySide construction smoke test: app title created, 15 pages loaded, assistant page loaded first.
- Deterministic command routing smoke test: `open tools` routed to Connected Tools, `add task verify voice command routing` routed to Tasks, and the test task was removed.
- Google Workspace connector check confirmed tool path, `credentials.json`, and `.secrets\token.json` are present without reading their contents.
- Assistant brain checks:
  - `hi`;
  - `weather in Dhaka`;
  - `latest news technology`;
  - `research WordPress plugin security`;
  - `what is Content Review Manager`;
  - `WhatsApp status`;
  - `connection status`.
- Google productivity checks:
  - token is present and Google Tasks/Calendar authorization is connected;
  - Google Tasks API live read currently returns API-disabled HTTP 403 from the Google Cloud project;
  - Google Calendar API live read currently returns API-disabled HTTP 403 from the Google Cloud project;
  - `google productivity status` reports the API-disabled state instead of telling the user to reconnect.
- Unknown-answer fallback check: Noor automatically researched `what causes thunder`.
- AI brain router smoke test: medium-confidence research answered before Gemini/Codex, and non-question WhatsApp text returned a local acknowledgement without spending AI calls.
- WhatsApp unknown-message smart-reply smoke test: a simulated unread direct event used the shared AI reply path and wrote a `Sent` audit record with the reply source.
- Codex/Gemini CLI detection check: `gemini --version` returned `0.51.0`; `codex --version` returned `codex-cli 0.128.0`.
- AI Settings smoke test: offscreen UI construction loaded the AI Brain, Gemini, and Codex fallback controls with default `gpt-5-mini` and low reasoning.
- UI smoke test confirmed every page is now wrapped in a scroll area.
- WhatsApp automatic-reply rule-path test: a simulated unread direct `hello` produced the greeting reply only after chat and message-hash verification, then wrote a `Sent` audit record.
- Dedicated WhatsApp bridge live-status check: connected profile with heartbeat validation; no unread chats were present at the time of the check.
- Startup checks:
  - `python -m compileall -q main.py src scripts`
  - `node --check scripts\whatsapp_webjs_bridge.js`
  - offscreen PySide startup: `OFFSCREEN_STARTUP_OK False False 15`
  - offscreen `run_noor_silent.vbs` launcher test starts the venv `pythonw.exe` launcher and child `C:\Python314\pythonw.exe`, then cleanup leaves no Noor/bridge processes.
- Voice troubleshooting evidence:
  - bad recognition examples in activity had confidence around `0.06` to `0.13`;
  - default minimum confidence is now `0.35`;
  - low-confidence recognitions now ask the user to repeat instead of routing to the assistant brain.
  - hybrid mode with no speech returns no text instead of random dictation.

## Partially Done

- Voice commands use hybrid productivity grammar, not full open conversational dictation.
- The app can speak confirmations and summaries. Text answers now use local deterministic knowledge plus optional cached research, Gemini, and Codex fallbacks.
- Google Workspace is connected by safely detecting the existing content workflow OAuth setup. Google Tasks/Calendar authorization is connected, but this machine's OAuth project still needs Google Tasks API and Google Calendar API enabled before live reads/creation can work.
- Direct Google Sheets/Docs browsing inside this app is not implemented yet.
- Codex sessions run through `codex exec`; interactive resume opens in a terminal.
- Web research now extracts short source evidence from top result pages, but it is still lightweight and not a full browser automation research agent.
- The UI is now assistant-style and scrollable, but more layout polish can still be added after testing on the real screen.

## Not Started Yet

- Microsoft Teams alert automation.
- Google Find Hub manual device setup and play-sound-only automation.
- Real speech-to-action flows for arbitrary message composition.
- Full browser-controlled research sessions with screenshots and page extraction.
- Windows startup registration and system tray controls.
- Backup/restore/export UI.
- Structured parsing of Elementor, Gutenberg, content review, and plugin review reports inside the dashboard.

## Next Recommended Development Order

1. Enable Google Tasks API and Google Calendar API for the Google Cloud project behind `E:\ESEO\content-review-manager\credentials.json`.
2. Refresh Noor's Tasks and Calendar pages, then test: `what are my todos`, `what reminders do I have`, and `what's on my calendar`.
3. Manually launch the app and review the cockpit UI on the actual display.
4. Add a dedicated Connections page with deeper Google, tools, projects, Codex, WhatsApp, and voice diagnostics.
5. Add structured report readers for the four existing tools.
6. Add a compact browser-research mode for deeper research tasks.
7. Add a compatible Gemini CLI authentication route if unknown-message replies are required without an API key.
8. Add Teams and Find Hub only after acknowledgement, cooldown, and manual test screens are finished.

## Security Notes

- Existing Google token and credential files are checked for presence only; contents are not opened or printed.
- No browser profile, token, or credential is committed.
- WhatsApp automatic sending is limited to unread direct chats that match a local rule or receive a valid shared-brain reply. It has duplicate protection, chat/message verification, quiet hours, per-chat cooldowns, hourly caps, group exclusion by default, and an audit trail.
- Find Hub must stay play-sound-only when implemented.
- Voice uses local Windows speech APIs, not Gemini and not an AI model.

## Runtime Notes

- One Noor launch can appear as two `pythonw.exe` processes on this machine: the venv launcher at `E:\ESEO\standalone-windows-assistant\.venv\Scripts\pythonw.exe` and its child interpreter at `C:\Python314\pythonw.exe`. That is normal and not two assistant instances.
