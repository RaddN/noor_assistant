# Project Progress

Last updated: 2026-07-21

## Goal

Build a professional local-first Windows assistant for ESEO that connects Google workflows, existing tools, development projects, Codex, tasks, approvals, escalation state, and voice interaction.

Assistant identity:

- Assistant: `Noor`
- Owner: `Raihan Hossain`

## Completed

- Created standalone project at `E:\ESEO\standalone-windows-assistant`.
- Set the assistant identity to `Noor` for owner `Raihan Hossain`.
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
- Replaced the selector-driven WhatsApp bridge with an event-driven `whatsapp-web.js` bridge using an isolated local authentication session, duplicate fingerprints, privacy-safe capture, and audit logging.
- Added a replaceable WhatsApp selector adapter in `config\whatsapp_web_selectors.json`; the bridge sends only when a WhatsApp rule matches one unread direct chat at a time.
- Upgraded WhatsApp rules from static replies to rule actions: direct reply, assistant command, research/Gemini/Codex AI reply, or configured safe tool command with optional AI summarization.
- Added optional Gemini CLI draft/answer support using `where gemini`, non-interactive `--prompt` plus `--output-format json`, strict timeouts, safe JSON parsing, bounded context, and no `--yolo` mode.
- Added optional Codex CLI answer fallback in read-only, ephemeral mode with explicit `gpt-5-mini` and low reasoning overrides so it does not inherit a high-cost default Codex config.
- Upgraded `whatsapp-web.js` to `1.34.7`, bound it to the installed Google Chrome executable, and verified the dedicated event bridge reaches `CONNECTED` after QR authentication.
- Confirmed Gemini CLI `0.51.0` and Codex CLI `0.128.0` are both detectable on this machine; answer generation remains gated by explicit commands/rules and Settings.
- Replaced the selected `Khodeja Poly` test with direct-message auto replies for any unread contact: matching rules only, no WhatsApp cooldowns or hourly limits, group exclusion, duplicate protection, send-time chat/message verification, and audit records.
- Added Find My Phone support through Google Find Hub with safe automatic `Play sound` automation for the configured phone.
- Fixed Find My Phone UI timeouts by running the ring operation in the background and narrowing the Chrome automation to the active Find Hub window.
- Switched visible Noor timestamps to 12-hour AM/PM display.
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
- Employee report automation:
  - added an employee directory/report registry for `HR & Payroll` and the `Degebitskliniek Project` content-writer sheet;
  - employee directory ignores owner row `Ashfuq Hossain Raihan` and uses only active employee profile fields needed for reporting;
  - weekly and monthly report images are generated from manageable HTML templates under `templates\reports\`, rendered to local PNG files under `data\reports\`;
  - report weekend days are configurable in Settings, with Friday as the default weekend so Sunday is counted as a workday;
  - report templates include the ESEO logo, employee photos from the HR sheet when the linked Drive thumbnail is accessible, and clean initials fallback when it is not;
  - content-writer targets now support historical changes: 6 rows per working day before Jul 21, 2026 and 9 rows per working day from Jul 21, 2026 onward;
  - WhatsApp report rules can send a caption plus image attachment through the dedicated whatsapp-web.js bridge;
  - manual `weekly report` and `monthly report` message rules are scoped to My Teletalk for testing;
  - scheduled rules are configured for Friday 9:00 PM weekly reports and last-day 9:15 PM monthly reports, currently to My Teletalk.
- Employee report checks:
  - live weekly HTML-to-PNG report generated for Jul 20 - Jul 24, 2026 with 24 items, 61,144 tracked words, 18/30 translated target rows, and 6 pending checks;
  - live monthly HTML-to-PNG report generated for Jul 1 - Jul 31, 2026 with 117 items, 308,908 tracked words, 111/186 translated target rows, and 6 pending checks;
  - visual inspection confirmed the weekly and monthly report PNGs fit the 1280x720 WhatsApp image layout with employee photos, ESEO branding, compact metrics, and unclipped table badges;
  - rule-engine dry run confirmed `weekly report` and `monthly report` produce report captions with media paths;
  - schedule matching confirmed Friday 9:00 PM triggers only weekly and Jul 31, 2026 9:15 PM triggers only monthly;
  - test weekly report image was sent to My Teletalk through WhatsApp.
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
- WhatsApp bridge health check:
  - live probe reported `ok=False` with `bridge_state=orphaned` when the node bridge helper was alive but Noor's dedicated Chrome profile was not open.
  - compile check passed after the health-gate change.
  - live repair cleared orphaned bridge PID `22768`, started fresh bridge PID `15036`, and status moved to `CONNECTED` with `authenticated=True`.
- WhatsApp call fallback check:
  - temporary unmatched incoming call event produced `No WhatsApp call rule matched. Teams alert sent.`;
  - the test asserted the AI fallback path was not called and the event file was consumed.

## Partially Done

- Voice commands use hybrid productivity grammar, not full open conversational dictation.
- The app can speak confirmations and summaries. Text answers now use local deterministic knowledge plus optional cached research, Gemini, and Codex fallbacks.
- Google Workspace is connected by safely detecting the existing content workflow OAuth setup. Google Tasks/Calendar authorization is connected, but this machine's OAuth project still needs Google Tasks API and Google Calendar API enabled before live reads/creation can work.
- Direct Google Sheets employee/project report reading is implemented for configured sheets; generic ad hoc Google Sheets/Docs browsing is not implemented yet.
- Codex sessions run through `codex exec`; interactive resume opens in a terminal.
- Web research now extracts short source evidence from top result pages, but it is still lightweight and not a full browser automation research agent.
- The Assistant dashboard now displays Codex quota windows from local Codex session `rate_limits` data and Gemini daily remaining usage from Noor's local Gemini call count plus the configured daily cap.
- One-way Microsoft Teams fallback alerts are implemented for WhatsApp no-rule, rule-failure, and send-failure paths. Graph direct chat, incoming webhook, and open Teams window modes are configurable in Settings; Teams OAuth/chat discovery is still manual.
- Teams fallback now stores active urgency state and suppresses repeat alerts by chat/event/reason; Settings and assistant commands can acknowledge the current Teams urgency.
- Teams Graph reply detection can acknowledge an active urgency when a reply appears in the target Teams chat after Noor's last alert, preventing further alerts and phone escalation for that urgency.
- Unmatched WhatsApp direct messages now use a staged fallback: local rules first, Gemini second, Codex only if Gemini is unavailable or defers, then Teams if Raihan/manager input is required.
- Teams urgency defaults now allow up to five alerts before a one-time Find My Phone escalation, trying `Symphony innova30` before `Redmi 10`.
- Assistant dashboard and floating chat commands now dispatch slow answers in a background worker instead of blocking the Qt UI thread.
- Gemini/Codex answer work and Codex sessions now use a floating progress notice; successful completion dismisses it and errors stay visible with an `X` dismiss control.
- Hybrid voice listening now includes dictation fallback plus Gemini, Teams, and employee-report command phrases; the live voice defaults are 12 seconds and 25% minimum confidence.
- WhatsApp bridge status now treats an alive helper without Noor's dedicated Chrome profile as not connected, so stale bridge heartbeats no longer show "already open" or "connected."
- The WhatsApp open action now self-heals that orphaned-helper state by verifying the PID belongs to Noor's bridge, closing only that stale helper, clearing stale bridge files, and opening a fresh dedicated WhatsApp profile.
- Unmatched WhatsApp calls now bypass Gemini/Codex and escalate directly to Teams; Gemini/Codex fallback remains message-only.
- The UI is now assistant-style and scrollable, but more layout polish can still be added after testing on the real screen.

## Not Started Yet

- Microsoft Teams OAuth setup wizard and direct chat discovery for Graph mode.
- Google Find Hub manual device setup for additional devices.
- Real speech-to-action flows for arbitrary message composition.
- Full browser-controlled research sessions with screenshots and page extraction.
- Windows startup registration and system tray controls.
- Backup/restore/export UI.
- Structured parsing of Elementor, Gutenberg, content review, and plugin review reports inside the dashboard.
- A dedicated Employee Reports settings page for managing future team sheets and production WhatsApp groups.

## Next Recommended Development Order

1. Enable Google Tasks API and Google Calendar API for the Google Cloud project behind `E:\ESEO\content-review-manager\credentials.json`.
2. Refresh Noor's Tasks and Calendar pages, then test: `what are my todos`, `what reminders do I have`, and `what's on my calendar`.
3. Manually launch the app and review the cockpit UI on the actual display.
4. Add a dedicated Connections page with deeper Google, tools, projects, Codex, WhatsApp, and voice diagnostics.
5. Add structured report readers for the four existing tools.
6. Add a compact browser-research mode for deeper research tasks.
7. Add a compatible Gemini CLI authentication route if unknown-message replies are required without an API key.
8. Add a Teams OAuth setup wizard, chat picker, and safer token refresh flow.

## Security Notes

- Existing Google token and credential files are checked for presence only; contents are not opened or printed.
- No browser profile, token, or credential is committed.
- Employee report images are runtime output and ignored under `data\reports\`; private payroll, bank, NID, and personal-contact fields are excluded from WhatsApp report images.
- WhatsApp automatic sending is limited to unread direct chats that match a local rule or pass the unmatched-message Gemini/Codex fallback. Messages that still require Raihan/manager input are escalated to Teams. It has duplicate protection, chat/message verification, group exclusion by default, and an audit trail.
- Teams fallback does not reply in Teams. Graph mode can read the target chat only for active-urgency reply detection; local-window mode sends through the already-open Teams chat but still needs Graph read settings for automatic reply detection. Teams tokens and webhook URLs remain local configuration, and `data\teams_graph_token.txt` is ignored by git.
- Find My Phone opens Google Find Hub and clicks only the configured device plus `Play sound`; lost/reset actions remain untouched and manual.
- Voice uses local Windows speech APIs, not Gemini and not an AI model.

## Runtime Notes

- One Noor launch can appear as two `pythonw.exe` processes on this machine: the venv launcher at `E:\ESEO\standalone-windows-assistant\.venv\Scripts\pythonw.exe` and its child interpreter at `C:\Python314\pythonw.exe`. That is normal and not two assistant instances.
