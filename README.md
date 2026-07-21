# Noor Assistant

Local-first PySide6 desktop assistant for Raihan Hossain's daily work, connected tools, project tracking, Codex sessions, tasks, rules, approvals, and escalation state. The assistant is `Noor`.

## Launch

Double-click:

```powershell
E:\ESEO\standalone-windows-assistant\run_app.bat
```

Or from PowerShell:

```powershell
cd /d E:\ESEO\standalone-windows-assistant
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

Quick local check:

```powershell
.\.venv\Scripts\python.exe main.py --check
```

## First Version Scope

Built now:

- Assistant-style cockpit dashboard with central avatar, connection cards, command bar, and quick actions
- PySide6 Windows application shell
- Local SQLite database under `data\assistant.sqlite`
- Seeded connector registry for Google Workspace and the tools you listed
- Seeded project registry for the assistant and ESEO tool folders
- Connected Tools page with path status, test command, open command, safe command execution, and errors
- Development Projects page with project registration, `AGENTS.md` selection, git preflight, dirty-worktree warning, and Codex run output
- Codex Sessions page with transcript history
- Tasks and Calendar pages
- Rules page with approval defaults and sample-message testing
- Knowledge page for local trusted notes
- Assistant Chat page for local command capture, quick tasks, and quick notes
- Event-driven WhatsApp Web bridge with an isolated `whatsapp-web.js` session, rule replies, and audit history
- Reply Approvals and Urgent Escalations pages with persistent incident state
- Settings page for approval, escalation, and AI brain switches
- Settings voice controls for installed Windows voices, speed, volume, and listen timeout
- Activity History page
- Local text-to-speech and first-pass push-to-talk voice commands without Gemini or AI
- Visible voice diagnostics showing what the assistant heard and the recognition confidence
- Basic assistant chat for greetings, status questions, weather, news, web research, and optional AI fallback answers
- Tool and project knowledge from the local connector/project registries
- Google Tasks and Google Calendar productivity commands after one-time OAuth authorization
- Find My Phone launcher through Google Find Hub
- Scrollable pages so the assistant cockpit remains usable on smaller windows
- Assistant dashboard usage cards for Codex weekly quota windows and Noor-tracked Gemini daily request usage
- One-way Microsoft Teams fallback alerts through Graph, webhook, or the open Teams window for WhatsApp events Noor cannot answer
- Employee report weekend settings; Friday is the default weekend, so weekly reports count Saturday-Thursday workweeks
- Non-blocking assistant and floating chat command handling, so slow research/report/AI answers do not freeze the window

Not live yet:

- Bulk messaging, chat-history export, and group-chat auto replies.
- Microsoft Teams OAuth setup wizard and Teams reply handling
- Automatic reminder sending outside Google Calendar event reminders
- Free-form conversational voice understanding
- Full browser-controlled research sessions

Those are intentionally staged behind manual setup, acknowledgement tracking, audit history, allowlists, and test controls.

## WhatsApp Web Bridge

Noor uses an isolated `whatsapp-web.js` session at `data\whatsapp-webjs-auth`; she never reads or writes your normal Chrome profile. Noor starts the bridge automatically when the app opens. Scan its QR code once if the dedicated session is not authenticated. The bridge is intentionally narrow:

- It receives new direct-message events instead of polling or exporting chat history.
- It records a stable message fingerprint for duplicate protection. Incoming message text is not stored unless **Store message previews** is enabled in Settings.
- Matching rules decide whether to send a direct reply, run an assistant action, call research/Gemini/Codex, run a configured safe tool command, or combine multiple actions.
- The WhatsApp Rules page manages rule ID, audience, multiple triggers, and multiple actions without requiring manual JSON edits. The JSON file remains the portable storage format at `config\whatsapp_reply_rules.json`.
- Unmatched direct messages can use Gemini, then Codex, then Teams fallback when enabled. There are no WhatsApp quiet-hour, cooldown, or hourly reply limits.
- Groups are skipped by default. Duplicate protection, chat verification immediately before sending, and audit history are applied before a reply is sent.
- It uses an unofficial WhatsApp Web client, so WhatsApp-side changes can require a library update.

### Automatic Replies

When automatic replies are enabled in **Settings**, Noor processes new direct-message events from the dedicated bridge. A `hello` or `hi` matches the included greeting rule and replies automatically from any direct contact. Add or change rules on the **WhatsApp Rules** page; they apply on the next check. Rules support message, call, date, and time triggers; everyone, specific-contact, and excluded-contact audiences; and `reply`, `assistant`, `ai`, `research`, `gemini`, `codex`, `tool`, and log actions. When no rule matches, the optional unmatched-message fallback asks Gemini first, uses Codex only when Gemini is unavailable or defers, and escalates to Teams if Raihan/manager input is required.

Install the local browser runtime once after installing dependencies:

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

### Teams Fallback Alerts

Noor can send a Teams alert when a WhatsApp message or call cannot be answered because no WhatsApp rule matched, a matched rule failed, or WhatsApp sending failed. Noor does not reply through Teams. When Graph read access is configured, Noor can detect a reply in the target Teams chat and acknowledge the active urgency so no more Teams alerts or phone rings are sent for that WhatsApp issue.

Configure this in **Settings > Microsoft Teams Alerts**:

- **Graph direct chat** sends from a delegated work or school Teams account to an existing Teams chat using Microsoft Graph.
- **Incoming webhook channel** posts to a Teams channel webhook. Webhooks do not send direct user-to-user chats.
- **Open Teams window** activates the already-open Teams desktop app or Teams tab in Chrome, focuses the compose box, pastes the alert, and sends it to the visible chat.

For Graph mode, provide the target `chat_id` in Settings or `NOOR_TEAMS_CHAT_ID`, and put a delegated access token in `NOOR_TEAMS_GRAPH_TOKEN` or `data\teams_graph_token.txt`. That same token is used for optional reply detection if it can read chat messages. Set the optional sender user ID in Settings or `NOOR_TEAMS_SENDER_USER_ID` so Noor can ignore its own Graph-sent messages precisely. Local token files are ignored by git.

For open-window mode, keep the target one-contact Teams chat open. The Settings field `Target window` controls which window title Noor activates.

Teams fallback keeps one active urgency record per WhatsApp chat/event/reason. Before sending another alert for the same urgency, Noor checks for a Teams reply when Graph reply detection is configured. By default Noor sends at most five Teams alerts for the active urgency, then suppresses repeats until the urgency is acknowledged or the silence window expires. After the fifth Teams alert, Noor rings one phone once through Find My Phone, trying `Symphony innova30` before `Redmi 10`. Use **Acknowledge Current Teams Urgency** in Settings, or ask Noor `teams ack`, `acknowledge teams`, or `stop teams urgency` after you handle the Teams alert.

### Employee Report Calendar

Weekly and monthly employee reports use **Settings > Employee Reports > Weekend days**. Friday is selected by default, so Sunday is counted as a working day and weekly reports cover Saturday through Thursday. Change the checked days in Settings if the team weekend changes.

## AI Brain Fallbacks

Noor's default brain is local and deterministic: rules, trusted notes, tool/project registries, and direct status checks run before any external AI. For unknown questions, Settings can enable this fallback order:

1. Reuse a recent cached answer.
2. Do lightweight web research and answer from extracted source evidence when confidence is medium or high.
3. Use Gemini CLI with bounded context and a strict timeout.
4. Use Codex CLI if Gemini fails or is unavailable.

Gemini is detected on Windows using `where gemini`; Codex is detected using the local `codex` launcher. The Codex fallback is separate from editable Codex sessions and runs read-only with `gpt-5-mini`, low reasoning, ephemeral mode, and a short timeout by default. WhatsApp uses local rules first; unmatched direct messages can ask Gemini, fall back to Codex when Gemini is unavailable or defers, and escalate to Teams when Raihan/manager input is required.

When Gemini or Codex is preparing an answer, Noor shows a floating progress notice. Successful answers dismiss the notice; errors and attention states stay visible until the `X` button is clicked.

The Assistant dashboard shows local usage indicators:

- Codex reads the latest local Codex session `rate_limits` data and shows the reported window remaining, normally the weekly `7d` window. If the `5h` window is not reported locally, Noor says so instead of guessing.
- Gemini shows Noor's locally tracked Gemini fallback calls for today against the configured daily request limit. The default is `1000` for Google account Gemini Code Assist individual usage and can be changed in Settings.

## Connected Tools

The initial registry is in:

```text
config\tools.json
```

It references these existing tools without copying or modifying them:

- Google Workspace through `E:\ESEO\content-review-manager` OAuth setup
- `E:\ESEO\content-review-manager`
- `E:\ESEO\Websites-build-with-elementor`
- `E:\ESEO\Websites-build-with-gutenburg`
- `C:\Users\user\Desktop\WP-Plugin-Review-Assistant`

Credentials, browser profiles, tokens, logs, and tool databases are not copied into this project.

## Codex Behavior

The app detects the local Codex CLI through the Windows PowerShell launcher. On this machine the check found:

```text
codex-cli 0.128.0
```

Codex sessions run with:

- `read-only` sandbox when file changes are not allowed
- `workspace-write` sandbox when file changes are allowed
- selected project path passed with `-C`
- selected `AGENTS.md` path included in the prompt context
- live output saved to `data\codex-sessions\`

The app warns before allowing edits in a dirty git worktree.

For answer fallback only, Noor uses Codex in a cheaper non-editing mode: read-only sandbox, `gpt-5-mini`, low reasoning, no approvals, ephemeral session, and `--output-last-message`.

## Google Tasks And Calendar

The existing Google connection in `E:\ESEO\content-review-manager` covers Sheets, Docs, and Drive. Google Tasks and Calendar require one more OAuth approval because they use different scopes.

In the assistant, say or type:

```text
connect google productivity
```

After the browser approval finishes, Noor stores the new Tasks/Calendar token locally at:

```text
data\google_productivity_token.json
```

That file is ignored by git. After connecting, you can say or type:

- `create todo call client tomorrow at 10 am`
- `add task review homepage today at 4 pm`
- `remind me to check WhatsApp tomorrow at 9 am`
- `schedule meeting with team tomorrow at 3 pm`
- `what's on my calendar`

If Noor says Google productivity is authorized but data cannot be read, enable these APIs for the Google Cloud project used by `E:\ESEO\content-review-manager\credentials.json`:

- Google Tasks API
- Google Calendar API

The Tasks and Calendar pages include an `Open Google API Setup` button for this.

## Voice

Voice does not use Gemini or any AI provider. It uses Windows speech APIs through PowerShell.

On this machine, installed voices include:

- `Microsoft David Desktop`
- `Microsoft Zira Desktop`

The assistant now defaults to Edge Neural TTS with `en-US-JennyNeural` for a more human female voice. If Edge TTS is unavailable, it falls back to Windows desktop voices such as `Microsoft Zira Desktop`. You can change the provider and voice in Settings.

The Listen button now uses hybrid productivity mode by default. It understands exact commands, constrained dictation after phrases like `create todo`, `remind me to`, `schedule meeting`, and `research`, plus open dictation fallback for normal questions. Unclear microphone captures below the configured confidence threshold are rejected.

Settings can also be changed by asking:

- `use edge voice`
- `use windows voice`
- `make voice faster`
- `make voice slower`
- `set voice confidence to fifty`
- `set listen timeout to ten`
- `switch to command mode`
- `switch to hybrid mode`
- `switch to dictation mode`

Supported first-pass voice commands include:

- `open tools`
- `open projects`
- `open codex`
- `open tasks`
- `open settings`
- `show approvals`
- `read summary`
- `test connections`
- `pause escalations`
- `add task ...`
- `connect google productivity`
- `create todo ...`
- `remind me to ...`
- `schedule meeting ...`
- `what's on my calendar`

## Assistant Questions

Noor uses a local deterministic brain, local trusted notes, tool/project registries, and lightweight web research first. Optional Gemini and Codex fallbacks are used only after local/research answers are not reliable enough, with caching and hourly limits to control usage.

The assistant can answer basic local and current questions:

- `hi`
- `tool status`
- `project status`
- `what is Content Review Manager`
- `Google status`
- `Codex status`
- `WhatsApp status`
- `connection status`
- `weather in Dhaka`
- `latest news technology`
- `research WordPress plugin security`

Current weather/news/research use lightweight web requests. They return compact summaries and source links where available.

For Google productivity, ask:

- `google productivity status`
- `what are my todos`
- `what reminders do I have`
- `what's upcoming`
- `what's on my calendar`

## Local Data

Runtime data lives under:

```text
data\
```

Ignored local-only paths include:

- `.venv\`
- `data\*.sqlite`
- `data\codex-sessions\`
- `browser-profiles\`
- `config\local_settings.json`
- logs and Python cache files

## Current Safety Defaults

- Sending messages is approval-required.
- Risky commands are approval-required.
- File deletion, database changes, dependency installation, plugin installation, commits, pushes, pull requests, and production changes are approval-required.
- Escalation integrations are disabled until configured and tested.
- Find My Phone opens Google Find Hub in play-sound-only mode; the browser session still controls device selection.
- No credentials are stored in source code.
