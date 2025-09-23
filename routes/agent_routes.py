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
def orchestration_agent(audit_id, context, from_user=False, bootstrap=False):
    from models import Audit, AuditStep, AuditMedia  # ensure imports

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

    # --- Save only true user input ---
    if from_user and context.strip():
        save_message(audit_id, "orchestrator", "user", context)

    # --- System prompt changes based on bootstrap ---
    if bootstrap:
        system_prompt = (
            "You are the Orchestrator Agent for a home energy audit.\n"
            "On first load (bootstrap):\n"
            "1. Review all context (interview, notes, utility bill, photos).\n"
            "2. Call insulation and siding agents.\n"
            "3. Produce ONE unified summary of findings.\n"
            "4. Produce ONE unified, deduplicated list of follow-up questions.\n"
            "Do not generate upgrade recommendations yet."
        )
    else:
        system_prompt = (
            "You are the Orchestrator Agent for a home energy audit.\n"
            "User has provided new answers.\n"
            "Your task now:\n"
            "1. Incorporate the new answers into context.\n"
            "2. Ask insulation and siding agents for updated follow-up questions.\n"
            "3. Produce ONLY a unified, deduplicated list of remaining follow-up questions.\n"
            "Do not repeat the full summary unless explicitly asked."
        )

    # --- Orchestrator messages ---
    messages = [
        {"role": "system", "content": system_prompt},
        *[{"role": m["role"], "content": m["content"]} for m in history],
        {"role": "user", "content": f"Context so far:\n{full_context}\n\n{context}"},
    ]

    logger.debug("📥 Orchestrator Input Messages:\n%s", messages)

    # --- Step 1: Orchestrator decides relevance ---
    orchestration_reply = call_llm(messages)
    logger.debug("🤖 Orchestrator Raw Reply: %s", orchestration_reply)

    # --- Step 2: Delegate to specialized agents ---
    agent_replies = []
    if "insulation" in orchestration_reply.lower():
        ins_reply = insulation_agent(full_context + "\n" + context)
        save_message(audit_id, "insulation", "assistant", ins_reply)
        agent_replies.append(ins_reply)
    if "siding" in orchestration_reply.lower():
        sid_reply = siding_agent(full_context + "\n" + context)
        save_message(audit_id, "siding", "assistant", sid_reply)
        agent_replies.append(sid_reply)

    # --- Step 3: Merge results ---
    merge_prompt = [
        {
            "role": "system",
            "content": (
                "You are the Orchestrator Agent merging multiple agent outputs.\n"
                "On bootstrap: return one unified summary + unified deduplicated follow-up questions.\n"
                "On later turns: return only an updated, unified list of remaining follow-up questions.\n"
                "Do not repeat earlier summaries unless asked."
            ),
        },
        {"role": "user", "content": "\n\n".join([orchestration_reply] + agent_replies)},
    ]

    final_reply = call_llm(merge_prompt)

    # --- Save orchestrator output ---
    save_message(audit_id, "orchestrator", "assistant", final_reply)
    logger.debug("✅ Final Orchestrator Reply Saved")

    return final_reply


# --- Route ---
@bp.route("/agent-review", methods=["POST"])
def agent_review():
    data = request.json
    audit_id = data.get("auditId")
    context = data.get("context", "")
    bootstrap = data.get("bootstrap", False)

    if not audit_id:
        return jsonify({"error": "auditId required"}), 400

    try:
        response = orchestration_agent(audit_id, context, from_user=not bootstrap, bootstrap=bootstrap)
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
        logger.exception("❌ Failed to save conversation message")
        return jsonify({"error": str(e)}), 500