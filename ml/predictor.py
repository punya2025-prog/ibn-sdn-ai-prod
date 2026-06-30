import os
from datetime import datetime

FAULT_RULES = {
    "err_disabled":   {"precursors":["sec_violation","sec_violation"],"prob":0.92},
    "bgp_down":       {"precursors":["cpu_stress","interface_down"],  "prob":0.88},
    "interface_down": {"precursors":["stp_change","stp_change"],      "prob":0.80},
}
EVENT_VOCAB = {"err_disabled":1,"interface_down":2,"bgp_down":3,
               "stp_change":4,"cpu_stress":5,"sec_violation":6,
               "ospf_down":7,"lacp_fault":8,"hardware_fault":9,
               "tunnel_fault":10,"unknown":11}

class FaultPredictor:
    def __init__(self):
        self._cnn = None
        self._rf  = None
        self._load_models()

    def _load_models(self):
        try:
            import torch
            from ml.cnn_model import LogCNN
            if os.path.exists("ml/log_cnn_model.pt"):
                m = LogCNN()
                m.load_state_dict(torch.load("ml/log_cnn_model.pt", map_location="cpu"))
                m.eval()
                self._cnn = m
        except Exception:
            pass
        try:
            import joblib
            if os.path.exists("ml/rf_model.pkl"):
                self._rf = joblib.load("ml/rf_model.pkl")
        except Exception:
            pass

    async def predict(self, body: dict) -> dict:
        host   = body.get("host","unknown")
        events = body.get("events",[])
        if not events:
            return {"host":host,"fault_probability":0.0,"predicted_fault":"none",
                    "method":"no_events","rca":None,"timestamp":datetime.utcnow().isoformat()}
        prob, method = self._rule_predict(events)
        fault_type   = self._most_likely_fault(events)
        rca          = self._rca(fault_type, events)
        return {"host":host,"fault_probability":round(float(prob),3),
                "predicted_fault":fault_type if prob>0.5 else "none",
                "confidence":round(float(prob),3),"method":method,
                "rca":rca,"event_count":len(events),
                "timestamp":datetime.utcnow().isoformat()}

    def _rule_predict(self, events):
        labels  = [e.get("label","unknown") for e in events]
        sev_sum = sum(1 for e in events if e.get("severity",5)<=2)
        score   = min(1.0,(sev_sum/max(len(events),1))+0.1*len(set(labels)))
        return score, "rule_based"

    def _most_likely_fault(self, events):
        from collections import Counter
        labels = [e.get("label","unknown") for e in events if e.get("label")!="unknown"]
        return Counter(labels).most_common(1)[0][0] if labels else "unknown"

    def _rca(self, fault_type, events):
        rule = FAULT_RULES.get(fault_type)
        if not rule: return None
        labels  = [e.get("label") for e in events]
        matched = sum(1 for p in rule["precursors"] if p in labels)
        conf    = round((matched/len(rule["precursors"]))*rule["prob"],3)
        causes  = {"err_disabled":"Port security violation or BPDU guard trip",
                   "bgp_down":"CPU overload dropped keepalives or underlay link failure",
                   "interface_down":"STP flapping or physical link error"}
        return {"fault_type":fault_type,"cause":causes.get(fault_type,"Unknown"),
                "confidence":conf,"precursors_matched":matched}
