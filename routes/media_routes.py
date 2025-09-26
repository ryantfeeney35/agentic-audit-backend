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

MAX_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB

bp = Blueprint("media", __name__)

# Supabase setup
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_BUCKET_NAME = os.getenv("SUPABASE_BUCKET_NAME")
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# --- helpers ---
def compress_video(input_path, output_path):
    try:
        print(f"🎞️ [compress_video] Compressing {input_path} -> {output_path}")
        subprocess.run([
            "ffmpeg", "-y", "-i", input_path,
            "-vf", "scale=1280:-2",
            "-c:v", "libx264", "-crf", "28", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-map", "0:v:0", "-map", "0:a:0",
            output_path
        ], check=True)
        size = os.path.getsize(output_path)
        print(f"✅ [compress_video] Finished compression, size={size} bytes")
    except subprocess.CalledProcessError as e:
        print(f"❌ [compress_video] Failed: {e}")
        raise

def summarize_image(path: str, step_type: str = "exterior") -> str:
    """
    Summarize an image using CREIA-aligned guidance, based on step_type.
    """
    print(f"📸 [summarize_image] Starting for {path} (step_type={step_type})")
    with open(path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("utf-8")

    if step_type == "insulation":
        system_prompt = (
            "You are an energy audit assistant following the CREIA Energy Assessment Protocol.\n"
            "Analyze attic, wall, or crawl space insulation photos:\n"
            "- Identify insulation type (fiberglass, cellulose, rockwool, foam, etc.)\n"
            "- Estimate thickness/depth if visible\n"
            "- Rate condition: Good (continuous), Fair (minor gaps), Poor (missing/major gaps)\n"
            "- Flag issues: thermal breaks, gaps, attic scuttle covers, recessed lights, air leakage\n"
            "- Recommend upgrades: add insulation, air seal, insulate attic cover, replace can lights\n"
            "Keep the summary concise, professional, and relevant to comfort and efficiency."
        )
    else:  # default to exterior/siding
        system_prompt = (
            "You are an energy audit assistant following the CREIA Energy Assessment Protocol.\n"
            "Analyze this exterior siding image:\n"
            "- Identify orientation (N, NE, E, SE, S, SW, W, NW) if visible\n"
            "- Note shading (trees, eaves, landscape, structures)\n"
            "- Assess glass–wall ratio (window area vs wall) and implications\n"
            "- Identify siding type (stucco, wood, vinyl, fiberboard)\n"
            "Provide a concise, factual summary highlighting comfort and efficiency impacts."
        )

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
                ],
            },
        ],
    )
    summary = resp.choices[0].message.content.strip()
    print(f"📸 [summarize_image] Summary: {summary[:120]}...")
    return summary

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

def summarize_video(path: str, step_type: str = "exterior") -> str:
    """
    Summarize a video with transcript + frames, guided by CREIA protocol and step_type.
    """
    print(f"📹 [summarize_video] Starting for {path} (step_type={step_type})")

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
        frame_summaries = []
        for fp in frame_paths:
            b64_img = image_to_base64(fp)
            print(f"📸 [summarize_video] Processing frame {fp}")

            if step_type == "insulation":
                system_prompt = (
                    "You are an energy audit assistant (CREIA Protocol).\n"
                    "Analyze insulation from this video frame:\n"
                    "- Identify insulation type and depth if visible\n"
                    "- Assess condition (Good/Fair/Poor)\n"
                    "- Flag gaps, compressed sections, attic scuttle covers, or recessed lighting issues\n"
                    "- Mention upgrade opportunities if relevant.\n"
                    "Keep it short and factual."
                )
            else:  # exterior siding/orientation
                system_prompt = (
                    "You are an energy audit assistant (CREIA Protocol).\n"
                    "Analyze this exterior siding frame:\n"
                    "- Note orientation (N/S/E/W)\n"
                    "- Assess shading (trees, eaves, structures)\n"
                    "- Evaluate glass–wall ratio and energy impact\n"
                    "- Identify siding type (stucco, wood, vinyl, etc.)\n"
                    "Be concise and factual."
                )

            resp = client.chat.completions.create(
                model="gpt-4.1",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
                        ],
                    },
                ],
            )
            frame_summaries.append(resp.choices[0].message.content.strip())
            print(f"📸 [summarize_video] Frame summary: {frame_summaries[-1][:100]}...")

        # --- Step 3: Merge transcript + frame summaries ---
        if step_type == "insulation":
            merge_prompt = (
                "You are an energy auditor assistant (CREIA Protocol).\n"
                "Synthesize transcript + frame observations into a concise insulation note.\n"
                "- Identify insulation type, depth, condition (Good/Fair/Poor)\n"
                "- Flag issues (gaps, thermal breaks, recessed lights, attic cover)\n"
                "- Recommend upgrades where useful.\n"
                "Output a professional, concise summary."
            )
        else:
            merge_prompt = (
                "You are an energy auditor assistant (CREIA Protocol).\n"
                "Synthesize transcript + frame observations into a cohesive siding/orientation note.\n"
                "- Orientation, shading, glass–wall ratio, siding type\n"
                "- Comfort/efficiency implications (heat gain, cold comfort)\n"
                "- Upgrade opportunities (shading, window treatments).\n"
                "Provide one concise professional audit summary."
            )

        merged_resp = client.chat.completions.create(
            model="gpt-4.1",
            messages=[
                {"role": "system", "content": merge_prompt},
                {
                    "role": "user",
                    "content": (
                        f"Transcript:\n{transcript}\n\n"
                        f"Frame observations:\n" + "\n".join(frame_summaries)
                    ),
                },
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

# ✅ background processor with step_type-aware summarization
def process_media_async(app, media_id: int, tmp_path: str, public_url: str, media_type: str):
    with app.app_context():  # push proper app context
        summary = "❌ Processing failed"
        try:
            media = AuditMedia.query.get(media_id)
            step_type = None
            if media:
                step_type = media.step_type
                print(f"🚀 [process_media_async] Start for media_id={media_id}, type={media_type}, step_type={step_type}")
            else:
                print(f"⚠️ [process_media_async] Media {media_id} not found in DB")
                return

            if media_type == "photo":
                summary = summarize_image(tmp_path, step_type=step_type or "exterior")
            elif media_type == "video":
                summary = summarize_video(tmp_path, step_type=step_type or "exterior")
            elif media_type == "audio":
                transcript = summarize_audio(tmp_path)
                summary = f"Audio transcript: {transcript}"

        except Exception as e:
            print(f"❌ [process_media_async] Error while processing media {media_id}: {e}")
            summary = f"❌ Failed to process: {e}"

        # ✅ Save summary to DB
        media = AuditMedia.query.get(media_id)
        if media:
            media.summary = summary
            db.session.commit()
            print(f"✅ [process_media_async] Saved summary for media_id={media_id}")
        else:
            print(f"⚠️ [process_media_async] Media {media_id} disappeared before save")

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

        upload_path = tmp_path
        file_size = os.path.getsize(tmp_path)

        # compress if video and >50MB
        if media_type == "video" and file_size > MAX_SIZE_BYTES:
            compressed_path = os.path.join(tempfile.gettempdir(), f"compressed_{filename}")
            compress_video(tmp_path, compressed_path)
            upload_path = compressed_path
            print(f"📉 [upload_media_by_step_label] Compressed from {file_size} → {os.path.getsize(upload_path)} bytes")
        else:
            print(f"📦 [upload_media_by_step_label] Skipping compression (size={file_size} bytes)")

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
            args=(current_app._get_current_object(), media.id, upload_path, public_url, media_type)
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