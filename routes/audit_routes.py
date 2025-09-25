from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename
from models import Audit, AuditStep, AuditMedia, db
from openai import OpenAI
from supabase_utils import upload_to_supabase_and_get_url
import os
import tempfile
import fitz  # PyMuPDF
import base64
import json

bp = Blueprint("audits", __name__)

# OpenAI client (requires OPENAI_API_KEY in env)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# --- Helper: Extract usage from bill ---
def extract_usage_from_bill(pdf_path: str):
    """
    Extract annual usage (total + TOU breakdown) from a utility bill PDF.

    Returns structured JSON.
    """
    doc = fitz.open(pdf_path)
    # ⚠️ Adjust page index if chart lives on another page
    page = doc[0]
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # render high-res
    img_bytes = pix.tobytes("png")
    img_b64 = base64.b64encode(img_bytes).decode("utf-8")

    prompt = """
    You are an energy audit assistant.
    The image is from a utility bill showing ANNUAL USAGE with monthly totals and 
    a Time-of-Use (TOU) split: On-Peak, Off-Peak, Super Off-Peak.

    Extract the data into structured JSON with this schema:

    {
      "annual_usage": [
        {
          "month": "MMM YYYY",
          "total_kWh": int,
          "on_peak_kWh": int,
          "off_peak_kWh": int,
          "super_off_peak_kWh": int
        }
      ],
      "summary": {
        "yearly_total_kWh": int,
        "average_monthly_kWh": int,
        "peak_month": "MMM YYYY (X kWh)",
        "lowest_month": "MMM YYYY (X kWh)",
        "tou_split_percentages": {
          "on_peak": "X%",
          "off_peak": "X%",
          "super_off_peak": "X%"
        },
        "insights": "short narrative about usage trends"
      }
    }

    Only return valid JSON, no commentary.
    """

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You extract structured data from images of utility bills."},
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}}
            ]}
        ],
        temperature=0.0,
    )

    text = resp.choices[0].message.content
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {"summary": "⚠️ Failed to parse structured JSON", "raw_output": text}

    return data

# --- Routes ---

@bp.route('/audits', methods=['POST'])
def create_audit():
    data = request.get_json()
    property_id = data.get("property_id")

    if not property_id:
        return jsonify({"error": "Missing property_id"}), 400

    try:
        new_audit = Audit(property_id=property_id)
        db.session.add(new_audit)
        db.session.commit()

        return jsonify({
            "id": new_audit.id,
            "property_id": new_audit.property_id,
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
        "steps": [
            {
                "id": step.id,
                "step_type": step.step_type,
                "label": step.label,
                "is_completed": step.is_completed
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
    print(f"📂 Saved audio temp file at {temp_path}")

    # Step 1: Transcribe audio with Whisper
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

    # Step 2: Summarize transcript
    try:
        summary_resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": (
                    "You are an energy auditor assistant. Summarize the homeowner's concerns, comfort issues, "
                    "and upgrade plans in concise, professional language."
                )},
                {"role": "user", "content": transcript}
            ]
        )
        summary = summary_resp.choices[0].message.content
    except Exception as e:
        os.remove(temp_path)
        return jsonify({'error': 'LLM summarization failed', 'details': str(e)}), 500

    # Step 3: Upload audio file
    try:
        file_url = upload_to_supabase_and_get_url(
            file_path=temp_path,
            audit_id=audit_id,
            step_label='Initial Interview',
            media_type='audio',
            step_type='interview'
        )
    finally:
        os.remove(temp_path)

    # Step 4: Save step + media
    step = AuditStep(
        audit_id=audit_id,
        step_type='interview',
        label='Initial Interview',
        notes="Interview completed",
        is_completed=True
    )
    db.session.add(step)
    db.session.commit()

    media = AuditMedia(
        audit_id=audit_id,
        step_id=step.id,
        step_type='interview',
        file_name=secure_filename(file.filename),
        media_type='audio',
        media_url=file_url,
        summary=summary
    )
    db.session.add(media)
    db.session.commit()

    return jsonify({
        'transcript': transcript,
        'summary': summary,
        'media_url': file_url,
        'step_id': step.id,
        'media_id': media.id
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

    # Step 2: Extract structured usage from PDF
    try:
        analysis = extract_usage_from_bill(temp_path)
        summary = json.dumps(analysis, indent=2)
    except Exception as e:
        summary = f"⚠️ Failed to analyze bill: {e}"
    finally:
        os.remove(temp_path)

    # Step 3: Save new AuditStep + AuditMedia
    step = AuditStep(
        audit_id=audit_id,
        step_type='interview',
        label='Utility Bill',
        notes="Utility bill uploaded",
        is_completed=True
    )
    db.session.add(step)
    db.session.commit()

    media = AuditMedia(
        audit_id=audit_id,
        step_id=step.id,
        step_type='interview',
        file_name=secure_filename(file.filename),
        media_type='document',
        media_url=file_url,
        summary=summary
    )
    db.session.add(media)
    db.session.commit()

    return jsonify({
        'summary': summary,
        'media_url': file_url,
        'step_id': step.id,
        'media_id': media.id
    }), 201