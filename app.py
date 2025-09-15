from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from dotenv import load_dotenv
import os
from sqlalchemy import text
from models import db
from supabase import create_client, Client
from models import Audit, AuditStep, AuditMedia, AuditFinding
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename

# Load environment variables first
load_dotenv()

# Create Flask app
app = Flask(__name__)

# Configure DB from environment
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv("DATABASE_URL")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Setup extensions
db.init_app(app)
migrate = Migrate(app, db)
CORS(app)

from models import Property

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_BUCKET_NAME = os.getenv("SUPABASE_BUCKET_NAME")
SUPABASE_AUDIT_BUCKET_NAME = os.getenv("SUPABASE_AUDIT_BUCKET_NAME")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# ---------------------- AUDIT MEDIA ----------------------
@app.route('/api/audits/<int:audit_id>/steps/<string:step_label>/upload', methods=['POST'])
def upload_media_by_step_label(audit_id, step_label):
    step_type = request.form.get('step_type', 'exterior')
    media_type = request.form.get('media_type', 'photo')

    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    filename = secure_filename(f"{audit_id}_{step_label}_{file.filename}")
    file_content = file.read()

    # Find or create the step
    step = AuditStep.query.filter_by(audit_id=audit_id, label=step_label, step_type=step_type).first()
    if not step:
        step = AuditStep(audit_id=audit_id, label=step_label, step_type=step_type)
        db.session.add(step)
        db.session.commit()

    # Upload to Supabase (private bucket)
    try:
        supabase.storage.from_(SUPABASE_AUDIT_BUCKET_NAME).upload(
            path=filename,
            file=file_content,
            file_options={"content-type": file.mimetype}
        )

        # Store file path in DB (NOT public URL)
        media = AuditMedia(
            audit_id=audit_id,
            step_id=step.id,
            step_type=step.step_type,
            side=step.label.replace(" Side", ""),
            media_url=filename,
            file_name=file.filename,
            media_type=media_type
        )
        db.session.add(media)
        db.session.commit()

        # Generate signed URL for return
        signed = supabase.storage.from_(SUPABASE_AUDIT_BUCKET_NAME).create_signed_url(
            path=filename,
            expires_in=3600
        )

        return jsonify({
            "message": "Uploaded",
            "media_url": signed.get("signedURL"),
            "step_id": step.id
        }), 201

    except Exception as e:
        print(f"❌ Upload failed: {e}")
        return jsonify({'error': 'Upload failed'}), 500

@app.route('/api/audits/<int:audit_id>/steps/<string:step_label>/media', methods=['GET'])
def get_media_by_step_label(audit_id, step_label):
    step = AuditStep.query.filter_by(audit_id=audit_id, label=step_label).first()
    if not step:
        return jsonify([])

    media_items = AuditMedia.query.filter_by(step_id=step.id).all()
    response = []
    for m in media_items:
        signed_url = supabase.storage.from_(SUPABASE_AUDIT_BUCKET_NAME).create_signed_url(
            path=m.media_url,
            expires_in=3600
        )
        response.append({
            "id": m.id,
            "media_url": signed_url.get("signedURL"),
            "file_name": m.file_name,
            "media_type": m.media_type,
            "created_at": m.created_at.isoformat()
        })
    return jsonify(response)