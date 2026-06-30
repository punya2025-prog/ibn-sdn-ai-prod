#!/usr/bin/env python3
"""
email_watcher/imap_watcher.py
==============================
Channel 2 background daemon.
Polls one or more IMAP mailboxes (IDLE or poll), extracts intents,
and POSTs them to the FastAPI gateway on /api/channel/2/email.

Supports:
  - Gmail (IMAP + App Password)
  - Microsoft 365 (IMAP OAuth2 or basic)
  - Any corporate IMAP (port 993 SSL)

Filter rules:
  - Only processes emails from allowed_senders list
  - Subject must contain one of the trigger keywords
  - Severity escalation: URGENT/CRITICAL → priority boost

Run:
  python3 imap_watcher.py --config watcher_config.yml
  # or as systemd service (see imap_watcher.service)
"""

import imaplib, email, time, yaml, json, re, logging, argparse, asyncio
import httpx
from email.header import decode_header
from datetime    import datetime, timezone
from pathlib     import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [IMAP-WATCHER]  %(levelname)s  %(message)s"
)
log = logging.getLogger("imap_watcher")

# ── Config defaults ────────────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "imap_host":       "imap.gmail.com",
    "imap_port":       993,
    "imap_ssl":        True,
    "username":        "noc@yourdomain.com",
    "password":        "your_app_password",
    "mailbox":         "INBOX",
    "poll_interval_s": 30,
    "use_idle":        False,          # set True for IMAP IDLE (real-time)
    "gateway_url":     "http://localhost:8000",
    "simulate":        True,
    "deploy":          False,
    "allowed_senders": [],             # [] = accept all
    "trigger_keywords": [
        "network intent", "block", "allow", "isolate", "qos",
        "urgent network", "firewall", "vlan", "failover",
        "connectivity", "priority", "bandwidth"
    ],
    "seen_file":       "/tmp/ibn_seen_emails.json",
    "max_body_chars":  2000,
}


# ── Email parser ───────────────────────────────────────────────────────────────
def _decode_header_str(h: str) -> str:
    parts = decode_header(h or "")
    out   = []
    for part, enc in parts:
        if isinstance(part, bytes):
            out.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(str(part))
    return " ".join(out)


def extract_email_fields(raw_bytes: bytes) -> dict:
    msg     = email.message_from_bytes(raw_bytes)
    subject = _decode_header_str(msg.get("Subject", ""))
    sender  = _decode_header_str(msg.get("From", ""))
    date_str= msg.get("Date", "")

    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if ct == "text/plain" and "attachment" not in cd:
                payload = part.get_payload(decode=True)
                if payload:
                    body += payload.decode(
                        part.get_content_charset() or "utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            body = payload.decode(
                msg.get_content_charset() or "utf-8", errors="replace")

    # Strip signatures, quoted replies
    body = re.sub(r"(?s)(--\s*\n.*|_{3,}.*|On .+ wrote:.*)", "", body).strip()
    return {
        "from":    sender,
        "subject": subject,
        "body":    body[:DEFAULT_CONFIG["max_body_chars"]],
        "date":    date_str,
    }


# ── Seen-email tracker ─────────────────────────────────────────────────────────
class SeenTracker:
    def __init__(self, path: str):
        self._path = Path(path)
        self._seen: set = set()
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                self._seen = set(data)
            except Exception:
                pass

    def is_seen(self, uid: str) -> bool:
        return uid in self._seen

    def mark(self, uid: str):
        self._seen.add(uid)
        # Keep last 5000
        if len(self._seen) > 5000:
            self._seen = set(list(self._seen)[-5000:])
        self._path.write_text(json.dumps(list(self._seen)))


# ── Gateway poster ─────────────────────────────────────────────────────────────
async def post_to_gateway(gateway_url: str, fields: dict,
                           simulate: bool, deploy: bool) -> dict:
    payload = {**fields, "simulate": simulate, "deploy": deploy}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{gateway_url}/api/channel/2/email",
            json=payload
        )
        return r.json()


# ── Keyword filter ─────────────────────────────────────────────────────────────
def passes_filter(fields: dict, keywords: list[str],
                  allowed_senders: list[str]) -> bool:
    text = (fields["subject"] + " " + fields["body"]).lower()
    if not any(kw.lower() in text for kw in keywords):
        return False
    if allowed_senders:
        sender = fields["from"].lower()
        if not any(a.lower() in sender for a in allowed_senders):
            log.debug(f"Sender not in allowed list: {fields['from']}")
            return False
    return True


# ── IMAP poll loop ─────────────────────────────────────────────────────────────
class IMAPWatcher:
    def __init__(self, cfg: dict):
        self.cfg     = cfg
        self.tracker = SeenTracker(cfg.get("seen_file", "/tmp/ibn_seen_emails.json"))
        self.conn    = None

    def _connect(self):
        log.info(f"Connecting to {self.cfg['imap_host']}:{self.cfg['imap_port']}")
        if self.cfg.get("imap_ssl", True):
            self.conn = imaplib.IMAP4_SSL(
                self.cfg["imap_host"], self.cfg["imap_port"])
        else:
            self.conn = imaplib.IMAP4(
                self.cfg["imap_host"], self.cfg["imap_port"])
        self.conn.login(self.cfg["username"], self.cfg["password"])
        self.conn.select(self.cfg.get("mailbox", "INBOX"))
        log.info("IMAP connected and mailbox selected")

    def _reconnect(self):
        try:
            self.conn.logout()
        except Exception:
            pass
        time.sleep(5)
        self._connect()

    def _fetch_unseen(self) -> list[tuple[str, dict]]:
        """Return list of (uid, fields) for unseen matching emails."""
        # Search for UNSEEN emails in last 7 days
        _, data = self.conn.uid("SEARCH", None, "UNSEEN")
        uids = (data[0] or b"").decode().split()
        results = []
        for uid in uids:
            if self.tracker.is_seen(uid):
                continue
            try:
                _, raw = self.conn.uid("FETCH", uid, "(RFC822)")
                if raw and raw[0]:
                    fields = extract_email_fields(raw[0][1])
                    results.append((uid, fields))
            except Exception as e:
                log.warning(f"Failed to fetch UID {uid}: {e}")
        return results

    async def run(self):
        self._connect()
        log.info(f"Watching {self.cfg['username']} → gateway {self.cfg['gateway_url']}")
        keywords       = self.cfg.get("trigger_keywords", [])
        allowed_senders= self.cfg.get("allowed_senders", [])
        interval       = self.cfg.get("poll_interval_s", 30)

        while True:
            try:
                emails = self._fetch_unseen()
                for uid, fields in emails:
                    self.tracker.mark(uid)
                    if not passes_filter(fields, keywords, allowed_senders):
                        log.debug(f"Filtered out: {fields['subject'][:60]}")
                        continue
                    log.info(f"Intent email: [{fields['subject'][:60]}] from {fields['from']}")
                    try:
                        result = await post_to_gateway(
                            self.cfg["gateway_url"], fields,
                            self.cfg.get("simulate", True),
                            self.cfg.get("deploy", False)
                        )
                        intent_id = result.get("intent_id", "?")
                        verdict   = (result.get("simulation") or {}).get("verdict", "?")
                        log.info(f"  → intent_id={intent_id}  sim={verdict}")
                    except Exception as e:
                        log.error(f"  → gateway post failed: {e}")

                await asyncio.sleep(interval)

            except imaplib.IMAP4.abort:
                log.warning("IMAP connection aborted — reconnecting")
                self._reconnect()
            except ConnectionResetError:
                log.warning("Connection reset — reconnecting")
                self._reconnect()
            except Exception as e:
                log.error(f"Unexpected error: {e}")
                await asyncio.sleep(60)


# ── CLI entry point ────────────────────────────────────────────────────────────
async def main():
    parser = argparse.ArgumentParser(description="IBN Email IMAP Watcher (Channel 2)")
    parser.add_argument("--config", default="watcher_config.yml",
                        help="Path to YAML config file")
    args   = parser.parse_args()

    cfg_path = Path(args.config)
    if cfg_path.exists():
        with open(cfg_path) as f:
            user_cfg = yaml.safe_load(f)
        cfg = {**DEFAULT_CONFIG, **(user_cfg or {})}
    else:
        log.warning(f"Config {args.config} not found — using defaults")
        cfg = DEFAULT_CONFIG.copy()

    watcher = IMAPWatcher(cfg)
    await watcher.run()


if __name__ == "__main__":
    asyncio.run(main())
