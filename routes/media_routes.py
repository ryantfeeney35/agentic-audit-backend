import shutil
import os
import base64
import tempfile
import subprocess
from threading import Thread
from flask import Blueprint, request, jsonify, current_app
from werkzeug.utils import secure_filename
from supabase import create_client
from openai import OpenAI
from models import AuditMedia, AuditStep, db

bp = Blueprint("media", __name__)

# Supabase setup
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_BUCKET_NAME = os.getenv("SUPABASE_BUCKET_NAME")
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# --- helpers ---
def compress_video(input_path, output_path):
    print(f"🎞️ [compress_video] Compressing {input_path} -> {output_path}")
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", input_path,
            "-vcodec", "libx264", "-crf", "28", "-preset", "veryfast",
            "-acodec", "aac", "-b:a", "128k",
            output_path
        ], check=True)
        print(f"✅ [compress_video] Compression complete: {output_path}")
    except subprocess.CalledProcessError as e:
        print(f"❌ [compress_video] Compression failed: {e}")
        raise

def summarize_image(path: str) -> str:
    print(f"📸 [summarize_image] Summarizing {path}")
    with open(path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("utf-8")

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are an energy audit assistant. Analyze the insulation photo."},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
            ]}
        ],
    )
    return resp.choices[0].message.content.strip()

def extract_frames(video_path, out_dir, fps=0.2, max_frames=5):
    print(f"🎞️ [extract_frames] Extracting frames every {1/fps:.1f}s from {video_path}")
    subprocess.run([
        "ffmpeg", "-i", video_path,
        "-vf", f"fps={fps}",
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
    # Step 1: transcribe
    with open(path, "rb") as f:
        transcript = client.audio.transcriptions.create(
            model="gpt-4o-transcribe",
            file=f
        ).text
    print(f"📹 [summarize_video] Transcript length: {len(transcript)} chars")

    # Step 2: extract frames
    out_dir = tempfile.mkdtemp()
    frame_paths = extract_frames(path, out_dir)

    try:
        frame_summaries = []
        for fp in frame_paths:
            print(f"📸 [summarize_video] Processing frame {fp}")
            b64_img = image_to_base64(fp)
            resp = client.chat.completions.create(
                model="gpt-4.1",
                messages=[
                    {"role": "system", "content": "Analyze this video frame for building envelope details."},
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
                    ]}
                ]
            )
            summary = resp.choices[0].message.content.strip()
            print(f"📸 [summarize_video] Frame summary: {summary[:60]}...")
            frame_summaries.append(summary)

        # Step 3: merge
        merged_resp = client.chat.completions.create(
            model="gpt-4.1",
            messages=[
                {"role": "system", "content": "Combine transcript + frame analyses into concise audit notes."},
                {"role": "user", "content": f"Transcript:\n{transcript}\n\nFrames:\n" + "\n".join(frame_summaries)}
            ]
        )
        final_summary = merged_resp.choices[0].message.content.strip()
        print(f"📹 [summarize_video] Final summary: {final_summary[:80]}...")
        return final_summary
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)

def summarize_audio(path: str) -> str:
    print(f"🎙️ [summarize_audio] Summarizing {path}")
    with open(path, "rb") as f:
        transcript = client.audio.transcriptions.create(
            model="gpt-4o-transcribe", file=f
        ).text
    summary_resp = client.chat.completions.create(
        model="gpt-4.1",
        messages=[
            {"role": "system", "content": "Summarize the homeowner's statements clearly and concisely."},
            {"role": "user", "content": transcript}
        ]
    )
    return summary_resp.choices[0].message.content.strip()

def process_media_async(app, media_id: int, tmp_path: str, public_url: str, media_type: str):
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
            print(summary)

        media = AuditMedia.query.get(media_id)
        if media:
            media.summary = summary
            db.session.commit()
            print(f"✅ [process_media_async] Saved summary for media_id={media_id}")

@bp.route('/audits/<int:audit_id>/steps/<string:step_label>/upload', methods=['POST'])
def upload_media_by_step_label(audit_id, step_label):
    print(f"⬆️ [upload_media_by_step_label] Called for audit {audit_id}, step '{step_label}'")

    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    filename = secure_filename(f"{audit_id}_{step_label}_{file.filename}")
    file_content = file.read()
    print(f"⬆️ [upload_media_by_step_label] Received file {filename}, size={len(file_content)} bytes")

    step_type = request.form.get('step_type', 'exterior')
    media_type = request.form.get('media_type', 'photo')
    print(f"⬆️ [upload_media_by_step_label] step_type={step_type}, media_type={media_type}")

    step = AuditStep.query.filter_by(audit_id=audit_id, label=step_label).first()
    if not step:
        step = AuditStep(audit_id=audit_id, label=step_label, step_type=step_type)
        db.session.add(step)
        db.session.commit()
    print(f"📌 Found step id={step.id}")

    try:
        # save tmp file
        tmp_path = os.path.join(tempfile.gettempdir(), filename)
        with open(tmp_path, "wb") as f:
            f.write(file_content)
        print(f"📂 Saved temp file {tmp_path}")

        # compress if video
        upload_path = tmp_path
        if media_type == "video":
            compressed_path = os.path.join(tempfile.gettempdir(), f"compressed_{filename}")
            compress_video(tmp_path, compressed_path)
            upload_path = compressed_path

        # upload to supabase
        with open(upload_path, "rb") as f:
            supabase.storage.from_(SUPABASE_BUCKET_NAME).upload(filename, f.read())
        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET_NAME}/{filename}"
        print(f"⬆️ Uploaded file to {public_url}")

        # DB entry
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

        # async summarization
        Thread(
            target=process_media_async,
            args=(current_app._get_current_object(), media.id, tmp_path, public_url, media_type)
        ).start()
        print(f"🚀 Spawned background thread for media {media.id}")

        return jsonify({"id": media.id, "media_url": public_url, "summary": media.summary, "status": "processing"}), 201
    except Exception as e:
        print(f"❌ Upload failed: {e}")
        return jsonify({'error': 'Upload failed', 'details': str(e)}), 500


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