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
from datetime import datetime
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

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# Properties Endpoints
@app.route('/api/properties', methods=['GET', 'POST'])
def handle_properties():
    if request.method == 'GET':
        with db.engine.connect() as conn:
            result = conn.execute(text("""
                SELECT id, street, city, state, zip_code, year_built, sqft FROM properties
            """))
            properties = [
                {
                    "id": row.id,
                    "street": row.street,
                    "city": row.city,
                    "state": row.state,
                    "zip_code": row.zip_code,
                    "year_built": row.year_built,
                    "sqft": row.sqft
                }
                for row in result
            ]
            return jsonify(properties)
    elif request.method == 'POST':
        data = request.get_json()
        new_property = Property(
            street=data.get('street'),
            city=data.get('city'),
            state=data.get('state'),
            zip_code=data.get('zip_code'),
            year_built=data.get('year_built'),
            sqft=data.get('sqft')
        )
        db.session.add(new_property)
        db.session.commit()
        return jsonify({'id': new_property.id}), 201

@app.route('/api/properties/<int:property_id>', methods=['GET'])
def get_property(property_id):
    with db.engine.connect() as conn:
        result = conn.execute(text("""
            SELECT id, street, city, state, zip_code, year_built, sqft, utility_bill_name
            FROM properties
            WHERE id = :id
        """), {"id": property_id}).fetchone()

        if result:
            return jsonify({
                "id": result.id,
                "street": result.street,
                "city": result.city,
                "state": result.state,
                "zip_code": result.zip_code,
                "year_built": result.year_built,
                "sqft": result.sqft,
                "utility_bill_name": result.utility_bill_name  # ✅ NEW 
            })
        else:
            return jsonify({"error": "Property not found"}), 404

@app.route('/api/properties/<int:id>', methods=['PUT'])
def update_property(id):
    data = request.get_json()
    stmt = text("""
        UPDATE properties
        SET street=:street, city=:city, state=:state, zip_code=:zip_code, year_built=:year_built, sqft=:sqft
        WHERE id=:id
    """)
    with db.engine.connect() as conn:
        conn.execute(stmt, {**data, "id": id})
        conn.commit()
    return jsonify({"message": "Property updated"})

@app.route('/api/properties/<int:property_id>', methods=['DELETE'])
def delete_property(property_id):
    with db.engine.begin() as conn:
        result = conn.execute(
            text("DELETE FROM properties WHERE id = :id RETURNING id"),
            {"id": property_id}
        )
        deleted = result.fetchone()
        if deleted:
            return jsonify({"message": "Property deleted", "id": deleted.id}), 200
        else:
            return jsonify({"error": "Property not found"}), 404

@app.route('/api/properties/<int:property_id>/upload-utility-bill', methods=['POST'])
def upload_utility_bill(property_id):
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    original_filename = file.filename
    filename = f'property_{property_id}_{original_filename}'
    file_content = file.read()

    try:
        # Upload to Supabase Storage
        supabase.storage.from_(SUPABASE_BUCKET_NAME).upload(
            path=filename,
            file=file_content,
            file_options={"content-type": file.mimetype}
        )

        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET_NAME}/{filename}"

        # Save URL + file name in DB
        property_obj = Property.query.get(property_id)
        if not property_obj:
            return jsonify({"error": "Property not found"}), 404

        property_obj.utility_bill_url = public_url
        property_obj.utility_bill_name = original_filename  # ✅ NEW
        db.session.commit()

        return jsonify({
            'message': 'Uploaded and saved successfully',
            'url': public_url,
            'fileName': original_filename
        }), 200

    except Exception as e:
        print(e)
        return jsonify({'error': 'Upload failed'}), 500

# ---------------------- AUDITS ----------------------
@app.route('/api/properties/<int:property_id>/audits', methods=['POST'])
@app.route('/api/audits', methods=['POST'])
def create_audit():
    data = request.get_json()
    property_id = data.get("property_id")

    if not property_id:
        return jsonify({"error": "Missing property_id"}), 400

    try:
        new_audit = Audit(property_id=property_id)
        db.session.add(new_audit)
        db.session.commit()

        return jsonify({
            "id": new_audit.id,
            "property_id": new_audit.property_id,
            "date": new_audit.date.isoformat()
        }), 201
    except Exception as e:
        print(f"❌ Error creating audit: {e}")
        return jsonify({"error": "Failed to create audit"}), 500

@app.route('/api/audits/<int:audit_id>', methods=['GET'])
def get_audit(audit_id):
    audit = Audit.query.get(audit_id)
    if not audit:
        return jsonify({"error": "Audit not found"}), 404

    return jsonify({
        "id": audit.id,
        "property_id": audit.property_id,
        "date": audit.date.strftime('%Y-%m-%d'),
        "auditor_name": audit.auditor_name,
        "notes": audit.notes,
        "steps": [
            {
                "id": step.id,
                "step_type": step.step_type,
                "label": step.label,
                "is_completed": step.is_completed
            }
            for step in audit.steps
        ]
    })

@app.route('/api/properties/<int:property_id>/audit', methods=['GET'])
def get_audit_by_property(property_id):
    audit = Audit.query.filter_by(property_id=property_id).first()
    if audit:
        return jsonify({
            "id": audit.id,
            "property_id": audit.property_id,
            "date": audit.date.isoformat()
        })
    else:
        return jsonify({"error": "No audit found"}), 404

# ---------------------- AUDIT STEPS ----------------------
@app.route('/api/audits/<int:audit_id>/steps', methods=['GET'])
def get_audit_steps(audit_id):
    steps = AuditStep.query.filter_by(audit_id=audit_id).all()
    result = []

    for step in steps:
        media_items = AuditMedia.query.filter_by(step_id=step.id).all()
        media = [{
            "id": m.id,
            "media_url": m.media_url,
            "file_name": m.file_name,
            "media_type": m.media_type,
            "created_at": m.created_at.isoformat()
        } for m in media_items]

        result.append({
            "id": step.id,
            "label": step.label,
            "step_type": step.step_type,
            "is_completed": step.is_completed,
            "not_accessible": step.not_accessible,
            "media": media  # ✅ Add this
        })

    return jsonify(result)

@app.route('/api/audits/<int:audit_id>/steps', methods=['POST'])
def create_or_update_audit_step(audit_id):
    data = request.get_json()
    step_type = data.get('step_type')
    label = data.get('label')
    is_completed = data.get('is_completed')
    not_accessible = data.get('not_accessible')
    notes = data.get('notes')

    if not step_type or not label:
        return jsonify({'error': 'Missing step_type or label'}), 400

    # Check if the step already exists
    existing_step = AuditStep.query.filter_by(
        audit_id=audit_id,
        step_type=step_type,
        label=label
    ).first()

    if existing_step:
        # Update existing values only if provided
        if is_completed is not None:
            existing_step.is_completed = is_completed
        if not_accessible is not None:
            existing_step.not_accessible = not_accessible
        if notes is not None:
            existing_step.notes = notes

        db.session.commit()
        return jsonify({"message": "Step updated", "id": existing_step.id}), 200

    else:
        # Create new step
        new_step = AuditStep(
            audit_id=audit_id,
            step_type=step_type,
            label=label,
            is_completed=is_completed if is_completed is not None else False,
            not_accessible=not_accessible if not_accessible is not None else False,
            notes=notes
        )
        db.session.add(new_step)
        db.session.commit()
        return jsonify({"message": "Step created", "id": new_step.id}), 201

@app.route('/api/audits/<int:audit_id>/steps/<string:step_label>/media', methods=['GET'])
def get_media_by_step_label(audit_id, step_label):
    step = AuditStep.query.filter_by(audit_id=audit_id, label=step_label).first()
    if not step:
        return jsonify([])

    media_items = AuditMedia.query.filter_by(step_id=step.id).all()
    return jsonify([
        {
            "id": m.id,
            "media_url": m.media_url,
            "file_name": m.file_name,
            "media_type": m.media_type,
            "created_at": m.created_at.isoformat(),
            "not_accessible": step.not_accessible
        } for m in media_items
    ])
# ---------------------- AUDIT MEDIA ----------------------
@app.route('/api/steps/<int:step_id>/upload', methods=['POST'])
def upload_step_media(step_id):
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    filename = f'step_{step_id}_{file.filename}'
    file_content = file.read()

    try:
        supabase.storage.from_(SUPABASE_BUCKET_NAME).update(
            path=filename,
            file=file_content,
            file_options={"content-type": file.mimetype}
        )
        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET_NAME}/{filename}"

        media = AuditMedia(
            audit_id=audit_id,
            step_id=step.id,
            step_type=step.step_type,
            side=step.label.replace(" Side", ""),
            media_url=public_url,
            file_name=file.filename,
            media_type=media_type
        )
        db.session.add(media)
        db.session.commit()

        return jsonify({"url": public_url}), 201

    except Exception as e:
        print(e)
        return jsonify({'error': 'Upload failed'}), 500

@app.route('/api/audits/<int:audit_id>/media', methods=['GET'])
def get_audit_media(audit_id):
    media = AuditMedia.query.filter_by(audit_id=audit_id).all()
    return jsonify([{
        "id": m.id,
        "audit_id": m.audit_id,
        "step_type": m.step_type,
        "side": m.side,
        "media_url": m.media_url,
        "created_at": m.created_at.isoformat()
    } for m in media])

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

    # Upload to Supabase
    try:
        supabase.storage.from_(SUPABASE_BUCKET_NAME).update(
            path=filename,
            file=file_content,
            file_options={"content-type": file.mimetype}
        )
        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET_NAME}/{filename}"

        media = AuditMedia(
            audit_id=audit_id,
            step_id=step.id,
            step_type=step.step_type,
            side=step.label.replace(" Side", ""),
            media_url=public_url,
            file_name=file.filename,
            media_type=media_type
        )
        db.session.add(media)
        db.session.commit()

        return jsonify({
            "message": "Uploaded",
            "media_url": public_url,
            "step_id": step.id
        }), 201

    except Exception as e:
        print(f"❌ Upload failed: {e}")
        return jsonify({'error': 'Upload failed'}), 500

# ---------------------- AUDIT CHAT ----------------------
@app.route('/api/agent-chat', methods=['POST'])
def agent_chat():
    data = request.get_json()
    messages = data.get('messages', [])

    # Get the latest user message
    user_message = messages[-1]['text'] if messages else "Hi"

    # 🤖 Simple placeholder logic — swap this with OpenAI or your agent
    if "insulation" in user_message.lower():
        reply = "Great! Do you know what type of insulation you currently have?"
    elif "yes" in user_message.lower():
        reply = "Perfect. Could you upload a photo of the attic insulation?"
    else:
        reply = "Could you clarify your goal—are you trying to reduce bills or increase comfort?"

    return jsonify({"reply": reply})

# ---------------------- AUDIT FINDINGS ----------------------
@app.route('/api/steps/<int:step_id>/findings', methods=['POST'])
def add_finding(step_id):
    data = request.get_json()
    finding = AuditFinding(
        step_id=step_id,
        title=data.get('title'),
        description=data.get('description'),
        recommendation=data.get('recommendation'),
        severity=data.get('severity'),
        source=data.get('source')
    )
    db.session.add(finding)
    db.session.commit()
    return jsonify({"id": finding.id}), 201
    
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=True)