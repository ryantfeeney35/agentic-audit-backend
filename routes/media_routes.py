from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename
from models import AuditMedia, AuditStep, db
import os
from supabase import create_client
from openai import OpenAI

bp = Blueprint("media", __name__)

# Supabase setup
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_BUCKET_NAME = os.getenv("SUPABASE_BUCKET_NAME")
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def analyze_media(file_url: str, mimetype: str) -> str | None:
    """Analyze uploaded image/video and return a concise summary for audit context."""
    if not (mimetype.startswith("image/") or mimetype.startswith("video/")):
        return None

    try:
        resp = client.chat.completions.create(
            model="gpt-4.1",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an energy audit assistant. Analyze the uploaded media "
                        "and provide a concise factual description relevant to an energy audit. "
                        "Focus on observable details (materials, condition, visible issues). "
                        "Do NOT give upgrade recommendations."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Please describe this media for an energy audit."},
                        {"type": "image_url", "image_url": {"url": file_url}},
                    ],
                },
            ],
            max_tokens=150,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"⚠️ Media analysis failed: {e}")
        return None

# --- Upload media by step_id ---
@bp.route('/steps/<int:step_id>/upload', methods=['POST'])
def upload_step_media(step_id):
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    filename = f'step_{step_id}_{secure_filename(file.filename)}'
    file_content = file.read()

    # Fetch the step so we can link properly
    step = AuditStep.query.get(step_id)
    if not step:
        return jsonify({'error': 'Step not found'}), 404

    media_type = request.form.get('media_type', 'photo')

    try:
        supabase.storage.from_(SUPABASE_BUCKET_NAME).update(
            path=filename,
            file=file_content,
            file_options={"content-type": file.mimetype}
        )
        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET_NAME}/{filename}"

        media = AuditMedia(
            audit_id=step.audit_id,
            step_id=step.id,
            step_type=step.step_type,
            side=step.label.replace(" Side", ""),
            media_url=public_url,
            file_name=file.filename,
            media_type=media_type
        )
        db.session.add(media)
        db.session.commit()

        return jsonify({"url": public_url}), 201

    except Exception as e:
        print(f"❌ Upload failed: {e}")
        return jsonify({'error': 'Upload failed'}), 500


# --- Get all media for an audit ---
@bp.route('/audits/<int:audit_id>/media', methods=['GET'])
def get_audit_media(audit_id):
    media = AuditMedia.query.filter_by(audit_id=audit_id).all()
    return jsonify([{
        "id": m.id,
        "audit_id": m.audit_id,
        "step_type": m.step_type,
        "side": m.side,
        "media_url": m.media_url,
        "file_name": m.file_name,
        "media_type": m.media_type,
        "created_at": m.created_at.isoformat()
    } for m in media])


# --- Upload media by step label ---
@bp.route('/audits/<int:audit_id>/steps/<string:step_label>/upload', methods=['POST'])
def upload_media_by_step_label(audit_id, step_label):
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    filename = secure_filename(f"{audit_id}_{step_label}_{file.filename}")
    file_content = file.read()

    # Parse step_type and media_type from form
    step_type = request.form.get('step_type', 'exterior')
    media_type = request.form.get('media_type', 'photo')

    # Find or create the step
    step = AuditStep.query.filter_by(audit_id=audit_id, label=step_label).first()
    if step:
        if step.step_type != step_type:
            step.step_type = step_type
            db.session.commit()
    else:
        step = AuditStep(audit_id=audit_id, label=step_label, step_type=step_type)
        db.session.add(step)
        db.session.commit()

    try:
        # Upload to Supabase
        supabase.storage.from_(SUPABASE_BUCKET_NAME).update(
            path=filename,
            file=file_content,
            file_options={"content-type": file.mimetype}
        )
        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET_NAME}/{filename}"

        # ✅ Auto-analyze image/video
        summary = analyze_media(public_url, file.mimetype)

        media = AuditMedia(
            audit_id=audit_id,
            step_id=step.id,
            step_type=step.step_type,
            side=step.label.replace(" Side", ""),
            media_url=public_url,
            file_name=file.filename,
            media_type=media_type,
            summary=summary,  # ✅ new field
        )
        db.session.add(media)
        db.session.commit()

        return jsonify({
            "message": "Uploaded",
            "media_url": public_url,
            "step_id": step.id,
            "summary": summary,  # ✅ return summary to frontend too
        }), 201

    except Exception as e:
        print(f"❌ Upload failed: {e}")
        return jsonify({'error': 'Upload failed'}), 500