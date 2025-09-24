from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename
from models import AuditMedia, AuditStep, db
import os
from supabase import create_client
from openai import OpenAI
import tempfile
import subprocess

bp = Blueprint("media", __name__)

# Supabase setup
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_BUCKET_NAME = os.getenv("SUPABASE_BUCKET_NAME")
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def summarize_image(public_url: str) -> str:
    return client.chat.completions.create(
        model="gpt-4.1",
        messages=[
            {"role": "system", "content": "You are an energy audit assistant. Describe defects, materials, and conditions relevant to insulation, siding, or energy performance."},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": public_url}}]},
        ],
    ).choices[0].message.content

def transcribe_audio(path: str) -> str:
    with open(path, "rb") as af:
        result = client.audio.transcriptions.create(
            model="gpt-4o-mini-transcribe",
            file=af
        )
    return result.text

def summarize_text(text: str) -> str:
    return client.chat.completions.create(
        model="gpt-4.1",
        messages=[
            {"role": "system", "content": "Summarize homeowner comments or observations for energy audit context."},
            {"role": "user", "content": text},
        ],
    ).choices[0].message.content

def summarize_video(path: str, base_filename: str, audit_id: int, step_label: str) -> str:
    # Extract audio
    audio_path = os.path.join(tempfile.gettempdir(), f"{base_filename}.mp3")
    subprocess.run(["ffmpeg", "-i", path, "-q:a", "0", "-map", "a", audio_path], check=True)

    transcript = transcribe_audio(audio_path) if os.path.exists(audio_path) else ""

    # Extract frames every 5 seconds
    frame_pattern = os.path.join(tempfile.gettempdir(), f"{base_filename}_frame_%03d.jpg")
    subprocess.run(["ffmpeg", "-i", path, "-vf", "fps=1/5", frame_pattern], check=True)

    frame_urls = []
    for fname in os.listdir(tempfile.gettempdir()):
        if fname.startswith(f"{base_filename}_frame_") and fname.endswith(".jpg"):
            fpath = os.path.join(tempfile.gettempdir(), fname)
            with open(fpath, "rb") as f:
                supabase.storage.from_(SUPABASE_BUCKET_NAME).upload(
                    f"{audit_id}_{step_label}_{fname}", f
                )
            url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET_NAME}/{audit_id}_{step_label}_{fname}"
            frame_urls.append(url)

    # Summarize frames + transcript
    vision_input = [{"type": "image_url", "image_url": {"url": url}} for url in frame_urls]
    if transcript:
        vision_input.append({"type": "text", "text": f"Transcript: {transcript}"})

    return client.chat.completions.create(
        model="gpt-4.1",
        messages=[
            {"role": "system", "content": "You are an energy auditor. Summarize what this video shows about insulation, siding, or energy performance."},
            {"role": "user", "content": vision_input},
        ],
    ).choices[0].message.content


@bp.route('/audits/<int:audit_id>/steps/<string:step_label>/upload', methods=['POST'])
def upload_media_by_step_label(audit_id, step_label):
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    filename = secure_filename(f"{audit_id}_{step_label}_{file.filename}")
    file_content = file.read()

    step_type = request.form.get('step_type', 'exterior')
    media_type = request.form.get('media_type', 'photo')  # photo | video | audio

    # Ensure step exists
    step = AuditStep.query.filter_by(audit_id=audit_id, label=step_label).first()
    if not step:
        step = AuditStep(audit_id=audit_id, label=step_label, step_type=step_type)
        db.session.add(step)
        db.session.commit()

    try:
        # Save original file to Supabase
        supabase.storage.from_(SUPABASE_BUCKET_NAME).upload(filename, file_content)
        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET_NAME}/{filename}"

        summary_text = ""

        # Save temp file for processing videos/audio
        tmp_path = os.path.join(tempfile.gettempdir(), filename)
        with open(tmp_path, "wb") as f:
            f.write(file_content)

        if media_type == "photo":
            summary_text = summarize_image(public_url)
        elif media_type == "video":
            summary_text = summarize_video(tmp_path, filename, audit_id, step_label)
        elif media_type == "audio":
            transcript = transcribe_audio(tmp_path)
            summary_text = summarize_text(transcript)

        media = AuditMedia(
            audit_id=audit_id,
            step_id=step.id,
            step_type=step.step_type,
            side=step.label.replace(" Side", ""),
            media_url=public_url,
            file_name=file.filename,
            media_type=media_type,
            summary=summary_text
        )
        db.session.add(media)
        db.session.commit()

        return jsonify({
            "message": "Uploaded",
            "media_url": public_url,
            "summary": summary_text,
            "step_id": step.id
        }), 201

    except Exception as e:
        print(f"❌ Upload failed: {e}")
        return jsonify({'error': 'Upload failed', 'details': str(e)}), 500

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