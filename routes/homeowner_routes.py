# routes/homeowner_routes.py
"""
Homeowner portal routes for lightweight phone+address authentication
and data submission (utility bills, Enphase CSV uploads).
"""
import os
import uuid
from datetime import datetime
from flask import Blueprint, request, jsonify, g
from models import Property, Audit, AuditStep, AuditMedia, UtilityConnection, EnphaseConnection, db
from supabase import create_client
import logging
from functools import wraps
from werkzeug.utils import secure_filename

# Import utilities from shared module
from utils.homeowner_utils import (
    normalize_phone,
    validate_phone,
    generate_homeowner_token,
    decode_homeowner_token,
    HOMEOWNER_JWT_EXPIRY_HOURS
)

bp = Blueprint('homeowner', __name__)

# Supabase configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_BUCKET_NAME = os.getenv("SUPABASE_BUCKET_NAME")

# Allowed file types and max size for utility bills
ALLOWED_UTILITY_BILL_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg'}
MAX_UTILITY_BILL_SIZE = 10 * 1024 * 1024  # 10MB


def _get_supabase_client():
    """Get Supabase client for file uploads."""
    if not (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY):
        return None
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def _allowed_file(filename: str, allowed_extensions: set) -> bool:
    """Check if file has an allowed extension."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in allowed_extensions


def _get_content_type(filename: str) -> str:
    """Get content type from filename."""
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    content_types = {
        'pdf': 'application/pdf',
        'png': 'image/png',
        'jpg': 'image/jpeg',
        'jpeg': 'image/jpeg'
    }
    return content_types.get(ext, 'application/octet-stream')


def require_homeowner_auth(f):
    """
    Decorator to require homeowner authentication.
    
    Expects Authorization header with Bearer token.
    Sets g.homeowner_property_id and g.homeowner_phone on success.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        
        if not auth_header:
            return jsonify({'error': 'Authorization header required'}), 401
        
        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != 'bearer':
            return jsonify({'error': 'Invalid authorization header format'}), 401
        
        token = parts[1]
        payload = decode_homeowner_token(token)
        
        if not payload:
            return jsonify({'error': 'Invalid or expired token'}), 401
        
        # Set context for the request
        g.homeowner_property_id = payload['property_id']
        g.homeowner_phone = payload['phone_number']
        
        return f(*args, **kwargs)
    
    return decorated_function


@bp.route('/homeowner/auth', methods=['POST'])
def homeowner_auth():
    """
    Authenticate homeowner with address and phone number.
    
    NOTE: Rate limiting (10 req/min per IP) should be configured at the
    infrastructure level (Railway/nginx) rather than in application code.
    
    Request body:
    {
        "street": "123 Main St",
        "city": "San Diego",
        "state": "CA",
        "zip_code": "92101",
        "phone_number": "555-123-4567"  # Any reasonable phone format
    }
    
    Returns:
    - 200 with JWT token if property found and phone matches
    - 401 if no matching property or phone mismatch
    - 400 if missing/invalid parameters
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'Request body required'}), 400
        
        street = data.get('street', '').strip()
        city = data.get('city', '').strip()
        state = data.get('state', '').strip()
        zip_code = data.get('zip_code', '').strip()
        phone_number = data.get('phone_number', '').strip()
        
        if not street or not city or not state or not zip_code:
            return jsonify({'error': 'Address fields (street, city, state, zip_code) are required'}), 400
        
        if not phone_number:
            return jsonify({'error': 'phone_number is required'}), 400
        
        # Normalize phone number
        normalized_phone = normalize_phone(phone_number)
        if not normalized_phone:
            return jsonify({'error': 'Invalid phone number format'}), 400
        
        # Look up property by normalized address (case-insensitive)
        property = Property.query.filter(
            db.func.lower(Property.street) == street.lower(),
            db.func.lower(Property.city) == city.lower(),
            db.func.upper(Property.state) == state.upper(),
            Property.zip_code == zip_code
        ).first()
        
        if not property:
            logging.info(f"Homeowner auth failed: no property found for address={street}, {city}, {state} {zip_code}")
            return jsonify({'error': 'No property found for this address'}), 401
        
        # Check phone number match
        if property.phone_number != normalized_phone:
            logging.info(f"Homeowner auth failed: phone mismatch for property_id={property.id}")
            return jsonify({'error': 'Phone number does not match our records'}), 401
        
        # Generate token
        token = generate_homeowner_token(property.id, normalized_phone)
        
        logging.info(f"Homeowner auth success: property_id={property.id}")
        
        return jsonify({
            'access_token': token,
            'token_type': 'bearer',
            'expires_in': HOMEOWNER_JWT_EXPIRY_HOURS * 3600,
            'property': {
                'id': property.id,
                'address': property.street,
                'city': property.city,
                'state': property.state,
                'zip_code': property.zip_code
            }
        }), 200
        
    except Exception as e:
        logging.error(f"Homeowner auth error: {e}")
        return jsonify({'error': 'Authentication failed'}), 500


@bp.route('/homeowner/session', methods=['GET'])
@require_homeowner_auth
def homeowner_session():
    """
    Get current homeowner session information.
    
    Returns property details for the authenticated homeowner.
    """
    try:
        property = Property.query.get(g.homeowner_property_id)
        
        if not property:
            return jsonify({'error': 'Property not found'}), 404
        
        # Build task completion status from actual DB records
        task_status = {
            'utility_bill': 'not-started',
            'utility_data': 'not-started',
            'solar_data': 'not-started',
        }
        
        audit = Audit.query.filter_by(property_id=property.id).order_by(Audit.created_at.desc()).first()
        if audit:
            # Check utility bill: look for completed step or media record
            bill_step = AuditStep.query.filter_by(
                audit_id=audit.id, step_type='utility_bill'
            ).first()
            if bill_step and bill_step.status == 'Completed':
                task_status['utility_bill'] = 'completed'
            else:
                # Also check media directly (mobile app uses step_type='interview' + label='Utility Bill')
                bill_media = AuditMedia.query.filter(
                    AuditMedia.audit_id == audit.id,
                    AuditMedia.media_type.in_(['utility_bill', 'document'])
                ).first()
                if bill_media:
                    task_status['utility_bill'] = 'completed'
            
            # Check utility data connection
            utility_conn = UtilityConnection.query.filter_by(
                audit_id=audit.id, status='connected'
            ).first()
            if utility_conn:
                task_status['utility_data'] = 'completed'
            
            # Check solar/Enphase data connection
            enphase_conn = EnphaseConnection.query.filter_by(
                audit_id=audit.id
            ).filter(EnphaseConnection.status.in_(['connected', 'sync_in_progress'])).first()
            if enphase_conn:
                task_status['solar_data'] = 'completed'
        
        return jsonify({
            'property': {
                'id': property.id,
                'address': property.street,
                'city': property.city,
                'state': property.state,
                'zip_code': property.zip_code,
                # Include audit status if available
                'has_audit': hasattr(property, 'audits') and len(property.audits) > 0
            },
            'task_status': task_status
        }), 200
        
    except Exception as e:
        logging.error(f"Homeowner session error: {e}")
        return jsonify({'error': 'Failed to get session'}), 500


@bp.route('/homeowner/utility-bill', methods=['POST'])
@require_homeowner_auth
def upload_utility_bill():
    """
    Upload a utility bill (PDF or image).
    
    Requires multipart/form-data with 'file' field.
    Accepts PDF, PNG, JPG, JPEG files up to 10MB.
    
    The file is stored in Supabase and linked to the property's audit
    as a new AuditMedia record with step_type='utility_bill'.
    """
    try:
        # Check if file was provided
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        # Validate file type
        if not _allowed_file(file.filename, ALLOWED_UTILITY_BILL_EXTENSIONS):
            return jsonify({
                'error': 'Invalid file type. Allowed: PDF, PNG, JPG, JPEG'
            }), 400
        
        # Read file and check size
        file_data = file.read()
        if len(file_data) > MAX_UTILITY_BILL_SIZE:
            return jsonify({
                'error': f'File too large. Maximum size is {MAX_UTILITY_BILL_SIZE // (1024*1024)}MB'
            }), 400
        
        # Get property and its audit
        property = Property.query.get(g.homeowner_property_id)
        if not property:
            return jsonify({'error': 'Property not found'}), 404
        
        # Get the most recent audit for this property
        audit = Audit.query.filter_by(property_id=property.id).order_by(Audit.created_at.desc()).first()
        if not audit:
            return jsonify({'error': 'No audit found for this property'}), 404
        
        # Find or create utility bill step
        utility_step = AuditStep.query.filter_by(
            audit_id=audit.id,
            step_type='utility_bill'
        ).first()
        
        if not utility_step:
            utility_step = AuditStep(
                user_id=audit.user_id,  # Use the audit owner's user_id
                audit_id=audit.id,
                step_type='utility_bill',
                label='Utility Bill',
                status='In Progress'
            )
            db.session.add(utility_step)
            db.session.flush()  # Get the step ID
        
        # Upload to Supabase
        supabase = _get_supabase_client()
        if not supabase or not SUPABASE_BUCKET_NAME:
            return jsonify({'error': 'File storage not configured'}), 500
        
        # Generate unique filename
        original_filename = secure_filename(file.filename)
        file_ext = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else ''
        unique_id = uuid.uuid4().hex
        storage_path = f"utility_bills/{audit.id}/{unique_id}.{file_ext}"
        content_type = _get_content_type(original_filename)
        
        # Upload to Supabase storage
        try:
            supabase.storage.from_(SUPABASE_BUCKET_NAME).upload(
                storage_path,
                file_data,
                file_options={"content-type": content_type}
            )
        except Exception as e:
            if "409" in str(e):  # File exists - update
                supabase.storage.from_(SUPABASE_BUCKET_NAME).update(
                    path=storage_path,
                    file=file_data,
                    file_options={"content-type": content_type}
                )
            else:
                raise
        
        # Get signed URL for the uploaded file
        signed = supabase.storage.from_(SUPABASE_BUCKET_NAME).create_signed_url(
            storage_path, 
            60 * 60 * 24 * 365  # 1 year expiry
        )
        media_url = signed.get('signedURL') if signed else None
        
        if not media_url:
            return jsonify({'error': 'Failed to generate file URL'}), 500
        
        # Create AuditMedia record
        media = AuditMedia(
            user_id=audit.user_id,  # Use the audit owner's user_id
            audit_id=audit.id,
            step_id=utility_step.id,
            media_url=media_url,
            file_name=original_filename,
            media_type='utility_bill',
            notes=f'Uploaded by homeowner via portal'
        )
        db.session.add(media)
        
        # Update step status
        utility_step.status = 'Completed'
        
        db.session.commit()
        
        logging.info(f"Utility bill uploaded: property_id={property.id}, audit_id={audit.id}, media_id={media.id}")
        
        return jsonify({
            'success': True,
            'media_id': media.id,
            'file_name': original_filename,
            'message': 'Utility bill uploaded successfully'
        }), 201
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Utility bill upload error: {e}")
        return jsonify({'error': 'Failed to upload utility bill'}), 500


@bp.route('/homeowner/enphase/upload', methods=['POST'])
@require_homeowner_auth
def upload_enphase_csv():
    """
    Upload Enphase telemetry data from Enlighten custom report CSV.
    
    Expected CSV format from Enlighten:
    - Date/Time (MM/DD/YYYY HH:MM)
    - Energy Produced (Wh)
    - Energy Consumed (Wh)
    - Exported to Grid (Wh)
    - Imported from Grid (Wh)
    - Stored in batteries (Wh)
    - Discharged from batteries (Wh)
    
    Creates EnphaseConnection with system_id='spreadsheet-{audit_id}' and
    inserts EnphaseTelemetryInterval records with source='spreadsheet'.
    
    Returns:
        201: Processing summary with records_created, date_range, totals
        400: Invalid file or parsing error
        404: No active audit for property
        413: File too large
        500: Server error
    """
    from utils.enphase_csv_parser import parse_csv, EnphaseCSVParseError
    from models import EnphaseConnection, EnphaseTelemetryInterval
    
    MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB limit for CSV
    
    try:
        property = Property.query.get(g.homeowner_property_id)
        if not property:
            return jsonify({'error': 'Property not found'}), 404
        
        # Get the audit associated with this property
        audit = Audit.query.filter_by(
            property_id=property.id,
            status='in_progress'
        ).first()
        
        if not audit:
            audit = Audit.query.filter_by(property_id=property.id).order_by(
                Audit.created_at.desc()
            ).first()
        
        if not audit:
            return jsonify({'error': 'No audit found for this property'}), 404
        
        # Validate file upload
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        
        if not file.filename:
            return jsonify({'error': 'No file selected'}), 400
        
        # Validate file extension
        allowed_extensions = {'csv'}
        extension = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
        if extension not in allowed_extensions:
            return jsonify({'error': 'Invalid file type. Only CSV files are accepted.'}), 400
        
        # Read file content
        file_data = file.read()
        
        # Check file size
        if len(file_data) > MAX_FILE_SIZE:
            return jsonify({'error': f'File too large. Maximum size is {MAX_FILE_SIZE // (1024 * 1024)}MB.'}), 413
        
        if len(file_data) == 0:
            return jsonify({'error': 'File is empty'}), 400
        
        # Parse CSV
        try:
            records, granularity, summary = parse_csv(file_data)
        except EnphaseCSVParseError as e:
            return jsonify({'error': f'CSV parsing error: {str(e)}'}), 400
        
        # Get or create EnphaseConnection for spreadsheet uploads
        system_id = f'spreadsheet-{audit.id}'
        connection = EnphaseConnection.query.filter_by(
            audit_id=audit.id,
            system_id=system_id
        ).first()
        
        if not connection:
            connection = EnphaseConnection(
                user_id=audit.user_id,
                audit_id=audit.id,
                property_id=property.id,
                system_id=system_id,
                system_name='Enlighten Spreadsheet Import',
                status='connected'
            )
            db.session.add(connection)
            db.session.flush()  # Get connection.id
        
        # Upsert telemetry intervals
        records_created = 0
        records_updated = 0
        
        for record in records:
            # Check for existing interval
            existing = EnphaseTelemetryInterval.query.filter_by(
                connection_id=connection.id,
                interval_start=record['interval_start']
            ).first()
            
            if existing:
                # Update existing record
                existing.production_kwh = record.get('production_kwh')
                existing.consumption_kwh = record.get('consumption_kwh')
                existing.grid_import_kwh = record.get('grid_import_kwh')
                existing.grid_export_kwh = record.get('grid_export_kwh')
                existing.battery_charge_kwh = record.get('battery_charge_kwh')
                existing.battery_discharge_kwh = record.get('battery_discharge_kwh')
                existing.interval_end = record['interval_end']
                existing.granularity = granularity
                existing.source = 'spreadsheet'
                records_updated += 1
            else:
                # Create new record
                telemetry = EnphaseTelemetryInterval(
                    audit_id=audit.id,
                    property_id=property.id,
                    connection_id=connection.id,
                    system_id=system_id,
                    interval_start=record['interval_start'],
                    interval_end=record['interval_end'],
                    granularity=granularity,
                    production_kwh=record.get('production_kwh'),
                    consumption_kwh=record.get('consumption_kwh'),
                    grid_import_kwh=record.get('grid_import_kwh'),
                    grid_export_kwh=record.get('grid_export_kwh'),
                    battery_charge_kwh=record.get('battery_charge_kwh'),
                    battery_discharge_kwh=record.get('battery_discharge_kwh'),
                    source='spreadsheet',
                    raw_payload={'source_file': file.filename}
                )
                db.session.add(telemetry)
                records_created += 1
        
        # Update connection last sync
        connection.last_sync_at = datetime.utcnow()
        
        db.session.commit()
        
        logging.info(
            f"Enphase CSV uploaded: property_id={property.id}, audit_id={audit.id}, "
            f"records_created={records_created}, records_updated={records_updated}"
        )
        
        return jsonify({
            'success': True,
            'connection_id': connection.id,
            'records_created': records_created,
            'records_updated': records_updated,
            'granularity': granularity,
            'date_range': {
                'start': summary['date_range_start'].isoformat() if summary['date_range_start'] else None,
                'end': summary['date_range_end'].isoformat() if summary['date_range_end'] else None
            },
            'totals': {
                'production_kwh': summary['total_production_kwh'],
                'consumption_kwh': summary['total_consumption_kwh'],
                'grid_export_kwh': summary['total_grid_export_kwh'],
                'grid_import_kwh': summary['total_grid_import_kwh'],
                'battery_charge_kwh': summary['total_battery_charge_kwh'],
                'battery_discharge_kwh': summary['total_battery_discharge_kwh']
            }
        }), 201
        
    except Exception as e:
        db.session.rollback()
        logging.error(f"Enphase CSV upload error: {e}")
        return jsonify({'error': 'Failed to process Enphase CSV'}), 500


@bp.route('/homeowner/enphase/connect', methods=['POST'])
@require_homeowner_auth
def homeowner_enphase_connect():
    """
    POST /api/homeowner/enphase/connect

    Initiate Enphase OAuth authorization flow for the homeowner portal.
    Mirrors /api/enphase/connect but uses homeowner JWT auth instead of
    Supabase auditor auth.

    Returns:
        200: { authorization_url, state, connection_id }
        404: No audit found for property
        409: Active connection already exists
        500: Server error
    """
    import os
    import secrets
    from utils.enphase import EnphaseClient

    # Fast-fail if Enphase credentials are not configured
    if not all([
        os.environ.get('ENPHASE_CLIENT_ID'),
        os.environ.get('ENPHASE_CLIENT_SECRET'),
        os.environ.get('ENPHASE_API_KEY'),
    ]):
        logging.warning("Homeowner Enphase connect: missing ENPHASE env vars")
        return jsonify({'error': 'Enphase integration is not configured on this server'}), 503

    try:
        property = Property.query.get(g.homeowner_property_id)
        if not property:
            return jsonify({'error': 'Property not found'}), 404

        # Find the audit for this property (prefer in_progress, fall back to most recent)
        audit = Audit.query.filter_by(
            property_id=property.id,
            status='in_progress'
        ).first()

        if not audit:
            audit = Audit.query.filter_by(property_id=property.id).order_by(
                Audit.created_at.desc()
            ).first()

        if not audit:
            return jsonify({'error': 'No audit found for this property'}), 404

        # Check for existing active connection
        existing = EnphaseConnection.query.filter_by(
            audit_id=audit.id
        ).filter(
            EnphaseConnection.status.in_(['connected', 'pending_authorization', 'sync_in_progress'])
        ).first()

        if existing:
            if existing.status == 'connected':
                return jsonify({
                    'error': 'Active Enphase connection already exists',
                    'connection_id': existing.id,
                    'status': existing.status
                }), 409
            elif existing.status == 'pending_authorization':
                # Return existing pending auth URL
                client = EnphaseClient()
                auth_url = client.get_authorization_url(existing.oauth_state)
                return jsonify({
                    'authorization_url': auth_url,
                    'state': existing.oauth_state,
                    'connection_id': existing.id
                }), 200

        # Generate CSRF state token
        oauth_state = secrets.token_urlsafe(32)

        # Create pending connection with portal source marker
        connection = EnphaseConnection(
            user_id=audit.user_id,
            audit_id=audit.id,
            property_id=property.id,
            status='pending_authorization',
            oauth_state=oauth_state,
            provider_metadata={'source': 'portal'}
        )
        db.session.add(connection)
        db.session.commit()

        # Generate authorization URL
        client = EnphaseClient()
        auth_url = client.get_authorization_url(oauth_state)

        logging.info(
            f"Homeowner Enphase connect: property_id={property.id}, "
            f"audit_id={audit.id}, connection_id={connection.id}"
        )

        return jsonify({
            'authorization_url': auth_url,
            'state': oauth_state,
            'connection_id': connection.id
        }), 200

    except Exception as e:
        db.session.rollback()
        logging.error(f"Homeowner Enphase connect error: {e}", exc_info=True)
        return jsonify({'error': f'Failed to initiate Enphase connection: {e}'}), 500


@bp.route('/homeowner/utility/connect', methods=['POST'])
@require_homeowner_auth
def homeowner_utility_connect():
    """
    POST /api/homeowner/utility/connect

    Initiate utility data connection for the homeowner portal.
    Mirrors /api/utility/connect but uses homeowner JWT auth.

    Body (optional):
    {
        "utility_name": "SDGE",   // defaults to "SDGE"
        "data_scope": "electric"  // defaults to "electric"
    }

    Returns:
        200: { auth_url, state, connection_id, requires_redirect, provider_used }
        404: No audit found for property
        409: Active connection already exists
        500: Server error
    """
    from utils.providers.registry import get_registry

    try:
        property = Property.query.get(g.homeowner_property_id)
        if not property:
            return jsonify({'error': 'Property not found'}), 404

        # Find the audit for this property
        audit = Audit.query.filter_by(
            property_id=property.id,
            status='in_progress'
        ).first()

        if not audit:
            audit = Audit.query.filter_by(property_id=property.id).order_by(
                Audit.created_at.desc()
            ).first()

        if not audit:
            return jsonify({'error': 'No audit found for this property'}), 404

        data = request.get_json() or {}
        utility_name = data.get('utility_name', 'SDGE').upper()
        data_scope = data.get('data_scope', 'electric')

        if data_scope not in ['electric', 'gas', 'both']:
            return jsonify({'error': 'data_scope must be "electric", "gas", or "both"'}), 400

        # Check for existing active connection
        existing = UtilityConnection.query.filter_by(
            audit_id=audit.id
        ).filter(UtilityConnection.status.in_([
            'connected', 'pending_authorization', 'sync_in_progress'
        ])).first()

        if existing:
            return jsonify({
                'error': 'Active utility connection already exists for this audit',
                'existing_connection_id': existing.id,
                'existing_status': existing.status
            }), 409

        # Use the provider registry to initiate connection
        registry = get_registry()
        result = registry.connect(audit.id, audit.user_id, utility_name, data_scope)

        if not result.success:
            return jsonify({
                'error': result.error or 'Connection failed',
                'connection_id': result.connection_id
            }), 400

        # Tag the connection with portal source so the callback redirects correctly
        if result.connection_id:
            conn = UtilityConnection.query.get(result.connection_id)
            if conn:
                metadata = conn.provider_metadata or {}
                metadata['source'] = 'portal'
                conn.provider_metadata = metadata
                db.session.commit()

        logging.info(
            f"Homeowner utility connect: property_id={property.id}, "
            f"audit_id={audit.id}, connection_id={result.connection_id}"
        )

        response = {
            'auth_url': result.auth_url,
            'state': result.state,
            'connection_id': result.connection_id,
            'requires_redirect': result.auth_url is not None
        }

        if hasattr(result, 'provider_name') and result.provider_name:
            response['provider_used'] = result.provider_name

        return jsonify(response), 200

    except Exception as e:
        db.session.rollback()
        logging.error(f"Homeowner utility connect error: {e}", exc_info=True)
        return jsonify({'error': f'Failed to initiate utility connection: {e}'}), 500