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

# Sample route: Get properties
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

# PUT /api/properties/<id>
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
    return jsonify([
        {
            "id": step.id,
            "label": step.label,
            "step_type": step.step_type,
            "is_completed": step.is_completed
        } for step in steps
    ])

@app.route('/api/audits/<int:audit_id>/steps', methods=['POST'])
def add_audit_step(audit_id):
    data = request.get_json()
    step = AuditStep(
        audit_id=audit_id,
        step_type=data.get('step_type'),
        label=data.get('label'),
        is_completed=data.get('is_completed', False),
        notes=data.get('notes')
    )
    db.session.add(step)
    db.session.commit()
    return jsonify({"id": step.id}), 201

@app.route('/api/audits/<int:audit_id>/steps', methods=['POST'])
def complete_audit_step(audit_id):
    data = request.get_json()
    step_type = data.get("step_type")
    label = data.get("label")

    if not step_type or not label:
        return jsonify({"error": "Missing step_type or label"}), 400

    step = AuditStep.query.filter_by(audit_id=audit_id, step_type=step_type, label=label).first()
    if not step:
        step = AuditStep(
            audit_id=audit_id,
            step_type=step_type,
            label=label,
            is_completed=True
        )
        db.session.add(step)
    else:
        step.is_completed = True

    db.session.commit()
    return jsonify({"message": "Step marked complete", "step_id": step.id}), 200

@app.route('/api/steps/<int:step_id>', methods=['PATCH'])
def update_step_status(step_id):
    data = request.get_json()
    step = AuditStep.query.get(step_id)
    if not step:
        return jsonify({"error": "Step not found"}), 404

    step.is_completed = data.get('is_completed', step.is_completed)
    db.session.commit()
    return jsonify({"message": "Step updated"})

# ---------------------- AUDIT MEDIA ----------------------
@app.route('/api/steps/<int:step_id>/upload', methods=['POST'])
def upload_step_media(step_id):
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    file = request.files['file']
    filename = f'step_{step_id}_{file.filename}'
    file_content = file.read()

    try:
        supabase.storage.from_(SUPABASE_BUCKET_NAME).upload(
            path=filename,
            file=file_content,
            file_options={"content-type": file.mimetype}
        )
        public_url = f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET_NAME}/{filename}"

        media = AuditMedia(
            step_id=step_id,
            file_url=public_url,
            file_name=file.filename,
            media_type=request.form.get('media_type', 'photo')
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