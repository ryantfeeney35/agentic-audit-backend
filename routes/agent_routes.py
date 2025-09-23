# agent_routes.py
from flask import Blueprint, jsonify, request
from models import AgentConversation, Audit, AuditStep, AuditMedia, db
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
        {"role": "system", "content": (
            "You are the Insulation Agent. Only discuss insulation. "
            "If information is incomplete, ask clear follow-up questions. "
            "If enough info is provided, summarize insulation findings (no recommendations yet)."
        )},
        {"role": "user", "content": context},
    ])

def siding_agent(context):
    return call_llm([
        {"role": "system", "content": (
            "You are the Siding Agent. Only discuss siding and exterior walls. "
            "If information is incomplete, ask clear follow-up questions. "
            "If enough info is provided, summarize siding findings (no recommendations yet)."
        )},
        {"role": "user", "content": context},
    ])

# --- Orchestration Agent ---
def orchestration_agent(audit_id, context, bootstrap=False):
    audit = Audit.query.get(audit_id)
    steps = AuditStep.query.filter_by(audit_id=audit_id).all()
    property_obj = audit.property if audit else None

    # --- Build context ---
    context_summary = []
    if audit and audit.notes:
        context_summary.append(f"Interview summary: {audit.notes}")
    if property_obj and property_obj.utility_bill_url:
        context_summary.append(f"Utility Bill: {property_obj.utility_bill_name or 'uploaded bill'}")
    for step in steps:
        notes = step.notes or ""
        context_summary.append(f"{step.label} ({step.step_type}) - Notes: {notes}")

    full_context = "\n".join(context_summary) + f"\n{context}"

    # Save user input
    save_message(audit_id, "orchestrator", "user", context)

    # --- Call specialized agents ---
    logger.debug("📤 Sending to Insulation Agent...")
    ins_reply = insulation_agent(full_context)
    save_message(audit_id, "insulation", "assistant", ins_reply)

    logger.debug("📤 Sending to Siding Agent...")
    sid_reply = siding_agent(full_context)
    save_message(audit_id, "siding", "assistant", sid_reply)

    # --- Orchestrator consolidation ---
    system_prompt = (
        "You are the Orchestrator Agent.\n"
        f"Bootstrap={bootstrap}.\n"
        "You will receive the insulation and siding agent replies.\n"
        "- If bootstrap=True: Produce a single coherent SUMMARY of the home’s status AND a unified, deduplicated list of follow-up questions.\n"
        "- If bootstrap=False: ONLY produce an updated unified list of follow-up questions. Do not repeat the summary.\n"
        "- If no further questions are needed, respond exactly: 'No further questions. Ready for recommendations.'"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Insulation Agent reply:\n{ins_reply}\n\nSiding Agent reply:\n{sid_reply}"},
    ]

    orchestration_reply = call_llm(messages)
    logger.debug("🤖 Orchestrator Final Reply: %s", orchestration_reply)

    save_message(audit_id, "orchestrator", "assistant", orchestration_reply)
    return orchestration_reply

# --- Flask Routes ---
@bp.route("/agent-review", methods=["POST"])
def agent_review():
    data = request.json
    audit_id = data.get("auditId")
    context = data.get("context", "")
    bootstrap = data.get("bootstrap", False)

    if not audit_id:
        return jsonify({"error": "auditId required"}), 400

    try:
        response = orchestration_agent(audit_id, context, bootstrap=bootstrap)
        return jsonify({"response": response})
    except Exception as e:
        logger.exception("❌ Orchestration failed")
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
        logger.exception("❌ Failed to save conversation message")
        return jsonify({"error": str(e)}), 500