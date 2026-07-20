# Inspection Notes

Generated from the prompt file:

```text
C:\Users\user\Downloads\CODEX_PROMPT_STANDALONE_ASSISTANT_WITH_ESCALATION.md
```

Target project path:

```text
E:\ESEO\standalone-windows-assistant
```

## Existing Tools Inspected

### Content Review Manager

Path:

```text
E:\ESEO\content-review-manager
```

Observed files:

- `README.md`
- `requirements.txt`
- `run_app.bat`
- `run_writer_app.bat`
- `content_review_manager.py`
- `content_writer_manager.py`
- `content_review_app.py`
- `content_writer_app.py`
- `project.json`
- `projects.json`

Workflow:

- PySide/Python desktop app for Google Sheets, Docs, and Drive content review.
- CLI entry point: `content_review_manager.py review`.
- Supports `--dry-run`, `--limit`, `--all`, `--recheck-changes`, and `--recheck-all`.
- Uses a local `.venv`; system Python did not have Google packages installed, but `.venv\Scripts\python.exe content_review_manager.py --help` worked.
- Review manager writes review status to Google Sheets and creates issue Docs only for major issues.
- Writer assistant creates multi-tab Google Docs research packs and updates Sheet fields.

Security-sensitive files detected but not opened:

- `.secrets\token.json`
- `credentials.json`

Connector decision:

- Use the tool folder as a separate external tool.
- Prefer its `.venv\Scripts\python.exe` for CLI commands.
- Use `run_app.bat` and `run_writer_app.bat` for GUI launch.
- Treat Google OAuth and write operations as approval-sensitive.

### Websites Build With Elementor

Path:

```text
E:\ESEO\Websites-build-with-elementor
```

Observed files:

- `README.md`
- `preview_elementor_template.py`
- `elementor-ai-instruction-file.md`
- `.elementor-preview\`
- project/template folders

Workflow:

- Stores Elementor JSON templates.
- Provides a local preview and QA runner.
- Useful commands include `--list`, `--latest`, `--all`, `--no-server`, `--open`, `--report`, and `--strict`.
- Default preview server port is `8787`.

Git status:

- Dirty files and untracked files were present.
- The new assistant must not modify this folder unless explicitly requested.

Connector decision:

- Use `python preview_elementor_template.py --list` as the safe test command.
- Use `python preview_elementor_template.py --latest --no-server` as a safe secondary command.
- Open the folder with Explorer.

### Websites Build With Gutenberg

Path:

```text
E:\ESEO\Websites-build-with-gutenburg
```

Observed files:

- `README.md`
- `preview_gutenberg_blocks.ps1`
- `gutenberg-block-editor-ai-instruction-file.md`
- `.gutenberg-preview\`
- `.playwright-cli\`
- `examples\service-home-page.block.html`
- multiple project `.block.html` files

Workflow:

- Stores Gutenberg and Spectra serialized block markup.
- Provides PowerShell preview and QA runner.
- Useful commands include `-List`, `-Latest`, `-NoServer`, `-Open`, `-Strict`, and `-Report`.
- Default preview server port is `8788`.

Git status:

- Clean at inspection time.

Connector decision:

- Use PowerShell `-ExecutionPolicy Bypass -File .\preview_gutenberg_blocks.ps1 -List` as the safe test command.
- Use `-Latest -NoServer` as a safe secondary command.
- Open the folder with Explorer.

### WP Plugin Review Assistant

Path:

```text
C:\Users\user\Desktop\WP-Plugin-Review-Assistant
```

Observed files:

- `README.md`
- `requirements.txt`
- `pyproject.toml`
- `run.bat`
- `main.py`
- `src\`
- `tests\`
- multiple docs

Workflow:

- PySide6 Windows desktop app and CLI for WordPress/WooCommerce plugin review.
- CLI entry point: `python main.py --plugin ... --site ... --json ... --html ... --markdown ...`.
- Supports Plugin Check, deterministic static analysis, LocalWP runtime smoke, browser smoke, WPML review, PHPCS/WPCS integration, and report generation.
- Uses LocalWP and WP-CLI when available.

Git status:

- Dirty runtime/local files were present, including settings/log/cache files.
- The new assistant must not modify this folder unless explicitly requested.

Security-sensitive files detected but not opened:

- `settings.json`
- `wp_plugin_review.log`

Connector decision:

- Use `python main.py --help` as the safe test command.
- Use `run.bat` for GUI launch.
- Treat plugin install, dependency install, WP-CLI mutation, browser smoke auto-setup, and report writes as approval-sensitive.

## `AGENTS.md` Files

No `AGENTS.md` file was found inside the four listed tool folders during inspection.

The standalone assistant supports selecting an `AGENTS.md` manually when registering a development project. It does not assume that these tool folders contain project instructions.

## Codex CLI

Detected local CLI:

```text
codex-cli 0.128.0
```

Python subprocess detection had to prefer the PowerShell launcher:

```text
C:\Users\user\AppData\Roaming\npm\codex.ps1
```

Using the `.CMD` shim directly from Python produced an access-denied result on this machine, so the assistant uses the `.ps1` launcher.

## Architecture Chosen

The first version is a local-first PySide6 application with these layers:

- `config\tools.json`: connector definitions for existing tools.
- `config\app_settings.json`: default approval, privacy, escalation, and Codex settings.
- `src\standalone_assistant\core\storage.py`: SQLite persistence and audit logging.
- `src\standalone_assistant\core\connectors.py`: safe tool test/open/run adapter.
- `src\standalone_assistant\core\project_scanner.py`: git, `AGENTS.md`, and Codex preflight.
- `src\standalone_assistant\ui\main_window.py`: desktop UI pages.
- `data\assistant.sqlite`: runtime database created on first check or launch.
- `data\codex-sessions\`: Codex transcript output.

## Practical Development Order

1. Application shell, local data, settings, tasks, rules, tools, project registration, and Codex sessions.
2. Add structured tool adapters for content review, Elementor preview reports, Gutenberg preview reports, and WP Plugin Review Assistant report reads.
3. Add WhatsApp Web read-and-draft automation with a dedicated Playwright profile.
4. Add approval workflow, duplicate protection, quiet hours, send limits, and audit review.
5. Add Teams notification automation after acknowledgement tracking is reliable.
6. Add Google Find Hub ringing only after manual device selection, stable allowlist verification, cooldowns, and successful manual tests.
7. Add Gemini CLI as an optional language provider, not as a hard dependency.

## Current Limitations

- Live WhatsApp, Teams, and Find Hub automation are not enabled.
- The assistant can create manual WhatsApp approval drafts, but does not read WhatsApp Web yet.
- Codex execution is non-interactive `codex exec` with live output capture; resume opens the Codex resume command in a terminal.
- Tool connector commands are configured but deeper structured report parsing is not implemented yet.
- Existing tool folders with dirty worktrees were left untouched.
