from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename
from models import Audit, AuditStep, AuditMedia, db
from openai import OpenAI
from supabase_utils import upload_to_supabase_and_get_url
import os
import tempfile
import fitz  # PyMuPDF
import base64

bp = Blueprint("audits", __name__)

# OpenAI client (requires OPENAI_API_KEY in env)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# --- Helper: Extract usage from bill ---
def summarize_bill_from_pdf(pdf_path: str) -> str:
    """Render the bill chart as an image and send it to GPT for a natural language summary."""
    doc = fitz.open(pdf_path)
    page = doc[0]  # ⚠️ adjust if usage chart on another page
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
    img_bytes = pix.tobytes("png")
    img_b64 = base64.b64encode(img_bytes).decode("utf-8")

    prompt = """
    You are an experienced energy auditor. Analyze the attached utility bill chart and
    write a concise, professional summary that highlights:

    - The billing period covered
    - Total annual kWh usage
    - Seasonal or monthly trends (peak vs. low months)
    - Any notable patterns (summer peaks, winter lows, unusual fluctuations)
    - Breakdown of usage by Time-of-Use (if visible)
    - Practical insights a homeowner or auditor would find useful

    Keep it short and in plain text.
    """

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You summarize utility bills for home energy audits."},
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}}
            ]}
        ],
        temperature=0.3,
    )

    return resp.choices[0].message.content.strip()


# --- Routes ---
@bp.route('/audits', methods=['POST'])
def create_audit():
    data = request.get_json()
    property_id = data.get("property_id")
    audit_type = data.get("audit_type", "energy_audit")

    if not property_id:
        return jsonify({"error": "Missing property_id"}), 400

    try:
        new_audit = Audit(property_id=property_id, audit_type=audit_type)
        db.session.add(new_audit)
        db.session.commit()

        return jsonify({
            "id": new_audit.id,
            "property_id": new_audit.property_id,
            "audit_type": new_audit.audit_type,
            "date": new_audit.date.isoformat()
        }), 201
    except Exception as e:
        print(f"❌ Error creating audit: {e}")
        return jsonify({"error": "Failed to create audit"}), 500


@bp.route('/audits/<int:audit_id>', methods=['GET'])
def get_audit(audit_id):
    audit = Audit.query.get(audit_id)
    if not audit:
        return jsonify({"error": "Audit not found"}), 404

    return jsonify({
        "id": audit.id,
        "property_id": audit.property_id,
        "date": audit.date.strftime('%Y-%m-%d'),
        "auditor_name": audit.auditor_name,
        "notes": audit.notes,
        "audit_type": audit.audit_type,
        "steps": [
            {
                "id": step.id,
                "step_type": step.step_type,
                "label": step.label,
                "status": step.status,
                "summary": step.summary
            }
            for step in audit.steps
        ]
    })


@bp.route('/properties/<int:property_id>/audit', methods=['GET'])
def get_audit_by_property(property_id):
    audit = Audit.query.filter_by(property_id=property_id).first()
    if audit:
        return jsonify({
            "id": audit.id,
            "property_id": audit.property_id,
            "audit_type": audit.audit_type,
            "date": audit.date.isoformat()
        })
    else:
        return jsonify({"error": "No audit found"}), 404


@bp.route('/audits/<int:audit_id>/interview', methods=['POST'])
def handle_interview(audit_id):
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'Missing audio file'}), 400

    # Save temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix='.m4a') as tmp:
        file.save(tmp.name)
        temp_path = tmp.name

    # --- Step 1: Ensure interview step exists ---
    step = AuditStep.query.filter_by(
        audit_id=audit_id,
        step_type="interview",
        label="Initial Interview"
    ).first()
    if not step:
        step = AuditStep(
            audit_id=audit_id,
            step_type="interview",
            label="Initial Interview",
            status="Processing"
        )
        db.session.add(step)
        db.session.commit()

    # --- Step 2: Transcribe this recording ---
    try:
        with open(temp_path, "rb") as f:
            transcript_resp = client.audio.transcriptions.create(
                model="whisper-1",
                file=f
            )
        transcript = transcript_resp.text
    except Exception as e:
        os.remove(temp_path)
        return jsonify({'error': 'Transcription failed', 'details': str(e)}), 500

    # --- Step 3: Upload audio ---
    try:
        file_url = upload_to_supabase_and_get_url(
            file_path=temp_path,
            audit_id=audit_id,
            step_label="Initial Interview",
            media_type="audio",
            step_type="interview"
        )
    finally:
        os.remove(temp_path)

    # --- Step 4: Save media row ---
    media = AuditMedia(
        audit_id=audit_id,
        step_id=step.id,
        file_name=secure_filename(file.filename),
        media_type="audio",
        media_url=file_url,
        notes=transcript  # raw transcript stored in notes
    )
    db.session.add(media)
    db.session.commit()

    # --- Step 5: Collect ALL interview recordings and re-summarize ---
    all_media = AuditMedia.query.filter_by(
        audit_id=audit_id,
        step_id=step.id,
        media_type="audio"
    ).all()
    all_summaries = [m.notes for m in all_media if m.notes]

    if all_summaries:
        try:
            combined_prompt = (
                """
You are an energy auditor assistant. You will be given multiple homeowner interview transcripts. 
Your job is to create a concise, professional summary suitable for an audit report.

Rules:
- ONLY include issues, plans, or upgrades explicitly mentioned in the transcripts. 
- If a category is not mentioned, say "No information provided."
- Do NOT infer or assume details that are not stated.
- Keep the output plain text in narrative form, no bullet points.

Focus on:
1. Comfort issues (hot/cold rooms, seasonal patterns, times of day).
2. Remodeling/upgrade plans (HVAC, insulation, water heater, EV, etc.).

"""
            )
            combined_resp = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": combined_prompt},
                    {"role": "user", "content": "\n\n".join(all_summaries)}
                ]
            )
            combined_summary = combined_resp.choices[0].message.content.strip()
        except Exception:
            combined_summary = "\n".join(all_summaries)

        # Save combined summary to step
        step.summary = combined_summary
        step.status = "Completed"
        db.session.commit()

    short_label = " ".join(transcript.split()[:5]) + ("…" if len(transcript.split()) > 5 else "")

    return jsonify({
        "transcript": transcript,
        "media_url": file_url,
        "short_label": short_label,
        "step_id": step.id,
        "media_id": media.id,
        "summary": step.summary
    })


@bp.route('/audits/<int:audit_id>/utility-bill', methods=['POST'])
def handle_utility_bill(audit_id):
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'Missing utility bill file'}), 400

    # Save temp PDF
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
        file.save(tmp.name)
        temp_path = tmp.name
    print(f"📂 Saved utility bill temp file at {temp_path}")

    # Step 1: Upload file to Supabase
    try:
        file_url = upload_to_supabase_and_get_url(
            file_path=temp_path,
            audit_id=audit_id,
            step_label='Utility Bill',
            media_type='document',
            step_type='interview'
        )
    except Exception as e:
        os.remove(temp_path)
        return jsonify({'error': 'Upload to Supabase failed', 'details': str(e)}), 500

    # Step 2: Summarize the bill
    try:
        summary = summarize_bill_from_pdf(temp_path)
    except Exception as e:
        summary = f"⚠️ Failed to summarize bill: {e}"
    finally:
        os.remove(temp_path)

    # Step 3: Save AuditStep
    step = AuditStep(
        audit_id=audit_id,
        step_type='interview',
        label='Utility Bill',
        status="Completed",
        summary=summary
    )
    db.session.add(step)
    db.session.commit()

    # Step 4: Save AuditMedia
    media = AuditMedia(
        audit_id=audit_id,
        step_id=step.id,
        file_name=secure_filename(file.filename),
        media_type='document',
        media_url=file_url,
        notes="Utility bill uploaded"
    )
    db.session.add(media)
    db.session.commit()

    return jsonify({
        'summary': summary,
        'media_url': file_url,
        'step_id': step.id,
        'media_id': media.id
    }), 201