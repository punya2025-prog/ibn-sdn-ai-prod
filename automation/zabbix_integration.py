import os
from models.intent_models import CanonicalIntent, EndpointSpec

ZABBIX_HOST = os.getenv("ZABBIX_HOST","localhost")
ZABBIX_PORT = int(os.getenv("ZABBIX_PORT","10051"))

class ZabbixIntegration:
    TRIGGER_MAP = {
        "interface down": ("resiliency","remediate",90),
        "high cpu":       ("remediation","rate_limit",80),
        "bgp":            ("resiliency","remediate",88),
        "err-disabled":   ("security","quarantine",92),
        "stp":            ("resiliency","remediate",85),
        "unreachable":    ("reachability","remediate",78),
        "port security":  ("security","quarantine",95),
    }
    async def alert_to_intent(self, body: dict):
        trigger = body.get("trigger_name","").lower()
        host    = body.get("host","unknown")
        sev     = body.get("severity","warning")
        cat, action, prio = "remediation", "remediate", 70
        for kw, (c, a, p) in self.TRIGGER_MAP.items():
            if kw in trigger:
                cat, action, prio = c, a, p
                break
        sev_boost = {"disaster":20,"high":10,"average":5}.get(sev,0)
        return CanonicalIntent(channel=0, category=cat, action=action,
            priority=min(100, prio+sev_boost),
            subject=EndpointSpec(endpoint_group=host, zone="fabric"),
            target=EndpointSpec(endpoint_group="odl_controller"),
            description=f"Zabbix auto-remediation: {trigger} on {host}",
            raw_input=str(body)[:300],
            metadata={"zabbix_trigger":trigger,"zabbix_host":host})
