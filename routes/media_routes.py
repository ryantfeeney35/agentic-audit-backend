# routes/media.py
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

# -------------------------
# Config / Clients
# -------------------------
MAX_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB threshold for compression
FRAME_FPS = 0.2  # ~1 frame every 5 seconds
MAX_FRAMES = 6   # limit frames analyzed per video

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_BUCKET_NAME = os.getenv("SUPABASE_BUCKET_NAME")
if not (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY and SUPABASE_BUCKET_NAME):
    print("⚠️  [media] Missing Supabase env vars (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY / SUPABASE_BUCKET_NAME)")
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# -------------------------
# Utilities
# -------------------------
def _guess_content_type(filename: str, fallback: str = "application/octet-stream") -> str:
    fn = filename.lower()
    if fn.endswith(".jpg") or fn.endswith(".jpeg"):
        return "image/jpeg"
    if fn.endswith(".png"):
        return "image/png"
    if fn.endswith(".mp4") or fn.endswith(".m4v") or fn.endswith(".mov"):
        return "video/mp4"
    if fn.endswith(".mp3") or fn.endswith(".m4a") or fn.endswith(".aac") or fn.endswith(".wav"):
        return "audio/mpeg"
    return fallback

def _upload_to_supabase_bytes(path_in_bucket: str, data: bytes, content_type: str):
    """
    Upload with retry: try upload; if it exists (409) then update.
    """
    print(f"⬆️ [supabase] Uploading {path_in_bucket} (content-type={content_type}, bytes={len(data)})")
    try:
        supabase.storage.from_(SUPABASE_BUCKET_NAME).upload(
            path_in_bucket,
            data,
            file_options={"content-type": content_type}
        )
        print("✅ [supabase] upload() OK")
    except Exception as e:
        msg = str(e)
        if "409" in msg or "already exists" in msg.lower():
            print("ℹ️  [supabase] Path exists; attempting update()")
            supabase.storage.from_(SUPABASE_BUCKET_NAME).update(
                path=path_in_bucket,
                file=data,
                file_options={"content-type": content_type}
            )
            print("✅ [supabase] update() OK")
        else:
            print(f"❌ [supabase] Upload error: {e}")
            raise

# -------------------------
# FFmpeg helpers
# -------------------------
def compress_video(input_path: str, output_path: str):
    """
    Transcode to H.264 + AAC, 1280px wide, CRF 28 (visually OK + small), veryfast preset.
    Forces yuv420p for maximum compatibility and maps the primary audio stream.
    """
    try:
        print(f"🎞️ [compress_video] Compressing {input_path} -> {output_path}")
        subprocess.run([
            "ffmpeg", "-y",
            "-analyzeduration", "5000000", "-probesize", "5000000",
            "-i", input_path,
            "-vf", "scale=1280:-2",
            "-c:v", "libx264", "-crf", "28", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-map", "0:v:0", "-map", "0:a:0?",
            output_path
        ], check=True)
        size = os.path.getsize(output_path)
        print(f"✅ [compress_video] Finished compression, size={size} bytes")
    except subprocess.CalledProcessError as e:
        print(f"❌ [compress_video] Failed: {e}")
        raise

def extract_frames(video_path: str, out_dir: str, fps: float = FRAME_FPS, max_frames: int = MAX_FRAMES):
    """
    Save frames to out_dir at ~1 every (1/fps) seconds.
    """
    print(f"🎞️ [extract_frames] Extracting frames every ~{1.0/fps:.1f}s from {video_path}")
    subprocess.run([
        "ffmpeg", "-y", "-i", video_path,
        "-vf", f"fps={fps}",
        os.path.join(out_dir, "frame_%03d.jpg")
    ], check=True)

    frame_files = sorted([f for f in os.listdir(out_dir) if f.lower().endswith(".jpg")])
    if len(frame_files) > max_frames:
        frame_files = frame_files[:max_frames]
    print(f"🎞️ [extract_frames] Extracted {len(frame_files)} frames")
    return [os.path.join(out_dir, f) for f in frame_files]

def image_to_base64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

# -------------------------
# LLM helpers
# -------------------------
def summarize_image(path: str, step_type: str = "exterior", orientation: str = None) -> str:
    """
    Summarize an image using CREIA-aligned guidance, based on step_type + orientation.
    """
    print(f"📸 [summarize_image] Start path={path} step_type={step_type} orientation={orientation}")
    with open(path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("utf-8")

    if step_type == "insulation":
        system_prompt = (
            "You are an energy audit assistant (CREIA Protocol).\n"
            "Analyze attic, wall, or crawl space insulation photos:\n"
            "- Identify insulation type (fiberglass, cellulose, rockwool, foam, etc.)\n"
            "- Estimate thickness/depth if visible\n"
            "- Rate condition: Good (continuous), Fair (minor gaps), Poor (missing/major gaps)\n"
            "- Flag issues: thermal breaks, gaps, attic scuttle covers, recessed lights, air leakage\n"
            "- Recommend upgrades where helpful.\n"
            "Keep the summary concise and professional."
        )
    elif step_type == "hvac":
        system_prompt = (
            "You are an energy audit assistant (CREIA Protocol).\n"
            "Analyze HVAC system photos (heating, cooling, ducting):\n"
            "- Identify system type (furnace, heat pump, mini-split, etc.)\n"
            "- Note brand, model, efficiency ratings (AFUE, SEER, HSPF) if visible\n"
            "- Assess age/condition (wear, rust, leaks)\n"
            "- Ducting: type, sealing, insulation, asbestos tape, air filter condition\n"
            "- Flag safety issues (cracked heat exchanger, CO risk, dirty/absent filters)\n"
            "- Mention efficiency implications.\n"
            "Provide a concise professional summary."
        )
    else:
        # Exterior / Siding context with orientation
        system_prompt = (
            "You are an energy audit assistant (CREIA Protocol).\n"
            f"Analyze the exterior siding image for the {orientation or 'given'} side:\n"
            "- Note shading (trees, eaves, landscape, nearby structures)\n"
            "- Assess glass–wall ratio (window area vs wall) and implications\n"
            "- Identify siding type (stucco, wood, vinyl, fiberboard)\n"
            "- Highlight comfort/efficiency impacts for this orientation.\n"
            "Be concise and factual."
        )

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
            ]},
        ],
    )
    summary = resp.choices[0].message.content.strip()
    print(f"📸 [summarize_image] Done: {summary[:120]}...")
    return summary

def summarize_video(path: str, step_type: str = "exterior", orientation: str = None) -> str:
    """
    Summarize a video with transcript + sampled frames, guided by CREIA protocol and orientation.
    """
    print(f"📹 [summarize_video] Start path={path} step_type={step_type} orientation={orientation}")

    # Step 1: Transcribe audio
    with open(path, "rb") as f:
        transcript = client.audio.transcriptions.create(
            model="gpt-4o-transcribe",
            file=f
        ).text
    print(f"📹 [summarize_video] Transcript length={len(transcript)}")

    # Step 2: Extract frames
    out_dir = tempfile.mkdtemp()
    frame_paths = extract_frames(path, out_dir, fps=FRAME_FPS, max_frames=MAX_FRAMES)

    try:
        # Step 3: Summarize frames
        frame_summaries = []
        for fp in frame_paths:
            b64_img = image_to_base64(fp)
            print(f"📸 [summarize_video] Frame -> {fp}")
            if step_type == "insulation":
                system_prompt = (
                    "You are an energy audit assistant (CREIA Protocol).\n"
                    "Analyze insulation from this video frame:\n"
                    "- Identify insulation type & depth\n"
                    "- Condition (Good/Fair/Poor)\n"
                    "- Issues: gaps, thermal breaks, attic cover, recessed lights\n"
                    "Short and factual."
                )
            elif step_type == "hvac":
                sys_prompt = (
                    "You are an energy audit assistant (CREIA Protocol).\n"
                    "Analyze this HVAC video frame:\n"
                    "- Identify equipment type, brand/model, efficiency labels\n"
                    "- Assess condition (wear, leaks, age)\n"
                    "- Ducts: type, sealing, insulation, asbestos tape\n"
                    "- Air filter placement/condition\n"
                    "Provide concise professional notes."
                )
            else:
                system_prompt = (
                    "You are an energy audit assistant (CREIA Protocol).\n"
                    f"Analyze this exterior siding frame for the {orientation or 'given'} side:\n"
                    "- Note/confirm orientation if visible\n"
                    "- Shading (trees, eaves, structures)\n"
                    "- Glass–wall ratio & comfort impact\n"
                    "- Siding type (stucco, wood, vinyl, etc.)\n"
                    "Keep it concise and factual."
                )

            resp = client.chat.completions.create(
                model="gpt-4.1",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
                    ]},
                ],
            )
            piece = resp.choices[0].message.content.strip()
            frame_summaries.append(piece)
            print(f"📸 [summarize_video] Frame summary: {piece[:100]}...")

        # Step 4: Merge transcript + frames
        if step_type == "insulation":
            merge_prompt = (
                "You are an energy auditor assistant (CREIA Protocol).\n"
                "Synthesize transcript + frame observations into a concise insulation note:\n"
                "- Type, depth, condition (Good/Fair/Poor)\n"
                "- Issues (gaps, thermal breaks, attic cover, recessed lights)\n"
                "- Upgrades if helpful.\n"
                "Output a short professional summary."
            )
        else:
            merge_prompt = (
                "You are an energy auditor assistant (CREIA Protocol).\n"
                f"Synthesize transcript + frames into a cohesive note for the {orientation or 'given'} side:\n"
                "- Orientation, shading, glass–wall ratio, siding type\n"
                "- Comfort/efficiency implications (heat gain, cold comfort)\n"
                "- Potential upgrades (shading, window treatments)\n"
                "Provide one concise professional audit summary."
            )

        merged_resp = client.chat.completions.create(
            model="gpt-4.1",
            messages=[
                {"role": "system", "content": merge_prompt},
                {"role": "user", "content": f"Transcript:\n{transcript}\n\nFrame observations:\n" + "\n".join(frame_summaries)},
            ],
        )
        final_summary = merged_resp.choices[0].message.content.strip()
        print(f"📹 [summarize_video] Final summary: {final_summary[:120]}...")
        return final_summary

    finally:
        shutil.rmtree(out_dir, ignore_errors=True)

def summarize_audio(path: str) -> str:
    print(f"🎙️ [summarize_audio] Summarizing {path}")
    with open(path, "rb") as f:
        transcript = client.audio.transcriptions.create(
            model="gpt-4o-transcribe",
            file=f
        ).text
    summary_resp = client.chat.completions.create(
        model="gpt-4.1",
        messages=[
            {"role": "system", "content": "Summarize the homeowner's statements clearly and concisely."},
            {"role": "user", "content": transcript}
        ]
    )
    return summary_resp.choices[0].message.content.strip()

# -------------------------
# Background worker
# -------------------------
def process_media_async(app, media_id: int, local_path: str, public_url: str, media_type: str, orientation: str = None):
    with app.app_context():
        summary = "❌ Processing failed"
        new_status = "Error"
        try:
            media = AuditMedia.query.get(media_id)
            step_type = media.step_type if media else "exterior"
            print(f"🚀 [process_media_async] media_id={media_id}, type={media_type}, step_type={step_type}, orientation={orientation}")

            if media_type == "photo":
                summary = summarize_image(local_path, step_type=step_type, orientation=orientation)
                new_status = "Completed"
            elif media_type == "video":
                summary = summarize_video(local_path, step_type=step_type, orientation=orientation)
                new_status = "Completed"
            elif media_type == "audio":
                transcript = summarize_audio(local_path)
                summary = f"Audio transcript: {transcript}"
                new_status = "Completed"
            else:
                summary = "ℹ️ Unsupported media type"
                new_status = "Error"
        except Exception as e:
            print(f"❌ [process_media_async] Error: {e}")
            summary = f"❌ Failed to process: {e}"
            new_status = "Error"

        # Save back
        media = AuditMedia.query.get(media_id)
        if media:
            media.summary = summary
            db.session.commit()
            print(f"✅ [process_media_async] Saved summary for media_id={media_id}")

            # Also update step status
            step = AuditStep.query.get(media.step_id)
            if step:
                step.status = new_status
                db.session.commit()
                print(f"📌 Step {step.id} status -> {new_status}")
            else:
                print(f"⚠️ [process_media_async] Step {media.step_id} not found at save time")

# -------------------------
# Routes
# -------------------------
@bp.route('/audits/<int:audit_id>/steps/<string:step_label>/upload', methods=['POST'])
def upload_media_by_step_label(audit_id, step_label):
    print(f"⬆️ [upload_media_by_step_label] Called for audit {audit_id}, step '{step_label}'")

    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    filename = secure_filename(f"{audit_id}_{step_label}_{file.filename}")

    # read into memory once (lets us reuse bytes for tmp + upload)
    file_bytes = file.read()
    print(f"⬆️ [upload_media_by_step_label] Received file {filename}, size={len(file_bytes)} bytes")

    step_type = request.form.get('step_type', 'exterior')
    media_type = request.form.get('media_type', 'photo')
    print(f"⬆️ [upload_media_by_step_label] step_type={step_type}, media_type={media_type}")

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
        print(f"🆕 Created step id={step.id} ({step_type}, '{step_label}')")
    else:
        if step.step_type != step_type:
            step.step_type = step_type
        step.status = "Processing"   # Always set Processing when new upload starts
        db.session.commit()
        print(f"✏️ Updated step {step.id} step_type={step.step_type}, status=Processing")
    print(f"📌 Found step id={step.id}")

    try:
        # Save a temp copy (for processing/compression)
        tmp_path = os.path.join(tempfile.gettempdir(), filename)
        with open(tmp_path, "wb") as f:
            f.write(file_bytes)
        print(f"📂 Saved temp file {tmp_path}")

        # Decide upload path (may switch to compressed)
        upload_local_path = tmp_path
        orig_size = os.path.getsize(tmp_path)

        # Compress if large video
        if media_type == "video" and orig_size > MAX_SIZE_BYTES:
            compressed_path = os.path.join(tempfile.gettempdir(), f"compressed_{filename}")
            compress_video(tmp_path, compressed_path)
            upload_local_path = compressed_path
            print(f"📉 [upload_media_by_step_label] Compressed {orig_size} -> {os.path.getsize(upload_local_path)} bytes")
        else:
            print(f"📦 [upload_media_by_step_label] Skipping compression (size={orig_size} bytes)")

        # Upload to Supabase
        with open(upload_local_path, "rb") as f:
            data = f.read()
        content_type = file.mimetype or _guess_content_type(filename)
        _upload_to_supabase_bytes(filename, data, content_type)
        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET_NAME}/{filename}"
        print(f"✅ [upload_media_by_step_label] Uploaded -> {public_url}")

        # Create DB record with placeholder summary
        media_row = AuditMedia(
            audit_id=audit_id,
            step_id=step.id,
            step_type=step.step_type,
            side=step.label.replace(" Side", ""),
            media_url=public_url,
            file_name=file.filename,
            media_type=media_type,
            summary="Processing…"
        )
        db.session.add(media_row)
        db.session.commit()
        print(f"📌 Media record created id={media_row.id}")

        # Spawn background summarization
        orientation = step_label.replace(" Side", "").strip() if step_type == "exterior" else None
        Thread(
            target=process_media_async,
            args=(current_app._get_current_object(), media_row.id, upload_local_path, public_url, media_type, orientation)
        ).start()
        print(f"🚀 Spawned background thread for media {media_row.id}")

        return jsonify({
            "id": media_row.id,
            "media_url": public_url,
            "summary": media_row.summary,
            "status": step.status
        }), 201

    except Exception as e:
        print(f"❌ [upload_media_by_step_label] Upload failed: {e}")
        step = AuditStep.query.filter_by(audit_id=audit_id, label=step_label).first()
        if step:
            step.status = "Error"
            db.session.commit()
            print(f"⚠️ Step {step.id} marked as Error due to upload failure")
        return jsonify({'error': 'Upload failed', 'details': str(e)}), 500


# --- Upload media by step_id (kept for compatibility) ---
@bp.route('/steps/<int:step_id>/upload', methods=['POST'])
def upload_step_media(step_id):
    print(f"⬆️ [upload_step_media] Called for step_id={step_id}")

    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    filename = f"step_{step_id}_{secure_filename(file.filename)}"
    file_bytes = file.read()
    print(f"⬆️ [upload_step_media] Received {filename}, size={len(file_bytes)} bytes")

    step = AuditStep.query.get(step_id)
    if not step:
        return jsonify({'error': 'Step not found'}), 404

    media_type = request.form.get('media_type', 'photo')
    print(f"⬆️ [upload_step_media] step_type={step.step_type}, media_type={media_type}")

    try:
        # ⏳ mark step as Processing
        step.status = "Processing"
        db.session.commit()

        # For parity with label route, optionally compress if needed
        tmp_path = os.path.join(tempfile.gettempdir(), filename)
        with open(tmp_path, "wb") as f:
            f.write(file_bytes)
        upload_local_path = tmp_path

        if media_type == "video" and os.path.getsize(tmp_path) > MAX_SIZE_BYTES:
            compressed_path = os.path.join(tempfile.gettempdir(), f"compressed_{filename}")
            compress_video(tmp_path, compressed_path)
            upload_local_path = compressed_path

        with open(upload_local_path, "rb") as f:
            data = f.read()

        content_type = file.mimetype or _guess_content_type(filename)
        _upload_to_supabase_bytes(filename, data, content_type)
        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET_NAME}/{filename}"
        print(f"✅ [upload_step_media] Uploaded -> {public_url}")

        media_row = AuditMedia(
            audit_id=step.audit_id,
            step_id=step.id,
            step_type=step.step_type,
            side=step.label.replace(" Side", ""),
            media_url=public_url,
            file_name=file.filename,
            media_type=media_type,
            summary="Processing…"
        )
        db.session.add(media_row)
        db.session.commit()
        print(f"📌 Media record created id={media_row.id}")

        # Background processing (no explicit orientation here)
        Thread(
            target=process_media_async,
            args=(current_app._get_current_object(), media_row.id, upload_local_path, public_url, media_type, None)
        ).start()
        print(f"🚀 Spawned background thread for media {media_row.id}")

        return jsonify({
            "id": media_row.id,
            "media_url": public_url,
            "summary": media_row.summary,
            "status": "processing"
        }), 201

    except Exception as e:
        print(f"❌ [upload_step_media] Upload failed: {e}")
        step = AuditStep.query.get(step_id)
        if step:
            step.status = "Error"
            db.session.commit()
        return jsonify({'error': 'Upload failed', 'details': str(e)}), 500

# --- Get all media for an audit ---
@bp.route('/audits/<int:audit_id>/media', methods=['GET'])
def get_audit_media(audit_id):
    print(f"📥 [get_audit_media] audit_id={audit_id}")
    media = AuditMedia.query.filter_by(audit_id=audit_id).all()
    payload = [{
        "id": m.id,
        "audit_id": m.audit_id,
        "step_type": m.step_type,
        "side": m.side,
        "media_url": m.media_url,
        "file_name": m.file_name,
        "media_type": m.media_type,
        "summary": m.summary,
        "created_at": m.created_at.isoformat()
    } for m in media]
    print(f"📤 [get_audit_media] returning {len(payload)} items")
    return jsonify(payload)


# --- Get media for a specific step ---
@bp.route('/steps/<int:step_id>/media', methods=['GET'])
def get_step_media(step_id):
    print(f"📥 [get_step_media] step_id={step_id}")
    media = AuditMedia.query.filter_by(step_id=step_id).all()
    payload = [{
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
    } for m in media]
    print(f"📤 [get_step_media] returning {len(payload)} items")
    return jsonify(payload)