# agent_routes.py
from flask import Blueprint, jsonify, request
from models import AgentConversation, db
from openai import OpenAI
import os
import logging

# configure logger
logger = logging.getLogger("orchestration")
logger.setLevel(logging.DEBUG)

bp = Blueprint("agent_review", __name__)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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

# --- Base LLM call ---
def call_llm(messages):
    response = client.chat.completions.create(
        model="gpt-4.1",
        messages=messages,
        temperature=0.3,
    )
    return response.choices[0].message.content

# --- Specialized agents ---
def insulation_agent(context):
    return call_llm([
        {
            "role": "system",
            "content": (
                "You are the Insulation Agent. "
                "Summarize insulation context and then ask follow-up questions if more info is needed. "
                "Use this format:\n\n"
                "Summary: ...\n"
                "Follow-up Questions: ...\n"
                "If none, write 'None'.\n"
                "Do NOT provide upgrade recommendations yet."
            )
        },
        {"role": "user", "content": context},
    ])

def siding_agent(context):
    return call_llm([
        {
            "role": "system",
            "content": (
                "You are the Siding Agent. "
                "Summarize siding/exterior context and then ask follow-up questions if more info is needed. "
                "Use this format:\n\n"
                "Summary: ...\n"
                "Follow-up Questions: ...\n"
                "If none, write 'None'.\n"
                "Do NOT provide upgrade recommendations yet."
            )
        },
        {"role": "user", "content": context},
    ])

# --- Orchestration Agent ---
def orchestration_agent(audit_id, context):
    from models import Audit, AuditStep, AuditMedia

    audit = Audit.query.get(audit_id)
    steps = AuditStep.query.filter_by(audit_id=audit_id).all()
    property_obj = audit.property if audit else None

    # --- Build context ---
    context_summary = []

    if audit and audit.notes:
        context_summary.append(f"Interview summary: {audit.notes}")

    if property_obj and property_obj.utility_bill_url:
        bill_desc = f"Utility Bill: {property_obj.utility_bill_name or 'uploaded bill'}"
        context_summary.append(bill_desc)

    for step in steps:
        notes = step.notes or ""
        context_summary.append(f"{step.label} ({step.step_type}) - Notes: {notes}")

    full_context = "\n".join(context_summary)

    # --- Conversation history ---
    history = get_conversation_history(audit_id)

    # --- Orchestrator system prompt ---
    messages = [
        {
            "role": "system",
            "content": (
                "You are the Orchestrator Agent for a home energy audit.\n"
                "You must elicit follow-up questions from the Insulation and Siding agents until no more are required. "
                "Rules:\n"
                "- Forward context to insulation and siding agents.\n"
                "- Collect their structured outputs (Summary + Follow-up Questions).\n"
                "- Merge their follow-up questions into ONE coherent assistant reply.\n"
                "- If BOTH agents return 'Follow-up Questions: None', reply with 'No further questions.'\n"
                "- Do NOT provide upgrade recommendations at this stage."
            ),
        },
        *[{"role": m["role"], "content": m["content"]} for m in history],
        {"role": "user", "content": f"Context so far:\n{full_context}\n\n{context}"},
    ]

    # Save incoming user message
    save_message(audit_id, "orchestrator", "user", context)

    logger.debug("📥 Orchestrator Input Messages:\n%s", messages)

    # --- Delegate to both agents ---
    logger.debug("🪵 Delegating to Insulation Agent...")
    ins_reply = insulation_agent(full_context)
    logger.debug("🪵 Insulation Agent Reply: %s", ins_reply)
    save_message(audit_id, "insulation", "assistant", ins_reply)

    logger.debug("🏠 Delegating to Siding Agent...")
    sid_reply = siding_agent(full_context)
    logger.debug("🏠 Siding Agent Reply: %s", sid_reply)
    save_message(audit_id, "siding", "assistant", sid_reply)

    # --- Merge outputs ---
    followups = []
    if "follow-up questions: none" not in ins_reply.lower():
        followups.append(ins_reply)
    if "follow-up questions: none" not in sid_reply.lower():
        followups.append(sid_reply)

    if followups:
        final_reply = "\n\n".join(followups)
    else:
        final_reply = "No further questions."

    # --- Save final merged response ---
    save_message(audit_id, "orchestrator", "assistant", final_reply)
    logger.debug("✅ Final Orchestrator Reply Saved")

    return final_reply

# --- Flask Routes ---
@bp.route("/agent-review", methods=["POST"])
def agent_review():
    data = request.json
    audit_id = data.get("auditId")
    context = data.get("context", "")

    if not audit_id:
        return jsonify({"error": "auditId required"}), 400

    try:
        response = orchestration_agent(audit_id, context)
        return jsonify({"response": response})
    except Exception as e:
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

    merged = [
        {
            "role": r.role,
            "content": r.content,
            "created_at": r.created_at.isoformat()
        }
        for r in rows if r.role in ["system", "user", "assistant"]
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
        return jsonify({"error": str(e)}), 500