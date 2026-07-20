# Security Notes

The first version keeps integrations local and disabled until configured.

## Secrets

Do not store these in source code:

- Google OAuth credentials
- Google refresh tokens
- WhatsApp browser profile cookies
- Teams browser profile cookies
- Google Find Hub browser profile cookies
- Codex authentication files
- private message logs
- production credentials

Existing sensitive files were detected but not opened:

- `E:\ESEO\content-review-manager\.secrets\token.json`
- `E:\ESEO\content-review-manager\credentials.json`
- `C:\Users\user\Desktop\WP-Plugin-Review-Assistant\settings.json`
- `C:\Users\user\Desktop\WP-Plugin-Review-Assistant\wp_plugin_review.log`

## Find Hub Rules

The assistant must never trigger lock, erase, factory reset, device security changes, or account changes.

Before automatic ringing is allowed:

- use a dedicated browser automation profile;
- require an explicit device allowlist;
- store stable identifying details for each approved device;
- require manual test success;
- require separate switches for owner phone and backup phone;
- require informed consent for a backup phone;
- add cooldowns and maximum attempts;
- stop when acknowledged.

## Teams Rules

Teams alerts should use privacy-safe summaries by default. Read receipts are not an acknowledgement.

## Codex Rules

Before a Codex run, verify:

- working directory;
- selected `AGENTS.md`;
- git status;
- dirty worktree state;
- analysis-only versus edit-allowed mode.

Risky operations remain approval-required by default.
