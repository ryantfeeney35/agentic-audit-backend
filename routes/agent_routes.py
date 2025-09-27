# agent_routes.py
from flask import Blueprint, jsonify, request
from models import AgentConversation, Audit, AuditStep, db
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
def insulation_agent(context, bootstrap=False):
    if bootstrap:
        system_content = (
            "You are the Insulation Agent. Focus ONLY on insulation.\n"
            "- Review provided context.\n"
            "- Provide a short summary of insulation findings.\n"
            "- List clear follow-up questions if info is incomplete.\n"
            "- Do not make upgrade recommendations yet."
        )
    else:
        system_content = (
            "You are the Insulation Agent. Focus ONLY on insulation.\n"
            "- DO NOT summarize.\n"
            "- DO NOT repeat previously answered questions.\n"
            "- ONLY output NEW follow-up questions that remain unanswered.\n"
            "- If you have no further questions, respond exactly with: 'No further insulation questions.'"
        )
    return call_llm([
        {"role": "system", "content": system_content},
        {"role": "user", "content": context},
    ])

def siding_agent(context, bootstrap=False):
    if bootstrap:
        system_content = (
            "You are the Siding Agent. Focus ONLY on siding and exterior walls.\n"
            "- Review provided context.\n"
            "- Provide a short summary of siding/exterior findings.\n"
            "- List clear follow-up questions if info is incomplete.\n"
            "- Do not make upgrade recommendations yet."
        )
    else:
        system_content = (
            "You are the Siding Agent. Focus ONLY on siding and exterior walls.\n"
            "- DO NOT summarize.\n"
            "- DO NOT repeat previously answered questions.\n"
            "- ONLY output NEW follow-up questions that remain unanswered.\n"
            "- If you have no further questions, respond exactly with: 'No further siding questions.'"
        )
    return call_llm([
        {"role": "system", "content": system_content},
        {"role": "user", "content": context},
    ])

def hvac_agent(context, bootstrap=False):
    if bootstrap:
        system_content = (
            "You are the HVAC Agent. Focus ONLY on heating, cooling, and ducting systems.\n"
            "- Review provided context.\n"
            "- Provide a short summary of HVAC findings (heating system, cooling system, ducting).\n"
            "- List clear follow-up questions if info is incomplete (brand, model, efficiency, condition, filter, duct sealing).\n"
            "- Do not make upgrade recommendations yet."
        )
    else:
        system_content = (
            "You are the HVAC Agent. Focus ONLY on heating, cooling, and ducting systems.\n"
            "- DO NOT summarize.\n"
            "- DO NOT repeat previously answered questions.\n"
            "- ONLY output NEW follow-up questions that remain unanswered.\n"
            "- If you have no further questions, respond exactly with: 'No further HVAC questions.'"
        )
    return call_llm([
        {"role": "system", "content": system_content},
        {"role": "user", "content": context},
    ])

# --- Orchestration Agent ---
def orchestration_agent(audit_id, context, from_user=False, bootstrap=False, user_answer=None):
    audit = Audit.query.get(audit_id)
    steps = AuditStep.query.filter_by(audit_id=audit_id).all()
    property_obj = audit.property if audit else None

    # --- Build context ---
    context_summary = []

    if property_obj:
        address = f"{property_obj.street}, {property_obj.city}, {property_obj.state} {property_obj.zip_code}"
        sqft = f"{property_obj.sqft} sqft" if property_obj.sqft else "sqft unknown"
        year = f"Year built: {property_obj.year_built}" if property_obj.year_built else "Year built unknown"
        context_summary.append(f"Property: {address}, {sqft}, {year}")

    if audit and audit.notes:
        context_summary.append(f"Interview summary: {audit.notes}")

    # Step + media summaries (skip if status == Not Accessible)
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

    # --- Conversation history ---
    history = get_conversation_history(audit_id)
    filtered_history = [
        m for m in history if not (m["role"] == "assistant" and m["domain"] == "orchestrator")
    ]

    # Save new user answers
    if from_user and user_answer and user_answer.strip():
        save_message(audit_id, "orchestrator", "user", user_answer.strip())

    # System prompt
    if bootstrap:
        system_prompt = (
            "You are the Orchestrator Agent for a home energy audit.\n"
            "Bootstrap mode:\n"
            "1. Review all context (interview, notes, utility bill, photos).\n"
            "2. Call insulation, siding, and HVAC agents.\n"
            "3. Return ONE unified summary of findings.\n"
            "4. Return ONE unified, deduplicated list of follow-up questions.\n"
            "Do not generate upgrade recommendations yet."
        )
    else:
        system_prompt = (
            "You are the Orchestrator Agent for a home energy audit.\n"
            "User has provided new answers.\n"
            "Your task now:\n"
            "1. Incorporate ONLY the new user answers.\n"
            "2. Call insulation, siding, and HVAC agents with updated context.\n"
            "3. Return ONLY a unified, deduplicated list of remaining follow-up questions.\n"
            "Do NOT include a summary unless bootstrap=True.\n"
            "Do NOT invent new domains or questions."
        )

    messages = [
        {"role": "system", "content": system_prompt},
        *[{"role": m["role"], "content": m["content"]} for m in filtered_history if m["role"] == "user"],
        {"role": "user", "content": f"Context so far:\n{full_context}\n\nNew answer: {context}"},
    ]

    logger.debug("📥 Orchestrator Input Messages:\n%s", messages)
    orchestration_reply = call_llm(messages)
    logger.debug("🤖 Orchestrator Raw Reply: %s", orchestration_reply)

    # --- Step 2: Delegate ---
    agent_replies = []
    if bootstrap:
        # Always call all three agents at bootstrap
        ins_reply = insulation_agent(full_context + "\n" + context, bootstrap=True)
        save_message(audit_id, "insulation", "assistant", ins_reply)
        agent_replies.append(ins_reply)

        sid_reply = siding_agent(full_context + "\n" + context, bootstrap=True)
        save_message(audit_id, "siding", "assistant", sid_reply)
        agent_replies.append(sid_reply)

        hvac_reply = hvac_agent(full_context + "\n" + context, bootstrap=True)
        save_message(audit_id, "hvac", "assistant", hvac_reply)
        agent_replies.append(hvac_reply)
    else:
        if "insulation" in orchestration_reply.lower():
            ins_reply = insulation_agent(full_context + "\n" + context, bootstrap=False)
            save_message(audit_id, "insulation", "assistant", ins_reply)
            agent_replies.append(ins_reply)
        if "siding" in orchestration_reply.lower():
            sid_reply = siding_agent(full_context + "\n" + context, bootstrap=False)
            save_message(audit_id, "siding", "assistant", sid_reply)
            agent_replies.append(sid_reply)
        if "hvac" in orchestration_reply.lower():
            hvac_reply = hvac_agent(full_context + "\n" + context, bootstrap=False)
            save_message(audit_id, "hvac", "assistant", hvac_reply)
            agent_replies.append(hvac_reply)

    # --- Step 3: Merge ---
    if not agent_replies:
        final_reply = "✅ No further follow-up questions. Proceed to recommendations."
    else:
        merge_prompt = [
            {
                "role": "system",
                "content": (
                    "You are the Orchestrator Agent merging outputs from insulation, siding, and HVAC agents.\n"
                    "Rules:\n"
                    "1. On bootstrap: output ONE unified summary AND ONE unified, deduplicated list of follow-up questions.\n"
                    "2. On later turns: output ONLY a unified, deduplicated list of follow-up questions (no summary).\n"
                    "3. If all agents respond with 'No further ... questions', output exactly:\n"
                    "'✅ No further follow-up questions. Proceed to recommendations.'\n"
                    "4. Never invent or repeat questions.\n"
                ),
            },
            {"role": "user", "content": "\n\n".join(agent_replies) or orchestration_reply},
        ]
        final_reply = call_llm(merge_prompt)

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