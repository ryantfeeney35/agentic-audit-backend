# agent_routes.py
from flask import Blueprint, jsonify, request
from models import AgentConversation, db
from openai import OpenAI
import os

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
        {"role": "system", "content": "You are the Insulation Agent. Only talk about insulation."},
        {"role": "user", "content": context},
    ])

def siding_agent(context):
    return call_llm([
        {"role": "system", "content": "You are the Siding Agent. Only talk about siding."},
        {"role": "user", "content": context},
    ])

# --- Orchestration Agent ---
def orchestration_agent(audit_id, context):
    history = get_conversation_history(audit_id)

    messages = [
        {"role": "system", "content": "You are the Orchestrator Agent. Your job is to decide if the insulation agent, siding agent, or yourself should respond. Delegate appropriately, merge results, and keep conversation coherent."},
        *[{"role": m["role"], "content": m["content"]} for m in history],
        {"role": "user", "content": context},
    ]

    # Save user input
    save_message(audit_id, "orchestrator", "user", context)

    # Orchestrator reply
    orchestration_reply = call_llm(messages)
    save_message(audit_id, "orchestrator", "assistant", orchestration_reply)

    final_reply = orchestration_reply

    # Delegate if orchestrator hints at domain
    if "insulation" in orchestration_reply.lower():
        ins_reply = insulation_agent(context)
        save_message(audit_id, "insulation", "assistant", ins_reply)
        final_reply = f"{orchestration_reply}\n\n{ins_reply}"

    elif "siding" in orchestration_reply.lower():
        sid_reply = siding_agent(context)
        save_message(audit_id, "siding", "assistant", sid_reply)
        final_reply = f"{orchestration_reply}\n\n{sid_reply}"

    # Save merged final reply under orchestrator
    save_message(audit_id, "orchestrator", "assistant", final_reply)

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