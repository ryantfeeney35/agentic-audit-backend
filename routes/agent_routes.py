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
    # Gather context from DB
    audit = Audit.query.get(audit_id)
    steps = AuditStep.query.filter_by(audit_id=audit_id).all()
    property_obj = audit.property if audit else None  # assuming relationship exists

    context_summary = []

    if audit and audit.notes:
        context_summary.append(f"Interview summary: {audit.notes}")

    # Include utility bill if available
    if property_obj and property_obj.utility_bill_url:
        bill_desc = f"Utility Bill: {property_obj.utility_bill_name or 'uploaded bill'}"
        context_summary.append(bill_desc)

    media_context = []
    for step in steps:
        notes = step.notes or ""
        step_summary = f"{step.label} ({step.step_type}) - Notes: {notes}"
        context_summary.append(step_summary)

        # Fetch related media
        media_items = AuditMedia.query.filter_by(step_id=step.id).all()
        for m in media_items:
            # Add text description + direct reference to URL
            if m.media_type in ["photo", "image"]:
                media_context.append({
                    "type": "image_url",
                    "image_url": {"url": m.media_url}
                })
            else:
                media_context.append({
                    "type": "text",
                    "text": f"{m.media_type.upper()} file '{m.file_name}' from step '{step.label}' - {m.media_url}"
                })

    full_context = "\n".join(context_summary)

    history = get_conversation_history(audit_id)

    messages = [
        {
            "role": "system",
            "content": (
                "You are the Orchestrator Agent for a home energy audit. "
                "You have access to:\n"
                "- Homeowner interview summary\n"
                "- Step notes (insulation thickness, siding notes, etc.)\n"
                "- Uploaded media (photos, videos)\n"
                "- Utility bills\n\n"
                "Your job is:\n"
                "1. Carefully review ALL provided context and media.\n"
                "2. If context is incomplete, ask the *minimum number* of precise follow-up questions.\n"
                "3. Avoid small talk. Jump straight into technical clarifications.\n"
                "4. When enough info is collected, provide a concise, actionable recommendation."
            )
        },
        *[{"role": m["role"], "content": m["content"]} for m in history],
        {"role": "user", "content": f"Context so far:\n{full_context}\n\n{context}"},
    ]

    # Add media + utility bill PDF as multimodal input
    if media_context:
        messages.append({
            "role": "user",
            "content": media_context
        })

    # If utility bill is a PDF, add a link for GPT to parse
    if property_obj and property_obj.utility_bill_url:
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": "Here is the uploaded utility bill PDF:"},
                {"type": "input_text", "text": property_obj.utility_bill_url}
            ]
        })

    save_message(audit_id, "orchestrator", "user", context)

    # 🔑 Call multimodal model (vision/text)
    response = client.chat.completions.create(
        model="gpt-4.1",
        messages=messages,
        temperature=0.3,
    )

    reply = response.choices[0].message.content
    save_message(audit_id, "orchestrator", "assistant", reply)

    return reply

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