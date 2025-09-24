from flask import Blueprint, request, jsonify, current_app
from werkzeug.utils import secure_filename
from models import AuditMedia, AuditStep, db
import os
import tempfile
from supabase import create_client
from openai import OpenAI
from threading import Thread

bp = Blueprint("media", __name__)

# Supabase setup
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_BUCKET_NAME = os.getenv("SUPABASE_BUCKET_NAME")
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# --- helpers for summarization ---
def summarize_image(url: str) -> str:
    resp = client.chat.completions.create(
        model="gpt-4.1",
        messages=[
            {"role": "system", "content": "You are an energy audit assistant. Summarize the key details visible in this photo for audit context."},
            {"role": "user", "content": f"Photo URL: {url}"}
        ]
    )
    return resp.choices[0].message.content.strip()

def summarize_video(path: str) -> str:
    # transcribe + summarize
    with open(path, "rb") as audio_file:
        transcript = client.audio.transcriptions.create(
            model="gpt-4o-transcribe",
            file=audio_file
        )
    text = transcript.text
    resp = client.chat.completions.create(
        model="gpt-4.1",
        messages=[
            {"role": "system", "content": "You are an energy audit assistant. Summarize the key details visible or discussed in this video."},
            {"role": "user", "content": text}
        ]
    )
    return resp.choices[0].message.content.strip()

def summarize_audio(path: str) -> str:
    with open(path, "rb") as audio_file:
        transcript = client.audio.transcriptions.create(
            model="gpt-4o-transcribe",
            file=audio_file
        )
    return transcript.text

# --- async worker ---
def process_media_async(media_id: int, tmp_path: str, public_url: str, media_type: str):
    with current_app.app_context():  # ✅ fix context issue
        summary = "❌ Processing failed"
        try:
            if media_type == "photo":
                summary = summarize_image(public_url)
            elif media_type == "video":
                summary = summarize_video(tmp_path)
            elif media_type == "audio":
                transcript = summarize_audio(tmp_path)
                summary = f"Audio transcript: {transcript}"
        except Exception as e:
            summary = f"❌ Failed to process: {e}"

        # update DB once done
        media = AuditMedia.query.get(media_id)
        if media:
            media.summary = summary
            db.session.commit()

# --- Upload by step label ---
@bp.route('/audits/<int:audit_id>/steps/<string:step_label>/upload', methods=['POST'])
def upload_media_by_step_label(audit_id, step_label):
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    filename = secure_filename(f"{audit_id}_{step_label}_{file.filename}")
    file_content = file.read()

    step_type = request.form.get('step_type', 'exterior')
    media_type = request.form.get('media_type', 'photo')

    # find or create step
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
        # upload to Supabase
        supabase.storage.from_(SUPABASE_BUCKET_NAME).upload(filename, file_content)
        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET_NAME}/{filename}"

        # save DB entry with placeholder summary
        media = AuditMedia(
            audit_id=audit_id,
            step_id=step.id,
            step_type=step.step_type,
            side=step.label.replace(" Side", ""),
            media_url=public_url,
            file_name=file.filename,
            media_type=media_type,
            summary="⏳ Processing…"  # placeholder
        )
        db.session.add(media)
        db.session.commit()

        # save temp copy for async worker
        tmp_path = os.path.join(tempfile.gettempdir(), filename)
        with open(tmp_path, "wb") as f:
            f.write(file_content)

        # spawn async summarization
        Thread(
            target=process_media_async,
            args=(media.id, tmp_path, public_url, media_type),
            daemon=True
        ).start()

        return jsonify({
            "id": media.id,
            "media_url": public_url,
            "summary": media.summary,
            "status": "processing"
        }), 201

    except Exception as e:
        print(f"❌ Upload failed: {e}")
        return jsonify({'error': 'Upload failed'}), 500

# --- Upload media by step_id ---
@bp.route('/steps/<int:step_id>/upload', methods=['POST'])
def upload_step_media(step_id):
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    filename = f'step_{step_id}_{secure_filename(file.filename)}'
    file_content = file.read()

    step = AuditStep.query.get(step_id)
    if not step:
        return jsonify({'error': 'Step not found'}), 404

    media_type = request.form.get('media_type', 'photo')

    try:
        supabase.storage.from_(SUPABASE_BUCKET_NAME).upload(filename, file_content)
        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET_NAME}/{filename}"

        media = AuditMedia(
            audit_id=step.audit_id,
            step_id=step.id,
            step_type=step.step_type,
            side=step.label.replace(" Side", ""),
            media_url=public_url,
            file_name=file.filename,
            media_type=media_type,
            summary="⏳ Processing…"
        )
        db.session.add(media)
        db.session.commit()

        return jsonify({"url": public_url, "summary": media.summary}), 201

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
        "summary": m.summary,
        "created_at": m.created_at.isoformat()
    } for m in media])

# --- Get media for a specific step ---
@bp.route('/steps/<int:step_id>/media', methods=['GET'])
def get_step_media(step_id):
    media = AuditMedia.query.filter_by(step_id=step_id).all()
    return jsonify([{
        "id": m.id,
        "audit_id": m.audit_id,
        "step_id": m.step_id,
        "step_type": m.step_type,
        "side": m.side,
        "media_url": m.media_url,
        "file_name": m.file_name,
        "media_type": m.media_type,
        "summary": m.summary,
        "created_at": m.created_at.isoformat()
    } for m in media])