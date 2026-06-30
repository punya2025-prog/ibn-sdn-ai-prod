#!/usr/bin/env python3
"""
telegram_bot/ibn_bot.py
========================
Channel 4 — Telegram Bot for IBN Gateway.

Commands:
  /intent <text>       — Submit a natural language intent
  /status <id>         — Check intent status by ID
  /list                — Show last 5 intents
  /predict <events>    — Run fault predictor (JSON or keywords)
  /deploy <id>         — Deploy a simulated+PASS intent
  /help                — Show all commands

Setup:
  1. Message @BotFather on Telegram → /newbot
  2. Copy the token to bot_config.yml
  3. Run:  python3 ibn_bot.py
     Or set webhook:
       python3 ibn_bot.py --webhook https://your-server.com/api/channel/4/telegram

Features:
  - Inline buttons for simulate/deploy confirmation
  - Broadcast alerts to subscribed chats (Zabbix → Telegram)
  - NOC approval workflow: sim result sent back to user with [Approve Deploy] button
"""

import asyncio, json, logging, yaml, argparse
from pathlib import Path
import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [TG-BOT]  %(levelname)s  %(message)s"
)
log = logging.getLogger("ibn_telegram_bot")

try:
    from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
                          BotCommand)
    from telegram.ext import (Application, CommandHandler, MessageHandler,
                               CallbackQueryHandler, filters, ContextTypes)
    TG_AVAILABLE = True
except ImportError:
    TG_AVAILABLE = False
    log.error("python-telegram-bot not installed. Run: pip install python-telegram-bot")


GATEWAY  = "http://localhost:8000"
BOT_TOKEN= ""   # loaded from config

# ── Helpers ────────────────────────────────────────────────────────────────────
async def _post(endpoint: str, body: dict) -> dict:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{GATEWAY}{endpoint}", json=body)
        return r.json()

async def _get(endpoint: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{GATEWAY}{endpoint}")
        return r.json()


def _sim_text(data: dict) -> str:
    """Format simulation result as Telegram message."""
    sim = data.get("simulation")
    if not sim:
        return "No simulation run."
    icon    = "✅" if sim["verdict"] == "PASS" else ("⚠️" if sim["verdict"] == "WARN" else "❌")
    lines   = [f"{icon} *Simulation: {sim['verdict']}*  ({sim.get('duration_ms',0)}ms)"]
    for c in (sim.get("checks") or []):
        mark = "✓" if c["passed"] else "✗"
        lines.append(f"  `{mark}` {c['check_name']} — {c['detail'][:60]}")
    if sim.get("diff_preview"):
        lines.append(f"\n```\n{sim['diff_preview']}\n```")
    return "\n".join(lines)


def _intent_keyboard(intent_id: str, sim_verdict: str) -> InlineKeyboardMarkup:
    buttons = [[
        InlineKeyboardButton("📋 Status",  callback_data=f"status:{intent_id}"),
        InlineKeyboardButton("🔍 Details", callback_data=f"detail:{intent_id}"),
    ]]
    if sim_verdict == "PASS":
        buttons.append([
            InlineKeyboardButton("🚀 Deploy via ODL",     callback_data=f"deploy:odl:{intent_id}"),
            InlineKeyboardButton("⚙️ Deploy via NETCONF", callback_data=f"deploy:netconf:{intent_id}"),
        ])
    return InlineKeyboardMarkup(buttons)


# ── Command handlers ───────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *IBN Gateway Bot*\n\n"
        "I translate your messages into network intents.\n\n"
        "Commands:\n"
        "`/intent <text>` — submit a network intent\n"
        "`/status <id>` — check intent status\n"
        "`/list` — last 5 intents\n"
        "`/predict` — fault predictor\n"
        "`/help` — all commands",
        parse_mode="Markdown"
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*IBN Gateway — All Commands*\n\n"
        "*/intent <text>*\n"
        "  e.g. `/intent block IoT VLAN 300 from corporate`\n\n"
        "*/status <intent\\_id>*\n"
        "  Check simulation + deployment status\n\n"
        "*/list*\n"
        "  Show last 5 intents\n\n"
        "*/predict {\\\"host\\\":\\\"dc-leaf1\\\",\\\"events\\\":[...]}*\n"
        "  Run ML fault predictor\n\n"
        "*/deploy <intent\\_id>*\n"
        "  Deploy a PASS-simulated intent via ODL\n\n"
        "*/approve <intent\\_id>*\n"
        "  Approve and deploy (NOC workflow)\n\n"
        "Or just *type any network request* — I'll parse it automatically.",
        parse_mode="Markdown"
    )


async def cmd_intent(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = " ".join(ctx.args)
    if not text:
        await update.message.reply_text("Usage: `/intent <describe what you want>`",
                                        parse_mode="Markdown")
        return

    msg = await update.message.reply_text("⏳ Parsing and simulating…")
    try:
        data = await _post("/api/channel/4/telegram", {
            "message": {
                "text": f"/intent {text}",
                "chat": {"id": update.effective_chat.id},
                "from": {"username": update.effective_user.username or "tg_user"}
            }
        })
        intent  = data.get("intent", {})
        verdict = (data.get("simulation") or {}).get("verdict", "?")
        sim_txt = _sim_text(data)
        reply   = (
            f"📨 *Intent received*\n"
            f"`{data.get('intent_id','?')[:16]}`\n\n"
            f"Category: `{intent.get('category','?')}`  "
            f"Action: `{intent.get('action','?')}`  "
            f"Priority: `{intent.get('priority','?')}`\n"
            f"Subject: `{(intent.get('subject') or {}).get('endpoint_group','?')}`\n"
            f"Target:  `{(intent.get('target')  or {}).get('endpoint_group','?')}`\n\n"
            + sim_txt
        )
        kb = _intent_keyboard(data.get("intent_id",""), verdict)
        await msg.edit_text(reply, parse_mode="Markdown", reply_markup=kb)
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: `/status <intent_id>`",
                                        parse_mode="Markdown")
        return
    intent_id = ctx.args[0]
    try:
        data = await _get(f"/api/intent/{intent_id}")
        sim  = data.get("simulation") or {}
        dep  = data.get("deployment") or {}
        txt  = (
            f"📋 *Intent {intent_id[:12]}…*\n"
            f"Status: `{data.get('status','?')}`\n"
            f"Simulation: `{sim.get('verdict','not run')}`\n"
            f"Deployment: `{dep.get('status','not deployed')}`"
        )
        await update.message.reply_text(txt, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        data   = await _get("/api/intents?limit=5")
        items  = data.get("items", [])
        if not items:
            await update.message.reply_text("No intents yet.")
            return
        lines  = ["📋 *Recent intents:*\n"]
        for d in reversed(items):
            i       = d.get("intent", {})
            verdict = (d.get("simulation") or {}).get("verdict", "—")
            vmark   = {"PASS":"✅","FAIL":"❌","WARN":"⚠️"}.get(verdict, "·")
            lines.append(
                f"{vmark} `{d.get('intent_id','?')[:12]}` "
                f"`{i.get('category','?')}` `{i.get('action','?')}` "
                f"ch:{i.get('channel','?')}"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


async def cmd_deploy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: `/deploy <intent_id>`",
                                        parse_mode="Markdown")
        return
    intent_id = ctx.args[0]
    method    = ctx.args[1] if len(ctx.args) > 1 else "odl"
    msg = await update.message.reply_text(f"⏳ Deploying via {method}…")
    try:
        data = await _post(f"/api/deploy/{intent_id}?method={method}", {})
        status = data.get("status","?")
        pushed = data.get("flows_pushed", 0)
        icon   = "✅" if status == "success" else "❌"
        await msg.edit_text(
            f"{icon} *Deployment {status}*\n"
            f"Method: `{data.get('method','?')}`\n"
            f"Flows pushed: `{pushed}`\n"
            f"Detail: {data.get('detail','')}",
            parse_mode="Markdown"
        )
    except Exception as e:
        await msg.edit_text(f"❌ Deploy error: {e}")


async def cmd_predict(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw  = " ".join(ctx.args)
    host = "unknown"
    try:
        body = json.loads(raw)
        host = body.get("host", "unknown")
    except Exception:
        # Accept keyword shorthand: predict dc-leaf1 stp_change stp_change interface_down
        parts  = raw.split()
        if parts:
            host   = parts[0]
            labels = parts[1:]
            body   = {"host": host,
                      "events": [{"label": l, "severity": 4} for l in labels]}
        else:
            await update.message.reply_text(
                "Usage: `/predict dc-leaf1 stp_change stp_change interface_down`\n"
                "or `/predict {\"host\":\"dc-leaf1\",\"events\":[...]}`",
                parse_mode="Markdown")
            return

    try:
        data = await _post("/api/predict", body)
        prob  = data.get("fault_probability", 0)
        icon  = "🔴" if prob > 0.7 else ("🟡" if prob > 0.4 else "🟢")
        rca   = data.get("rca") or {}
        txt   = (
            f"{icon} *Fault prediction for `{host}`*\n"
            f"Probability: `{prob*100:.1f}%`\n"
            f"Predicted fault: `{data.get('predicted_fault','none')}`\n"
            f"Method: `{data.get('method','?')}`\n"
        )
        if rca:
            txt += f"Root cause: _{rca.get('cause','?')}_ ({rca.get('confidence',0)*100:.0f}% conf)"
        await update.message.reply_text(txt, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")


# ── Free-text handler (no command prefix) ─────────────────────────────────────
async def free_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if len(text) < 5:
        return
    # Treat as channel 0 prompt
    ctx.args = text.split()
    await cmd_intent(update, ctx)


# ── Inline button callbacks ────────────────────────────────────────────────────
async def button_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data  = query.data or ""

    if data.startswith("status:"):
        intent_id = data.split(":")[1]
        ctx.args  = [intent_id]
        await cmd_status(update, ctx)

    elif data.startswith("deploy:"):
        parts     = data.split(":")
        method    = parts[1]
        intent_id = parts[2]
        ctx.args  = [intent_id, method]
        await cmd_deploy(update, ctx)

    elif data.startswith("detail:"):
        intent_id = data.split(":")[1]
        try:
            d   = await _get(f"/api/intent/{intent_id}")
            sim = d.get("simulation") or {}
            txt = _sim_text({"simulation": sim})
            await query.edit_message_text(txt, parse_mode="Markdown",
                                          reply_markup=query.message.reply_markup)
        except Exception as e:
            await query.edit_message_text(f"❌ {e}")


# ── Broadcast helper (call from Zabbix webhook or main) ───────────────────────
async def broadcast(token: str, chat_ids: list[int], message: str):
    """Send a message to all subscribed NOC chats."""
    async with httpx.AsyncClient() as c:
        for chat_id in chat_ids:
            try:
                await c.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": message,
                          "parse_mode": "Markdown"}
                )
            except Exception as e:
                log.warning(f"Broadcast to {chat_id} failed: {e}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    global GATEWAY, BOT_TOKEN

    parser = argparse.ArgumentParser(description="IBN Telegram Bot (Channel 4)")
    parser.add_argument("--config",  default="bot_config.yml")
    parser.add_argument("--webhook", help="Public HTTPS URL for webhook mode")
    args = parser.parse_args()

    cfg_path = Path(args.config)
    if cfg_path.exists():
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
    else:
        cfg = {}

    BOT_TOKEN = cfg.get("bot_token") or ""
    GATEWAY   = cfg.get("gateway_url", "http://localhost:8000")

    if not BOT_TOKEN:
        print("ERROR: bot_token not set in bot_config.yml")
        print("  1. Message @BotFather → /newbot")
        print("  2. Add token to bot_config.yml")
        return

    if not TG_AVAILABLE:
        print("Install: pip install python-telegram-bot")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CommandHandler("intent",  cmd_intent))
    app.add_handler(CommandHandler("status",  cmd_status))
    app.add_handler(CommandHandler("list",    cmd_list))
    app.add_handler(CommandHandler("deploy",  cmd_deploy))
    app.add_handler(CommandHandler("predict", cmd_predict))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, free_text))

    if args.webhook:
        log.info(f"Starting in webhook mode: {args.webhook}")
        app.run_webhook(
            listen="0.0.0.0", port=cfg.get("webhook_port", 8443),
            url_path=BOT_TOKEN,
            webhook_url=f"{args.webhook}/{BOT_TOKEN}",
            cert=cfg.get("ssl_cert"), key=cfg.get("ssl_key")
        )
    else:
        log.info("Starting in polling mode")
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
