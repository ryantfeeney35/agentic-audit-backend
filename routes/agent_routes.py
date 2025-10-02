# routes/agent_routes.py
from flask import Blueprint, jsonify, request
from models import AgentConversation, Audit, AuditStep, db
import logging
from agents.base_agent import run_agent
from agents.utils import merge_agent_outputs

# configure logger
logger = logging.getLogger("orchestration")
logger.setLevel(logging.DEBUG)

bp = Blueprint("agent_review", __name__)

# --- Conversation helpers ---
def get_conversation_history(audit_id):
    rows = (
        AgentConversation.query
        .filter_by(audit_id=audit_id)
        .order_by(AgentConversation.created_at.asc())
        .all()
    )
    return [{"role": r.role, "content": r.content, "domain": r.domain} for r in rows]

def save_message(audit_id, domain, role, content):
    msg = AgentConversation(
        audit_id=audit_id,
        domain=domain,
        role=role,
        content=content
    )
    db.session.add(msg)
    db.session.commit()
    return msg

# --- Orchestration Agent ---
def orchestration_agent(audit_id, context, from_user=False, bootstrap=False, user_answer=None):
    audit = Audit.query.get(audit_id)
    steps = AuditStep.query.filter_by(audit_id=audit_id).all()
    property_obj = audit.property if audit else None

    # --- Build context ---
    context_summary = []

    # Property details
    if property_obj:
        address = f"{property_obj.street}, {property_obj.city}, {property_obj.state} {property_obj.zip_code}"
        sqft = f"{property_obj.sqft} sqft" if property_obj.sqft else "sqft unknown"
        year = f"Year built: {property_obj.year_built}" if property_obj.year_built else "Year built unknown"
        context_summary.append(f"Property: {address}, {sqft}, {year}")

    # Interview summary (still stored in audit.notes if used)
    if audit and audit.notes:
        context_summary.append(f"Interview summary: {audit.notes}")

    # Step + media summaries (skip if Not Accessible)
    for step in steps:
        if step.status == "Not Accessible":
            continue

        if step.notes:
            context_summary.append(f"{step.label} ({step.step_type}) - Notes: {step.notes}")

        for media in step.media:
            if media.summary:
                if step.step_type == "interview":
                    context_summary.append(f"Interview media summary: {media.summary}")
                elif step.step_type == "utility_bill":
                    context_summary.append(f"Utility bill summary: {media.summary}")
                else:
                    context_summary.append(f"{step.label} media summary: {media.summary}")

    full_context = "\n".join(context_summary)

    # --- Conversation history (exclude orchestrator assistant summaries) ---
    history = get_conversation_history(audit_id)
    filtered_history = [
        m for m in history if not (m["role"] == "assistant" and m["domain"] == "orchestrator")
    ]

    # --- Save raw user answers ---
    if from_user and user_answer and user_answer.strip():
        save_message(audit_id, "orchestrator", "user", user_answer.strip())

    # --- Domain detection (simplified for now) ---
    # Always call all relevant domain agents
    agent_replies = []
    agent_replies.append(run_agent("insulation", full_context + "\n" + context, bootstrap=bootstrap).dict())
    agent_replies.append(run_agent("siding", full_context + "\n" + context, bootstrap=bootstrap).dict())
    agent_replies.append(run_agent("hvac", full_context + "\n" + context, bootstrap=bootstrap).dict())

    # --- Merge agent outputs ---
    final_reply = merge_agent_outputs(agent_replies, bootstrap=bootstrap)

    # --- Save orchestrator reply ---
    save_message(audit_id, "orchestrator", "assistant", final_reply)
    logger.debug("✅ Final Orchestrator Reply Saved")

    return final_reply

# --- Routes ---
@bp.route("/agent-review", methods=["POST"])
def agent_review():
    data = request.json
    audit_id = data.get("auditId")
    context = data.get("context", "")
    user_answer = data.get("userAnswer", "")
    bootstrap = data.get("bootstrap", False)

    if not audit_id:
        return jsonify({"error": "auditId required"}), 400

    try:
        response = orchestration_agent(
            audit_id,
            context,
            from_user=not bootstrap,
            bootstrap=bootstrap,
            user_answer=user_answer
        )
        return jsonify({"response": response})
    except Exception as e:
        logger.exception("❌ Orchestrator failed")
        return jsonify({"error": str(e)}), 500

@bp.route("/agent-conversations/merged", methods=["GET"])
def get_merged_conversation():
    audit_id = request.args.get("audit_id")
    if not audit_id:
        return jsonify({"error": "audit_id required"}), 400

    rows = (
        AgentConversation.query
        .filter_by(audit_id=audit_id)
        .order_by(AgentConversation.created_at.asc())
        .all()
    )

    # ✅ Only include orchestrator + user messages
    merged = [
        {
            "role": r.role,
            "domain": r.domain,
            "content": r.content,
            "created_at": r.created_at.isoformat()
        }
        for r in rows
        if r.domain == "orchestrator" or r.role == "user"
    ]

    return jsonify(merged)

@bp.route("/agent-conversations", methods=["POST"])
def add_conversation_message():
    data = request.get_json()
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
            "created_at": msg.created_at.isoformat()
        }), 201
    except Exception as e:
        logger.exception("❌ Failed to save conversation message")
        return jsonify({"error": str(e)}), 500