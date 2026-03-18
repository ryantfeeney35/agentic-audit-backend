# routes/media.py
import os
import base64
import tempfile
import subprocess
import json
from threading import Thread
from flask import Blueprint, request, jsonify, current_app, g
from werkzeug.utils import secure_filename
from supabase import create_client
from openai import OpenAI
import traceback
import requests

from models import AuditMedia, AuditStep, Audit, db
from auth import require_auth
from utils.idempotency import idempotent
from agents.base_agent import run_agent
from agents.schemas import ExteriorSidingSchema, HVACSchema, InsulationSchema, InterviewSchema, InteriorRoomSchema, RoofMediaSchema

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
def process_media_async(app, media_id: int, local_path: str, public_url: str, media_type: str):
    """Background processor for any uploaded media (photo, video, or audio)."""

    # Create an OpenAI client inside the background thread (explicitly pass key)
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    with app.app_context():
        media = AuditMedia.query.get(media_id)
        step = AuditStep.query.get(media.step_id) if media else None
        if not step:
            print("❌ [process_media_async] No step found for media_id", media_id)
            return

        try:
            # --- PHOTO / VIDEO ---
            if media_type in ["photo", "video"]:
                print(f"🖼 Processing {media_type} for step {step.id}")

                # 1️⃣ Gather all image/video media for this step
                all_media = AuditMedia.query.filter(
                    AuditMedia.step_id == step.id,
                    AuditMedia.media_type.in_(["photo", "video"])
                ).all()

                if not all_media:
                    print(f"⚠️ No media found for step {step.id}")
                    return

                # 2️⃣ Convert all to base64 for context and remember file paths so we can summarize
                images_b64 = []
                media_file_paths = {}
                for m in all_media:
                    try:
                        file_path = None

                        # if the current upload, use the just-saved local_path (fast path)
                        if m.id == media.id:
                            file_path = local_path
                        else:
                            # otherwise, download from Supabase storage temporarily
                            from urllib.request import urlopen
                            response = urlopen(m.media_url)
                            tmp_path = os.path.join(tempfile.gettempdir(), os.path.basename(m.media_url))
                            with open(tmp_path, "wb") as f:
                                f.write(response.read())
                            file_path = tmp_path

                        with open(file_path, "rb") as f:
                            img_bytes = f.read()
                            img_b64 = base64.b64encode(img_bytes).decode("utf-8")
                        images_b64.append({
                            "file_name": m.file_name,
                            "b64": img_b64
                        })
                        media_file_paths[m.id] = file_path
                    except Exception as e:
                        print(f"⚠️ Skipped media {m.id} due to error: {e}")

                # 3️⃣ Build a richer context for the agent
                context = {
                    "type": "image_batch",
                    "count": len(images_b64),
                    "images": images_b64
                }

                # 4️⃣ Pick schema and run agent
                schema_map = {
                    "exterior": ExteriorSidingSchema,
                    "hvac": HVACSchema,
                    "insulation": InsulationSchema,
                    "interview": InterviewSchema,
                    "interior": InteriorRoomSchema,
                    "roof": RoofMediaSchema,
                }
                schema_cls = schema_map.get(step.step_type)
                if not schema_cls:
                    raise ValueError(f"No schema for step_type={step.step_type}")

                parsed = run_agent(
                    domain=step.step_type,
                    context=context,
                    audit_id=media.audit_id,
                    mode="media",
                )

                # 5️⃣ Save parsed AI summary at the step level
                step.ai_summary = parsed
                step.status = "Completed"
                db.session.commit()
                print(f"✅ [process_media_async] Completed {len(images_b64)} {media_type}(s) for step {step.id}")

                # 6️⃣ Lightweight photo summarization: persist a short note per media so
                # we have textual context to later match to recommendations. This is
                # intentionally simple (filename + size + step label) as a Phase 1
                # implementation; later we will replace with LLM/embedding-based summaries.
                for m in all_media:
                    try:
                        fp = media_file_paths.get(m.id)
                        size = None
                        if fp and os.path.exists(fp):
                            try:
                                size = os.path.getsize(fp)
                            except Exception:
                                size = None

                        if size:
                            m.notes = f"Photo: {m.file_name} — {size} bytes — step: {step.label}"
                        else:
                            m.notes = f"Photo: {m.file_name} — step: {step.label}"
                        db.session.add(m)
                    except Exception as e:
                        print(f"⚠️ Failed to set notes for media {m.id}: {e}")
                db.session.commit()
                # 7️⃣ Phase 2: generate short LLM captions and embeddings per media.
                # Use a vision-capable model that can accept image URLs rather than
                # sending large base64 payloads in prompts. This is faster and more robust.
                def _generate_caption_from_url(client, image_url, file_name):
                    try:
                        vision_model = os.getenv('VISION_MODEL', 'gpt-4o-mini')
                        print(f"🔎 [caption] attempting vision model={vision_model} for url={image_url}")

                        # Quick reachability check before calling the LLM
                        try:
                            resp_check = requests.get(image_url, timeout=5)
                            if resp_check.status_code != 200:
                                print(f"⚠️ [caption] image URL not reachable (status={resp_check.status_code}): {image_url}")
                                return None
                        except Exception as e:
                            print(f"⚠️ [caption] failed to fetch image URL prior to captioning: {e}")
                            return None

                        prompt_system = (
                            "You are a helpful assistant that writes a single concise caption for an image. "
                            "Given the image URL and filename, return ONE short (<= 20 words) descriptive caption. "
                            "Do not invent details that cannot be seen in the image. Keep it factual and concise."
                        )
                        user_content = f"Filename: {file_name}\nImage URL: {image_url}\n"

                        # Try to call a vision-capable responses/chat model. If the model
                        # does not support image URLs, this call may fail — we catch
                        # exceptions and fallback to notes. Log full traceback for diagnostics.
                        try:
                            resp = client.chat.completions.create(
                                model=vision_model,
                                messages=[
                                    {"role": "system", "content": prompt_system},
                                    {"role": "user", "content": user_content},
                                ],
                            )
                            # Defensive: ensure structure exists
                            try:
                                caption = resp.choices[0].message.content.strip()
                                print(f"✅ [caption] generated caption for media {file_name}: {caption}")
                                return caption
                            except Exception:
                                print(f"⚠️ [caption] unexpected response shape from vision model: {resp}")
                                return None
                        except Exception as e:
                            print(f"⚠️ Vision-model caption generation failed for URL {image_url}: {e}")
                            traceback.print_exc()
                            return None
                    except Exception as e:
                        print(f"⚠️ _generate_caption_from_url unexpected error: {e}")
                        traceback.print_exc()
                        return None

                for m, img_entry in zip(all_media, images_b64):
                    try:
                        img_url = m.media_url or img_entry.get('url') or None
                        caption = None
                        if img_url:
                            caption = _generate_caption_from_url(client, img_url, img_entry.get('file_name') or m.file_name)

                        if not caption:
                            # fallback to lightweight note or filename
                            caption = m.notes or f"Photo: {m.file_name}"

                        # Persist caption
                        if caption:
                            m.ai_caption = caption

                            # Create embeddings for caption
                            try:
                                emb = client.embeddings.create(model="text-embedding-3-large", input=caption)
                                vector = emb.data[0].embedding if getattr(emb, 'data', None) else None
                                m.ai_embedding = {"vector": vector} if vector else {}
                            except Exception as e:
                                print(f"⚠️ Failed to create embedding for media {m.id}: {e}")

                        db.session.add(m)
                    except Exception as e:
                        print(f"⚠️ Failed to persist AI caption/embedding for media {m.id}: {e}")
                db.session.commit()

            # --- AUDIO ---
            elif media_type == "audio":
                print(f"🎧 Transcribing and summarizing audio for step {step.id}")

                # 1️⃣ Transcribe the audio
                with open(local_path, "rb") as f:
                    transcript = client.audio.transcriptions.create(
                        model="gpt-4o-mini-transcribe",
                        file=f
                    ).text.strip()

                media.notes = transcript
                db.session.commit()

                # 2️⃣ Gather all audio transcripts for this step
                all_audio = AuditMedia.query.filter_by(
                    audit_id=step.audit_id,
                    step_id=step.id,
                    media_type="audio"
                ).all()
                transcripts = [
                    m.notes for m in all_audio
                    if m.notes and "Processing" not in m.notes
                ]

                if transcripts:
                    # 3️⃣ Summarize all transcripts into one step-level narrative
                    summarization_prompt = f"""
                            You are an expert residential energy auditor assistant. You will be given one or more audio transcripts 
                            recorded during a home energy audit.

                            The recordings may include:
                            - Homeowner interviews
                            - Auditor field observations (exterior, insulation, HVAC, etc.)
                            - Verbal notes describing site conditions, comfort issues, or improvement opportunities

                            Your task:
                            - Summarize the combined content clearly and professionally.
                            - Focus on relevant findings, comfort complaints, and upgrade opportunities.
                            - Include contextual clues (e.g., "North exterior wall shows…" or "Auditor noted attic insulation gaps").
                            - Write in a factual, concise narrative suitable for an audit report.
                            - Do NOT infer or add unspoken details.

                            Step context:
                            - Step type: {step.step_type or "unknown"}
                            - Step label: {step.label or "unspecified"}

                            Now summarize the following transcripts:
                            """

                    try:
                        response = client.chat.completions.create(
                            model="gpt-4o",
                            messages=[
                                {"role": "system", "content": summarization_prompt},
                                {"role": "user", "content": "\n\n".join(transcripts)}
                            ],
                        )
                        summary = response.choices[0].message.content.strip()
                        step.summary = summary
                        db.session.commit()
                    except Exception as e:
                        print(f"⚠️ Summarization failed: {e}")
                        traceback.print_exc()
                        if step:
                            step.status = "Error"
                            step.summary = {"error": str(e)}
                            db.session.commit()

                    
                    print(f"✅ [process_media_async] Audio summary saved for step {step.id}")

            else:
                raise ValueError(f"Unsupported media type: {media_type}")
            
            step.status = "Completed"
            db.session.commit()

        except Exception as e:
            print(f"❌ [process_media_async] Failed: {e}")
            traceback.print_exc()
            if step:
                step.status = "Error"
                step.ai_summary = {"error": str(e)}
                db.session.commit()

# -------------------------
# Routes
# -------------------------
@bp.route('/audits/<int:audit_id>/steps/<string:step_label>/upload', methods=['POST'])
@require_auth
@idempotent
def upload_media_by_step_label(audit_id, step_label):
    # Check audit ownership
    audit = Audit.query.filter_by(id=audit_id, user_id=g.current_user['id']).first()
    if not audit:
        return jsonify({'error': 'Audit not found or access denied'}), 403
        
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    filename = secure_filename(f"{audit_id}_{step_label}_{file.filename}")
    file_bytes = file.read()

    step_type = request.form.get('step_type', 'exterior')
    media_type = request.form.get('media_type', 'photo')

    # Find/create step
    step = AuditStep.query.filter_by(audit_id=audit_id, label=step_label, user_id=g.current_user['id']).first()
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
@require_auth
def get_step_media(step_id):
    media = AuditMedia.query.filter_by(step_id=step_id, user_id=g.current_user['id']).all()
    return jsonify([{
        "id": m.id,
        "audit_id": m.audit_id,
        "step_id": m.step_id,
        "step_type": AuditStep.query.get(m.step_id).step_type if m.step_id else None,
        "side": AuditStep.query.get(m.step_id).label if m.step_id else None,
        "media_url": m.media_url,
        "file_name": m.file_name,
        "media_type": m.media_type,
        "created_at": m.created_at.isoformat(),
        "short_label": " ".join(m.notes.split()[:5]) + ("…" if m.notes and len(m.notes.split()) > 5 else "")
                       if m.notes else m.file_name
    } for m in media])


@bp.route('/audits/<int:audit_id>/media', methods=['GET'])
@require_auth
def get_audit_media(audit_id):
    """Return all media for an audit. Optional query param 'media_type' to filter (photo, video, audio)."""
    media_type = request.args.get('media_type')
    q = AuditMedia.query.filter_by(audit_id=audit_id, user_id=g.current_user['id'])
    if media_type:
        q = q.filter(AuditMedia.media_type == media_type)
    media = q.order_by(AuditMedia.created_at.desc()).all()
    return jsonify([{
        "id": m.id,
        "audit_id": m.audit_id,
        "step_id": m.step_id,
        "step_type": AuditStep.query.get(m.step_id).step_type if m.step_id else None,
        "side": AuditStep.query.get(m.step_id).label if m.step_id else None,
        "media_url": m.media_url,
        "file_name": m.file_name,
        "media_type": m.media_type,
        "created_at": m.created_at.isoformat(),
        "short_label": " ".join((m.notes or m.file_name).split()[:5]) + ("…" if m.notes and len((m.notes or m.file_name).split()) > 5 else "")
    } for m in media])

@bp.route('/media/<int:media_id>', methods=['DELETE'])
@require_auth
def delete_media(media_id):
    media = AuditMedia.query.filter_by(id=media_id, user_id=g.current_user['id']).first()
    if not media:
        return jsonify({"error": "Media not found or access denied"}), 404

    try:
        step = AuditStep.query.get(media.step_id)

        # Try to delete from Supabase bucket
        try:
            if media.media_url:
                path_in_bucket = os.path.basename(media.media_url)
                supabase.storage.from_(SUPABASE_BUCKET_NAME).remove([path_in_bucket])
        except Exception as e:
            print(f"⚠️ [media] Failed to delete from Supabase: {e}")

        # Delete DB row
        db.session.delete(media)
        db.session.commit()

        # After commit, check if step still has media
        if step:
            remaining = AuditMedia.query.filter_by(step_id=step.id).count()
            if remaining == 0:
                step.status = "Not Started"
                step.summary = None
                db.session.commit()

        return jsonify({"message": "Media deleted successfully"}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": f"Failed to delete media: {e}"}), 500
    
@bp.route('/steps/<int:step_id>/upload', methods=['POST'])
@require_auth
@idempotent
def upload_media_by_step_id(step_id):
    """Upload photo or audio directly to a specific step_id"""
    

    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'No file uploaded'}), 400

    media_type = request.form.get('media_type', 'photo')
    step_type = request.form.get('step_type', 'exterior')

    step = AuditStep.query.filter_by(id=step_id, user_id=g.current_user['id']).first()
    if not step:
        return jsonify({'error': 'Step not found or access denied'}), 404

    # 🔄 Mark the step as "Processing" immediately for all uploads
    step.status = "Processing"
    db.session.commit()
    filename = f"{step.audit_id}_{step.label}_{file.filename}"
    tmp_path = os.path.join(tempfile.gettempdir(), filename)
    file.save(tmp_path)

    # compress video if needed
    MAX_SIZE_BYTES = 50 * 1024 * 1024
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

    # upload to supabase
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_BUCKET_NAME = os.getenv("SUPABASE_BUCKET_NAME")
    content_type = file.mimetype or _guess_content_type(filename)
    with open(upload_local_path, "rb") as f:
        data = f.read()
    _upload_to_supabase_bytes(filename, data, content_type)
    public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET_NAME}/{filename}"

    # create AuditMedia record
    media = AuditMedia(
        user_id=g.current_user['id'],
        audit_id=step.audit_id,
        step_id=step.id,
        media_url=public_url,
        file_name=file.filename,
        media_type=media_type,
        notes="Processing…"
    )
    db.session.add(media)
    db.session.commit()

    # async AI processing
    Thread(
        target=process_media_async,
        args=(current_app._get_current_object(), media.id, upload_local_path, public_url, media_type)
    ).start()

    return jsonify({
        "id": media.id,
        "media_url": public_url,
        "status": "Processing"
    }), 201