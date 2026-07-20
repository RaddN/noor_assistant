from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from time import monotonic, sleep, time


def emit(ok: bool, message: str, *, data: dict | None = None, error: str = "") -> int:
    print(json.dumps({"ok": ok, "message": message, "data": data or {}, "error": error}, ensure_ascii=True))
    return 0 if ok else 1


def load_selectors() -> dict[str, list[str]]:
    path = Path(__file__).resolve().parents[1] / "config" / "whatsapp_web_selectors.json"
    return json.loads(path.read_text(encoding="utf-8"))


def read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=True), encoding="utf-8")
    for attempt in range(10):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            sleep(0.05)


class WhatsAppAdapter:
    """Selectors are intentionally isolated in config/whatsapp_web_selectors.json."""

    def __init__(self, page, selectors: dict[str, list[str]]) -> None:
        self.page = page
        self.selectors = selectors

    def first(self, name: str):
        for selector in self.selectors.get(name, []):
            locator = self.page.locator(selector)
            if locator.count() > 0:
                return locator.first
        return None

    def logged_in(self) -> bool:
        return self.first("logged_in") is not None

    def login_required(self) -> bool:
        return self.first("login_qr") is not None

    def marker_counts(self) -> dict[str, int]:
        """Neutral selector diagnostics only; never expose chats or message content."""
        return {
            name: sum(self.page.locator(selector).count() for selector in values)
            for name, values in self.selectors.items()
            if name != "outgoing_metadata_markers"
        }

    def selected_chat(self) -> tuple[str | None, list[str]]:
        title = self.first("chat_title")
        chat = title.inner_text().strip() if title else ""
        if not chat or chat.lower() == "whatsapp":
            return None, []
        messages: list[str] = []
        for selector in self.selectors.get("message_rows", []):
            rows = self.page.locator(selector)
            if rows.count() > 0:
                # This is bounded to the currently selected conversation. It never scans the chat list.
                messages = [value.strip() for value in rows.all_inner_texts()[-25:] if value.strip()]
                break
        return chat, messages

    def incoming_messages(self, chat: str) -> list[dict[str, str]]:
        rows = self.page.locator("[data-pre-plain-text]")
        outgoing_markers = [value.lower() for value in self.selectors.get("outgoing_metadata_markers", ["you:"])]
        messages: list[dict[str, str]] = []
        for row in rows.all()[-25:]:
            raw_metadata = row.get_attribute("data-pre-plain-text") or ""
            metadata = raw_metadata.lower()
            is_outgoing = bool(row.evaluate("element => Boolean(element.closest('.message-out'))"))
            if is_outgoing or any(marker in metadata for marker in outgoing_markers):
                continue
            body = row.inner_text().strip()
            if body:
                messages.append({"hash": hashlib.sha256(f"{chat}\n{raw_metadata}\n{body}".encode("utf-8")).hexdigest(), "text": body})
        return messages

    def selected_unread_messages(self, chat: str) -> list[dict[str, str]]:
        """Read only incoming messages rendered after WhatsApp's unread divider."""
        divider = self.first("unread_divider")
        if not divider:
            return []
        divider_box = divider.bounding_box()
        if not divider_box:
            return []
        unread: list[dict[str, str]] = []
        for message in self.incoming_messages(chat):
            # Re-find the bounded current rows by their hash inputs, then keep rows below the divider.
            # The current chat stays at the bottom while this single inbox action is processed.
            for row in self.page.locator("[data-pre-plain-text]").all()[-25:]:
                raw_metadata = row.get_attribute("data-pre-plain-text") or ""
                body = row.inner_text().strip()
                row_hash = hashlib.sha256(f"{chat}\n{raw_metadata}\n{body}".encode("utf-8")).hexdigest()
                row_box = row.bounding_box()
                if row_hash == message["hash"] and row_box and row_box["y"] > divider_box["y"]:
                    unread.append(message)
                    break
        return unread

    def chat_activity(self, known_hashes: set[str]) -> dict:
        """Detect recent chat-list changes without exporting previews or message text."""
        grid = self.page.locator("[role='grid'][aria-label='Chat list']")
        if grid.count() == 0:
            return {"ok": False, "message": "WhatsApp chat list was not found.", "data": {}, "error": ""}
        rows_locator = grid.first.locator("[role='row']")
        row_data = rows_locator.evaluate_all(
            """rows => rows.slice(0, 25).map((row, index) => ({
                index,
                height: row.getBoundingClientRect().height,
                titles: Array.from(row.querySelectorAll('[title]')).slice(0, 5).map(node => node.getAttribute('title') || ''),
                raw: row.innerText || ''
            }))"""
        )
        observed: list[str] = []
        candidates: list[tuple[int, str, str]] = []
        seen: set[str] = set()
        for item in row_data:
            if float(item.get("height") or 0) < 30:
                continue
            chat = ""
            for value in item.get("titles", []):
                value = str(value).strip()
                if value:
                    chat = value
                    break
            raw = str(item.get("raw") or "").strip()
            if not chat:
                chat = next((line.strip() for line in raw.splitlines() if line.strip()), "")
            if not chat or chat in seen:
                continue
            seen.add(chat)
            fingerprint = hashlib.sha256(f"{chat}\n{raw}".encode("utf-8")).hexdigest()
            observed.append(fingerprint)
            if fingerprint not in known_hashes:
                candidates.append((int(item["index"]), chat, fingerprint))
        if not known_hashes or not candidates:
            return {"ok": True, "message": "WhatsApp chat activity scanned.", "data": {"has_unread": False, "observed_hashes": observed}, "error": ""}
        row_index, expected_chat, _ = candidates[0]
        try:
            rows_locator.nth(row_index).click(timeout=5000)
            self.page.wait_for_timeout(350)
        except Exception as exc:
            return {"ok": False, "message": "Could not open newly active WhatsApp chat.", "data": {"observed_hashes": observed}, "error": str(exc)[:180]}
        chat, _ = self.selected_chat()
        if not chat or chat.casefold() != expected_chat.casefold():
            return {"ok": False, "message": "Chat changed during activity check; reply blocked.", "data": {"observed_hashes": observed}, "error": ""}
        incoming = self.selected_unread_messages(chat)
        is_group = any(self.page.locator(selector).count() > 0 for selector in self.selectors.get("group_indicators", []))
        return {
            "ok": True,
            "message": "New WhatsApp chat activity checked.",
            "data": {"has_unread": bool(incoming), "chat": chat, "is_group": is_group, "incoming_messages": incoming[-5:], "observed_hashes": observed},
            "error": "",
        }

    def next_unread_chat(self) -> dict:
        """Open one unread chat only. This is a bounded inbox check, not a history scan."""
        badge = self.first("unread_badges")
        row = badge.locator("xpath=ancestor::*[@role='row' or @role='listitem'][1]") if badge else None
        if row and row.count() == 0:
            row = None
        if not row:
            chat, _ = self.selected_chat()
            if chat:
                incoming = self.selected_unread_messages(chat)
                if incoming:
                    is_group = any(self.page.locator(selector).count() > 0 for selector in self.selectors.get("group_indicators", []))
                    return {
                        "ok": True,
                        "message": "Unread WhatsApp message found in the selected chat.",
                        "data": {"has_unread": True, "chat": chat, "is_group": is_group, "incoming_messages": incoming[-5:]},
                        "error": "",
                    }
            return {"ok": True, "message": "No unread WhatsApp chats found.", "data": {"has_unread": False}, "error": ""}
        try:
            row.click(timeout=5000)
            self.page.wait_for_timeout(350)
        except Exception as exc:
            return {"ok": False, "message": "Could not open the next unread WhatsApp chat.", "data": {}, "error": str(exc)[:180]}
        chat, _ = self.selected_chat()
        if not chat:
            return {"ok": False, "message": "Unread chat could not be verified after opening it.", "data": {}, "error": ""}
        is_group = any(self.page.locator(selector).count() > 0 for selector in self.selectors.get("group_indicators", []))
        return {
            "ok": True,
            "message": "Unread WhatsApp chat opened.",
            "data": {"has_unread": True, "chat": chat, "is_group": is_group, "incoming_messages": self.incoming_messages(chat)[-5:]},
            "error": "",
        }

    def inbox_diagnostics(self) -> dict:
        """Return structural inbox data only, never contact names or message bodies."""
        tabs = self.page.locator("[role='tab']")
        labels: list[str] = []
        for index in range(min(tabs.count(), 10)):
            label = (tabs.nth(index).get_attribute("aria-label") or "").strip()
            if label:
                labels.append(label[:80])
        return {
            "ok": True,
            "message": "WhatsApp inbox selector diagnostics.",
            "data": {
                "tab_labels": labels,
                "unread_selector_found": self.first("unread_tab") is not None,
                "unread_badge_count": sum(self.page.locator(selector).count() for selector in self.selectors.get("unread_badges", [])),
                "chat_grid_count": self.page.locator("[role='grid'][aria-label='Chat list']").count(),
                "configured_row_count": sum(self.page.locator(selector).count() for selector in self.selectors.get("unread_rows", [])),
            },
            "error": "",
        }

    def capture_diagnostic(self, output_path: Path) -> dict:
        self.page.screenshot(path=str(output_path), full_page=False)
        return {"ok": True, "message": "WhatsApp diagnostic screenshot captured.", "data": {}, "error": ""}

    def send_reply(self, expected_chat: str, expected_message_hash: str, reply: str) -> tuple[bool, str]:
        selected_chat, _ = self.selected_chat()
        if not selected_chat or selected_chat.casefold() != expected_chat.casefold():
            return False, "Selected chat changed before sending; reply blocked."
        if expected_message_hash and expected_message_hash not in {item["hash"] for item in self.incoming_messages(selected_chat)}:
            return False, "The incoming message changed before sending; reply blocked."
        composer = self.first("composer")
        if not composer:
            return False, "WhatsApp composer was not found; reply blocked."
        composer.fill(reply)
        send_button = self.first("send_button")
        if not send_button:
            composer.fill("")
            return False, "WhatsApp send button was not found; reply blocked."
        send_button.click()
        return True, "Reply sent."


def selected_chat_response(adapter: WhatsAppAdapter, *, include_messages: bool = False) -> dict:
    if not adapter.logged_in():
        return {"ok": False, "message": "WhatsApp Web is not connected in Noor's dedicated profile.", "data": {"connected": False}, "error": ""}
    chat, messages = adapter.selected_chat()
    if not chat:
        return {"ok": False, "message": "No chat is selected. Choose one chat in Noor's dedicated WhatsApp window, then try again.", "data": {"connected": True}, "error": ""}
    hashes = [hashlib.sha256(f"{chat}\n{message}".encode("utf-8")).hexdigest() for message in messages]
    data = {"chat": chat, "message_hashes": hashes}
    if include_messages:
        data["incoming_messages"] = adapter.incoming_messages(chat)
    return {"ok": True, "message": "Selected chat read.", "data": data, "error": ""}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("serve", "status", "read-selected"))
    parser.add_argument("--profile-dir", required=True)
    parser.add_argument("--state-dir")
    args = parser.parse_args()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return emit(False, "Playwright is not installed.", error="Run pip install -r requirements.txt and python -m playwright install chromium.")

    if args.command == "serve" and not args.state_dir:
        return emit(False, "The persistent WhatsApp bridge needs a state directory.")
    try:
        selectors = load_selectors()
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(args.profile_dir, headless=False, viewport={"width": 1280, "height": 900})
            page = context.pages[0] if context.pages else context.new_page()
            page.goto("https://web.whatsapp.com/", wait_until="domcontentloaded", timeout=30000)
            adapter = WhatsAppAdapter(page, selectors)
            if args.command != "serve":
                deadline = monotonic() + 20
                while monotonic() < deadline and not adapter.logged_in() and not adapter.login_required():
                    sleep(1)
                if args.command == "status":
                    connected = adapter.logged_in()
                    diagnostics = {"connected": connected, "login_visible": adapter.login_required(), "markers": adapter.marker_counts()}
                    context.close()
                    return emit(connected, "WhatsApp Web is connected in Noor's dedicated Playwright profile." if connected else "WhatsApp Web is not connected in Noor's dedicated profile.", data=diagnostics)
                payload = selected_chat_response(adapter)
                context.close()
                return emit(bool(payload["ok"]), str(payload["message"]), data=payload["data"], error=str(payload["error"]))

            state_dir = Path(args.state_dir)
            state_dir.mkdir(parents=True, exist_ok=True)
            status_path = state_dir / "status.json"
            request_path = state_dir / "request.json"
            response_path = state_dir / "response.json"
            last_request = ""
            while True:
                connected = adapter.logged_in()
                write_json(
                    status_path,
                    {
                        "connected": connected,
                        "login_visible": adapter.login_required(),
                        "process_id": os.getpid(),
                        "updated_at": time(),
                    },
                )
                request = read_json(request_path)
                request_id = str((request or {}).get("id") or "")
                if request_id and request_id != last_request:
                    last_request = request_id
                    action = str(request.get("action") or "")
                    try:
                        if action == "read-selected":
                            payload = selected_chat_response(adapter)
                        elif action == "next-unread":
                            payload = adapter.next_unread_chat() if connected else {"ok": False, "message": "WhatsApp Web is not connected in Noor's dedicated profile.", "data": {"connected": False}, "error": ""}
                        elif action == "scan-activity":
                            known = {str(value) for value in request.get("known_hashes", []) if len(str(value)) == 64}
                            payload = adapter.chat_activity(known) if connected else {"ok": False, "message": "WhatsApp Web is not connected in Noor's dedicated profile.", "data": {"connected": False}, "error": ""}
                        elif action == "inbox-diagnostics":
                            payload = adapter.inbox_diagnostics() if connected else {"ok": False, "message": "WhatsApp Web is not connected in Noor's dedicated profile.", "data": {"connected": False}, "error": ""}
                        elif action == "capture-diagnostic":
                            payload = adapter.capture_diagnostic(state_dir / "diagnostic.png") if connected else {"ok": False, "message": "WhatsApp Web is not connected in Noor's dedicated profile.", "data": {"connected": False}, "error": ""}
                        elif action == "send-reply":
                            expected_chat = str(request.get("expected_chat") or "")
                            expected_message_hash = str(request.get("expected_message_hash") or "")
                            reply = str(request.get("reply") or "").strip()
                            if not expected_chat or not reply:
                                payload = {"ok": False, "message": "Reply request is incomplete.", "data": {}, "error": ""}
                            else:
                                ok, message = adapter.send_reply(expected_chat, expected_message_hash, reply)
                                payload = {"ok": ok, "message": message, "data": {"chat": expected_chat}, "error": ""}
                        else:
                            payload = {"ok": False, "message": "Unsupported WhatsApp bridge action.", "data": {}, "error": action}
                    except Exception as exc:
                        payload = {"ok": False, "message": "WhatsApp browser action failed without sending a message.", "data": {}, "error": str(exc)[:300]}
                    payload["id"] = request_id
                    write_json(response_path, payload)
                sleep(0.25)
    except Exception as exc:
        return emit(False, "WhatsApp Web bridge could not complete the request.", error=str(exc)[:300])


if __name__ == "__main__":
    raise SystemExit(main())
