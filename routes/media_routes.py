# routes/media.py
import os
import base64
import tempfile
import subprocess
import json
from threading import Thread
from flask import Blueprint, request, jsonify, current_app
from werkzeug.utils import secure_filename
from supabase import create_client
from openai import OpenAI

from models import AuditMedia, AuditStep, db
from agents.base_agent import run_agent
from agents.schemas import ExteriorSidingSchema, HVACSchema, InsulationSchema, InterviewSchema

bp = Blueprint("media", __name__)

# -------------------------
# Config / Clients
# -------------------------
MAX_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB threshold for compression

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_BUCKET_NAME = os.getenv("SUPABASE_BUCKET_NAME")
if not (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY and SUPABASE_BUCKET_NAME):
    print("⚠️  [media] Missing Supabase env vars")
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# -------------------------
# Utilities
# -------------------------
def _guess_content_type(filename: str, fallback: str = "application/octet-stream") -> str:
    fn = filename.lower()
    if fn.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if fn.endswith(".png"):
        return "image/png"
    if fn.endswith((".mp4", ".m4v", ".mov")):
        return "video/mp4"
    if fn.endswith((".mp3", ".m4a", ".aac", ".wav")):
        return "audio/mpeg"
    return fallback

def _upload_to_supabase_bytes(path_in_bucket: str, data: bytes, content_type: str):
    print(f"⬆️ [supabase] Uploading {path_in_bucket} ({len(data)} bytes)")
    try:
        supabase.storage.from_(SUPABASE_BUCKET_NAME).upload(
            path_in_bucket,
            data,
            file_options={"content-type": content_type}
        )
    except Exception as e:
        if "409" in str(e):  # file exists → update
            supabase.storage.from_(SUPABASE_BUCKET_NAME).update(
                path=path_in_bucket,
                file=data,
                file_options={"content-type": content_type}
            )
        else:
            raise

def safe_parse(s: str):
    try:
        return json.loads(s)
    except Exception:
        return s

# -------------------------
# Media Processing Worker
# -------------------------
def process_media_async(app, media_id: int, local_path: str, public_url: str, media_type: str):
    with app.app_context():
        media = AuditMedia.query.get(media_id)
        step = AuditStep.query.get(media.step_id) if media else None
        if not step:
            print("❌ [process_media_async] No step found for media_id", media_id)
            return

        try:
            # Prepare context
            if media_type in ["photo", "video"]:
                with open(local_path, "rb") as f:
                    img_b64 = base64.b64encode(f.read()).decode("utf-8")
                context = {"type": "image", "b64": img_b64}
            elif media_type == "audio":
                with open(local_path, "rb") as f:
                    transcript = client.audio.transcriptions.create(
                        model="whisper-1",
                        file=f
                    ).text
                media.notes = transcript
                context = {"type": "audio", "transcript": transcript}
            else:
                raise ValueError("Unsupported media type")

            # Select schema
            schema_map = {
                "exterior": ExteriorSidingSchema,
                "hvac": HVACSchema,
                "insulation": InsulationSchema,
                "interview": InterviewSchema,
            }
            schema_cls = schema_map.get(step.step_type)
            if not schema_cls:
                raise ValueError(f"No schema for step_type={step.step_type}")

            # Run agent
            parsed = run_agent(
                domain=step.step_type,
                context=context,
                audit_id=media.audit_id,
                mode="media",
            )

            # Save structured output to step
            step.ai_summary = parsed
            step.status = "Completed"
            db.session.commit()

        except Exception as e:
            print(f"❌ [process_media_async] Failed: {e}")
            if step:
                step.status = "Error"
                step.ai_summary = {"error": str(e)}
                db.session.commit()

# -------------------------
# Routes
# -------------------------
@bp.route('/audits/<int:audit_id>/steps/<string:step_label>/upload', methods=['POST'])
def upload_media_by_step_label(audit_id, step_label):
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    filename = secure_filename(f"{audit_id}_{step_label}_{file.filename}")
    file_bytes = file.read()

    step_type = request.form.get('step_type', 'exterior')
    media_type = request.form.get('media_type', 'photo')

    # Find/create step
    step = AuditStep.query.filter_by(audit_id=audit_id, label=step_label).first()
    if not step:
        step = AuditStep(
            audit_id=audit_id,
            label=step_label,
            step_type=step_type,
            status="Processing"
        )
        db.session.add(step)
        db.session.commit()
    else:
        step.status = "Processing"
        db.session.commit()

    try:
        # Save temp file
        tmp_path = os.path.join(tempfile.gettempdir(), filename)
        with open(tmp_path, "wb") as f:
            f.write(file_bytes)

        upload_local_path = tmp_path
        if media_type == "video" and os.path.getsize(tmp_path) > MAX_SIZE_BYTES:
            compressed_path = os.path.join(tempfile.gettempdir(), f"compressed_{filename}")
            subprocess.run([
                "ffmpeg", "-y", "-i", tmp_path,
                "-vf", "scale=1280:-2",
                "-c:v", "libx264", "-crf", "28", "-preset", "veryfast",
                "-c:a", "aac", "-b:a", "128k", compressed_path
            ], check=True)
            upload_local_path = compressed_path

        with open(upload_local_path, "rb") as f:
            data = f.read()
        content_type = file.mimetype or _guess_content_type(filename)
        _upload_to_supabase_bytes(filename, data, content_type)
        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET_NAME}/{filename}"

        # Save AuditMedia row
        media_row = AuditMedia(
            audit_id=audit_id,
            step_id=step.id,
            media_url=public_url,
            file_name=file.filename,
            media_type=media_type,
            notes="Processing…" if media_type != "audio" else None
        )
        db.session.add(media_row)
        db.session.commit()

        # Spawn async processing
        Thread(
            target=process_media_async,
            args=(current_app._get_current_object(), media_row.id, upload_local_path, public_url, media_type)
        ).start()

        return jsonify({
            "id": media_row.id,
            "media_url": public_url,
            "status": step.status
        }), 201

    except Exception as e:
        step.status = "Error"
        db.session.commit()
        return jsonify({'error': 'Upload failed', 'details': str(e)}), 500


@bp.route('/steps/<int:step_id>/media', methods=['GET'])
def get_step_media(step_id):
    media = AuditMedia.query.filter_by(step_id=step_id).all()
    return jsonify([{
        "id": m.id,
        "audit_id": m.audit_id,
        "step_id": m.step_id,
        "media_url": m.media_url,
        "file_name": m.file_name,
        "media_type": m.media_type,
        "notes": m.notes,
        "created_at": m.created_at.isoformat()
    } for m in media])