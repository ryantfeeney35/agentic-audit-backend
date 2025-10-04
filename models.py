# routes/agent_routes.py
from flask import Blueprint, jsonify, request
import logging
from agents.base_agent import run_agent
from agents.utils import merge_agent_outputs

# ----------------------------
# Logging setup
# ----------------------------
logger = logging.getLogger("orchestration")
logger.setLevel(logging.DEBUG)

bp = Blueprint("agent_review", __name__)

# ----------------------------
# Conversation helpers
# ----------------------------
def get_conversation_history(audit_id):
    """Retrieve all messages for an audit, ordered by creation time."""
    rows = (
        AgentConversation.query
        .filter_by(audit_id=audit_id)
        .order_by(AgentConversation.created_at.asc())
        .all()
    )
    return [{"role": r.role, "content": r.content, "domain": r.domain} for r in rows]


def save_message(audit_id, domain, role, content):
    """Save a single conversation message."""
    msg = AgentConversation(
        audit_id=audit_id,
        domain=domain,
        role=role,
        content=content,
    )
    db.session.add(msg)
    db.session.commit()
    return msg

# ----------------------------
# Main Orchestration Agent
# ----------------------------
def orchestration_agent(audit_id, context, from_user=False, bootstrap=False, user_answer=None):
    """Run orchestrator to aggregate insights from all domain agents."""
    audit = Audit.query.get(audit_id)
    steps = AuditStep.query.filter_by(audit_id=audit_id).all()
    property_obj = audit.property if audit else None

    # --- Build context summary ---
    context_summary = []

    if property_obj:
        address = f"{property_obj.street}, {property_obj.city}, {property_obj.state} {property_obj.zip_code}"
        sqft = f"{property_obj.sqft} sqft" if property_obj.sqft else "sqft unknown"
        year = f"Year built: {property_obj.year_built}" if property_obj.year_built else "Year built unknown"
        context_summary.append(f"🏠 Property: {address}, {sqft}, {year}")

    if audit and audit.notes:
        context_summary.append(f"🗣️ Interview summary: {audit.notes}")

    # --- Step-level context ---
    for step in steps:
        if step.status == "Not Accessible":
            continue

        # Step summary
        if step.summary:
            context_summary.append(f"📋 {step.label} ({step.step_type}) — {step.summary}")

        # AI summary (structured findings)
        if step.ai_summary and isinstance(step.ai_summary, dict):
            ai_parts = []
            for k, v in step.ai_summary.items():
                if isinstance(v, (str, int, float)):
                    ai_parts.append(f"{k.replace('_', ' ').title()}: {v}")
            if ai_parts:
                context_summary.append(f"🤖 {step.step_type.title()} findings: " + ", ".join(ai_parts))

    full_context = "\n".join(context_summary)
    logger.debug(f"🧠 [Orchestrator Context]\n{full_context[:1000]}")

    # --- Conversation history ---
    history = get_conversation_history(audit_id)
    filtered_history = [
        m for m in history if not (m["role"] == "assistant" and m["domain"] == "orchestrator")
    ]

    # --- Save new user answer if provided ---
    if from_user and user_answer and user_answer.strip():
        save_message(audit_id, "orchestrator", "user", user_answer.strip())

    # --- Run all relevant agents ---
    agent_replies = []
    domains = ["exterior", "insulation", "hvac"]
    for domain in domains:
        try:
            logger.info(f"⚙️ Running {domain} agent...")
            result = run_agent(domain, full_context + "\n" + context, bootstrap=bootstrap, audit_id=audit_id)
            agent_replies.append(result)
        except Exception as e:
            logger.exception(f"❌ {domain} agent failed: {e}")
            agent_replies.append({"summary": f"Error in {domain} agent: {str(e)}"})

    # --- Merge agent outputs ---
    final_reply = merge_agent_outputs(agent_replies, bootstrap=bootstrap)

    # --- Save orchestrator reply ---
    save_message(audit_id, "orchestrator", "assistant", final_reply)
    logger.debug("✅ Final Orchestrator Reply Saved")

    return final_reply

# ----------------------------
# Routes
# ----------------------------
@bp.route("/agent-review", methods=["POST"])
def agent_review():
    """Primary endpoint for ReviewPage orchestration."""
    data = request.json or {}
    audit_id = data.get("auditId")
    context = data.get("context", "")
    user_answer = data.get("userAnswer", "")
    bootstrap = data.get("bootstrap", False)

    if not audit_id:
        return jsonify({"error": "auditId required"}), 400

    try:
        response = orchestration_agent(
            audit_id=audit_id,
            context=context,
            from_user=not bootstrap,
            bootstrap=bootstrap,
            user_answer=user_answer,
        )
        return jsonify({"response": response})
    except Exception as e:
        logger.exception("❌ Orchestrator failed")
        return jsonify({"error": str(e)}), 500


@bp.route("/agent-conversations/merged", methods=["GET"])
def get_merged_conversation():
    """Return orchestrator + user conversation thread."""
    audit_id = request.args.get("audit_id")
    if not audit_id:
        return jsonify({"error": "audit_id required"}), 400

    rows = (
        AgentConversation.query
        .filter_by(audit_id=audit_id)
        .order_by(AgentConversation.created_at.asc())
        .all()
    )

    merged = [
        {
            "role": r.role,
            "domain": r.domain,
            "content": r.content,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
        if r.domain == "orchestrator" or r.role == "user"
    ]

    return jsonify(merged)


@bp.route("/agent-conversations", methods=["POST"])
def add_conversation_message():
    """Manually append a message to conversation (debug / interactive mode)."""
    data = request.get_json() or {}
    audit_id = data.get("audit_id")
    role = data.get("role")
    content = data.get("content")
    domain = data.get("domain", "orchestrator")

    if not audit_id or not role or not content:
        return jsonify({"error": "audit_id, role, and content are required"}), 400

    try:
        msg = save_message(audit_id, domain, role, content)
        return jsonify({
            "id": msg.id,
            "audit_id": msg.audit_id,
            "domain": msg.domain,
            "role": msg.role,
            "content": msg.content,
            "created_at": msg.created_at.isoformat(),
        }), 201
    except Exception as e:
        logger.exception("❌ Failed to save conversation message")
        return jsonify({"error": str(e)}), 500