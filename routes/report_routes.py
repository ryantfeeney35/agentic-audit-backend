import os
import uuid
from datetime import datetime
from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename
from .media_routes import SUPABASE_URL, SUPABASE_BUCKET_NAME, _upload_to_supabase_bytes

bp = Blueprint("reports", __name__)


@bp.route('/audits/<int:audit_id>/report/upload', methods=['POST'])
def upload_report(audit_id: int):
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    # Basic validation for PDF
    mimetype = (file.mimetype or '').lower()
    filename = secure_filename(file.filename or "report.pdf")
    if not (mimetype.startswith('application/pdf') or filename.lower().endswith('.pdf')):
        return jsonify({'error': 'Invalid file type. PDF required.'}), 400

    # Build path: reports/{audit_id}/{timestamp}_{uuid}.pdf
    ts = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    unique = uuid.uuid4().hex[:8]
    path_in_bucket = f"reports/{audit_id}/{ts}_{unique}.pdf"

    try:
        data = file.read()
        if not data:
            return jsonify({'error': 'Empty file'}), 400

        _upload_to_supabase_bytes(path_in_bucket, data, 'application/pdf')
        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET_NAME}/{path_in_bucket}"
        return jsonify({"url": public_url}), 200
    except Exception as e:
        return jsonify({'error': 'Upload failed', 'details': str(e)}), 500
