from dataclasses import dataclass, field
from enum        import Enum
from datetime    import datetime, timedelta

class Decision(str, Enum):
    AUTO_DEPLOY       = "auto_deploy"
    OPERATOR_APPROVAL = "operator_approval"
    REJECT            = "reject"
    FEASIBILITY_CHECK = "feasibility_check"

@dataclass
class ScoreBreakdown:
    llm_confidence:  float    = 0.0
    sim_adjustment:  float    = 0.0
    conflict_adj:    float    = 0.0
    topology_adj:    float    = 0.0
    priority_adj:    float    = 0.0
    final_score:     float    = 0.0
    decision:        Decision = Decision.FEASIBILITY_CHECK
    reason:          str      = ""
    recommendations: list     = field(default_factory=list)
    timestamp:       str      = field(default_factory=lambda: datetime.utcnow().isoformat())

@dataclass
class ApprovalRequest:
    intent_id:      str
    score:          float
    decision:       Decision
    breakdown:      ScoreBreakdown
    intent_summary: str
    simulation_info:dict
    analysis_info:  dict
    approval_url:   str
    telegram_msg:   str
    expires_at:     str

def score_intent(llm_confidence=0.65, sim_verdict=None, has_conflict=False,
                 has_redundancy=True, intent_priority=50, category="reachability") -> ScoreBreakdown:
    b = ScoreBreakdown()
    b.llm_confidence = max(0.0, min(1.0, llm_confidence))
    b.sim_adjustment = {"PASS":+0.20,"WARN":+0.05,"FAIL":-0.30}.get(sim_verdict,-0.10)
    b.conflict_adj   = -0.20 if has_conflict else +0.05
    b.topology_adj   = +0.05 if has_redundancy else -0.05
    b.priority_adj   = round((intent_priority-50)/1000, 3)
    raw = b.llm_confidence+b.sim_adjustment+b.conflict_adj+b.topology_adj+b.priority_adj
    b.final_score = round(max(0.0, min(1.0, raw)), 3)
    if b.final_score >= 0.90:
        b.decision = Decision.AUTO_DEPLOY
        b.reason   = f"Score {b.final_score:.2f} ≥ 0.90 — auto-deploy"
    elif b.final_score >= 0.70:
        b.decision = Decision.OPERATOR_APPROVAL
        b.reason   = f"Score {b.final_score:.2f} in [0.70,0.90) — operator approval required"
    elif b.final_score >= 0.50:
        b.decision = Decision.REJECT
        b.reason   = f"Score {b.final_score:.2f} in [0.50,0.70) — rejected, clarify intent"
    else:
        b.decision = Decision.FEASIBILITY_CHECK
        b.reason   = f"Score {b.final_score:.2f} < 0.50 — feasibility analysis required"
    if sim_verdict == "FAIL":   b.recommendations.append("Fix simulation failures")
    if has_conflict:            b.recommendations.append("Resolve policy conflict")
    if not has_redundancy:      b.recommendations.append("No redundant path — verify topology")
    if b.llm_confidence < 0.6: b.recommendations.append("Rephrase intent more specifically")
    return b

def build_approval_request(intent, breakdown, sim_result, fault_analysis,
                            gateway_url="http://localhost:8000") -> ApprovalRequest:
    intent_id = intent.get("intent_id","unknown")
    i  = intent.get("intent", intent)
    summary  = (f"{i.get('action','?').upper()} "
                f"{(i.get('subject') or {}).get('endpoint_group','?')} → "
                f"{(i.get('target')  or {}).get('endpoint_group','?')} [{i.get('category','?')}]")
    expires  = (datetime.utcnow()+timedelta(hours=4)).isoformat()
    tg_msg   = (f"⚠️ *IBN Approval Required*\n`{intent_id[:16]}`\n{summary}\n"
                f"Score: `{breakdown.final_score:.2f}`\n"
                f"Sim: `{(sim_result or {}).get('verdict','?')}`\n"
                f"{gateway_url}/ui/approve/{intent_id}\n⏰ Expires: {expires}")
    return ApprovalRequest(intent_id=intent_id, score=breakdown.final_score,
        decision=breakdown.decision, breakdown=breakdown, intent_summary=summary,
        simulation_info=sim_result or {}, analysis_info=fault_analysis or {},
        approval_url=f"{gateway_url}/ui/approve/{intent_id}",
        telegram_msg=tg_msg, expires_at=expires)
