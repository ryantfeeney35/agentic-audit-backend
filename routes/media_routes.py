import shutil
from flask import Blueprint, request, jsonify, current_app
from werkzeug.utils import secure_filename
from models import AuditMedia, AuditStep, db
import os
from supabase import create_client
from openai import OpenAI
import tempfile
from threading import Thread
import base64
import subprocess

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

def extract_frames(video_path, out_dir, fps=0.2, max_frames=5):
    """
    Extract frames from video at ~1 frame every (1/fps) seconds.
    Defaults: 0.2 fps = 1 frame every 5 seconds.
    """
    subprocess.run([
        "ffmpeg", "-i", video_path,
        "-vf", f"fps={fps}",
        os.path.join(out_dir, "frame_%03d.jpg")
    ], check=True)

    frame_files = sorted(os.listdir(out_dir))
    if len(frame_files) > max_frames:
        frame_files = frame_files[:max_frames]  # limit analysis
    return [os.path.join(out_dir, f) for f in frame_files]

def image_to_base64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

def summarize_video(path: str) -> str:
    # --- Step 1: Transcribe audio ---
    with open(path, "rb") as f:
        transcript = client.audio.transcriptions.create(
            model="gpt-4o-transcribe",
            file=f
        ).text

    # --- Step 2: Extract frames ---
    out_dir = tempfile.mkdtemp()
    frame_paths = extract_frames(path, out_dir)

    try:
        # --- Step 3: Summarize frames with GPT-4 Vision ---
        frame_summaries = []
        for fp in frame_paths:
            b64_img = image_to_base64(fp)
            resp = client.chat.completions.create(
                model="gpt-4.1",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an energy audit assistant. "
                            "Analyze this video frame for siding, shading, insulation cues, or other building envelope details. "
                            "Keep it concise and factual."
                        )
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}
                            }
                        ]
                    }
                ]
            )
            frame_summaries.append(resp.choices[0].message.content.strip())

        # --- Step 4: Merge transcript + frame summaries ---
        merged_resp = client.chat.completions.create(
            model="gpt-4.1",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an energy auditor assistant. "
                        "Combine the homeowner's voiceover (transcript) with frame analyses into one cohesive, concise summary. "
                        "Highlight key details for insulation, siding, shading, or other envelope/energy efficiency factors. "
                        "Do not repeat verbatim text; synthesize into useful audit notes."
                    )
                },
                {
                    "role": "user",
                    "content": (
                        f"Transcript:\n{transcript}\n\n"
                        f"Frame observations:\n" + "\n".join(frame_summaries)
                    )
                }
            ]
        )
        return merged_resp.choices[0].message.content.strip()

    finally:
        # --- Always clean up temp frames ---
        shutil.rmtree(out_dir, ignore_errors=True)

def summarize_audio(path: str) -> str:
    # --- Step 1: Transcribe ---
    with open(path, "rb") as f:
        transcript = client.audio.transcriptions.create(
            model="gpt-4o-transcribe",
            file=f
        ).text

    # --- Step 2: Summarize transcript into clean audit note ---
    summary_resp = client.chat.completions.create(
        model="gpt-4.1",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an energy auditor assistant. "
                    "Summarize the homeowner's statements clearly and concisely. "
                    "Highlight comfort issues, planned upgrades, and any contextual insights. "
                    "Do not just repeat verbatim text; condense into professional notes."
                )
            },
            {"role": "user", "content": transcript}
        ]
    )

    return summary_resp.choices[0].message.content.strip()

# ✅ background processor with real app context
def process_media_async(app, media_id: int, tmp_path: str, public_url: str, media_type: str):
    with app.app_context():  # push proper app context
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

        media = AuditMedia.query.get(media_id)
        if media:
            media.summary = summary
            db.session.commit()

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
            summary="Processing…"  # placeholder
        )
        db.session.add(media)
        db.session.commit()

        # save temp copy for async worker
        tmp_path = os.path.join(tempfile.gettempdir(), filename)
        with open(tmp_path, "wb") as f:
            f.write(file_content)

        # spawn async summarization with app context
        Thread(
            target=process_media_async,
            args=(current_app._get_current_object(), media.id, tmp_path, public_url, media_type)
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
            media_type=media_type,
            summary="Processing…"  # keep consistent
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