from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename
from models import AuditMedia, AuditStep, db
import os
from supabase import create_client

bp = Blueprint("media", __name__)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_BUCKET_NAME = os.getenv("SUPABASE_BUCKET_NAME")
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

@bp.route("/steps/<int:step_id>/upload", methods=["POST"])
def upload_step_media(step_id):
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["file"]
    filename = f"step_{step_id}_{secure_filename(file.filename)}"
    file_content = file.read()

    supabase.storage.from_(SUPABASE_BUCKET_NAME).update(
        path=filename,
        file=file_content,
        file_options={"content-type": file.mimetype}
    )
    public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET_NAME}/{filename}"

    media = AuditMedia(
        audit_id=None,  # fill if needed
        step_id=step_id,
        step_type="unknown",
        media_url=public_url,
        file_name=file.filename,
        media_type="photo",
    )
    db.session.add(media)
    db.session.commit()
    return jsonify({"url": public_url}), 201