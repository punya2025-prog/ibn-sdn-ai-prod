"""
Add these routes to your main.py to support the event generator
and the unified console. Paste after the existing route definitions.

Also add this CORS setting to allow the HTML file to connect from file://
(already in main.py but make sure origins=["*"])
"""

# ── Syslog / event ingest endpoint ────────────────────────────────────────────
# This is already handled by /api/pipeline/event but we add a dedicated
# syslog endpoint that formats messages correctly and feeds the log store.

from collections import deque

# In-memory event log (last 500 events) — add this near INTENT_STORE
EVENT_LOG: deque = deque(maxlen=500)


@app.post("/api/events/ingest", tags=["events"])
async def ingest_event(body: dict, background_tasks: BackgroundTasks):
    """
    Ingest a syslog or network event.
    Stores in EVENT_LOG and optionally triggers the pipeline.

    Body:
    {
      "host": "dc-leaf2",
      "severity": "crit",        # crit | warn | info | ok
      "message": "%ETH-4-ERRDISABLE...",
      "label": "err_disabled",   # optional ML label
      "type": "syslog",          # syslog | snmp | zabbix | manual
      "trigger_pipeline": false  # set true to also run full pipeline
    }
    """
    from datetime import datetime
    entry = {
        "host":      body.get("host", "unknown"),
        "severity":  body.get("severity", "info"),
        "message":   body.get("message", ""),
        "msg":       body.get("message", ""),  # alias
        "label":     body.get("label"),
        "type":      body.get("type", "syslog"),
        "timestamp": datetime.utcnow().isoformat(),
    }
    EVENT_LOG.appendleft(entry)

    # Optionally trigger full pipeline
    result = {}
    if body.get("trigger_pipeline", False):
        from automation_pipeline.pipeline import IBNPipeline
        pipeline = IBNPipeline()
        result = await pipeline.process_event({**entry, "source": entry["host"]})

    return {"status": "ingested", "event": entry, "pipeline": result}


@app.get("/api/events", tags=["events"])
async def get_events(limit: int = 100, severity: str = None, host: str = None):
    """
    Get recent events from the in-memory log.
    Filter by severity (crit/warn/info) or host name.
    """
    items = list(EVENT_LOG)
    if severity:
        items = [e for e in items if e.get("severity","").lower() == severity.lower()]
    if host:
        items = [e for e in items if host.lower() in e.get("host","").lower()]
    return {"total": len(items), "items": items[:limit]}


@app.delete("/api/events", tags=["events"])
async def clear_events():
    """Clear the event log."""
    EVENT_LOG.clear()
    return {"status": "cleared"}


@app.get("/api/events/stats", tags=["events"])
async def event_stats():
    """Event counts by severity and host."""
    from collections import Counter
    items = list(EVENT_LOG)
    by_sev  = dict(Counter(e.get("severity","info") for e in items))
    by_host = dict(Counter(e.get("host","?") for e in items).most_common(10))
    by_label= dict(Counter(e.get("label") for e in items if e.get("label")).most_common(10))
    return {
        "total":    len(items),
        "by_severity": by_sev,
        "top_hosts":   by_host,
        "top_labels":  by_label,
    }
