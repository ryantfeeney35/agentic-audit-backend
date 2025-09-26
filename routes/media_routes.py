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
import json

bp = Blueprint("media", __name__)

# Supabase setup
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_BUCKET_NAME = os.getenv("SUPABASE_BUCKET_NAME")
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# --- helpers for summarization ---
def summarize_image(path: str) -> str:
    print(f"🖼️ [summarize_image] Starting summarization for {path}")
    with open(path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("utf-8")

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an energy audit assistant. "
                    "Analyze the insulation photo. Describe insulation type, thickness, "
                    "condition (good/fair/poor), and any visible issues like air leaks, "
                    "ductwork, or obstructions. Be concise and professional."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}
                    }
                ],
            },
        ],
    )
    summary = resp.choices[0].message.content.strip()
    print(f"🖼️ [summarize_image] Done: {summary[:80]}...")
    return summary


def extract_frames(video_path, out_dir, interval=5, max_frames=10):
    """
    Extract frames from video every N seconds (default: 5s).
    """
    print(f"🎞️ [extract_frames] Extracting frames every {interval}s from {video_path}")
    subprocess.run([
        "ffmpeg", "-i", video_path,
        "-vf", f"fps=1/{interval}",
        os.path.join(out_dir, "frame_%03d.jpg")
    ], check=True)

    frame_files = sorted(os.listdir(out_dir))
    if len(frame_files) > max_frames:
        frame_files = frame_files[:max_frames]
    print(f"🎞️ [extract_frames] Extracted {len(frame_files)} frames")
    return [os.path.join(out_dir, f) for f in frame_files]


def image_to_base64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def summarize_video(path: str) -> str:
    print(f"📹 [summarize_video] Starting summarization for {path}")

    # --- Step 1: Transcribe audio ---
    with open(path, "rb") as f:
        transcript = client.audio.transcriptions.create(
            model="gpt-4o-transcribe",
            file=f
        ).text
    print(f"📹 [summarize_video] Transcript length: {len(transcript)} chars")

    # --- Step 2: Extract frames ---
    out_dir = tempfile.mkdtemp()
    frame_paths = extract_frames(path, out_dir)

    try:
        # --- Step 3: Summarize frames ---
        frame_summaries = []
        for fp in frame_paths:
            print(f"📸 [summarize_video] Processing frame {fp}")
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
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
                        ]
                    }
                ]
            )
            frame_summary = resp.choices[0].message.content.strip()
            frame_summaries.append(frame_summary)
            print(f"📸 [summarize_video] Frame summary: {frame_summary[:80]}...")

        # --- Step 4: Merge transcript + frames ---
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
        summary = merged_resp.choices[0].message.content.strip()
        print(f"📹 [summarize_video] Final summary: {summary[:120]}...")
        return summary

    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def summarize_audio(path: str) -> str:
    print(f"🎤 [summarize_audio] Starting transcription for {path}")
    with open(path, "rb") as f:
        transcript = client.audio.transcriptions.create(
            model="gpt-4o-transcribe",
            file=f
        ).text
    print(f"🎤 [summarize_audio] Transcript length: {len(transcript)} chars")

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
    summary = summary_resp.choices[0].message.content.strip()
    print(f"🎤 [summarize_audio] Final summary: {summary[:120]}...")
    return summary


# ✅ background processor
def process_media_async(app, media_id: int, tmp_path: str, public_url: str, media_type: str):
    print(f"🚀 [process_media_async] Start for media_id={media_id}, type={media_type}")
    with app.app_context():
        summary = "❌ Processing failed"
        try:
            if media_type == "photo":
                summary = summarize_image(tmp_path)
            elif media_type == "video":
                summary = summarize_video(tmp_path)
            elif media_type == "audio":
                summary = summarize_audio(tmp_path)
        except Exception as e:
            summary = f"❌ Failed to process: {e}"
            print(f"🔥 [process_media_async] Error: {e}")

        media = AuditMedia.query.get(media_id)
        if media:
            media.summary = summary
            db.session.commit()
            print(f"✅ [process_media_async] Saved summary for media_id={media_id}")


@bp.route('/audits/<int:audit_id>/steps/<string:step_label>/upload', methods=['POST'])
def upload_media_by_step_label(audit_id, step_label):
    print(f"⬆️ [upload_media_by_step_label] Called for audit {audit_id}, step '{step_label}'")
    if 'file' not in request.files:
        print("❌ No file in request")
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    filename = secure_filename(f"{audit_id}_{step_label}_{file.filename}")
    file_content = file.read()
    print(f"⬆️ [upload_media_by_step_label] Received file {filename}, size={len(file_content)} bytes")

    step_type = request.form.get('step_type', 'exterior')
    media_type = request.form.get('media_type', 'photo')
    print(f"⬆️ [upload_media_by_step_label] step_type={step_type}, media_type={media_type}")

    # find or create step
    step = AuditStep.query.filter_by(audit_id=audit_id, label=step_label).first()
    if step:
        print(f"📌 Found existing step id={step.id}")
        if step.step_type != step_type:
            step.step_type = step_type
            db.session.commit()
            print(f"📌 Updated step_type to {step_type}")
    else:
        step = AuditStep(audit_id=audit_id, label=step_label, step_type=step_type)
        db.session.add(step)
        db.session.commit()
        print(f"📌 Created new step id={step.id}")

    try:
        # upload to Supabase
        supabase.storage.from_(SUPABASE_BUCKET_NAME).upload(filename, file_content)
        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET_NAME}/{filename}"
        print(f"⬆️ Uploaded file to {public_url}")

        # save DB entry with placeholder
        media = AuditMedia(
            audit_id=audit_id,
            step_id=step.id,
            step_type=step.step_type,
            side=step.label.replace(" Side", ""),
            media_url=public_url,
            file_name=file.filename,
            media_type=media_type,
            summary="Processing…"
        )
        db.session.add(media)
        db.session.commit()
        print(f"📌 Media record created id={media.id}")

        # temp copy for async worker
        tmp_path = os.path.join(tempfile.gettempdir(), filename)
        with open(tmp_path, "wb") as f:
            f.write(file_content)
        print(f"📂 Saved temp file {tmp_path}")

        Thread(
            target=process_media_async,
            args=(current_app._get_current_object(), media.id, tmp_path, public_url, media_type)
        ).start()
        print(f"🚀 Spawned background thread for media {media.id}")

        return jsonify({
            "id": media.id,
            "media_url": public_url,
            "summary": media.summary,
            "status": "processing"
        }), 201

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