from flask import Flask, request, jsonify, send_from_directory, g, session
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from pywebpush import webpush, WebPushException
import os, uuid, secrets, datetime, base64, hashlib, hmac, requests, json, io, shutil, functools

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import Flow
    from googleapiclient.discovery import build as google_build
except ImportError:
    Credentials = None
    Flow = None
    google_build = None

try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
except ImportError:
    canvas = None
    A4 = None
    mm = None

try:
    import qrcode
except ImportError:
    qrcode = None

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:
    Fernet = None
    InvalidToken = Exception

try:
    import pyotp
except ImportError:
    pyotp = None

app = Flask(__name__)
CORS(app)
app.secret_key = os.environ.get("ACE_SECRET_KEY") or os.environ.get("SECRET_KEY") or secrets.token_hex(32)
_database_url=os.environ.get("DATABASE_URL","sqlite:///ace.db")
if _database_url.startswith("postgres://"):
    _database_url="postgresql://"+_database_url[len("postgres://"):]
app.config["SQLALCHEMY_DATABASE_URI"]=_database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
UPLOAD_DIR = os.environ.get("ACE_UPLOAD_DIR") or os.environ.get("UPLOAD_DIR") or os.path.join(os.path.dirname(__file__), "uploads")
os.environ.setdefault("ACE_UPLOAD_DIR", UPLOAD_DIR)
os.makedirs(UPLOAD_DIR, exist_ok=True)
db = SQLAlchemy(app)

LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET","")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN") or os.environ.get("LINE_ACCESS_TOKEN","")

GOOGLE_CLIENT_ID=os.getenv("GOOGLE_CLIENT_ID","")
GOOGLE_CLIENT_SECRET=os.getenv("GOOGLE_CLIENT_SECRET","")
GOOGLE_REDIRECT_URI=os.getenv("GOOGLE_REDIRECT_URI","")
GOOGLE_CALENDAR_SCOPES=["https://www.googleapis.com/auth/calendar"]
GOOGLE_TOKEN_FILE=os.getenv("GOOGLE_TOKEN_FILE","google_calendar_token.json")
GOOGLE_MAPS_API_KEY=os.getenv("GOOGLE_MAPS_API_KEY","")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY","")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL","gpt-5-mini")
PAYMENT_SECRET_KEY = os.environ.get("PAYMENT_SECRET_KEY","")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY","")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY","")
VAPID_CLAIMS_EMAIL = os.environ.get("VAPID_CLAIMS_EMAIL","mailto:admin@example.com")

class User(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    name=db.Column(db.String(120),nullable=False)
    email=db.Column(db.String(200),unique=True,nullable=False)
    password_hash=db.Column(db.String(255),nullable=False)
    role=db.Column(db.String(30),default="staff")
    token=db.Column(db.String(255),nullable=True)
    token_expires=db.Column(db.DateTime,nullable=True)


class PushSubscription(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    user_id=db.Column(db.Integer,db.ForeignKey("user.id"),nullable=False)
    endpoint=db.Column(db.Text,unique=True,nullable=False)
    p256dh=db.Column(db.Text,nullable=False)
    auth=db.Column(db.Text,nullable=False)
    user_agent=db.Column(db.Text)
    enabled=db.Column(db.Boolean,default=True)
    created_at=db.Column(db.DateTime,default=datetime.datetime.utcnow)
    updated_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,onupdate=datetime.datetime.utcnow)


class Customer(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    type=db.Column(db.String(30),default="個人")
    name=db.Column(db.String(200),nullable=False)
    phone=db.Column(db.String(50)); address=db.Column(db.Text)
    management=db.Column(db.String(200)); owner=db.Column(db.String(200))
    floor=db.Column(db.String(200)); warranty=db.Column(db.String(50))
    member_status=db.Column(db.String(50)); next_inspection=db.Column(db.String(50))
    referrer=db.Column(db.String(200)); archived=db.Column(db.Boolean,default=False)

class Project(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    customer_id=db.Column(db.Integer,db.ForeignKey("customer.id"))
    name=db.Column(db.String(200),nullable=False)
    address=db.Column(db.Text); status=db.Column(db.String(50))
    progress=db.Column(db.String(50)); owner=db.Column(db.String(120)); archived=db.Column(db.Boolean,default=False)

class Booking(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    customer_id=db.Column(db.Integer); project_id=db.Column(db.Integer)
    name=db.Column(db.String(200)); phone=db.Column(db.String(50))
    date=db.Column(db.String(50)); time=db.Column(db.String(50))
    address=db.Column(db.Text); note=db.Column(db.Text); status=db.Column(db.String(50))

class Membership(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    customer_id=db.Column(db.Integer,unique=True)
    member_status=db.Column(db.String(50)); plan=db.Column(db.String(100))
    contract_start=db.Column(db.String(50)); renew_date=db.Column(db.String(50))
    billing_status=db.Column(db.String(50)); payment_status=db.Column(db.String(50))
    cancel_date=db.Column(db.String(50)); repair_balance=db.Column(db.String(50))
    payment_customer_id=db.Column(db.String(200),nullable=True)

class Message(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    project_id=db.Column(db.Integer); body=db.Column(db.Text)
    source=db.Column(db.String(30),default="ACE")

class Photo(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    customer_id=db.Column(db.Integer,nullable=True)
    project_id=db.Column(db.Integer,nullable=True)
    category=db.Column(db.String(50)); name=db.Column(db.String(255))
    filename=db.Column(db.String(255))


















class IdempotencyRecord(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    key=db.Column(db.String(255),unique=True,index=True)
    path=db.Column(db.String(255),index=True)
    response_body=db.Column(db.Text,nullable=True)
    status_code=db.Column(db.Integer,default=200)
    created_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,index=True)


class PortalCustomerRequest(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    customer_id=db.Column(db.String(255),index=True)
    request_type=db.Column(db.String(50),index=True)
    payload=db.Column(db.Text,nullable=False)
    status=db.Column(db.String(30),default="open",index=True)
    created_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,index=True)
    updated_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,onupdate=datetime.datetime.utcnow)


class PortalRepairEstimate(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    customer_id=db.Column(db.String(255),index=True)
    project_id=db.Column(db.String(255),index=True)
    project_name=db.Column(db.String(255))
    payload=db.Column(db.Text,nullable=False)
    status=db.Column(db.String(30),default="published",index=True)
    consent_name=db.Column(db.String(255),nullable=True)
    approved_at=db.Column(db.DateTime,nullable=True)
    consented_at=db.Column(db.DateTime,nullable=True)
    created_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,index=True)


class PortalInspectionResult(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    customer_id=db.Column(db.String(255),index=True)
    project_id=db.Column(db.String(255),index=True)
    project_name=db.Column(db.String(255))
    payload=db.Column(db.Text,nullable=False)
    created_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,index=True)


class PortalNotice(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    customer_id=db.Column(db.String(255),index=True)
    title=db.Column(db.String(255))
    body=db.Column(db.Text)
    created_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,index=True)
    read_at=db.Column(db.DateTime,nullable=True)

class PortalPhoto(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    portal_id=db.Column(db.Integer,db.ForeignKey("customer_portal.id"),index=True)
    customer_id=db.Column(db.String(255),index=True)
    cloud_file_id=db.Column(db.Integer,db.ForeignKey("cloud_file.id"),index=True)
    created_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,index=True)


class PortalOtp(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    portal_id=db.Column(db.Integer,db.ForeignKey("customer_portal.id"),index=True)
    code_hash=db.Column(db.String(128))
    expires_at=db.Column(db.DateTime,index=True)
    verified_at=db.Column(db.DateTime,nullable=True)
    created_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,index=True)

class PortalRequest(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    portal_id=db.Column(db.Integer,db.ForeignKey("customer_portal.id"),index=True)
    customer_id=db.Column(db.String(255),index=True)
    request_type=db.Column(db.String(50),index=True)
    detail=db.Column(db.Text)
    requested_date=db.Column(db.String(20),nullable=True)
    requested_time=db.Column(db.String(20),nullable=True)
    status=db.Column(db.String(30),default="open",index=True)
    suggested_slots=db.Column(db.Text,nullable=True)
    selected_date=db.Column(db.String(20),nullable=True)
    selected_time=db.Column(db.String(20),nullable=True)
    classification=db.Column(db.Text,nullable=True)
    created_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,index=True)
    updated_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,onupdate=datetime.datetime.utcnow)


class CustomerPortal(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    token=db.Column(db.String(255),unique=True,index=True)
    customer_id=db.Column(db.String(255),index=True)
    customer_name=db.Column(db.String(255))
    active=db.Column(db.Boolean,default=True)
    created_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,index=True)

class DocumentDelivery(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    issue_id=db.Column(db.Integer,db.ForeignKey("document_issue.id"),index=True)
    customer_id=db.Column(db.String(255),nullable=True,index=True)
    channel=db.Column(db.String(30))
    sent_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,index=True)



class CompletionArchive(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    project_id=db.Column(db.String(255),unique=True,index=True)
    payload=db.Column(db.Text,nullable=False)
    created_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,index=True)
    updated_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,onupdate=datetime.datetime.utcnow)


class DocumentIssue(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    document_no=db.Column(db.String(100),unique=True,index=True)
    kind=db.Column(db.String(50),index=True)
    project_id=db.Column(db.String(255),index=True)
    customer_id=db.Column(db.String(255),nullable=True,index=True)
    cloud_file_id=db.Column(db.Integer,db.ForeignKey("cloud_file.id"),nullable=True)
    reissue_of=db.Column(db.String(100),nullable=True)
    status=db.Column(db.String(30),default="active",index=True)
    verify_token=db.Column(db.String(255),unique=True,index=True,nullable=True)
    revoked_at=db.Column(db.DateTime,nullable=True)
    issued_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,index=True)


class ExtendedState(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    key=db.Column(db.String(80),unique=True,index=True)
    payload=db.Column(db.Text,nullable=False)
    updated_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,index=True)

class CloudFile(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    name=db.Column(db.String(255))
    mime_type=db.Column(db.String(255))
    size=db.Column(db.Integer,default=0)
    path=db.Column(db.String(500))
    linked_customer_id=db.Column(db.String(255),nullable=True,index=True)
    linked_project_id=db.Column(db.String(255),nullable=True,index=True)
    asset_kind=db.Column(db.String(50),default="attachment",index=True)
    created_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,index=True)






class DeviceApproval(db.Model):
    id=db.Column(db.String(255),primary_key=True)
    user_key=db.Column(db.String(255),index=True)
    device_name=db.Column(db.String(255))
    approved=db.Column(db.Boolean,default=False,index=True)
    created_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,index=True)
    approved_at=db.Column(db.DateTime,nullable=True)
    revoked_at=db.Column(db.DateTime,nullable=True)
    last_seen_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,index=True)

class SecurityEvent(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    event_type=db.Column(db.String(80),index=True)
    severity=db.Column(db.String(20),index=True)
    user_key=db.Column(db.String(255),nullable=True,index=True)
    title=db.Column(db.String(255))
    detail=db.Column(db.Text)
    created_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,index=True)

class LoginAttempt(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    user_key=db.Column(db.String(255),index=True)
    success=db.Column(db.Boolean,default=False,index=True)
    ip_hash=db.Column(db.String(128),nullable=True)
    created_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,index=True)


class UserSecurity(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    user_key=db.Column(db.String(255),unique=True,index=True)
    role=db.Column(db.String(50),default="staff",index=True)
    two_factor_secret=db.Column(db.String(255),nullable=True)
    two_factor_enabled=db.Column(db.Boolean,default=False)
    created_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,index=True)
    updated_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,onupdate=datetime.datetime.utcnow)

class SecuritySession(db.Model):
    id=db.Column(db.String(255),primary_key=True)
    user_key=db.Column(db.String(255),index=True)
    device_name=db.Column(db.String(255))
    ip_hash=db.Column(db.String(128))
    user_agent=db.Column(db.Text)
    created_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,index=True)
    last_seen_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,index=True)
    revoked_at=db.Column(db.DateTime,nullable=True)


class BackupConfig(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    daily_enabled=db.Column(db.Boolean,default=False)
    last_daily_at=db.Column(db.DateTime,nullable=True)
    encryption_enabled=db.Column(db.Boolean,default=False)
    external_replica_enabled=db.Column(db.Boolean,default=False)
    updated_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,onupdate=datetime.datetime.utcnow)


class ServerAuditLog(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    action=db.Column(db.String(120),index=True)
    actor=db.Column(db.String(255),nullable=True,index=True)
    target=db.Column(db.String(255),nullable=True,index=True)
    detail=db.Column(db.Text,nullable=True)
    created_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,index=True)

class DeletedEntitySnapshot(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    entity_type=db.Column(db.String(50),index=True)
    entity_id=db.Column(db.String(255),index=True)
    payload=db.Column(db.Text,nullable=False)
    deleted_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,index=True)
    restored_at=db.Column(db.DateTime,nullable=True)


class CoreDeleted(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    entity_type=db.Column(db.String(30),index=True)
    entity_id=db.Column(db.String(255),index=True)
    deleted_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,index=True)


class CoreCustomer(db.Model):
    id=db.Column(db.String(255),primary_key=True)
    payload=db.Column(db.Text,nullable=False)
    updated_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,index=True)

class CoreBooking(db.Model):
    id=db.Column(db.String(255),primary_key=True)
    payload=db.Column(db.Text,nullable=False)
    updated_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,index=True)

class CoreProject(db.Model):
    id=db.Column(db.String(255),primary_key=True)
    payload=db.Column(db.Text,nullable=False)
    updated_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,index=True)


class StaffLocation(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    staff_id=db.Column(db.String(255),index=True,unique=True)
    staff_name=db.Column(db.String(255))
    lat=db.Column(db.Float)
    lng=db.Column(db.Float)
    accuracy=db.Column(db.Float,default=0)
    updated_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,index=True)

class TrackingLink(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    token=db.Column(db.String(255),unique=True,index=True)
    booking_id=db.Column(db.String(255),index=True)
    customer_id=db.Column(db.String(255),nullable=True)
    customer_name=db.Column(db.String(255))
    staff_name=db.Column(db.String(255))
    booking_date=db.Column(db.String(20))
    booking_time=db.Column(db.String(20))
    active=db.Column(db.Boolean,default=True)
    expires_at=db.Column(db.DateTime,nullable=True,index=True)
    created_at=db.Column(db.DateTime,default=datetime.datetime.utcnow)


class LineUserLink(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    line_user_id=db.Column(db.String(255),unique=True,nullable=False)
    customer_id=db.Column(db.Integer,nullable=True)
    display_name=db.Column(db.String(255))
    created_at=db.Column(db.DateTime,default=datetime.datetime.utcnow)
    updated_at=db.Column(db.DateTime,default=datetime.datetime.utcnow,onupdate=datetime.datetime.utcnow)


class LineCrmMessage(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    event_id=db.Column(db.String(255),unique=True)
    line_user_id=db.Column(db.String(255))
    customer_id=db.Column(db.Integer,nullable=True)
    customer_name=db.Column(db.String(255))
    message=db.Column(db.Text)
    category=db.Column(db.String(30))
    priority=db.Column(db.String(20))
    assignee=db.Column(db.String(30))
    summary=db.Column(db.Text)
    status=db.Column(db.String(30),default="open")
    received_at=db.Column(db.DateTime,default=datetime.datetime.utcnow)


class DeliveryLog(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    queue_id=db.Column(db.String(100))
    channel=db.Column(db.String(20))
    recipient=db.Column(db.String(255))
    status=db.Column(db.String(30))
    error=db.Column(db.Text)
    created_at=db.Column(db.DateTime,default=datetime.datetime.utcnow)


class NotificationRule(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    name=db.Column(db.String(120))
    min_priority=db.Column(db.String(20))
    channel=db.Column(db.String(20))
    assignee=db.Column(db.String(30))
    start_time=db.Column(db.String(10))
    end_time=db.Column(db.String(10))
    enabled=db.Column(db.Boolean,default=True)

class NotificationQueue(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    notification_key=db.Column(db.String(255))
    title=db.Column(db.String(200))
    text=db.Column(db.Text)
    priority=db.Column(db.String(20))
    channel=db.Column(db.String(20))
    assignee=db.Column(db.String(30))
    status=db.Column(db.String(30),default="queued")
    retry_count=db.Column(db.Integer,default=0)
    max_retries=db.Column(db.Integer,default=3)
    last_error=db.Column(db.Text)
    sent_at=db.Column(db.DateTime,nullable=True)
    created_at=db.Column(db.DateTime,default=datetime.datetime.utcnow)


class AuditLog(db.Model):
    id=db.Column(db.Integer,primary_key=True)
    user_name=db.Column(db.String(120))
    type=db.Column(db.String(50))
    target=db.Column(db.String(200))
    action=db.Column(db.String(100))
    detail=db.Column(db.Text)
    created_at=db.Column(db.DateTime,default=datetime.datetime.utcnow)


ROLE_ORDER={"viewer":0,"staff":1,"manager":2,"admin":3}

def auth_required(min_role="viewer"):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args,**kwargs):
            auth=request.headers.get("Authorization","")
            if not auth.startswith("Bearer "): return jsonify({"error":"unauthorized"}),401
            token=auth.split(" ",1)[1]
            u=User.query.filter_by(token=token).first()
            if not u or not u.token_expires or u.token_expires < datetime.datetime.utcnow():
                return jsonify({"error":"unauthorized"}),401
            if ROLE_ORDER.get(u.role,-1)<ROLE_ORDER.get(min_role,0):
                return jsonify({"error":"forbidden"}),403
            g.user=u
            return fn(*args,**kwargs)
        return wrapper
    return deco

def openai_client():
    if not OPENAI_API_KEY or OpenAI is None:
        return None
    return OpenAI(api_key=OPENAI_API_KEY)

def ai_text(system_prompt, payload):
    client=openai_client()
    if not client:
        return None
    response=client.responses.create(
        model=OPENAI_MODEL,
        instructions=system_prompt,
        input=json.dumps(payload,ensure_ascii=False)
    )
    return response.output_text

def parse_json_loose(text):
    if not text:return None
    try:return json.loads(text)
    except Exception:
        start=text.find("{");end=text.rfind("}")
        if start>=0 and end>start:
            try:return json.loads(text[start:end+1])
            except Exception:return None
    return None


def google_token_path(user_key=None):
    if not user_key:
        return GOOGLE_TOKEN_FILE
    root,ext=os.path.splitext(GOOGLE_TOKEN_FILE)
    return f"{root}_{user_key}{ext or '.json'}"

def google_credentials(user_key=None):
    token_file=google_token_path(user_key)
    if Credentials is None or not os.path.exists(token_file):
        return None
    try:
        data=json.load(open(token_file,"r",encoding="utf-8"))
        creds=Credentials(
            token=data.get("token"),
            refresh_token=data.get("refresh_token"),
            token_uri=data.get("token_uri","https://oauth2.googleapis.com/token"),
            client_id=data.get("client_id") or GOOGLE_CLIENT_ID,
            client_secret=data.get("client_secret") or GOOGLE_CLIENT_SECRET,
            scopes=data.get("scopes") or GOOGLE_CALENDAR_SCOPES
        )
        if creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request as GoogleRequest
            creds.refresh(GoogleRequest())
            save_google_credentials(creds,user_key)
        return creds
    except Exception:
        return None

def save_google_credentials(creds,user_key=None):
    data={
        "token":creds.token,
        "refresh_token":creds.refresh_token,
        "token_uri":creds.token_uri,
        "client_id":creds.client_id,
        "client_secret":creds.client_secret,
        "scopes":list(creds.scopes or GOOGLE_CALENDAR_SCOPES)
    }
    with open(google_token_path(user_key),"w",encoding="utf-8") as f:
        json.dump(data,f,ensure_ascii=False)

def google_calendar_service(user_key=None):
    creds=google_credentials(user_key)
    if not creds or google_build is None:
        return None
    return google_build("calendar","v3",credentials=creds,cache_discovery=False)


@app.post("/api/auth/login")
def login():
    d=request.get_json() or {}
    u=User.query.filter_by(email=(d.get("email") or "").lower()).first()
    if not u or not check_password_hash(u.password_hash,d.get("password") or ""):
        return jsonify({"error":"invalid_credentials"}),401
    u.token=secrets.token_urlsafe(32)
    u.token_expires=datetime.datetime.utcnow()+datetime.timedelta(hours=12)
    db.session.commit()
    return jsonify({"token":u.token,"user":{"id":u.id,"name":u.name,"email":u.email,"role":u.role}})


@app.get("/api/push/config")
@auth_required("viewer")
def push_config():
    if not VAPID_PUBLIC_KEY:return jsonify({"error":"vapid_not_configured"}),503
    return jsonify({"publicKey":VAPID_PUBLIC_KEY})

@app.post("/api/push/subscribe")
@auth_required("viewer")
def push_subscribe():
    d=request.get_json() or {}
    s=d.get("subscription") or {}
    endpoint=s.get("endpoint")
    keys=s.get("keys") or {}
    if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
        return jsonify({"error":"invalid_subscription"}),400
    row=PushSubscription.query.filter_by(endpoint=endpoint).first()
    if not row:
        row=PushSubscription(user_id=g.user.id,endpoint=endpoint,p256dh=keys["p256dh"],auth=keys["auth"])
        db.session.add(row)
    row.user_id=g.user.id
    row.p256dh=keys["p256dh"];row.auth=keys["auth"]
    row.user_agent=d.get("userAgent","");row.enabled=True
    db.session.commit()
    return jsonify({"ok":True,"id":row.id})

@app.post("/api/push/unsubscribe")
@auth_required("viewer")
def push_unsubscribe():
    d=request.get_json() or {}
    row=PushSubscription.query.filter_by(endpoint=d.get("endpoint"),user_id=g.user.id).first()
    if row:
        row.enabled=False;db.session.commit()
    return jsonify({"ok":True})

def send_web_push(row,payload):
    if not VAPID_PUBLIC_KEY or not VAPID_PRIVATE_KEY:
        raise RuntimeError("VAPID not configured")
    subscription={
        "endpoint":row.endpoint,
        "keys":{"p256dh":row.p256dh,"auth":row.auth}
    }
    webpush(
        subscription_info=subscription,
        data=json.dumps(payload,ensure_ascii=False),
        vapid_private_key=VAPID_PRIVATE_KEY,
        vapid_claims={"sub":VAPID_CLAIMS_EMAIL}
    )

@app.post("/api/push/send")
@auth_required("manager")
def push_send():
    d=request.get_json() or {}
    role=d.get("targetRole","all")
    q=PushSubscription.query.filter_by(enabled=True)
    rows=q.all()
    if role!="all":
        allowed_ids=[u.id for u in User.query.filter_by(role=role).all()]
        rows=[r for r in rows if r.user_id in allowed_ids]
    sent=0;failed=0;errors=[]
    payload={"title":d.get("title","ACE"),"body":d.get("body",""),"data":d.get("data") or {}}
    for row in rows:
        try:
            send_web_push(row,payload);sent+=1
        except WebPushException as e:
            failed+=1;errors.append(str(e))
            if getattr(e.response,"status_code",None) in (404,410):
                row.enabled=False
        except Exception as e:
            failed+=1;errors.append(str(e))
    db.session.commit()
    return jsonify({"sent":sent,"failed":failed,"errors":errors[:5]})


@app.get("/api/integrations/status")
@auth_required("admin")
def integration_status():
    return jsonify({
        "line": bool(LINE_CHANNEL_SECRET and LINE_CHANNEL_ACCESS_TOKEN),
        "ai": bool(OPENAI_API_KEY and OpenAI is not None),
        "payment": bool(PAYMENT_SECRET_KEY),
        "push": bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY)
    })

@app.post("/api/ai/chat")
@auth_required("staff")
def ai_chat():
    d=request.get_json() or {}
    prompt="""あなたは内装・床工事会社ACEの社内AIです。
与えられた顧客・現場・進捗・会話だけを根拠に、日本語で簡潔かつ実務的に回答してください。
不明なことは推測せず、不明と伝えてください。"""
    out=ai_text(prompt,d)
    if out:return jsonify({"answer":out})
    project=d.get("project") or {}
    return jsonify({"answer":f"AI未接続です。現在の進捗は「{project.get('progress','未設定')}」です。"})

@app.post("/api/ai/analyze-project")
@auth_required("staff")
def analyze_project():
    d=request.get_json() or {}
    prompt="""あなたは内装・床工事会社ACEの現場管理AIです。
入力された顧客、施工履歴、会員/保証情報、現場進捗、チャット、既存の道具候補を分析してください。
必ずJSONのみを返してください。形式:
{
 "tools":["必要な道具"],
 "risks":["注意事項"],
 "nextActions":["次のアクション"],
 "priority":"高|通常|低",
 "lineDraft":"顧客に送れる丁寧な短いLINE文面",
 "summary":"現場の要約"
}
情報にない施工方法や危険事項を断定しないでください。"""
    out=ai_text(prompt,d)
    parsed=parse_json_loose(out)
    if parsed:return jsonify(parsed)
    p=d.get("project") or {}
    return jsonify({
        "tools":d.get("localTools") or [],
        "risks":[],
        "nextActions":["現場進捗を確認","施工前写真を登録"],
        "priority":"高" if p.get("status")=="施工中" else "通常",
        "lineDraft":"ACEです。施工予定について確認のご連絡です。ご不明点がございましたらLINEでご連絡ください。",
        "summary":f"AI未接続のためローカル情報で表示しています。進捗: {p.get('progress','未設定')}"
    })

@app.post("/api/ai/line-draft")
@auth_required("manager")
def ai_line_draft():
    d=request.get_json() or {}
    prompt="""あなたはACEの顧客対応AIです。
顧客マスター、関連現場、配信目的を参照し、公式LINEで送る文面を日本語で作成してください。
事実として与えられていない内容は作らないでください。
過度に営業的にせず、丁寧で短くしてください。文面だけを返してください。"""
    out=ai_text(prompt,d)
    if out:return jsonify({"text":out})
    c=d.get("customer") or {}
    return jsonify({"text":f"{c.get('name','お客様')}様\nACEです。ご案内事項がございます。詳細についてこのLINEからお気軽にお問い合わせください。"})


@app.post("/api/ai/analyze-floor-images")
@auth_required("staff")
def analyze_floor_images():
    d=request.get_json() or {}
    before=(d.get("beforeImage") or {}).get("url")
    after=(d.get("afterImage") or {}).get("url")
    project=d.get("project") or {}
    customer=d.get("customer") or {}

    client=openai_client()
    if not client:
        return jsonify({
            "score":3,
            "criteria":{"scratch":3,"stain":3,"lift":3,"appearance":3,"finish":3},
            "condition":["AI未接続"],
            "changes":[],
            "repairCandidates":[],
            "followUp":["OPENAI_API_KEYを設定してください"],
            "comment":"AI接続後に施工前後写真から評価コメントを自動生成できます。",
            "confidence":"未評価"
        })

    content=[{
        "type":"input_text",
        "text":(
            "あなたは内装・床工事会社ACEの床状態評価AIです。"
            "施工前後の写真を比較し、目視できる範囲だけを評価してください。"
            "材質、損傷原因、施工不良などを画像だけで断定しないでください。"
            "必ずJSONのみを返してください。形式:"
            '{"score":1,"condition":[""],"changes":[""],"repairCandidates":[""],'
            '"followUp":[""],"comment":"","confidence":"高|中|低"}'
            f"\n現場:{project.get('name','')} 進捗:{project.get('progress','')} "
            f"床材:{customer.get('floor','')}"
        )
    }]
    if before:
        content.append({"type":"input_text","text":"以下は施工前写真です。"})
        content.append({"type":"input_image","image_url":before})
    if after:
        content.append({"type":"input_text","text":"以下は施工後写真です。"})
        content.append({"type":"input_image","image_url":after})

    resp=client.responses.create(
        model=OPENAI_MODEL,
        input=[{"role":"user","content":content}]
    )
    parsed=parse_json_loose(resp.output_text)
    if not parsed:
        return jsonify({"error":"invalid_ai_response","raw":resp.output_text}),502

    try:
        score=int(parsed.get("score",3))
    except Exception:
        score=3
    parsed["score"]=max(1,min(5,score))
    return jsonify(parsed)












@app.get("/api/calendar/status")
@auth_required("staff")
def calendar_status():
    svc=google_calendar_service()
    if not svc:
        return jsonify({"connected":False,"configured":bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URI)})
    try:
        cal=svc.calendarList().get(calendarId="primary").execute()
        return jsonify({"connected":True,"email":cal.get("id",""),"defaultCalendarId":"primary"})
    except Exception as e:
        return jsonify({"connected":False,"error":str(e)})

@app.post("/api/calendar/oauth/start")
@auth_required("staff")
def calendar_oauth_start():
    if Flow is None or not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URI):
        return jsonify({"error":"google_oauth_not_configured"}),503
    client_config={
      "web":{
        "client_id":GOOGLE_CLIENT_ID,
        "client_secret":GOOGLE_CLIENT_SECRET,
        "auth_uri":"https://accounts.google.com/o/oauth2/auth",
        "token_uri":"https://oauth2.googleapis.com/token",
        "redirect_uris":[GOOGLE_REDIRECT_URI]
      }
    }
    flow=Flow.from_client_config(client_config,scopes=GOOGLE_CALENDAR_SCOPES,redirect_uri=GOOGLE_REDIRECT_URI)
    d=request.get_json(silent=True) or {}
    user_key=str(d.get("userKey") or "")
    url,state=flow.authorization_url(access_type="offline",include_granted_scopes="true",prompt="consent")
    session["google_oauth_state"]=state
    session["google_oauth_user_key"]=user_key
    return jsonify({"authorizationUrl":url})

@app.get("/api/calendar/oauth/callback")
def calendar_oauth_callback():
    if Flow is None:
        return "Google OAuth library not installed",503
    state=session.get("google_oauth_state")
    client_config={
      "web":{
        "client_id":GOOGLE_CLIENT_ID,
        "client_secret":GOOGLE_CLIENT_SECRET,
        "auth_uri":"https://accounts.google.com/o/oauth2/auth",
        "token_uri":"https://oauth2.googleapis.com/token",
        "redirect_uris":[GOOGLE_REDIRECT_URI]
      }
    }
    flow=Flow.from_client_config(client_config,scopes=GOOGLE_CALENDAR_SCOPES,state=state,redirect_uri=GOOGLE_REDIRECT_URI)
    flow.fetch_token(authorization_response=request.url)
    save_google_credentials(flow.credentials,session.get("google_oauth_user_key") or None)
    return "<html><body><h3>Google Calendar connected.</h3><p>You can close this window and return to ACE.</p></body></html>"

@app.post("/api/calendar/oauth/disconnect")
@auth_required("staff")
def calendar_oauth_disconnect():
    try:
        if os.path.exists(GOOGLE_TOKEN_FILE):
            os.remove(GOOGLE_TOKEN_FILE)
    except Exception:
        pass
    return jsonify({"ok":True})

@app.post("/api/calendar/calendars/check")
@auth_required("staff")
def calendar_check():
    d=request.get_json() or {}
    calendar_id=d.get("calendarId","primary")
    svc=google_calendar_service()
    if not svc:return jsonify({"ok":False,"error":"not_connected"}),503
    try:
        data=svc.calendars().get(calendarId=calendar_id).execute()
        return jsonify({"ok":True,"id":data.get("id"),"summary":data.get("summary")})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),400

@app.post("/api/calendar/sync")
@auth_required("staff")
def calendar_sync():
    d=request.get_json() or {}
    calendar_id=d.get("calendarId","primary")
    svc=google_calendar_service()
    if not svc:return jsonify({"error":"google_calendar_not_connected"}),503
    time_min=d.get("timeMin") or datetime.datetime.utcnow().isoformat()+"Z"
    time_max=d.get("timeMax")
    kwargs={"calendarId":calendar_id,"timeMin":time_min,"singleEvents":True,"orderBy":"startTime","maxResults":2500}
    if time_max:kwargs["timeMax"]=time_max
    result=svc.events().list(**kwargs).execute()
    events=[]
    for ev in result.get("items",[]):
        start=ev.get("start",{})
        dt=start.get("dateTime") or start.get("date")
        date=""
        tm=""
        if dt:
            if "T" in dt:
                date=dt[:10];tm=dt[11:16]
            else:
                date=dt[:10]
        events.append({
          "id":ev.get("id"),"title":ev.get("summary","Google予定"),"date":date,"time":tm,
          "cancelled":ev.get("status")=="cancelled","updated":ev.get("updated","")
        })
    return jsonify({"calendarId":calendar_id,"events":events,"syncToken":result.get("nextSyncToken","")})

@app.post("/api/calendar/events")
@auth_required("staff")
def calendar_create_event():
    d=request.get_json() or {}
    calendar_id=d.get("calendarId","primary")
    svc=google_calendar_service()
    if not svc:return jsonify({"error":"google_calendar_not_connected"}),503
    date=d.get("date");time=d.get("time","09:00");duration=int(d.get("durationMinutes",90))
    if not date:return jsonify({"error":"date_required"}),400
    start=datetime.datetime.fromisoformat(f"{date}T{time}:00")
    end=start+datetime.timedelta(minutes=duration)
    body={
      "summary":d.get("title","ACE予約"),
      "description":d.get("description",""),
      "start":{"dateTime":start.isoformat(),"timeZone":"Asia/Tokyo"},
      "end":{"dateTime":end.isoformat(),"timeZone":"Asia/Tokyo"}
    }
    ev=svc.events().insert(calendarId=calendar_id,body=body).execute()
    return jsonify({"id":ev.get("id"),"htmlLink":ev.get("htmlLink")})


@app.patch("/api/calendar/events/<event_id>")
@auth_required("staff")
def calendar_update_event(event_id):
    d=request.get_json() or {}
    calendar_id=d.get("calendarId","primary")
    svc=google_calendar_service()
    if not svc:return jsonify({"error":"google_calendar_not_connected"}),503
    try:
        if d.get("status")=="cancelled":
            svc.events().delete(calendarId=calendar_id,eventId=event_id).execute()
            return jsonify({"ok":True,"cancelled":True})
        ev=svc.events().get(calendarId=calendar_id,eventId=event_id).execute()
        date=d.get("date");tm=d.get("time","09:00");duration=int(d.get("durationMinutes",90))
        if date:
            start=datetime.datetime.fromisoformat(f"{date}T{tm}:00")
            end=start+datetime.timedelta(minutes=duration)
            ev["start"]={"dateTime":start.isoformat(),"timeZone":"Asia/Tokyo"}
            ev["end"]={"dateTime":end.isoformat(),"timeZone":"Asia/Tokyo"}
        if d.get("title"):ev["summary"]=d.get("title")
        if "description" in d:ev["description"]=d.get("description","")
        updated=svc.events().update(calendarId=calendar_id,eventId=event_id,body=ev).execute()
        return jsonify({"ok":True,"id":updated.get("id"),"updated":updated.get("updated")})
    except Exception as e:
        return jsonify({"error":"google_calendar_update_failed","detail":str(e)}),502











def _core_rows(model, since=None):
    q=model.query
    if since:
        q=q.filter(model.updated_at>since)
    rows=q.all()
    out=[]
    for r in rows:
        try: out.append(json.loads(r.payload))
        except Exception: pass
    return out

def _parse_iso(v):
    if not v:return None
    try:return datetime.datetime.fromisoformat(str(v).replace("Z","+00:00")).replace(tzinfo=None)
    except Exception:return None

def _upsert_core(model, rows):
    count=0
    for item in rows or []:
        rid=str(item.get("id") or "")
        if not rid: continue
        row=db.session.get(model,rid)
        incoming_dt=_parse_iso(item.get("updatedAt")) or datetime.datetime.utcnow()
        if not row:
            row=model(id=rid,payload=json.dumps(item,ensure_ascii=False),updated_at=incoming_dt)
            db.session.add(row);count+=1
        elif incoming_dt >= (row.updated_at or datetime.datetime.min):
            row.payload=json.dumps(item,ensure_ascii=False);row.updated_at=incoming_dt;count+=1
    return count

def _apply_deletes(model, entity_type, rows):
    count=0
    for item in rows or []:
        rid=str(item.get("id") or "")
        if not rid:continue
        row=db.session.get(model,rid)
        if row:
            try:
                db.session.add(DeletedEntitySnapshot(entity_type=entity_type,entity_id=rid,payload=row.payload))
            except Exception:
                pass
            db.session.delete(row)
        deleted_at=_parse_iso(item.get("deletedAt")) or datetime.datetime.utcnow()
        exists=CoreDeleted.query.filter_by(entity_type=entity_type,entity_id=rid).first()
        if not exists:
            db.session.add(CoreDeleted(entity_type=entity_type,entity_id=rid,deleted_at=deleted_at))
        else:
            exists.deleted_at=deleted_at
        count+=1
    return count


def _extended_get(key, default):
    row=ExtendedState.query.filter_by(key=key).first()
    if not row:return default
    try:return json.loads(row.payload)
    except Exception:return default

def _extended_set(key,value):
    row=ExtendedState.query.filter_by(key=key).first()
    if not row:
        row=ExtendedState(key=key,payload="{}")
        db.session.add(row)
    row.payload=json.dumps(value,ensure_ascii=False)
    row.updated_at=datetime.datetime.utcnow()

def _cloud_file_json(r):
    return {
      "id":r.id,"name":r.name,"type":r.mime_type,"size":r.size,
      "url":f"/api/files/{r.id}/content",
      "linkedCustomerId":r.linked_customer_id,"linkedProjectId":r.linked_project_id,
      "assetKind":r.asset_kind or "attachment","createdAt":r.created_at.isoformat()
    }

@app.get("/api/extended-sync")
@auth_required("staff")
def extended_sync_get():
    return jsonify({
      "lineCampaignHistory":_extended_get("lineCampaignHistory",[]),
      "lineCrmItems":_extended_get("lineCrmItems",[]),
      "estimateRows":_extended_get("estimateRows",[]),
      "materialMasters":_extended_get("materialMasters",[]),
      "scrapInventory":_extended_get("scrapInventory",[]),
      "autoNotificationQueue":_extended_get("autoNotificationQueue",[]),
      "documentIssueHistory":_extended_get("documentIssueHistory",[]),
      "toolMasters":_extended_get("toolMasters",[]),
      "toolCheckouts":_extended_get("toolCheckouts",[]),
      "staffSkillProfiles":_extended_get("staffSkillProfiles",{}),
      "departureChecks":_extended_get("departureChecks",{}),
      "progressPinnedItems":_extended_get("progressPinnedItems",[]),
      "homeCarryChecks":_extended_get("homeCarryChecks",{}),
      "fieldworkLogs":_extended_get("fieldworkLogs",[]),
      "completionChecks":_extended_get("completionChecks",{}),
      "customerSignatures":_extended_get("customerSignatures",{}),
      "standardWorkTimes":_extended_get("standardWorkTimes",{}),
      "completionArchives":_extended_get("completionArchives",{}),
      "inspectionRecords":_extended_get("inspectionRecords",{}),
      "warrantyRules":_extended_get("warrantyRules",[]),
      "repairEstimates":_extended_get("repairEstimates",{}),
      "estimateApprovals":_extended_get("estimateApprovals",{}),
      "financeRecords":_extended_get("financeRecords",[]),
      "invoiceRecords":_extended_get("invoiceRecords",[]),
      "projectCostBreakdowns":_extended_get("projectCostBreakdowns",{}),
      "staffPayrollMaster":_extended_get("staffPayrollMaster",{}),
      "invoiceReminderConfig":_extended_get("invoiceReminderConfig",{}),
      "bankTransactions":_extended_get("bankTransactions",[]),
      "expenseRecords":_extended_get("expenseRecords",[]),
      "fixedCostRecords":_extended_get("fixedCostRecords",[]),
      "cashStartingBalance":_extended_get("cashStartingBalance",0),
      "fixedCostBudgetMonthly":_extended_get("fixedCostBudgetMonthly",0),
      "managementAlerts":_extended_get("managementAlerts",[]),
      "monthlyBudgets":_extended_get("monthlyBudgets",{}),
      "workflowTasks":_extended_get("workflowTasks",[]),
      "workflowState":_extended_get("workflowState",{}),
      "lineAutomationRules":_extended_get("lineAutomationRules",[]),
      "communicationHistory":_extended_get("communicationHistory",[]),
      "reconciliationRules":_extended_get("reconciliationRules",{}),
      "reconciliationHistory":_extended_get("reconciliationHistory",[]),
      "aiManagementState":_extended_get("aiManagementState",{}),
      "kpiSnapshots":_extended_get("kpiSnapshots",[]),
      "nextMonthActions":_extended_get("nextMonthActions",[]),
      "aiManagementSchedule":_extended_get("aiManagementSchedule",{}),
      "subcontractors":_extended_get("subcontractors",[]),
      "subcontractOrders":_extended_get("subcontractOrders",[]),
      "accountingMappings":_extended_get("accountingMappings",{}),
      "journalEntries":_extended_get("journalEntries",[]),
      "portalAdminState":_extended_get("portalAdminState",{}),
      "workflowRules":_extended_get("workflowRules",{}),
      "workflowExceptions":_extended_get("workflowExceptions",[]),
      "workflowAutoActionHistory":_extended_get("workflowAutoActionHistory",[]),
      "membership":_extended_get("membership",[]),
      "cloudFiles":[_cloud_file_json(r) for r in CloudFile.query.order_by(CloudFile.created_at.desc()).all()]
    })

@app.post("/api/extended-sync")
@auth_required("staff")
def extended_sync_post():
    d=request.get_json() or {}
    for key in ["lineCampaignHistory","lineCrmItems","estimateRows","materialMasters","scrapInventory","autoNotificationQueue","documentIssueHistory","toolMasters","toolCheckouts","staffSkillProfiles","departureChecks","progressPinnedItems","homeCarryChecks","fieldworkLogs","completionChecks","customerSignatures","standardWorkTimes","completionArchives","inspectionRecords","warrantyRules","repairEstimates","estimateApprovals","financeRecords","invoiceRecords","projectCostBreakdowns","staffPayrollMaster","invoiceReminderConfig","bankTransactions","expenseRecords","fixedCostRecords","cashStartingBalance","fixedCostBudgetMonthly","managementAlerts","monthlyBudgets","workflowTasks","workflowState","workflowRules","workflowExceptions","workflowAutoActionHistory","lineAutomationRules","communicationHistory","reconciliationRules","reconciliationHistory","aiManagementState","kpiSnapshots","nextMonthActions","aiManagementSchedule","subcontractors","subcontractOrders","accountingMappings","journalEntries","portalAdminState"]:
        if key in d:_extended_set(key,d.get(key) or [])
    if "customers" in d:_extended_set("membership",d.get("customers") or [])
    db.session.commit()
    return jsonify({"ok":True,"cloudFiles":[_cloud_file_json(r) for r in CloudFile.query.order_by(CloudFile.created_at.desc()).all()]})



def _next_document_no(kind):
    prefix="CERT" if kind=="certificate_pdf" else "SCORE"
    day=datetime.datetime.now().strftime("%Y%m%d")
    count=DocumentIssue.query.filter(DocumentIssue.document_no.like(f"ACE-{prefix}-{day}-%")).count()+1
    return f"ACE-{prefix}-{day}-{count:04d}"

def _pdf_text(c, x, y, text, size=10):
    c.setFont("Helvetica",size)
    c.drawString(x,y,str(text or ""))



@app.post("/api/completion-archives/<project_id>/zip")
@auth_required("staff")
def completion_archive_zip(project_id):
    row=CompletionArchive.query.filter_by(project_id=str(project_id)).first()
    if not row:return jsonify({"error":"not_found"}),404
    try:
        payload=json.loads(row.payload)
    except Exception:
        return jsonify({"error":"invalid_payload"}),500
    import zipfile,uuid
    upload_dir=os.getenv("ACE_UPLOAD_DIR","ace_uploads");os.makedirs(upload_dir,exist_ok=True)
    zip_name=f"ACE_completion_{project_id}_{uuid.uuid4().hex[:8]}.zip"
    zip_path=os.path.join(upload_dir,zip_name)
    file_ids=[]
    for x in payload.get("afterPhotos") or []:
        if x.get("id"):file_ids.append(int(x["id"]))
    sig=payload.get("signature") or {}
    if sig.get("cloudFileId"):file_ids.append(int(sig["cloudFileId"]))
    for x in payload.get("documents") or []:
        issue_id=x.get("id")
        issue=db.session.get(DocumentIssue,int(issue_id)) if issue_id else None
        if issue and issue.cloud_file_id:file_ids.append(int(issue.cloud_file_id))
    with zipfile.ZipFile(zip_path,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("archive.json",json.dumps(payload,ensure_ascii=False,indent=2))
        for fid in dict.fromkeys(file_ids):
            f=db.session.get(CloudFile,fid)
            if f and f.path and os.path.exists(f.path):
                z.write(f.path,arcname=f.name or os.path.basename(f.path))
    cf=CloudFile(name=zip_name,mime_type="application/zip",size=os.path.getsize(zip_path),path=zip_path,
                 linked_project_id=str(project_id),asset_kind="completion_archive_zip")
    db.session.add(cf);db.session.commit()
    return jsonify({"ok":True,"id":cf.id,"url":f"/api/files/{cf.id}/content","name":cf.name})


@app.post("/api/completion-archives")
@auth_required("staff")
def completion_archive_upsert():
    d=request.get_json() or {}
    project_id=str(d.get("projectId") or "")
    if not project_id:return jsonify({"error":"project_id_required"}),400
    row=CompletionArchive.query.filter_by(project_id=project_id).first()
    if not row:
        row=CompletionArchive(project_id=project_id,payload="{}")
        db.session.add(row)
    row.payload=json.dumps(d,ensure_ascii=False)
    row.updated_at=datetime.datetime.utcnow()
    db.session.commit()
    return jsonify({"ok":True})

@app.get("/api/completion-archives/<project_id>")
@auth_required("staff")
def completion_archive_get(project_id):
    row=CompletionArchive.query.filter_by(project_id=str(project_id)).first()
    if not row:return jsonify({"error":"not_found"}),404
    try:return jsonify(json.loads(row.payload))
    except Exception:return jsonify({"error":"invalid_payload"}),500


@app.post("/api/documents/generate")
@auth_required("staff")
def documents_generate():
    d=request.get_json() or {}
    kind=d.get("kind")
    if kind not in ("score_pdf","certificate_pdf"):
        return jsonify({"error":"invalid_kind"}),400
    document_no=_next_document_no(kind)
    verify_token=secrets.token_urlsafe(20)
    upload_dir=os.getenv("ACE_UPLOAD_DIR","ace_uploads")
    os.makedirs(upload_dir,exist_ok=True)
    filename=f"{document_no}.pdf"
    path=os.path.join(upload_dir,filename)

    c=canvas.Canvas(path,pagesize=A4)
    width,height=A4
    c.setTitle(document_no)
    c.setFont("Helvetica-Bold",20)
    c.drawString(20*mm,height-24*mm,"ACE")
    c.setFont("Helvetica-Bold",16)
    title="施工証明書" if kind=="certificate_pdf" else "床状態スコア表"
    # Japanese text is represented through metadata/body values; Helvetica may not render Japanese on all servers.
    # Keep ASCII title fallback for portability.
    c.drawString(20*mm,height-38*mm,"Construction Certificate" if kind=="certificate_pdf" else "Floor Condition Score")
    verify_url=request.host_url.rstrip("/") + f"/api/documents/verify/{verify_token}"
    try:
        qr=qrcode.make(verify_url)
        bio=BytesIO();qr.save(bio,format="PNG");bio.seek(0)
        from reportlab.lib.utils import ImageReader
        c.drawImage(ImageReader(bio),width-45*mm,height-45*mm,25*mm,25*mm,preserveAspectRatio=True,mask='auto')
    except Exception:
        pass
    y=height-52*mm
    fields=[
      ("Document No",document_no),("Customer",d.get("customerName","")),("Project",d.get("projectName","")),
      ("Address",d.get("address","")),("Work Date",d.get("workDate","")),("Floor",d.get("floor","")),
      ("Warranty",d.get("warranty",""))
    ]
    for label,val in fields:
        _pdf_text(c,20*mm,y,f"{label}: {val}",10);y-=7*mm
    if kind=="score_pdf":
        _pdf_text(c,20*mm,y,"Score:",11);y-=7*mm
        score=d.get("score") or {}
        for k,v in score.items():
            _pdf_text(c,24*mm,y,f"{k}: {v}",9);y-=6*mm
            if y<25*mm:c.showPage();y=height-25*mm
        _pdf_text(c,20*mm,y,f"Comment: {d.get('comment','')}",9)
    else:
        y-=10*mm
        _pdf_text(c,20*mm,y,"ACE certifies that the above work was performed.",11)
        sig_id=d.get("signatureCloudFileId")
        if sig_id:
            try:
                sig_file=db.session.get(CloudFile,int(sig_id))
                if sig_file and sig_file.path and os.path.exists(sig_file.path):
                    from reportlab.lib.utils import ImageReader
                    c.drawString(20*mm,45*mm,"Customer Signature:")
                    c.drawImage(ImageReader(sig_file.path),20*mm,15*mm,70*mm,25*mm,preserveAspectRatio=True,mask='auto')
            except Exception:
                pass
    c.save()

    size=os.path.getsize(path)
    file_row=CloudFile(
      name=filename,mime_type="application/pdf",size=size,path=path,
      linked_customer_id=str(d.get("customerId") or "") or None,
      linked_project_id=str(d.get("projectId") or "") or None,
      asset_kind=kind
    )
    db.session.add(file_row);db.session.flush()
    issue=DocumentIssue(
      document_no=document_no,kind=kind,project_id=str(d.get("projectId") or ""),
      customer_id=str(d.get("customerId") or "") or None,cloud_file_id=file_row.id,
      reissue_of=d.get("reissueOf") or None,status="active",verify_token=verify_token
    )
    db.session.add(issue);db.session.commit()
    file_json=_cloud_file_json(file_row)
    issue_json={
      "id":issue.id,"documentNo":issue.document_no,"kind":issue.kind,"projectId":issue.project_id,
      "customerId":issue.customer_id,"cloudFileId":issue.cloud_file_id,"url":file_json["url"],
      "reissueOf":issue.reissue_of,"status":issue.status,"issuedAt":issue.issued_at.isoformat()
    }
    return jsonify({"file":file_json,"issue":issue_json})


@app.get("/api/documents/verify/<token>")
def document_verify(token):
    issue=DocumentIssue.query.filter_by(verify_token=token).first()
    if not issue:return jsonify({"valid":False,"reason":"not_found"}),404
    return jsonify({
      "valid":issue.status=="active",
      "documentNo":issue.document_no,"kind":issue.kind,
      "status":issue.status,"issuedAt":issue.issued_at.isoformat(),
      "revokedAt":issue.revoked_at.isoformat() if issue.revoked_at else None
    })

@app.post("/api/documents/issues/<int:issue_id>/revoke")
@auth_required("manager")
def document_revoke(issue_id):
    issue=db.session.get(DocumentIssue,issue_id)
    if not issue:return jsonify({"error":"not_found"}),404
    issue.status="revoked";issue.revoked_at=datetime.datetime.utcnow()
    db.session.commit()
    return jsonify({"ok":True})

@app.post("/api/customer-portals")
@auth_required("staff")
def customer_portal_create():
    d=request.get_json() or {}
    cid=str(d.get("customerId") or "")
    if not cid:return jsonify({"error":"customer_id_required"}),400
    row=CustomerPortal.query.filter_by(customer_id=cid,active=True).first()
    if not row:
        row=CustomerPortal(token=secrets.token_urlsafe(24),customer_id=cid,customer_name=d.get("customerName",""),active=True)
        db.session.add(row);db.session.commit()
    return jsonify({"id":row.id,"customerId":row.customer_id,"token":row.token,"url":request.host_url.rstrip("/")+f"/portal/{row.token}","active":row.active,"createdAt":row.created_at.isoformat()})


@app.post("/api/customer-portals/<token>/otp/request")
def customer_portal_otp_request(token):
    portal=CustomerPortal.query.filter_by(token=token,active=True).first()
    if not portal:return jsonify({"error":"not_found"}),404
    import hashlib,random
    code=f"{random.randint(0,999999):06d}"
    row=PortalOtp(
      portal_id=portal.id,code_hash=hashlib.sha256(code.encode()).hexdigest(),
      expires_at=datetime.datetime.utcnow()+datetime.timedelta(minutes=10)
    )
    db.session.add(row);db.session.commit()
    payload={"ok":True,"expiresMinutes":10}
    # Try LINE delivery through persisted LineUserLink.
    link=LineUserLink.query.filter_by(customer_id=int(portal.customer_id) if str(portal.customer_id).isdigit() else portal.customer_id).first()
    delivered=False
    if link and LINE_CHANNEL_ACCESS_TOKEN:
        try:
            rr=requests.post(
              "https://api.line.me/v2/bot/message/push",
              headers={"Authorization":f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}","Content-Type":"application/json"},
              json={"to":link.line_user_id,"messages":[{"type":"text","text":f"ACE顧客ポータル認証コード: {code}\n有効期限は10分です。"}]},
              timeout=20
            )
            delivered=200<=rr.status_code<300
        except Exception:
            delivered=False
    payload["deliveredBy"]="line" if delivered else "none"
    if os.getenv("ACE_PORTAL_DEBUG_OTP","0")=="1":payload["debugCode"]=code
    return jsonify(payload)

@app.post("/api/customer-portals/<token>/otp/verify")
def customer_portal_otp_verify(token):
    portal=CustomerPortal.query.filter_by(token=token,active=True).first()
    if not portal:return jsonify({"error":"not_found"}),404
    d=request.get_json() or {}
    import hashlib
    code=str(d.get("code") or "")
    row=PortalOtp.query.filter_by(portal_id=portal.id,verified_at=None).order_by(PortalOtp.created_at.desc()).first()
    if not row or row.expires_at<datetime.datetime.utcnow():return jsonify({"ok":False,"error":"expired"}),400
    if hashlib.sha256(code.encode()).hexdigest()!=row.code_hash:return jsonify({"ok":False,"error":"invalid"}),400
    row.verified_at=datetime.datetime.utcnow();db.session.commit()
    session[f"portal_verified_{portal.id}"]=True
    return jsonify({"ok":True})

def _portal_verified(portal):
    return bool(session.get(f"portal_verified_{portal.id}"))


@app.post("/api/customer-portals/<token>/photos")
def customer_portal_photo_upload(token):
    portal=CustomerPortal.query.filter_by(token=token,active=True).first()
    if not portal:return jsonify({"error":"not_found"}),404
    if not _portal_verified(portal):return jsonify({"error":"otp_required"}),401
    d=request.get_json() or {}
    name=(d.get("name") or "photo.jpg").replace("/","_").replace("\\","_")
    data_url=d.get("dataUrl") or ""
    if "," not in data_url:return jsonify({"error":"invalid_data_url"}),400
    import base64,uuid
    raw=base64.b64decode(data_url.split(",",1)[1])
    upload_dir=os.getenv("ACE_UPLOAD_DIR","ace_uploads");os.makedirs(upload_dir,exist_ok=True)
    path=os.path.join(upload_dir,f"{uuid.uuid4().hex}_{name}")
    with open(path,"wb") as f:f.write(raw)
    cf=CloudFile(name=name,mime_type=d.get("type") or "image/jpeg",size=len(raw),path=path,linked_customer_id=portal.customer_id,asset_kind="portal_photo")
    db.session.add(cf);db.session.flush()
    pp=PortalPhoto(portal_id=portal.id,customer_id=portal.customer_id,cloud_file_id=cf.id)
    db.session.add(pp);db.session.commit()
    return jsonify({"ok":True,"id":pp.id,"url":f"/api/files/{cf.id}/content"})


@app.post("/api/customer-portals/<token>/requests")
def customer_portal_request_create(token):
    portal=CustomerPortal.query.filter_by(token=token,active=True).first()
    if not portal:return jsonify({"error":"not_found"}),404
    if not _portal_verified(portal):return jsonify({"error":"otp_required"}),401
    d=request.get_json() or {}
    typ=d.get("type")
    if typ not in ("booking_change","repair"):return jsonify({"error":"invalid_type"}),400
    row=PortalRequest(
      portal_id=portal.id,customer_id=portal.customer_id,request_type=typ,
      detail=d.get("detail",""),requested_date=d.get("date"),requested_time=d.get("time"),status="open"
    )
    db.session.add(row);db.session.commit()
    return jsonify({"ok":True,"id":row.id})


def _portal_request_json(x):
    try:
        slots=json.loads(x.suggested_slots) if x.suggested_slots else []
    except Exception:
        slots=[]
    try:
        classification=json.loads(x.classification) if x.classification else None
    except Exception:
        classification=None
    return {
      "id":x.id,"customerId":x.customer_id,"type":x.request_type,"detail":x.detail,
      "date":x.requested_date,"time":x.requested_time,"status":x.status,
      "suggestedSlots":slots,"selectedDate":x.selected_date,"selectedTime":x.selected_time,
      "classification":classification,
      "createdAt":x.created_at.isoformat(),"updatedAt":x.updated_at.isoformat()
    }

@app.get("/api/customer-portal/admin-data")
@auth_required("staff")
def customer_portal_admin_data():
    requests_rows=PortalRequest.query.order_by(PortalRequest.created_at.desc()).limit(500).all()
    notices=PortalNotice.query.order_by(PortalNotice.created_at.desc()).limit(500).all()
    photos=PortalPhoto.query.order_by(PortalPhoto.created_at.desc()).limit(500).all()
    return jsonify({
      "requests":[_portal_request_json(x) for x in requests_rows],
      "notices":[{"id":x.id,"customerId":x.customer_id,"title":x.title,"body":x.body,"createdAt":x.created_at.isoformat(),"readAt":x.read_at.isoformat() if x.read_at else None} for x in notices],
      "photos":[{"id":x.id,"customerId":x.customer_id,"cloudFileId":x.cloud_file_id,"url":f"/api/files/{x.cloud_file_id}/content","name":(db.session.get(CloudFile,x.cloud_file_id).name if db.session.get(CloudFile,x.cloud_file_id) else "photo"),"createdAt":x.created_at.isoformat()} for x in photos]
    })


@app.post("/api/customer-portals/<token>/notices/<int:notice_id>/read")
def customer_portal_notice_read(token,notice_id):
    portal=CustomerPortal.query.filter_by(token=token,active=True).first()
    if not portal:return jsonify({"error":"not_found"}),404
    if not _portal_verified(portal):return jsonify({"error":"otp_required"}),401
    row=db.session.get(PortalNotice,notice_id)
    if not row or str(row.customer_id)!=str(portal.customer_id):return jsonify({"error":"not_found"}),404
    if not row.read_at:row.read_at=datetime.datetime.utcnow()
    db.session.commit()
    return jsonify({"ok":True})







@app.errorhandler(400)
def ace_bad_request(e):
    return jsonify({"error":"bad_request","message":getattr(e,"description",str(e))}),400

@app.errorhandler(401)
def ace_unauthorized(e):
    return jsonify({"error":"unauthorized","message":"authentication required"}),401

@app.errorhandler(403)
def ace_forbidden(e):
    return jsonify({"error":"forbidden","message":"permission denied"}),403

@app.errorhandler(404)
def ace_not_found(e):
    return jsonify({"error":"not_found","message":"resource not found"}),404

@app.errorhandler(409)
def ace_conflict(e):
    return jsonify({"error":"conflict","message":getattr(e,"description",str(e))}),409

@app.errorhandler(429)
def ace_too_many(e):
    return jsonify({"error":"rate_limited","message":"too many requests"}),429

@app.errorhandler(500)
def ace_internal_error(e):
    try:
        db.session.rollback()
        _security_event("server_error","high","サーバー内部エラー",str(e))
        db.session.commit()
    except Exception:
        pass
    return jsonify({"error":"internal_error","message":"server error"}),500





def _env_bool(name, default=False):
    raw=os.getenv(name)
    if raw is None:return default
    return str(raw).strip().lower() in {"1","true","yes","on"}

def _production_required_env():
    return {
      "ACE_SECRET_KEY": bool(os.getenv("ACE_SECRET_KEY") or os.getenv("SECRET_KEY")),
      "DATABASE_URL": bool(os.getenv("DATABASE_URL")),
      "ACE_UPLOAD_DIR": bool(os.getenv("ACE_UPLOAD_DIR") or os.getenv("UPLOAD_DIR")),
    }

def _production_optional_env():
    return {
      "LINE_CHANNEL_SECRET": bool(os.getenv("LINE_CHANNEL_SECRET")),
      "LINE_CHANNEL_ACCESS_TOKEN": bool(os.getenv("LINE_CHANNEL_ACCESS_TOKEN") or os.getenv("LINE_ACCESS_TOKEN")),
      "GOOGLE_CLIENT_ID": bool(os.getenv("GOOGLE_CLIENT_ID")),
      "GOOGLE_CLIENT_SECRET": bool(os.getenv("GOOGLE_CLIENT_SECRET")),
      "GOOGLE_REDIRECT_URI": bool(os.getenv("GOOGLE_REDIRECT_URI")),
      "OPENAI_API_KEY": bool(os.getenv("OPENAI_API_KEY")),
      "ACE_BACKUP_ENCRYPTION_KEY": bool(os.getenv("ACE_BACKUP_ENCRYPTION_KEY")),
      "ACE_REPLICA_DIR": bool(os.getenv("ACE_REPLICA_DIR")),
    }

def _bootstrap_admin_from_env():
    email=os.getenv("ACE_ADMIN_EMAIL","").strip().lower()
    password=os.getenv("ACE_ADMIN_PASSWORD","")
    name=os.getenv("ACE_ADMIN_NAME","管理者").strip() or "管理者"
    if not email or not password:return False
    existing=User.query.filter_by(email=email).first()
    if existing:
        if existing.role not in {"manager","admin"}:
            existing.role="admin"
            db.session.commit()
        return False
    row=User(
      name=name,email=email,password_hash=generate_password_hash(password),
      role="admin"
    )
    db.session.add(row)
    db.session.commit()
    return True

def _initialize_production_once():
    try:
        db.create_all()
        _bootstrap_admin_from_env()
    except Exception:
        db.session.rollback()


@app.get("/")
def ace_frontend():
    return send_from_directory(os.path.dirname(__file__), "index.html")

@app.get("/manifest.json")
def ace_manifest():
    return send_from_directory(os.path.dirname(__file__), "manifest.json")

@app.get("/service-worker.js")
def ace_service_worker():
    response=send_from_directory(os.path.dirname(__file__), "service-worker.js")
    response.headers["Service-Worker-Allowed"]="/"
    return response

@app.get("/icon-192.png")
def ace_icon_192():
    return send_from_directory(os.path.dirname(__file__), "icon-192.png")

@app.get("/icon-512.png")
def ace_icon_512():
    return send_from_directory(os.path.dirname(__file__), "icon-512.png")


@app.get("/api/health")
def public_health():
    db_ok=False
    try:
        db.session.execute(db.text("SELECT 1"))
        db_ok=True
    except Exception:
        pass
    return jsonify({
      "ok":db_ok,
      "version":"13.3",
      "database":db_ok,
      "timestamp":datetime.datetime.utcnow().isoformat()
    }), 200 if db_ok else 503


@app.get("/api/admin/deployment-summary")
@auth_required("manager")
@permission_required("audit")
def deployment_summary():
    required=_production_required_env()
    optional=_production_optional_env()
    storage={
      "upload":os.path.isdir(os.getenv("ACE_UPLOAD_DIR","ace_uploads")),
      "backup":os.path.isdir(_backup_dir()),
      "disasterBackup":os.path.isdir(_disaster_dir()),
      "replica":bool(_replica_dir())
    }
    return jsonify({
      "version":"13.3",
      "required":required,
      "optional":optional,
      "storage":storage,
      "ready":all(required.values()) and storage["upload"] and storage["backup"] and storage["disasterBackup"]
    })


@app.get("/api/admin/production-readiness")
@auth_required("manager")
@permission_required("audit")
def production_readiness():
    required=_production_required_env()
    optional=_production_optional_env()
    deps={
      "openai": OpenAI is not None,
      "google": Credentials is not None and Flow is not None and google_build is not None,
      "reportlab": canvas is not None and A4 is not None and mm is not None,
      "qrcode": qrcode is not None,
      "cryptography": Fernet is not None,
      "pyotp": pyotp is not None,
    }
    backup_ok=os.path.isdir(_backup_dir())
    disaster_ok=os.path.isdir(_disaster_dir())
    upload_ok=os.path.isdir(os.getenv("ACE_UPLOAD_DIR","ace_uploads"))
    ready=all(required.values()) and upload_ok and backup_ok and disaster_ok
    return jsonify({
      "ok":ready,
      "version":"13.3",
      "required":required,
      "optional":optional,
      "dependencies":deps,
      "storage":{"upload":upload_ok,"backup":backup_ok,"disasterBackup":disaster_ok}
    })


@app.get("/api/admin/environment-diagnostics")
@auth_required("manager")
@permission_required("audit")
def environment_diagnostics():
    required={
      "ACE_SECRET_KEY": bool(os.getenv("ACE_SECRET_KEY") or os.getenv("SECRET_KEY")),
      "DATABASE_URL": bool(os.getenv("DATABASE_URL")),
      "ACE_UPLOAD_DIR": bool(os.getenv("ACE_UPLOAD_DIR") or os.getenv("UPLOAD_DIR")),
      "LINE_CHANNEL_SECRET": bool(os.getenv("LINE_CHANNEL_SECRET")),
      "LINE_CHANNEL_ACCESS_TOKEN": bool(os.getenv("LINE_CHANNEL_ACCESS_TOKEN") or os.getenv("LINE_ACCESS_TOKEN")),
      "GOOGLE_CLIENT_ID": bool(os.getenv("GOOGLE_CLIENT_ID")),
      "GOOGLE_CLIENT_SECRET": bool(os.getenv("GOOGLE_CLIENT_SECRET")),
      "GOOGLE_REDIRECT_URI": bool(os.getenv("GOOGLE_REDIRECT_URI")),
      "OPENAI_API_KEY": bool(os.getenv("OPENAI_API_KEY")),
      "ACE_BACKUP_DIR": bool(os.getenv("ACE_BACKUP_DIR")),
      "ACE_DISASTER_BACKUP_DIR": bool(os.getenv("ACE_DISASTER_BACKUP_DIR")),
      "ACE_BACKUP_ENCRYPTION_KEY": bool(os.getenv("ACE_BACKUP_ENCRYPTION_KEY")),
    }
    dependencies={
      "openai": OpenAI is not None,
      "google": Credentials is not None and Flow is not None and google_build is not None,
      "reportlab": canvas is not None and A4 is not None and mm is not None,
      "qrcode": qrcode is not None,
      "cryptography": Fernet is not None,
      "pyotp": pyotp is not None,
    }
    must_have=["ACE_SECRET_KEY","DATABASE_URL","ACE_UPLOAD_DIR"]
    production_ready=all(required[k] for k in must_have)
    return jsonify({"ok":production_ready,"required":required,"dependencies":dependencies,"version":"13.3"})

@app.get("/api/admin/release-diagnostics")
@auth_required("manager")
@permission_required("audit")
def release_diagnostics():
    checks={}
    try:
        db.session.execute(db.text("SELECT 1"));checks["database"]=True
    except Exception:checks["database"]=False
    checks["uploadDir"]=os.path.isdir(os.getenv("ACE_UPLOAD_DIR","ace_uploads"))
    checks["backupDir"]=os.path.isdir(_backup_dir())
    checks["disasterBackupDir"]=os.path.isdir(_disaster_dir())
    checks["backupEncryption"]=bool(_backup_fernet())
    checks["replicaConfigured"]=bool(_replica_dir())
    checks["openaiConfigured"]=bool(os.getenv("OPENAI_API_KEY"))
    checks["lineConfigured"]=bool(os.getenv("LINE_CHANNEL_ACCESS_TOKEN") or os.getenv("LINE_ACCESS_TOKEN"))
    checks["googleConfigured"]=bool(os.getenv("GOOGLE_CLIENT_ID") or os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"))
    return jsonify({"ok":all([checks["database"],checks["uploadDir"],checks["backupDir"],checks["disasterBackupDir"]]),"checks":checks,"version":"12.6"})


@app.get("/api/admin/pre-release-health")
@auth_required("manager")
@permission_required("audit")
def pre_release_health():
    checks={}
    try:
        db.session.execute(db.text("SELECT 1"));checks["database"]=True
    except Exception:checks["database"]=False
    checks["backupEncryption"]=bool(_backup_fernet())
    checks["replicaConfigured"]=bool(_replica_dir())
    checks["uploadDir"]=os.path.isdir(os.getenv("ACE_UPLOAD_DIR","ace_uploads"))
    checks["backupDir"]=os.path.isdir(_backup_dir())
    checks["disasterBackupDir"]=os.path.isdir(_disaster_dir())
    checks["openaiConfigured"]=bool(os.getenv("OPENAI_API_KEY"))
    return jsonify({"ok":all([checks["database"],checks["uploadDir"],checks["backupDir"],checks["disasterBackupDir"]]),"checks":checks,"timestamp":datetime.datetime.utcnow().isoformat()})


@app.get("/api/customer-portal/admin-summary")
@auth_required("manager")
def customer_portal_admin_summary():
    portals=CustomerPortal.query.order_by(CustomerPortal.created_at.desc()).limit(500).all()
    out=[]
    for p in portals:
        cid=str(p.customer_id)
        customer_name=""
        try:
            c=CoreCustomer.query.filter_by(id=cid).first()
            if c:
                customer_name=(json.loads(c.payload) or {}).get("name","")
        except Exception:
            pass
        docs=DocumentIssue.query.filter_by(customer_id=cid).count() if hasattr(DocumentIssue,"customer_id") else 0
        photos=PortalPhoto.query.filter_by(customer_id=cid).count()
        inspections=PortalInspectionResult.query.filter_by(customer_id=cid).count()
        invoices=CloudFile.query.filter_by(linked_customer_id=cid,asset_kind="invoice_pdf").count()
        out.append({
          "id":p.id,"customerId":cid,"customerName":customer_name,"active":p.active,
          "lastAccessAt":getattr(p,"last_access_at",None).isoformat() if getattr(p,"last_access_at",None) else None,
          "documentCount":docs,"photoCount":photos,"inspectionCount":inspections,"invoiceCount":invoices
        })
    reqs=PortalCustomerRequest.query.order_by(PortalCustomerRequest.created_at.desc()).limit(500).all()
    requests_out=[]
    for r in reqs:
        try: payload=json.loads(r.payload)
        except Exception: payload={}
        requests_out.append({"id":r.id,"customerId":r.customer_id,"type":r.request_type,"status":r.status,"payload":payload,"createdAt":r.created_at.isoformat()})
    unread=PortalNotice.query.filter_by(read_at=None).count() if hasattr(PortalNotice,"read_at") else 0
    return jsonify({"portals":out,"requests":requests_out,"unreadNotices":unread})

@app.post("/api/customer-portals/<token>/requests")
def customer_portal_request_create(token):
    portal=CustomerPortal.query.filter_by(token=token,active=True).first()
    if not portal:return jsonify({"error":"not_found"}),404
    if not _portal_verified(portal):return jsonify({"error":"otp_required"}),401
    d=request.get_json() or {}
    rtype=str(d.get("type") or "")
    if rtype not in {"reschedule","inquiry","repair"}:return jsonify({"error":"invalid_type"}),400
    row=PortalCustomerRequest(customer_id=str(portal.customer_id),request_type=rtype,payload=json.dumps(d,ensure_ascii=False),status="open")
    db.session.add(row);db.session.commit()
    return jsonify({"ok":True,"id":row.id})


@app.post("/api/customer-portal/repair-estimates")
@auth_required("staff")
def portal_repair_estimate_create():
    d=request.get_json() or {}
    row=PortalRepairEstimate(
      customer_id=str(d.get("customerId") or ""),project_id=str(d.get("projectId") or ""),
      project_name=d.get("projectName",""),payload=json.dumps(d.get("estimate") or {},ensure_ascii=False),
      status="published"
    )
    db.session.add(row);db.session.commit()
    return jsonify({"ok":True,"id":row.id})

@app.get("/api/customer-portal/repair-estimates/admin")
@auth_required("staff")
def portal_repair_estimate_admin():
    rows=PortalRepairEstimate.query.order_by(PortalRepairEstimate.created_at.desc()).limit(500).all()
    return jsonify([{
      "id":x.id,"customerId":x.customer_id,"projectId":x.project_id,"projectName":x.project_name,
      "status":x.status,"approvedAt":x.approved_at.isoformat() if x.approved_at else None,
      "consentedAt":x.consented_at.isoformat() if x.consented_at else None,
      "consentName":x.consent_name or ""
    } for x in rows])

@app.post("/api/customer-portals/<token>/repair-estimates/<int:estimate_id>/approve")
def portal_repair_estimate_approve(token,estimate_id):
    portal=CustomerPortal.query.filter_by(token=token,active=True).first()
    if not portal:return jsonify({"error":"not_found"}),404
    if not _portal_verified(portal):return jsonify({"error":"otp_required"}),401
    row=db.session.get(PortalRepairEstimate,estimate_id)
    if not row or str(row.customer_id)!=str(portal.customer_id):return jsonify({"error":"not_found"}),404
    d=request.get_json() or {}
    consent_name=(d.get("consentName") or "").strip()
    if not consent_name:return jsonify({"error":"consent_name_required"}),400
    row.status="approved";row.approved_at=datetime.datetime.utcnow();row.consented_at=datetime.datetime.utcnow();row.consent_name=consent_name
    db.session.commit()
    return jsonify({"ok":True})

@app.post("/api/customer-portals/<token>/repair-estimates/<int:estimate_id>/reject")
def portal_repair_estimate_reject(token,estimate_id):
    portal=CustomerPortal.query.filter_by(token=token,active=True).first()
    if not portal:return jsonify({"error":"not_found"}),404
    if not _portal_verified(portal):return jsonify({"error":"otp_required"}),401
    row=db.session.get(PortalRepairEstimate,estimate_id)
    if not row or str(row.customer_id)!=str(portal.customer_id):return jsonify({"error":"not_found"}),404
    row.status="rejected";db.session.commit()
    return jsonify({"ok":True})


@app.post("/api/customer-portal/inspection-results")
@auth_required("staff")
def customer_portal_inspection_result_create():
    d=request.get_json() or {}
    row=PortalInspectionResult(
      customer_id=str(d.get("customerId") or ""),project_id=str(d.get("projectId") or ""),
      project_name=d.get("projectName",""),payload=json.dumps(d,ensure_ascii=False)
    )
    db.session.add(row);db.session.commit()
    return jsonify({"ok":True,"id":row.id})


@app.post("/api/customer-portal/notices")
@auth_required("staff")
def customer_portal_notice_create():
    d=request.get_json() or {}
    row=PortalNotice(customer_id=str(d.get("customerId") or ""),title=d.get("title",""),body=d.get("body",""))
    db.session.add(row);db.session.commit()
    return jsonify({"id":row.id,"customerId":row.customer_id,"title":row.title,"body":row.body,"createdAt":row.created_at.isoformat(),"readAt":None})

@app.post("/api/customer-portal/requests/<int:req_id>/suggest-slots")
@auth_required("staff")
def customer_portal_request_suggest_slots(req_id):
    row=db.session.get(PortalRequest,req_id)
    if not row:return jsonify({"error":"not_found"}),404
    d=request.get_json() or {}
    row.suggested_slots=json.dumps(d.get("slots") or [],ensure_ascii=False)
    row.updated_at=datetime.datetime.utcnow()
    db.session.commit()
    return jsonify({"ok":True})






@app.post("/api/invoices/generate")
@auth_required("staff")
@permission_required("finance")
def invoice_generate():
    if canvas is None or A4 is None or mm is None:
        return jsonify({"error":"reportlab_not_installed"}),503
    d=request.get_json() or {}
    inv=d.get("invoice") or {}
    no=inv.get("invoiceNo") or f"ACE-INV-{int(datetime.datetime.utcnow().timestamp())}"
    upload_dir=os.getenv("ACE_UPLOAD_DIR","ace_uploads");os.makedirs(upload_dir,exist_ok=True)
    path=os.path.join(upload_dir,f"{no}.pdf")
    c=canvas.Canvas(path,pagesize=A4)
    width,height=A4
    c.setFont("Helvetica-Bold",20);c.drawString(20*mm,height-24*mm,"ACE")
    c.setFont("Helvetica-Bold",16);c.drawString(20*mm,height-38*mm,"Invoice")
    y=height-55*mm
    fields=[
      ("Invoice No",no),("Customer",inv.get("customerName","")),
      ("Issued",inv.get("issuedAt","")),("Due",inv.get("dueDate","")),
      ("Total",f"JPY {int(inv.get('total') or 0):,}"),
      ("Paid",f"JPY {int(inv.get('paidAmount') or 0):,}"),
      ("Balance",f"JPY {int(inv.get('balance') or 0):,}")
    ]
    for label,val in fields:
        _pdf_text(c,20*mm,y,f"{label}: {val}",10);y-=8*mm
    c.save()
    cf=CloudFile(name=f"{no}.pdf",mime_type="application/pdf",size=os.path.getsize(path),path=path,
                 linked_customer_id=str(inv.get("customerId") or "") or None,
                 linked_project_id=str(inv.get("projectId") or "") or None,
                 asset_kind="invoice_pdf")
    db.session.add(cf);db.session.commit()
    return jsonify({"ok":True,"id":cf.id,"url":f"/api/files/{cf.id}/content","name":cf.name})





@app.post("/api/accounting/export")
@auth_required("manager")
@permission_required("finance")
def accounting_export():
    d=request.get_json() or {}
    rows=d.get("rows") or []
    fmt=d.get("format") or "generic"
    import csv,io
    buf=io.StringIO()
    writer=csv.writer(buf)
    if fmt=="freee":
        writer.writerow(["取引日","収支区分","勘定科目","税区分","金額","取引先","備考"])
        for x in rows:
            writer.writerow([x.get("date"),"収入" if x.get("type")=="sale" else "支出",
                             x.get("credit") if x.get("type")=="sale" else x.get("debit"),
                             x.get("tax"),x.get("amount"),"",x.get("description")])
    elif fmt=="moneyforward":
        writer.writerow(["取引日","借方勘定科目","借方税区分","借方金額","貸方勘定科目","貸方税区分","貸方金額","摘要"])
        for x in rows:
            writer.writerow([x.get("date"),x.get("debit"),x.get("tax"),x.get("amount"),
                             x.get("credit"),x.get("tax"),x.get("amount"),x.get("description")])
    else:
        writer.writerow(["date","debit_account","credit_account","amount","tax","description","type","ref_id"])
        for x in rows:
            writer.writerow([x.get("date"),x.get("debit"),x.get("credit"),x.get("amount"),
                             x.get("tax"),x.get("description"),x.get("type"),x.get("refId")])
    raw=("\ufeff"+buf.getvalue()).encode("utf-8")
    upload_dir=os.getenv("ACE_UPLOAD_DIR","ace_uploads");os.makedirs(upload_dir,exist_ok=True)
    name=f"ACE_journal_{fmt}_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    path=os.path.join(upload_dir,name)
    with open(path,"wb") as f:f.write(raw)
    cf=CloudFile(name=name,mime_type="text/csv",size=len(raw),path=path,asset_kind="accounting_export")
    db.session.add(cf);db.session.commit()
    return jsonify({"ok":True,"id":cf.id,"url":f"/api/files/{cf.id}/content","name":name})


@app.post("/api/ai/next-month-actions")
@auth_required("manager")
@permission_required("finance")
def ai_next_month_actions():
    d=request.get_json() or {}
    client=openai_client()
    if not client:return jsonify({"error":"ai_not_configured"}),503
    prompt=(
      "あなたは内装会社ACEの経営改善アシスタントです。"
      "KPI、予算実績、原因分析、担当者別KPIから翌月の具体的なアクションを優先度付きで最大10件提案してください。"
      "抽象論ではなく、回収・受注・粗利・工数・品質・配置に落とし込んでください。"
      "JSONのみ。形式:{\"actions\":[{\"priority\":100,\"title\":\"\",\"detail\":\"\"}]}\n"
      + json.dumps(d,ensure_ascii=False)
    )
    resp=client.responses.create(model=OPENAI_MODEL,input=prompt)
    parsed=parse_json_loose(resp.output_text)
    if not parsed:return jsonify({"error":"invalid_ai_response"}),502
    return jsonify(parsed)


@app.post("/api/ai/management-assistant")
@auth_required("manager")
@permission_required("finance")
def ai_management_assistant():
    d=request.get_json() or {}
    client=openai_client()
    if not client:
        return jsonify({"error":"ai_not_configured"}),503
    prompt=(
      "あなたは内装会社ACEの経営アシスタントです。"
      "売上、粗利、工数、未収、資金繰り、業務フローを横断して分析してください。"
      "経営者が今日やるべきことを優先度100点満点で最大10件、原因分析を最大8件出してください。"
      "誇張せず、データに基づいて簡潔に。JSONのみ。"
      "形式:{\"comment\":\"\",\"insights\":[{\"severity\":\"high|mid|low\",\"title\":\"\",\"detail\":\"\"}],"
      "\"actions\":[{\"priority\":100,\"title\":\"\",\"detail\":\"\"}]}\n"
      + json.dumps(d,ensure_ascii=False)
    )
    resp=client.responses.create(model=OPENAI_MODEL,input=prompt)
    parsed=parse_json_loose(resp.output_text)
    if not parsed:return jsonify({"error":"invalid_ai_response"}),502
    return jsonify(parsed)


@app.post("/api/ai/repair-estimate")
@auth_required("staff")
def ai_repair_estimate():
    d=request.get_json() or {}
    client=openai_client()
    if not client:
        return jsonify({"error":"ai_not_configured"}),503
    prompt=(
        "あなたは内装会社ACEの補修見積AIです。点検結果、保証判定、既存見積行を参考に、"
        "補修内容と概算見積を作ってください。保証対応候補なら保証対象部分を0円にしてください。"
        "JSONのみ。形式:{\"reason\":\"\",\"items\":[{\"name\":\"\",\"qty\":1,\"amount\":10000}],\"total\":10000}\n"
        + json.dumps(d,ensure_ascii=False)
    )
    resp=client.responses.create(model=OPENAI_MODEL,input=prompt)
    parsed=parse_json_loose(resp.output_text)
    if not parsed:return jsonify({"error":"invalid_ai_response"}),502
    return jsonify(parsed)


@app.post("/api/ai/project-fulfillment")
@auth_required("staff")
def ai_project_fulfillment():
    d=request.get_json() or {}
    client=openai_client()
    if not client:
        return jsonify({"error":"ai_not_configured"}),503
    prompt=(
        "あなたは内装会社ACEの施工準備AIです。現場内容、必要道具、在庫、スタッフスキル、予定工数を見て、"
        "担当者候補、不足品、追加で必要な道具・材料を提案してください。"
        "JSONのみ。形式:{\"assignee\":\"\",\"missingItems\":[{\"name\":\"\",\"qty\":1}],\"additionalTools\":[\"\"]}\n"
        + json.dumps(d,ensure_ascii=False)
    )
    resp=client.responses.create(model=OPENAI_MODEL,input=prompt)
    parsed=parse_json_loose(resp.output_text)
    if not parsed:return jsonify({"error":"invalid_ai_response"}),502
    return jsonify(parsed)


@app.post("/api/bookings/<booking_id>/approve")
@auth_required("staff")
def booking_approve(booking_id):
    d=request.get_json() or {}
    booking=d.get("booking") or {}
    booking["id"]=booking_id
    booking["status"]="本予約"
    booking["confirmedAt"]=datetime.datetime.utcnow().isoformat()
    booking["updatedAt"]=datetime.datetime.utcnow().isoformat()
    _upsert_core(CoreBooking,[booking])
    db.session.commit()
    return jsonify({"ok":True,"booking":booking})


@app.post("/api/ai/repair-request-classify")
@auth_required("staff")
def ai_repair_request_classify():
    d=request.get_json() or {}
    client=openai_client()
    if not client:
        return jsonify({"error":"ai_not_configured"}),503
    prompt=(
        "あなたは内装会社ACEの補修受付AIです。顧客の補修依頼を分類してください。"
        "categoryは 施工不具合|表面補修|水濡れ|床鳴り|経年劣化|その他 のいずれか。"
        "priorityは high|mid|low。summaryはスタッフ向けに短く。"
        "toolsは必要道具の配列、assigneeは manager|staff。"
        "JSONのみ。形式:{\"category\":\"\",\"priority\":\"\",\"summary\":\"\",\"tools\":[\"\"],\"assignee\":\"\"}\n"
        + json.dumps(d,ensure_ascii=False)
    )
    resp=client.responses.create(model=OPENAI_MODEL,input=prompt)
    parsed=parse_json_loose(resp.output_text)
    if not parsed:return jsonify({"error":"invalid_ai_response"}),502
    return jsonify(parsed)

@app.post("/api/customer-portals/<token>/requests/<int:req_id>/select-slot")
def customer_portal_select_slot(token,req_id):
    portal=CustomerPortal.query.filter_by(token=token,active=True).first()
    if not portal:return jsonify({"error":"not_found"}),404
    if not _portal_verified(portal):return jsonify({"error":"otp_required"}),401
    row=db.session.get(PortalRequest,req_id)
    if not row or row.portal_id!=portal.id or row.request_type!="booking_change":
        return jsonify({"error":"request_not_found"}),404
    d=request.get_json() or {}
    date=d.get("date");time=d.get("time")
    try:
        slots=json.loads(row.suggested_slots) if row.suggested_slots else []
    except Exception:
        slots=[]
    if not any(str(s.get("date"))==str(date) and str(s.get("time"))==str(time) for s in slots):
        return jsonify({"error":"slot_not_offered"}),400

    # Create a tentative booking in CoreBooking.
    booking_id=str(int(datetime.datetime.utcnow().timestamp()*1000))
    booking={
      "id":booking_id,"customerId":portal.customer_id,"customerName":portal.customer_name,
      "date":date,"time":time,"status":"仮予約","source":"customer_portal",
      "portalRequestId":row.id,"updatedAt":datetime.datetime.utcnow().isoformat()
    }
    _upsert_core(CoreBooking,[booking])
    row.selected_date=date;row.selected_time=time;row.status="done";row.updated_at=datetime.datetime.utcnow()
    db.session.commit()
    return jsonify({"ok":True,"booking":booking})


@app.post("/api/customer-portal/requests/<int:req_id>/convert-project")
@auth_required("staff")
def customer_portal_request_convert_project(req_id):
    row=db.session.get(PortalRequest,req_id)
    if not row:return jsonify({"error":"not_found"}),404
    d=request.get_json() or {}
    project=d.get("project") or {}
    if project:
        _upsert_core(CoreProject,[project])
    row.status="done";row.updated_at=datetime.datetime.utcnow()
    db.session.commit()
    return jsonify({"ok":True})


@app.get("/api/customer-portal/requests")
@auth_required("staff")
def customer_portal_requests_admin():
    rows=PortalRequest.query.order_by(PortalRequest.created_at.desc()).limit(500).all()
    return jsonify([{
      "id":x.id,"customerId":x.customer_id,"type":x.request_type,"detail":x.detail,
      "date":x.requested_date,"time":x.requested_time,"status":x.status,
      "createdAt":x.created_at.isoformat(),"updatedAt":x.updated_at.isoformat()
    } for x in rows])

@app.get("/portal/<token>")
def customer_portal_view(token):
    portal=CustomerPortal.query.filter_by(token=token,active=True).first()
    if not portal:return "Portal not found",404
    verified=_portal_verified(portal)
    # Customer/member state currently comes from ExtendedState membership payload.
    membership=_extended_get("membership",[])
    member=next((x for x in membership if str(x.get("id"))==str(portal.customer_id)),{})
    core_customer=db.session.get(CoreCustomer,str(portal.customer_id))
    customer_data={}
    if core_customer:
        try: customer_data=json.loads(core_customer.payload)
        except Exception: customer_data={}
    bookings=[]
    for b in _core_rows(CoreBooking):
        if str(b.get("customerId"))==str(portal.customer_id):bookings.append(b)
    issues=DocumentIssue.query.filter_by(customer_id=portal.customer_id).order_by(DocumentIssue.issued_at.desc()).all()
    docs=[]
    for x in issues:
        if x.status!="active":continue
        docs.append(f'<li><a href="/api/files/{x.cloud_file_id}/content">{x.document_no}</a> - {x.issued_at.strftime("%Y-%m-%d")}</li>')
    next_booking=sorted([b for b in bookings if b.get("date")],key=lambda b:b.get("date"))[0] if bookings else {}
    name=portal.customer_name
    warranty=customer_data.get("warranty") or member.get("warranty") or "-"
    inspection=customer_data.get("next") or member.get("next") or "-"
    member_status=member.get("memberStatus") or customer_data.get("memberStatus") or "未加入"
    remaining=member.get("remainingRepair") or customer_data.get("remainingRepair") or 0
    verified_badge="認証済み" if verified else "OTP認証が必要"
    notices=PortalNotice.query.filter_by(customer_id=portal.customer_id).order_by(PortalNotice.created_at.desc()).limit(20).all()
    notice_html=''.join([f"<li><b>{n.title}</b><br>{n.body}<br><button onclick=\"markRead({n.id})\">既読にする</button></li>" for n in notices]) or "<li>お知らせはありません。</li>"
    pending_changes=PortalRequest.query.filter_by(portal_id=portal.id,request_type="booking_change",status="open").order_by(PortalRequest.created_at.desc()).all()
    slot_html=[]
    for req in pending_changes:
        try:slots=json.loads(req.suggested_slots) if req.suggested_slots else []
        except Exception:slots=[]
        if slots:
            buttons=''.join([f'<button onclick="selectSlot({req.id},\'{s.get("date")}\',\'{s.get("time")}\')">{s.get("date")} {s.get("time")}</button>' for s in slots])
            slot_html.append(f'<div class="card"><h3>予約変更候補</h3><p>{req.detail}</p>{buttons}</div>')
    slot_html=''.join(slot_html)
    photos=PortalPhoto.query.filter_by(customer_id=portal.customer_id).order_by(PortalPhoto.created_at.desc()).limit(20).all()
    photo_html=''.join([f'<img src="/api/files/{p.cloud_file_id}/content" style="width:120px;height:90px;object-fit:cover;border-radius:8px;margin:4px">' for p in photos]) or "写真はありません。"
    estimates=PortalRepairEstimate.query.filter_by(customer_id=portal.customer_id).order_by(PortalRepairEstimate.created_at.desc()).limit(20).all()
    estimate_html=[]
    for er in estimates:
        try:
            payload=json.loads(er.payload)
            total=payload.get("total",0)
            if er.status=="published":
                estimate_html.append(f"""<div class="card"><h3>補修見積</h3><p>¥{int(total):,}</p><input id="consentName{er.id}" placeholder="同意者氏名"><button onclick="approveEstimate({er.id})">内容に同意して承認</button><button onclick="rejectEstimate({er.id})">見送る</button></div>""")
            else:
                estimate_html.append(f"<div class='card'><h3>補修見積</h3><p>¥{int(total):,}</p><p>状態: {er.status}</p></div>")
        except Exception:
            pass
    estimate_html=''.join(estimate_html)
    inspections=PortalInspectionResult.query.filter_by(customer_id=portal.customer_id).order_by(PortalInspectionResult.created_at.desc()).limit(20).all()
    inspection_html=[]
    for ir in inspections:
        try:
            payload=json.loads(ir.payload)
            a=payload.get("analysis") or {}
            inspection_html.append(f"<li><b>{ir.project_name}</b><br>{a.get('deterioration','')} / {a.get('warrantyDecision','')} / {a.get('repairProposal','')}</li>")
        except Exception:
            pass
    inspection_html=''.join(inspection_html) or "<li>点検結果はありません。</li>"
    request_forms = """
      <div class="card"><h3>予約変更依頼</h3><input id="bd" type="date"><input id="bt" type="time"><textarea id="bdetail" placeholder="希望内容"></textarea><button onclick="sendReq('booking_change')">送信</button></div>
      <div class="card"><h3>補修依頼</h3><textarea id="rdetail" placeholder="気になる箇所・症状"></textarea><input id="repairPhoto" type="file" accept="image/*"><button onclick="uploadPhoto()">写真を添付</button><button onclick="sendReq('repair')">送信</button></div>
    """ if verified else """
      <div class="card"><h3>本人確認</h3><button onclick="requestOtp()">OTPを発行</button><input id="otp" placeholder="6桁コード"><button onclick="verifyOtp()">認証</button><div id="otpmsg"></div></div>
    """
    html=f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ACE Customer Portal</title><style>
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:760px;margin:24px auto;padding:16px;background:#f7f7f7}}.brand{{font-size:28px;font-weight:900}}.card{{background:#fff;border:1px solid #ddd;border-radius:14px;padding:16px;margin-top:12px}}input,textarea,button{{box-sizing:border-box;width:100%;padding:10px;margin-top:8px}}button{{font-weight:800}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}@media(max-width:600px){{.grid{{grid-template-columns:1fr}}}}</style></head><body>
    <div class="brand">ACE</div><h2>{name} 様</h2><div>{verified_badge}</div>
    <div class="grid">
      <div class="card"><b>次回予約</b><p>{next_booking.get('date','-')} {next_booking.get('time','')}</p></div>
      <div class="card"><b>次回点検</b><p>{inspection}</p></div>
      <div class="card"><b>保証期限</b><p>{warranty}</p></div>
      <div class="card"><b>会員状況</b><p>{member_status}</p><p>補修残額: ¥{remaining}</p></div>
    </div>
    <div class="card"><h3>発行書類</h3><ul>{''.join(docs) or '<li>現在閲覧できる書類はありません。</li>'}</ul></div>
    <div class="card"><h3>お知らせ</h3><ul>{notice_html}</ul></div>
    {slot_html}
    <div class="card"><h3>添付写真</h3><div>{photo_html}</div></div>
    <div class="card"><h3>点検結果</h3><ul>{inspection_html}</ul></div>
    {estimate_html}
    <div class="card"><h3>お問い合わせ・予約変更</h3>
      <input id="requestText" placeholder="内容をご入力ください">
      <button onclick="sendPortalRequest('inquiry')">問い合わせ</button>
      <button onclick="sendPortalRequest('reschedule')">予約変更を依頼</button>
      <button onclick="sendPortalRequest('repair')">補修相談</button>
    </div>
    <div class="card"><h3>請求書・書類</h3><p>施工証明書、スコア表、請求書などACEから発行された書類を確認できます。</p></div>
    <div class="card"><h3>保証・点検</h3><p>保証情報、次回点検予定、点検結果、補修提案を確認できます。</p></div>
    <div class="card"><h3>コミュニケーション履歴</h3><p>LINEでのご案内や重要なお知らせはACE側の履歴と連携されます。</p></div>
    {request_forms}
    <script>
    async function requestOtp(){{const r=await fetch('/api/customer-portals/{token}/otp/request',{{method:'POST'}});const d=await r.json();document.getElementById('otpmsg').textContent=d.debugCode?('開発用OTP: '+d.debugCode):'OTPを送信しました';}}
    async function verifyOtp(){{const code=document.getElementById('otp').value;const r=await fetch('/api/customer-portals/{token}/otp/verify',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{code}})}});if(r.ok)location.reload();else document.getElementById('otpmsg').textContent='認証できませんでした';}}
    async function sendPortalRequest(type){{const text=(document.getElementById('requestText')?.value||'').trim();if(!text)return alert('内容を入力してください');const r=await fetch('/api/customer-portals/{token}/requests',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{type,text}})}});alert(r.ok?'送信しました':'送信できませんでした');if(r.ok)document.getElementById('requestText').value='';}}
    async function approveEstimate(id){{const consentName=document.getElementById('consentName'+id).value;if(!consentName)return alert('同意者氏名を入力してください');const r=await fetch('/api/customer-portals/{token}/repair-estimates/'+id+'/approve',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{consentName}})}});alert(r.ok?'見積を承認しました':'承認できませんでした');if(r.ok)location.reload();}}
    async function rejectEstimate(id){{const r=await fetch('/api/customer-portals/{token}/repair-estimates/'+id+'/reject',{{method:'POST'}});alert(r.ok?'見積を見送りました':'処理できませんでした');if(r.ok)location.reload();}}
    async function markRead(id){{await fetch('/api/customer-portals/{token}/notices/'+id+'/read',{{method:'POST'}});location.reload();}}
    async function selectSlot(id,date,time){{const r=await fetch('/api/customer-portals/{token}/requests/'+id+'/select-slot',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{date,time}})}});alert(r.ok?'仮予約を作成しました':'予約できませんでした');if(r.ok)location.reload();}}
    async function uploadPhoto(){{const f=document.getElementById('repairPhoto').files[0];if(!f)return alert('写真を選択してください');const dataUrl=await new Promise((res,rej)=>{{const r=new FileReader();r.onload=()=>res(r.result);r.onerror=rej;r.readAsDataURL(f);}});const rr=await fetch('/api/customer-portals/{token}/photos',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{name:f.name,type:f.type,dataUrl}})}});alert(rr.ok?'写真を添付しました':'写真を送信できませんでした');}}
    async function sendReq(type){{const detail=type==='repair'?document.getElementById('rdetail').value:document.getElementById('bdetail').value;const date=document.getElementById('bd')?.value||'';const time=document.getElementById('bt')?.value||'';const r=await fetch('/api/customer-portals/{token}/requests',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{type,detail,date,time}})}});alert(r.ok?'送信しました':'送信できませんでした');}}
    </script></body></html>"""
    return html


@app.post("/api/documents/send-line")
@auth_required("staff")
def documents_send_line():
    d=request.get_json() or {}
    issue_id=d.get("issueId");user_id=d.get("userId")
    issue=db.session.get(DocumentIssue,int(issue_id)) if issue_id else None
    if not issue:return jsonify({"error":"issue_not_found"}),404
    file_row=db.session.get(CloudFile,issue.cloud_file_id) if issue.cloud_file_id else None
    if not file_row:return jsonify({"error":"file_not_found"}),404
    if not LINE_CHANNEL_ACCESS_TOKEN:return jsonify({"error":"line_not_configured"}),503
    file_url=request.host_url.rstrip("/") + f"/api/files/{file_row.id}/content"
    message=f"{d.get('customerName') or 'お客様'}様\nACEです。{issue.document_no} を発行しました。\n{file_url}"
    r=requests.post(
      "https://api.line.me/v2/bot/message/push",
      headers={"Authorization":f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}","Content-Type":"application/json"},
      json={"to":user_id,"messages":[{"type":"text","text":message}]},timeout=20
    )
    if not (200<=r.status_code<300):
        return jsonify({"error":"line_send_failed","detail":r.text}),502
    db.session.add(DocumentDelivery(issue_id=issue.id,customer_id=issue.customer_id,channel="line"))
    db.session.commit()
    return jsonify({"ok":True})

@app.get("/api/documents/issues")
@auth_required("staff")
def documents_issues():
    q=DocumentIssue.query
    project_id=request.args.get("projectId")
    customer_id=request.args.get("customerId")
    if project_id:q=q.filter_by(project_id=str(project_id))
    if customer_id:q=q.filter_by(customer_id=str(customer_id))
    rows=q.order_by(DocumentIssue.issued_at.desc()).all()
    return jsonify([{
      "id":x.id,"documentNo":x.document_no,"kind":x.kind,"projectId":x.project_id,
      "customerId":x.customer_id,"cloudFileId":x.cloud_file_id,
      "url":f"/api/files/{x.cloud_file_id}/content" if x.cloud_file_id else "",
      "reissueOf":x.reissue_of,"issuedAt":x.issued_at.isoformat()
    } for x in rows])


@app.get("/api/files")
@auth_required("staff")
def files_list():
    q=CloudFile.query
    customer_id=request.args.get("customerId")
    project_id=request.args.get("projectId")
    asset_kind=request.args.get("assetKind")
    if customer_id:q=q.filter_by(linked_customer_id=str(customer_id))
    if project_id:q=q.filter_by(linked_project_id=str(project_id))
    if asset_kind:q=q.filter_by(asset_kind=asset_kind)
    return jsonify([_cloud_file_json(r) for r in q.order_by(CloudFile.created_at.desc()).all()])


@app.post("/api/files/upload")
@auth_required("staff")
def files_upload():
    d=request.get_json() or {}
    name=(d.get("name") or "file").replace("/","_").replace("\\\\","_")
    mime=d.get("type") or "application/octet-stream"
    data_url=d.get("dataUrl") or ""
    if "," not in data_url:return jsonify({"error":"invalid_data_url"}),400
    import base64,uuid
    header,b64=data_url.split(",",1)
    raw=base64.b64decode(b64)
    upload_dir=os.getenv("ACE_UPLOAD_DIR","ace_uploads")
    os.makedirs(upload_dir,exist_ok=True)
    safe_name=f"{uuid.uuid4().hex}_{name}"
    path=os.path.join(upload_dir,safe_name)
    with open(path,"wb") as f:f.write(raw)
    row=CloudFile(
      name=name,mime_type=mime,size=len(raw),path=path,
      linked_customer_id=str(d.get("linkedCustomerId") or "") or None,
      linked_project_id=str(d.get("linkedProjectId") or "") or None,
      asset_kind=d.get("assetKind") or "attachment"
    )
    db.session.add(row);db.session.commit()
    return jsonify(_cloud_file_json(row))

@app.get("/api/files/<int:file_id>/content")
def files_content(file_id):
    from flask import send_file
    row=db.session.get(CloudFile,file_id)
    if not row or not os.path.exists(row.path):return "not found",404
    return send_file(row.path,mimetype=row.mime_type,download_name=row.name,as_attachment=False)

@app.delete("/api/files/<int:file_id>")
@auth_required("staff")
def files_delete(file_id):
    row=db.session.get(CloudFile,file_id)
    if not row:return jsonify({"error":"not_found"}),404
    try:
        if row.path and os.path.exists(row.path):os.remove(row.path)
    except Exception:pass
    db.session.delete(row);db.session.commit()
    return jsonify({"ok":True})



def _audit(action,target="",detail=""):
    actor=getattr(g,"current_user",None)
    actor_name=""
    try: actor_name=str(actor.get("email") or actor.get("name") or actor.get("id") or "")
    except Exception: actor_name=""
    db.session.add(ServerAuditLog(action=action,actor=actor_name,target=str(target or ""),detail=str(detail or "")))






def _idempotency_begin():
    if request.method not in {"POST","PUT","PATCH","DELETE"}:return None
    key=request.headers.get("X-Idempotency-Key","").strip()
    if not key:return None
    row=IdempotencyRecord.query.filter_by(key=key).first()
    if row:
        try:return jsonify(json.loads(row.response_body or "{}")),row.status_code
        except Exception:return jsonify({"ok":True,"duplicate":True}),row.status_code
    return None

def _idempotency_store(response):
    if request.method not in {"POST","PUT","PATCH","DELETE"}:return response
    key=request.headers.get("X-Idempotency-Key","").strip()
    if not key:return response
    try:
        if not IdempotencyRecord.query.filter_by(key=key).first():
            body=response.get_data(as_text=True)
            db.session.add(IdempotencyRecord(key=key,path=request.path,response_body=body,status_code=response.status_code))
            db.session.commit()
    except Exception:
        db.session.rollback()
    return response


def permission_required(permission):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args,**kwargs):
            if not _role_allowed(permission):
                _security_event("permission_denied","high","権限拒否",f"{permission} access denied")
                return jsonify({"error":"forbidden","permission":permission}),403
            if permission in {"finance","payroll","bank","security","backup","audit"}:
                if not _device_is_approved():
                    return jsonify({"error":"device_approval_required"}),403
            return fn(*args,**kwargs)
        return wrapper
    return deco

def _security_event(event_type,severity,title,detail=""):
    db.session.add(SecurityEvent(event_type=event_type,severity=severity,user_key=_current_user_key(),title=title,detail=detail))

def _device_id():
    ua=request.headers.get("User-Agent","")
    raw=f"{_current_user_key()}|{ua}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]

def _device_is_approved():
    did=_device_id()
    row=db.session.get(DeviceApproval,did)
    return bool(row and row.approved and not row.revoked_at)

def _touch_device():
    did=_device_id();ua=request.headers.get("User-Agent","")
    row=db.session.get(DeviceApproval,did)
    if not row:
        row=DeviceApproval(id=did,user_key=_current_user_key(),device_name=ua[:80] or "Browser",approved=False)
        db.session.add(row)
    if row.revoked_at or _session_expired(row):
        _security_event("session_invalid","high","セッション無効","revoked or expired")
        db.session.commit()
        raise PermissionError("session_invalid")
    row.last_seen_at=datetime.datetime.utcnow()
    _touch_device()
    db.session.commit()
    return row

def _session_expired(row):
    minutes=int(os.getenv("ACE_SESSION_TIMEOUT_MINUTES","480"))
    return bool(row and row.last_seen_at and (datetime.datetime.utcnow()-row.last_seen_at).total_seconds()>minutes*60)

def _login_lockout_status(user_key):
    max_fail=int(os.getenv("ACE_MAX_LOGIN_FAILURES","5"))
    lock_min=int(os.getenv("ACE_LOGIN_LOCKOUT_MINUTES","15"))
    since=datetime.datetime.utcnow()-datetime.timedelta(minutes=lock_min)
    fails=LoginAttempt.query.filter_by(user_key=user_key,success=False).filter(LoginAttempt.created_at>=since).count()
    return fails>=max_fail,fails


def _current_user_key():
    actor=getattr(g,"current_user",None)
    if isinstance(actor,dict):
        return str(actor.get("email") or actor.get("id") or actor.get("name") or "unknown")
    return str(actor or "unknown")

def _security_user():
    key=_current_user_key()
    row=UserSecurity.query.filter_by(user_key=key).first()
    if not row:
        role="manager"
        actor=getattr(g,"current_user",{}) or {}
        if isinstance(actor,dict):role=str(actor.get("role") or "manager")
        row=UserSecurity(user_key=key,role=role)
        db.session.add(row);db.session.commit()
    return row

def _session_id():
    sid=session.get("ace_security_session_id")
    if not sid:
        sid=secrets.token_urlsafe(24);session["ace_security_session_id"]=sid
    return sid

def _touch_security_session():
    sid=_session_id();key=_current_user_key()
    row=db.session.get(SecuritySession,sid)
    if not row:
        ua=request.headers.get("User-Agent","")
        ip=request.headers.get("X-Forwarded-For",request.remote_addr or "")
        row=SecuritySession(id=sid,user_key=key,device_name=ua[:80] or "Browser",
                            ip_hash=hashlib.sha256(ip.encode()).hexdigest() if ip else "",user_agent=ua)
        db.session.add(row)
    row.last_seen_at=datetime.datetime.utcnow()
    db.session.commit()
    return row

def _role_allowed(permission):
    role=_security_user().role
    matrix={
      "manager":{"customers","projects","finance","payroll","bank","security","backup","audit"},
      "staff":{"customers","projects","bookings","fieldwork","tools","line"},
      "subcontractor":{"assigned_projects","fieldwork","tools"}
    }
    return permission in matrix.get(role,set())


def _backup_config_row():
    row=db.session.get(BackupConfig,1)
    if not row:
        row=BackupConfig(id=1,daily_enabled=False,encryption_enabled=bool(os.getenv("ACE_BACKUP_ENCRYPTION_KEY")))
        db.session.add(row);db.session.commit()
    return row

def _backup_fernet():
    if Fernet is None:return None
    key=os.getenv("ACE_BACKUP_ENCRYPTION_KEY","").strip()
    if not key:return None
    try:return Fernet(key.encode() if isinstance(key,str) else key)
    except Exception:return None

def _disaster_dir():
    path=os.getenv("ACE_DISASTER_BACKUP_DIR","ace_disaster_backups")
    os.makedirs(path,exist_ok=True);return path

def _replica_dir():
    path=os.getenv("ACE_REPLICA_DIR","").strip()
    if path:os.makedirs(path,exist_ok=True)
    return path

def _write_maybe_encrypted(path, raw):
    f=_backup_fernet()
    if f:
        raw=f.encrypt(raw)
        path=path+".enc"
    with open(path,"wb") as h:h.write(raw)
    return path,bool(f)

def _read_maybe_encrypted(path):
    raw=open(path,"rb").read()
    if path.endswith(".enc"):
        f=_backup_fernet()
        if not f:raise RuntimeError("backup encryption key unavailable")
        raw=f.decrypt(raw)
    return raw

def _replicate_file(path):
    replica=_replica_dir()
    if not replica:return False
    try:
        shutil.copy2(path,os.path.join(replica,os.path.basename(path)))
        return True
    except Exception:
        return False

def maybe_run_daily_backup():
    cfg=_backup_config_row()
    if not cfg.daily_enabled:return
    now=datetime.datetime.utcnow()
    if cfg.last_daily_at and (now-cfg.last_daily_at).total_seconds()<20*3600:return
    try:
        _create_disaster_backup("daily_auto")
        cfg.last_daily_at=now
        db.session.commit()
    except Exception:
        db.session.rollback()

def _create_disaster_backup(reason="manual"):
    import tempfile,zipfile
    ts=datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    bid=f"disaster_{ts}_{secrets.token_hex(3)}"
    temp_dir=tempfile.mkdtemp(prefix="ace_dr_")
    try:
        state=_serialize_full_state()
        with open(os.path.join(temp_dir,"database.json"),"w",encoding="utf-8") as f:
            json.dump(state,f,ensure_ascii=False,indent=2)
        upload_dir=os.getenv("ACE_UPLOAD_DIR","ace_uploads")
        files_dir=os.path.join(temp_dir,"uploads")
        if os.path.isdir(upload_dir):
            shutil.copytree(upload_dir,files_dir,dirs_exist_ok=True)
        manifest={
          "id":bid,"createdAt":datetime.datetime.utcnow().isoformat(),"reason":reason,
          "uploadDirIncluded":os.path.isdir(upload_dir),"version":"11.2"
        }
        with open(os.path.join(temp_dir,"manifest.json"),"w",encoding="utf-8") as f:json.dump(manifest,f,ensure_ascii=False,indent=2)
        raw_io=io.BytesIO()
        with zipfile.ZipFile(raw_io,"w",zipfile.ZIP_DEFLATED) as z:
            for root,dirs,files in os.walk(temp_dir):
                for name in files:
                    p=os.path.join(root,name)
                    z.write(p,arcname=os.path.relpath(p,temp_dir))
        out_path=os.path.join(_disaster_dir(),f"{bid}.zip")
        final_path,encrypted=_write_maybe_encrypted(out_path,raw_io.getvalue())
        replicated=_replicate_file(final_path)
        _audit("disaster_backup_create",bid,f"reason={reason}; encrypted={encrypted}; replica={replicated}")
        db.session.commit()
        return {"id":bid,"createdAt":manifest["createdAt"],"path":final_path,"encrypted":encrypted,"replicated":replicated,"sizeKB":round(os.path.getsize(final_path)/1024,1)}
    finally:
        shutil.rmtree(temp_dir,ignore_errors=True)


def rotate_backups(keep=30):
    try:
        files=sorted([os.path.join(_backup_dir(),x) for x in os.listdir(_backup_dir()) if x.endswith(".json")],key=os.path.getmtime,reverse=True)
        for p in files[keep:]:
            try: os.remove(p)
            except Exception: pass
    except Exception:
        pass


def _backup_dir():
    path=os.getenv("ACE_BACKUP_DIR","ace_backups");os.makedirs(path,exist_ok=True);return path

def _serialize_full_state():
    def rows(model):
        return [{"id":r.id,"payload":r.payload,"updated_at":r.updated_at.isoformat() if r.updated_at else None} for r in model.query.all()]
    return {
      "createdAt":datetime.datetime.utcnow().isoformat(),
      "core":{"customers":rows(CoreCustomer),"bookings":rows(CoreBooking),"projects":rows(CoreProject)},
      "extended":{r.key:json.loads(r.payload) if r.payload else None for r in ExtendedState.query.all()},
      "deleted":[{"entity_type":r.entity_type,"entity_id":r.entity_id,"payload":r.payload,"deleted_at":r.deleted_at.isoformat()} for r in DeletedEntitySnapshot.query.filter_by(restored_at=None).all()]
    }




@app.post("/api/security/devices/<device_id>/approve")
@auth_required("manager")
@permission_required("security")
def security_device_approve(device_id):
    row=db.session.get(DeviceApproval,device_id)
    if not row:return jsonify({"error":"not_found"}),404
    row.approved=True;row.approved_at=datetime.datetime.utcnow();row.revoked_at=None
    _security_event("device_approved","mid","端末承認",row.device_name or device_id)
    db.session.commit()
    return jsonify({"ok":True})

@app.post("/api/security/devices/<device_id>/revoke")
@auth_required("manager")
@permission_required("security")
def security_device_revoke(device_id):
    row=db.session.get(DeviceApproval,device_id)
    if not row:return jsonify({"error":"not_found"}),404
    row.approved=False;row.revoked_at=datetime.datetime.utcnow()
    _security_event("device_revoked","mid","端末承認解除",row.device_name or device_id)
    db.session.commit()
    return jsonify({"ok":True})

@app.get("/api/security/events")
@auth_required("manager")
@permission_required("security")
def security_events_get():
    rows=SecurityEvent.query.order_by(SecurityEvent.created_at.desc()).limit(500).all()
    return jsonify([{"id":x.id,"type":x.event_type,"severity":x.severity,"title":x.title,"detail":x.detail,"createdAt":x.created_at.isoformat()} for x in rows])

@app.post("/api/security/login-attempt")
def security_login_attempt():
    d=request.get_json() or {}
    user_key=str(d.get("userKey") or "")
    success=bool(d.get("success"))
    ip=request.headers.get("X-Forwarded-For",request.remote_addr or "")
    locked,fails=_login_lockout_status(user_key)
    if locked and not success:
        return jsonify({"locked":True,"failures":fails}),429
    db.session.add(LoginAttempt(user_key=user_key,success=success,ip_hash=hashlib.sha256(ip.encode()).hexdigest() if ip else ""))
    if not success:
        _security_event("login_failed","high","ログイン失敗",f"{user_key}")
    db.session.commit()
    locked,fails=_login_lockout_status(user_key)
    return jsonify({"ok":True,"locked":locked,"failures":fails})




@app.post("/api/line/communication-event")
@auth_required("staff")
def line_communication_event():
    d=request.get_json() or {}
    key="communicationHistory"
    rows=_extended_get(key,[])
    rows.insert(0,{
      "id":int(datetime.datetime.utcnow().timestamp()*1000),
      "customerId":d.get("customerId"),"type":d.get("type","message"),"direction":d.get("direction","in"),
      "message":d.get("message",""),"createdAt":datetime.datetime.utcnow().isoformat(),
      "status":d.get("status","received"),"channel":"line","refId":d.get("refId")
    })
    _extended_set(key,rows[:2000])
    db.session.commit()
    return jsonify({"ok":True})


@app.get("/api/sync-entity-version")
@auth_required("staff")
def sync_entity_version():
    key=request.args.get("key","")
    if key.startswith("project:"):
        rid=key.split(":",1)[1];row=db.session.get(CoreProject,str(rid))
    elif key.startswith("customer:"):
        rid=key.split(":",1)[1];row=db.session.get(CoreCustomer,str(rid))
    elif key.startswith("booking:"):
        rid=key.split(":",1)[1];row=db.session.get(CoreBooking,str(rid))
    else:
        return jsonify({"updatedAt":None})
    return jsonify({"updatedAt":row.updated_at.isoformat() if row and row.updated_at else None})



@app.before_request
def ace_idempotency_before_request():
    result=_idempotency_begin()
    if result is not None:return result

@app.after_request
def ace_idempotency_after_request(response):
    return _idempotency_store(response)


@app.get("/api/security/state")
@auth_required("staff")
def security_state():
    me=_touch_security_session();sec=_security_user()
    rows=SecuritySession.query.filter_by(user_key=sec.user_key).filter(SecuritySession.revoked_at.is_(None)).order_by(SecuritySession.last_seen_at.desc()).all()
    sessions=[]
    for r in rows:
        sessions.append({"id":r.id,"deviceName":r.device_name,"ipMasked":(r.ip_hash[:8]+"…") if r.ip_hash else "-","lastSeenAt":r.last_seen_at.isoformat(),"current":r.id==me.id})
    alerts=[]
    if not sec.two_factor_enabled:alerts.append({"severity":"high","title":"2段階認証が未設定","detail":"管理アカウントは2段階認証を有効化してください。"})
    if len(rows)>5:alerts.append({"severity":"mid","title":"有効セッションが多い","detail":f"{len(rows)}件のセッションが有効です。"})
    devices_rows=DeviceApproval.query.filter_by(user_key=sec.user_key).order_by(DeviceApproval.last_seen_at.desc()).all()
    devices=[{"id":d.id,"userKey":d.user_key,"deviceName":d.device_name,"approved":d.approved and not d.revoked_at,"lastSeenAt":d.last_seen_at.isoformat()} for d in devices_rows]
    event_rows=SecurityEvent.query.order_by(SecurityEvent.created_at.desc()).limit(100).all()
    events=[{"id":e.id,"type":e.event_type,"severity":e.severity,"title":e.title,"detail":e.detail,"createdAt":e.created_at.isoformat()} for e in event_rows]
    return jsonify({"sessions":sessions,"devices":devices,"events":events,"twoFactorEnabled":sec.two_factor_enabled,"auditAlerts":alerts})

@app.post("/api/security/2fa/setup")
@auth_required("staff")
def security_2fa_setup():
    if pyotp is None:return jsonify({"error":"pyotp_not_installed"}),503
    sec=_security_user()
    secret=pyotp.random_base32()
    sec.two_factor_secret=secret;sec.two_factor_enabled=False;db.session.commit()
    return jsonify({"secret":secret,"otpauth":pyotp.totp.TOTP(secret).provisioning_uri(name=sec.user_key,issuer_name="ACE")})

@app.post("/api/security/2fa/verify")
@auth_required("staff")
def security_2fa_verify():
    if pyotp is None:return jsonify({"error":"pyotp_not_installed"}),503
    sec=_security_user()
    d=request.get_json() or {}
    if not sec.two_factor_secret:return jsonify({"error":"not_setup"}),400
    ok=pyotp.TOTP(sec.two_factor_secret).verify(str(d.get("code") or ""),valid_window=1)
    if not ok:return jsonify({"error":"invalid_code"}),400
    sec.two_factor_enabled=True;db.session.commit()
    _audit("2fa_enabled",sec.user_key,"")
    return jsonify({"ok":True})

@app.post("/api/security/sessions/<session_id>/revoke")
@auth_required("staff")
def security_session_revoke(session_id):
    row=db.session.get(SecuritySession,session_id)
    if not row or row.user_key!=_current_user_key():return jsonify({"error":"not_found"}),404
    row.revoked_at=datetime.datetime.utcnow();db.session.commit()
    _audit("session_revoke",session_id,"")
    return jsonify({"ok":True})

@app.post("/api/security/sessions/revoke-others")
@auth_required("staff")
def security_sessions_revoke_others():
    current=_session_id()
    rows=SecuritySession.query.filter_by(user_key=_current_user_key()).filter(SecuritySession.revoked_at.is_(None)).all()
    for r in rows:
        if r.id!=current:r.revoked_at=datetime.datetime.utcnow()
    db.session.commit()
    _audit("session_revoke_others",_current_user_key(),"")
    return jsonify({"ok":True})

@app.post("/api/security/audit")
@auth_required("manager")
@permission_required("security")
def security_audit():
    sec=_security_user()
    rows=SecuritySession.query.filter_by(user_key=sec.user_key).filter(SecuritySession.revoked_at.is_(None)).all()
    alerts=[]
    if not sec.two_factor_enabled:alerts.append({"severity":"high","title":"2段階認証未設定","detail":"管理者アカウントに2段階認証がありません。"})
    if len(rows)>5:alerts.append({"severity":"mid","title":"セッション過多","detail":f"有効セッション {len(rows)}件"})
    if not _backup_fernet():alerts.append({"severity":"mid","title":"バックアップ暗号化無効","detail":"ACE_BACKUP_ENCRYPTION_KEYが未設定です。"})
    if not _replica_dir():alerts.append({"severity":"mid","title":"外部複製未設定","detail":"ACE_REPLICA_DIRが未設定です。"})
    _audit("security_audit",sec.user_key,f"alerts={len(alerts)}");db.session.commit()
    return jsonify({"alerts":alerts})


@app.post("/api/admin/backup-config")
@auth_required("manager")
def admin_backup_config_update():
    d=request.get_json() or {}
    cfg=_backup_config_row()
    if "dailyEnabled" in d:cfg.daily_enabled=bool(d.get("dailyEnabled"))
    cfg.encryption_enabled=bool(_backup_fernet())
    cfg.external_replica_enabled=bool(_replica_dir())
    db.session.commit()
    return jsonify({"ok":True})

@app.get("/api/admin/disaster-backups")
@auth_required("manager")
def admin_disaster_backup_list():
    maybe_run_daily_backup()
    cfg=_backup_config_row()
    rows=[]
    for name in sorted(os.listdir(_disaster_dir()),reverse=True):
        if not (name.endswith(".zip") or name.endswith(".zip.enc")):continue
        p=os.path.join(_disaster_dir(),name)
        bid=name.replace(".zip.enc","").replace(".zip","")
        rows.append({"id":bid,"createdAt":datetime.datetime.fromtimestamp(os.path.getmtime(p)).isoformat(),"sizeKB":round(os.path.getsize(p)/1024,1),"encrypted":name.endswith(".enc")})
    return jsonify({"backups":rows[:20],"config":{"dailyEnabled":cfg.daily_enabled,"lastDailyAt":cfg.last_daily_at.isoformat() if cfg.last_daily_at else None,"encryptionEnabled":bool(_backup_fernet()),"externalReplicaEnabled":bool(_replica_dir())}})

@app.post("/api/admin/disaster-backups")
@auth_required("manager")
@permission_required("backup")
def admin_disaster_backup_create():
    d=request.get_json() or {}
    result=_create_disaster_backup(d.get("reason") or "manual")
    return jsonify({k:v for k,v in result.items() if k!="path"})

def _find_disaster_path(backup_id):
    for suffix in [".zip.enc",".zip"]:
        p=os.path.join(_disaster_dir(),backup_id+suffix)
        if os.path.exists(p):return p
    return None

@app.get("/api/admin/disaster-backups/<backup_id>/download")
@auth_required("manager")
def admin_disaster_backup_download(backup_id):
    from flask import send_file
    p=_find_disaster_path(backup_id)
    if not p:return "not found",404
    return send_file(p,as_attachment=True,download_name=os.path.basename(p))

@app.post("/api/admin/disaster-backups/<backup_id>/restore")
@auth_required("manager")
def admin_disaster_backup_restore(backup_id):
    import tempfile,zipfile
    p=_find_disaster_path(backup_id)
    if not p:return jsonify({"error":"not_found"}),404
    safety=_create_disaster_backup("pre_disaster_restore")
    raw=_read_maybe_encrypted(p)
    temp_dir=tempfile.mkdtemp(prefix="ace_restore_")
    try:
        with zipfile.ZipFile(io.BytesIO(raw),"r") as z:z.extractall(temp_dir)
        db_path=os.path.join(temp_dir,"database.json")
        if not os.path.exists(db_path):return jsonify({"error":"database_snapshot_missing"}),400
        data=json.load(open(db_path,"r",encoding="utf-8"))
        for model,key in [(CoreCustomer,"customers"),(CoreBooking,"bookings"),(CoreProject,"projects")]:
            model.query.delete()
            for item in data.get("core",{}).get(key,[]):
                dt=_parse_iso(item.get("updated_at")) or datetime.datetime.utcnow()
                db.session.add(model(id=str(item["id"]),payload=item["payload"],updated_at=dt))
        ExtendedState.query.delete()
        for key,val in (data.get("extended") or {}).items():
            db.session.add(ExtendedState(key=key,payload=json.dumps(val,ensure_ascii=False),updated_at=datetime.datetime.utcnow()))
        upload_dir=os.getenv("ACE_UPLOAD_DIR","ace_uploads")
        restored_uploads=os.path.join(temp_dir,"uploads")
        if os.path.isdir(restored_uploads):
            if os.path.isdir(upload_dir):shutil.rmtree(upload_dir)
            shutil.copytree(restored_uploads,upload_dir)
        _audit("disaster_backup_restore",backup_id,f"safety={safety['id']}")
        db.session.commit()
        return jsonify({"ok":True,"safetyBackup":safety["id"]})
    finally:
        shutil.rmtree(temp_dir,ignore_errors=True)

@app.post("/api/admin/replica-test")
@auth_required("manager")
def admin_replica_test():
    path=_replica_dir()
    if not path:return jsonify({"ok":False,"error":"ACE_REPLICA_DIR_not_set"})
    try:
        probe=os.path.join(path,"ace_replica_probe.txt")
        with open(probe,"w",encoding="utf-8") as f:f.write(datetime.datetime.utcnow().isoformat())
        os.remove(probe)
        cfg=_backup_config_row();cfg.external_replica_enabled=True;db.session.commit()
        return jsonify({"ok":True})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}),500


@app.post("/api/admin/backups")
@auth_required("manager")
@permission_required("backup")
def admin_backup_create():
    d=request.get_json() or {}
    data=_serialize_full_state()
    ts=datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    bid=f"backup_{ts}_{secrets.token_hex(3)}"
    path=os.path.join(_backup_dir(),f"{bid}.json")
    with open(path,"w",encoding="utf-8") as f:json.dump(data,f,ensure_ascii=False,indent=2)
    _audit("backup_create",bid,d.get("reason") or "manual");db.session.commit()
    return jsonify({"ok":True,"id":bid,"createdAt":data["createdAt"]})

@app.get("/api/admin/backups")
@auth_required("manager")
@permission_required("backup")
def admin_backup_list():
    rows=[]
    for name in sorted(os.listdir(_backup_dir()),reverse=True):
        if not name.endswith(".json"):continue
        p=os.path.join(_backup_dir(),name)
        try:
            with open(p,"r",encoding="utf-8") as f:data=json.load(f)
            rows.append({"id":name[:-5],"createdAt":data.get("createdAt"),"sizeKB":round(os.path.getsize(p)/1024,1),"reason":"backup"})
        except Exception: pass
    return jsonify({"backups":rows[:30]})

@app.get("/api/admin/backups/<backup_id>/download")
@auth_required("manager")
def admin_backup_download(backup_id):
    from flask import send_file
    path=os.path.join(_backup_dir(),f"{backup_id}.json")
    if not os.path.exists(path):return "not found",404
    return send_file(path,mimetype="application/json",as_attachment=True,download_name=f"{backup_id}.json")

@app.post("/api/admin/backups/<backup_id>/restore")
@auth_required("manager")
def admin_backup_restore(backup_id):
    path=os.path.join(_backup_dir(),f"{backup_id}.json")
    if not os.path.exists(path):return jsonify({"error":"not_found"}),404
    # safety backup before restore
    safety=_serialize_full_state()
    sid=f"pre_restore_{datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%S')}_{secrets.token_hex(3)}"
    with open(os.path.join(_backup_dir(),f"{sid}.json"),"w",encoding="utf-8") as f:json.dump(safety,f,ensure_ascii=False,indent=2)
    with open(path,"r",encoding="utf-8") as f:data=json.load(f)
    for model,key in [(CoreCustomer,"customers"),(CoreBooking,"bookings"),(CoreProject,"projects")]:
        model.query.delete()
        for item in data.get("core",{}).get(key,[]):
            dt=_parse_iso(item.get("updated_at")) or datetime.datetime.utcnow()
            db.session.add(model(id=str(item["id"]),payload=item["payload"],updated_at=dt))
    ExtendedState.query.delete()
    for key,val in (data.get("extended") or {}).items():
        db.session.add(ExtendedState(key=key,payload=json.dumps(val,ensure_ascii=False),updated_at=datetime.datetime.utcnow()))
    _audit("backup_restore",backup_id,f"safety={sid}")
    db.session.commit()
    return jsonify({"ok":True,"safetyBackup":sid})

@app.get("/api/admin/deleted")
@auth_required("manager")
@permission_required("backup")
def admin_deleted_list():
    rows=DeletedEntitySnapshot.query.filter_by(restored_at=None).order_by(DeletedEntitySnapshot.deleted_at.desc()).limit(500).all()
    return jsonify([{"id":r.id,"entityType":r.entity_type,"entityId":r.entity_id,"deletedAt":r.deleted_at.isoformat()} for r in rows])

@app.post("/api/admin/deleted/<int:snapshot_id>/restore")
@auth_required("manager")
def admin_deleted_restore(snapshot_id):
    snap=db.session.get(DeletedEntitySnapshot,snapshot_id)
    if not snap or snap.restored_at:return jsonify({"error":"not_found"}),404
    mapping={"customers":CoreCustomer,"bookings":CoreBooking,"projects":CoreProject}
    model=mapping.get(snap.entity_type)
    if not model:return jsonify({"error":"unsupported_type"}),400
    row=db.session.get(model,snap.entity_id)
    if not row:
        try:
            payload=json.loads(snap.payload)
            updated=_parse_iso(payload.get("updatedAt")) or datetime.datetime.utcnow()
        except Exception:
            updated=datetime.datetime.utcnow()
        db.session.add(model(id=snap.entity_id,payload=snap.payload,updated_at=updated))
    snap.restored_at=datetime.datetime.utcnow()
    _audit("deleted_restore",f"{snap.entity_type}:{snap.entity_id}","")
    db.session.commit()
    return jsonify({"ok":True})

@app.get("/api/admin/audit-logs")
@auth_required("manager")
@permission_required("audit")
def admin_audit_logs():
    rows=ServerAuditLog.query.order_by(ServerAuditLog.created_at.desc()).limit(500).all()
    return jsonify([{"id":r.id,"action":r.action,"actor":r.actor,"target":r.target,"detail":r.detail,"createdAt":r.created_at.isoformat()} for r in rows])

@app.get("/api/admin/recovery-health")
@auth_required("manager")
def admin_recovery_health():
    upload_dir=os.getenv("ACE_UPLOAD_DIR","ace_uploads")
    try:
        db.session.execute(db.text("SELECT 1"))
        database=True
    except Exception:
        database=False
    return jsonify({
      "database":database,
      "uploadDir":os.path.isdir(upload_dir) or bool(os.makedirs(upload_dir,exist_ok=True) is None),
      "backupDir":os.path.isdir(_backup_dir()),
      "timestamp":datetime.datetime.utcnow().isoformat()
    })


@app.get("/api/core-sync")
@auth_required("staff")
def core_sync_get():
    since=_parse_iso(request.args.get("since"))
    deleted_q=CoreDeleted.query
    if since: deleted_q=deleted_q.filter(CoreDeleted.deleted_at>since)
    deleted={"customers":[],"bookings":[],"projects":[]}
    for d in deleted_q.all():
        deleted.setdefault(d.entity_type,[]).append({"id":d.entity_id,"deletedAt":d.deleted_at.isoformat()})
    cursor=datetime.datetime.utcnow().isoformat()
    return jsonify({
      "customers":_core_rows(CoreCustomer,since),
      "bookings":_core_rows(CoreBooking,since),
      "projects":_core_rows(CoreProject,since),
      "deleted":deleted,
      "cursor":cursor
    })

@app.post("/api/core-sync")
@auth_required("staff")
def core_sync_post():
    d=request.get_json() or {}
    c=_upsert_core(CoreCustomer,d.get("customers") or [])
    b=_upsert_core(CoreBooking,d.get("bookings") or [])
    p=_upsert_core(CoreProject,d.get("projects") or [])
    dels=d.get("deleted") or {}
    dc=_apply_deletes(CoreCustomer,"customers",dels.get("customers") or [])
    dbk=_apply_deletes(CoreBooking,"bookings",dels.get("bookings") or [])
    dp=_apply_deletes(CoreProject,"projects",dels.get("projects") or [])
    db.session.commit()
    return jsonify({"ok":True,"customers":c,"bookings":b,"projects":p,"deleted":dc+dbk+dp})


@app.get("/api/staff-locations")
@auth_required("staff")
def staff_locations_get():
    rows=StaffLocation.query.order_by(StaffLocation.updated_at.desc()).all()
    return jsonify([{
      "staffId":r.staff_id,"staffName":r.staff_name,"lat":r.lat,"lng":r.lng,
      "accuracy":r.accuracy,"updatedAt":r.updated_at.isoformat()
    } for r in rows])

@app.post("/api/staff-locations")
@auth_required("staff")
def staff_locations_post():
    d=request.get_json() or {}
    staff_id=str(d.get("staffId") or "")
    if not staff_id:return jsonify({"error":"staff_id_required"}),400
    row=StaffLocation.query.filter_by(staff_id=staff_id).first()
    if not row:
        row=StaffLocation(staff_id=staff_id)
        db.session.add(row)
    row.staff_name=d.get("staffName","")
    row.lat=float(d.get("lat"))
    row.lng=float(d.get("lng"))
    row.accuracy=float(d.get("accuracy") or 0)
    row.updated_at=datetime.datetime.utcnow()
    db.session.commit()
    return jsonify({"ok":True})

@app.post("/api/tracking-links")
@auth_required("staff")
def tracking_link_create():
    d=request.get_json() or {}
    token=secrets.token_urlsafe(24)
    expiry_hours=int(d.get("expiryHours") or 24)
    row=TrackingLink(
      token=token,booking_id=str(d.get("bookingId") or ""),customer_id=str(d.get("customerId") or ""),
      customer_name=d.get("customerName",""),staff_name=d.get("staffName",""),
      booking_date=d.get("date",""),booking_time=d.get("time",""),active=True,
      expires_at=datetime.datetime.utcnow()+datetime.timedelta(hours=max(1,min(expiry_hours,168)))
    )
    db.session.add(row);db.session.commit()
    base=request.host_url.rstrip("/")
    return jsonify({"token":token,"url":f"{base}/?track={token}"})

@app.get("/api/tracking/<token>")
def tracking_public(token):
    link=TrackingLink.query.filter_by(token=token,active=True).first()
    if not link:return jsonify({"error":"not_found"}),404
    if link.expires_at and datetime.datetime.utcnow()>link.expires_at:
        link.active=False;db.session.commit()
        return jsonify({"error":"expired"}),410
    loc=None
    if link.staff_name:
        loc=StaffLocation.query.filter_by(staff_name=link.staff_name).order_by(StaffLocation.updated_at.desc()).first()
    eta=None
    status="移動状況を確認中"
    stale=True
    if loc:
        stale=(datetime.datetime.utcnow()-loc.updated_at).total_seconds()>600
    if loc and not stale and GOOGLE_MAPS_API_KEY and link.booking_date:
        try:
            # Customer address is intentionally not exposed here; ETA can be populated once booking/customer address
            # is persisted server-side. For now preserve privacy and return freshness/status only.
            status="担当者が移動中"
        except Exception:
            pass
    if link.booking_date and link.booking_time:
        try:
            appt=datetime.datetime.fromisoformat(f"{link.booking_date}T{link.booking_time}:00")
            if datetime.datetime.now()<appt and status=="移動状況を確認中":
                status="ご予約前"
        except Exception:
            pass
    return jsonify({
      "customerName":link.customer_name,"staffName":link.staff_name,
      "date":link.booking_date,"time":link.booking_time,"status":status,
      "etaMinutes":eta,"locationUpdatedAt":loc.updated_at.isoformat() if loc and not stale else None,
      "expiresAt":link.expires_at.isoformat() if link.expires_at else None
    }),404
    loc=None
    if link.staff_name:
        loc=StaffLocation.query.filter_by(staff_name=link.staff_name).order_by(StaffLocation.updated_at.desc()).first()
    eta=None
    status="移動状況を確認中"
    if loc and link.booking_date and link.booking_time:
        try:
            appt=datetime.datetime.fromisoformat(f"{link.booking_date}T{link.booking_time}:00")
            now=datetime.datetime.now()
            if now < appt: status="ご予約前"
            else: status="担当者が移動中"
        except Exception:
            pass
    return jsonify({
      "customerName":link.customer_name,"staffName":link.staff_name,
      "date":link.booking_date,"time":link.booking_time,"status":status,
      "etaMinutes":eta,"locationUpdatedAt":loc.updated_at.isoformat() if loc else None
    })


@app.get("/api/maps/frontend-config")
@auth_required("staff")
def maps_frontend_config():
    return jsonify({"apiKey":GOOGLE_MAPS_API_KEY if GOOGLE_MAPS_API_KEY else ""})


@app.post("/api/routes/geocode")
@auth_required("staff")
def route_geocode():
    d=request.get_json() or {}
    address=(d.get("address") or "").strip()
    if not address:return jsonify({"error":"address_required"}),400
    if not GOOGLE_MAPS_API_KEY:return jsonify({"error":"google_maps_not_configured"}),503
    try:
        r=requests.get(
          "https://maps.googleapis.com/maps/api/geocode/json",
          params={"address":address,"key":GOOGLE_MAPS_API_KEY,"language":"ja"},
          timeout=20
        )
        data=r.json()
        if data.get("status")=="OK" and data.get("results"):
            top=data["results"][0]
            loc=top["geometry"]["location"]
            return jsonify({
              "lat":loc["lat"],"lng":loc["lng"],
              "formattedAddress":top.get("formatted_address",address),
              "provider":"google_geocoding"
            })
        return jsonify({"error":"geocode_failed","status":data.get("status")}),400
    except Exception as e:
        return jsonify({"error":"geocode_failed","detail":str(e)}),502


@app.post("/api/routes/travel-time")
@auth_required("staff")
def route_travel_time():
    d=request.get_json() or {}
    origin=(d.get("origin") or "").strip()
    destination=(d.get("destination") or "").strip()
    origin_lat=d.get("originLat");origin_lng=d.get("originLng")
    departure=d.get("departureTime")
    if not destination or (not origin and (origin_lat is None or origin_lng is None)):
        return jsonify({"error":"origin_destination_required"}),400

    if GOOGLE_MAPS_API_KEY:
        try:
            body={
              "origin":({"location":{"latLng":{"latitude":float(origin_lat),"longitude":float(origin_lng)}}} if origin_lat is not None and origin_lng is not None else {"address":origin}),
              "destination":{"address":destination},
              "travelMode":"DRIVE",
              "routingPreference":"TRAFFIC_AWARE",
              "languageCode":"ja",
              "units":"METRIC"
            }
            if departure:
                body["departureTime"]=departure
            r=requests.post(
              "https://routes.googleapis.com/directions/v2:computeRoutes",
              headers={
                "Content-Type":"application/json",
                "X-Goog-Api-Key":GOOGLE_MAPS_API_KEY,
                "X-Goog-FieldMask":"routes.duration,routes.distanceMeters"
              },
              json=body,timeout=20
            )
            if 200 <= r.status_code < 300:
                data=r.json()
                routes=data.get("routes") or []
                if routes:
                    dur=routes[0].get("duration","0s")
                    seconds=float(str(dur).rstrip("s") or 0)
                    return jsonify({
                      "minutes":max(1,round(seconds/60)),
                      "distanceMeters":routes[0].get("distanceMeters",0),
                      "provider":"google_routes"
                    })
        except Exception:
            pass

    # fallback approximation when API key is missing or Google fails
    return jsonify({"minutes":20,"distanceMeters":0,"provider":"fallback"})


@app.post("/api/ai/workforce-optimization")
@auth_required("manager")
def ai_workforce_optimization():
    d=request.get_json() or {}
    client=openai_client()
    if not client:
        return jsonify({"error":"ai_not_configured"}),503
    prompt=(
        "あなたは内装会社ACEの人員配置AIです。現場、予約、予定工数、担当者負荷、住所情報を見て、"
        "担当割当と1日の訪問順を改善してください。移動を減らしつつ、予約時刻を守り、"
        "スタッフの負荷が偏りすぎないように3〜5個の改善案を出してください。"
        "JSONのみ。形式:{\"summary\":\"\",\"actions\":[\"\"]}\n"
        + json.dumps(d,ensure_ascii=False)
    )
    resp=client.responses.create(model=OPENAI_MODEL,input=prompt)
    parsed=parse_json_loose(resp.output_text)
    if not parsed:return jsonify({"error":"invalid_ai_response"}),502
    return jsonify(parsed)


@app.post("/api/ai/shift-optimization")
@auth_required("manager")
def ai_shift_optimization():
    d=request.get_json() or {}
    client=openai_client()
    if not client:
        return jsonify({"error":"ai_not_configured"}),503
    prompt=(
        "あなたは内装会社ACEの予約・シフト最適化AIです。"
        "スタッフ別予約負荷、ノーショー傾向、時間帯別予約スコア、今後の予約を見て、"
        "誰をどの時間帯に厚く配置するか、予約をどう振り分けるかを3〜5個提案してください。"
        "JSONのみ。形式:{\"summary\":\"\",\"actions\":[\"\"]}\n"
        + json.dumps(d,ensure_ascii=False)
    )
    resp=client.responses.create(model=OPENAI_MODEL,input=prompt)
    parsed=parse_json_loose(resp.output_text)
    if not parsed:return jsonify({"error":"invalid_ai_response"}),502
    return jsonify(parsed)


@app.post("/api/ai/booking-optimization")
@auth_required("manager")
def ai_booking_optimization():
    d=request.get_json() or {}
    client=openai_client()
    if not client:
        return jsonify({"error":"ai_not_configured"}),503
    prompt=(
        "あなたは内装会社ACEの予約運用改善AIです。"
        "ノーショー率、キャンセル率、リマインド効果、時間帯別実績、顧客傾向、担当者情報を分析し、"
        "予約枠・リマインド・担当配置の改善策を3〜5個、日本語で具体的に出してください。"
        "JSONのみ。形式:{\"summary\":\"\",\"actions\":[\"\"]}\n"
        + json.dumps(d,ensure_ascii=False)
    )
    resp=client.responses.create(model=OPENAI_MODEL,input=prompt)
    parsed=parse_json_loose(resp.output_text)
    if not parsed:return jsonify({"error":"invalid_ai_response"}),502
    return jsonify(parsed)


@app.post("/api/ai/booking-intent")
@auth_required("staff")
def ai_booking_intent():
    d=request.get_json() or {}
    client=openai_client()
    if not client:
        return jsonify({"error":"ai_not_configured"}),503
    prompt=(
        "LINE本文から予約希望日候補を抽出してください。todayを基準に相対日付もYYYY-MM-DDへ変換。"
        "時刻があればtimesにも入れてください。JSONのみ。形式:{\"dates\":[\"YYYY-MM-DD\"],\"times\":[\"HH:MM\"]}\n"
        + json.dumps(d,ensure_ascii=False)
    )
    resp=client.responses.create(model=OPENAI_MODEL,input=prompt)
    parsed=parse_json_loose(resp.output_text)
    if not parsed:return jsonify({"error":"invalid_ai_response"}),502
    return jsonify(parsed)


@app.post("/api/ai/line-reply-draft")
@auth_required("staff")
def ai_line_reply_draft():
    d=request.get_json() or {}
    client=openai_client()
    if not client:
        return jsonify({"error":"ai_not_configured"}),503
    prompt=(
        "あなたは内装会社ACEのLINE返信作成AIです。受信内容、分類、顧客情報、施工履歴を踏まえ、"
        "担当者がそのまま編集して送れる短い返信下書きを日本語で作ってください。"
        "クレーム時は謝意と早期確認を優先し、責任の断定や補償の確約はしないでください。"
        "予約希望は候補日時の確認につなげ、質問は簡潔に回答または確認事項を返してください。"
        "JSONのみ。形式:{\"message\":\"\"}\n"
        + json.dumps(d,ensure_ascii=False)
    )
    resp=client.responses.create(model=OPENAI_MODEL,input=prompt)
    parsed=parse_json_loose(resp.output_text)
    if not parsed:return jsonify({"error":"invalid_ai_response"}),502
    return jsonify(parsed)

@app.post("/api/line/reply-to-user")
@auth_required("staff")
def line_reply_to_user():
    if not LINE_CHANNEL_ACCESS_TOKEN:
        return jsonify({"error":"line_not_configured"}),503
    d=request.get_json() or {}
    user_id=d.get("userId");message=d.get("message","").strip()
    if not user_id or not message:return jsonify({"error":"user_id_and_message_required"}),400
    r=requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Authorization":f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}","Content-Type":"application/json"},
        json={"to":user_id,"messages":[{"type":"text","text":message}]},
        timeout=20
    )
    if not (200 <= r.status_code < 300):
        return jsonify({"error":"line_send_failed","detail":r.text}),502
    return jsonify({"ok":True})


@app.post("/api/ai/line-classify")
@auth_required("staff")
def ai_line_classify():
    d=request.get_json() or {}
    client=openai_client()
    if not client:
        return jsonify({"error":"ai_not_configured"}),503
    prompt=(
        "あなたは内装会社ACEのLINE一次対応AIです。顧客返信を分類し、優先度と担当ロールを決めてください。"
        "categoryは booking|question|complaint|other、priorityは high|mid|low、assigneeは admin|manager|staff。"
        "クレームや施工不具合は原則high/manager、予約希望はmid/staff、通常質問はmid/staff。"
        "JSONのみ。形式:{\"category\":\"\",\"priority\":\"\",\"assignee\":\"\",\"summary\":\"\"}\n"
        + json.dumps(d,ensure_ascii=False)
    )
    resp=client.responses.create(model=OPENAI_MODEL,input=prompt)
    parsed=parse_json_loose(resp.output_text)
    if not parsed:return jsonify({"error":"invalid_ai_response"}),502
    return jsonify(parsed)


@app.post("/api/line/link-customer")
@auth_required("staff")
def link_line_customer():
    d=request.get_json() or {}
    user_id=d.get("userId");customer_id=d.get("customerId");display_name=d.get("displayName","")
    if not user_id or not customer_id:return jsonify({"error":"user_id_and_customer_id_required"}),400
    row=LineUserLink.query.filter_by(line_user_id=user_id).first()
    if not row:
        row=LineUserLink(line_user_id=user_id)
        db.session.add(row)
    row.customer_id=int(customer_id);row.display_name=display_name
    db.session.commit()
    return jsonify({"ok":True})


@app.get("/api/line/crm/messages")
@auth_required("staff")
def get_line_crm_messages():
    rows=LineCrmMessage.query.order_by(LineCrmMessage.received_at.desc()).limit(500).all()
    return jsonify([{
        "id":r.id,"eventId":r.event_id,"userId":r.line_user_id,"customerId":r.customer_id,
        "customerName":r.customer_name,"message":r.message,"category":r.category,
        "priority":r.priority,"assignee":r.assignee,"summary":r.summary,"status":r.status,
        "receivedAt":r.received_at.isoformat()
    } for r in rows])


@app.post("/api/ai/line-message")
@auth_required("staff")
def ai_line_message():
    d=request.get_json() or {}
    client=openai_client()
    if not client:
        return jsonify({"error":"ai_not_configured"}),503
    prompt=(
        "あなたは内装会社ACEの顧客LINE文面作成AIです。"
        "顧客マスター、施工履歴、会員状況、点検時期、支払状況、今回の連絡理由を参照し、"
        "顧客に合わせた自然で短いLINE文面を日本語で作ってください。"
        "押し売り感を避け、必要に応じて点検予約・補修相談・支払確認・再受注案内へつなげてください。"
        "個人情報を過剰に本文へ書かないでください。"
        "JSONのみ。形式:{\"message\":\"\"}\n"
        + json.dumps(d,ensure_ascii=False)
    )
    resp=client.responses.create(model=OPENAI_MODEL,input=prompt)
    parsed=parse_json_loose(resp.output_text)
    if not parsed:return jsonify({"error":"invalid_ai_response"}),502
    return jsonify(parsed)


@app.post("/api/ai/customer-next-action")
@auth_required("staff")
def customer_next_action():
    d=request.get_json() or {}
    client=openai_client()
    if not client:
        return jsonify({"error":"ai_not_configured"}),503
    prompt=(
        "あなたは内装会社ACEの顧客対応AIです。顧客情報・施工履歴・会員状況・点検時期・支払状況を見て、"
        "次に取るべき具体的なアクションを1つ、日本語で簡潔に提案してください。"
        "営業的に押しすぎず、点検・補修・再受注・LINE連絡のどれを優先するか判断してください。"
        "JSONのみ。形式:{\"action\":\"\"}\n"
        + json.dumps(d,ensure_ascii=False)
    )
    resp=client.responses.create(model=OPENAI_MODEL,input=prompt)
    parsed=parse_json_loose(resp.output_text)
    if not parsed:return jsonify({"error":"invalid_ai_response"}),502
    return jsonify(parsed)


@app.post("/api/ai/executive-summary")
@auth_required("manager")
def executive_summary():
    d=request.get_json() or {}
    client=openai_client()
    if not client:
        return jsonify({"error":"ai_not_configured"}),503
    prompt=(
        "あなたは内装会社ACEの経営補助AIです。月次指標を分析し、"
        "経営者が次に見るべきポイントと実行策を3〜5個、日本語で簡潔に出してください。"
        "売上、粗利、粗利率、工数、未決済、仕入遅延、在庫、材料循環を横断して考えてください。"
        "JSONのみ。形式:{\"summary\":\"\",\"actions\":[\"\"]}\n"
        + json.dumps(d,ensure_ascii=False)
    )
    resp=client.responses.create(model=OPENAI_MODEL,input=prompt)
    parsed=parse_json_loose(resp.output_text)
    if not parsed:return jsonify({"error":"invalid_ai_response"}),502
    return jsonify(parsed)


@app.post("/api/ai/circular-improvement")
@auth_required("manager")
def circular_improvement():
    d=request.get_json() or {}
    client=openai_client()
    if not client:
        return jsonify({"error":"ai_not_configured"}),503
    prompt=(
        "あなたは内装会社ACEの業務改善AIです。材料循環KPIを分析し、"
        "現場で実行できる具体的な改善策を3〜5個、日本語で簡潔に出してください。"
        "一般論ではなく、端材登録・板取り・保管・再利用の運用に結びつけてください。"
        "JSONのみ。形式:{\"summary\":\"\",\"actions\":[\"\"]}\n"
        + json.dumps(d,ensure_ascii=False)
    )
    resp=client.responses.create(model=OPENAI_MODEL,input=prompt)
    parsed=parse_json_loose(resp.output_text)
    if not parsed:return jsonify({"error":"invalid_ai_response"}),502
    return jsonify(parsed)


@app.post("/api/ai/receipt")
@auth_required("staff")
def analyze_receipt():
    d=request.get_json() or {}
    image=d.get("image")
    client=openai_client()
    if not client:
        return jsonify({"error":"ai_not_configured"}),503
    content=[
      {"type":"input_text","text":"領収書画像から見える範囲だけを読み取り、JSONのみ返してください。形式: {\"date\":\"YYYY-MM-DD\",\"amount\":0,\"note\":\"摘要\",\"category\":\"material|outsource|transport|other\"}。不明な値は空文字または0にしてください。"},
      {"type":"input_image","image_url":image}
    ]
    resp=client.responses.create(model=OPENAI_MODEL,input=[{"role":"user","content":content}])
    parsed=parse_json_loose(resp.output_text)
    if not parsed:return jsonify({"error":"invalid_ai_response"}),502
    return jsonify(parsed)


@app.post("/api/line/push")
@auth_required("manager")
def line_push():
    if not LINE_CHANNEL_ACCESS_TOKEN:
        return jsonify({"error":"line_not_configured"}),503
    d=request.get_json() or {}
    r=requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Authorization":f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}","Content-Type":"application/json"},
        json={"to":d["userId"],"messages":[{"type":"text","text":d["text"]}]},
        timeout=20
    )
    return jsonify({"status":r.status_code,"body":r.text}),r.status_code

@app.post("/webhook")
def line_webhook():
    body=request.get_data();sig=request.headers.get("X-Line-Signature","")
    if not LINE_CHANNEL_SECRET:return "not configured",503
    digest=hmac.new(LINE_CHANNEL_SECRET.encode(),body,hashlib.sha256).digest()
    expected=base64.b64encode(digest).decode()
    if not hmac.compare_digest(expected,sig):return "invalid signature",400
    payload=request.get_json(silent=True) or {}
    for event in payload.get("events",[]):
        if event.get("type")=="message" and (event.get("message") or {}).get("type")=="text":
            source=(event.get("source") or {}).get("userId","")
            body_text=event["message"]["text"]
            db.session.add(Message(project_id=None,body=f"LINE {source}: {body_text}",source="LINE"))
    db.session.commit();return "OK",200

@app.post("/api/payment/sync")
@auth_required("manager")
def payment_sync():
    rows=[]
    for m in Membership.query.all():
        if PAYMENT_SECRET_KEY:
            if not m.payment_status:m.payment_status="正常"
            if not m.billing_status:m.billing_status="自動請求"
        rows.append({"customerId":m.customer_id,"paymentStatus":m.payment_status,
                     "memberStatus":m.member_status,"billingStatus":m.billing_status})
    db.session.commit();return jsonify({"memberships":rows})





@app.post("/api/line/webhook")
def line_webhook():
    body=request.get_data(as_text=True)
    signature=request.headers.get("X-Line-Signature","")
    if LINE_CHANNEL_SECRET:
        digest=hmac.new(LINE_CHANNEL_SECRET.encode(),body.encode(),hashlib.sha256).digest()
        expected=base64.b64encode(digest).decode()
        if not hmac.compare_digest(signature,expected):
            return "invalid signature",401
    payload=request.get_json() or {}
    for ev in payload.get("events",[]):
        if ev.get("type")!="message":continue
        msg=ev.get("message") or {}
        if msg.get("type")!="text":continue
        source=ev.get("source") or {}
        user_id=source.get("userId","")
        event_id=ev.get("webhookEventId") or f"{user_id}:{ev.get('timestamp')}:{msg.get('id')}"
        if LineCrmMessage.query.filter_by(event_id=event_id).first():
            continue
        link=LineUserLink.query.filter_by(line_user_id=user_id).first()
        customer_id=link.customer_id if link else None
        customer_name=link.display_name if link else ""
        text_message=msg.get("text","")
        category="other";priority="mid";assignee="staff";summary=""
        client=openai_client()
        if client:
            try:
                prompt=(
                    "LINE返信を分類。category booking|question|complaint|other、priority high|mid|low、"
                    "assignee admin|manager|staff。JSONのみ。本文:"+text_message
                )
                resp=client.responses.create(model=OPENAI_MODEL,input=prompt)
                parsed=parse_json_loose(resp.output_text) or {}
                category=parsed.get("category",category);priority=parsed.get("priority",priority)
                assignee=parsed.get("assignee",assignee);summary=parsed.get("summary","")
            except Exception:
                pass
        row=LineCrmMessage(
            event_id=event_id,line_user_id=user_id,customer_id=customer_id,customer_name=customer_name or user_id,
            message=text_message,category=category,priority=priority,assignee=assignee,summary=summary,status="open",
            received_at=datetime.datetime.utcfromtimestamp((ev.get("timestamp") or int(datetime.datetime.utcnow().timestamp()*1000))/1000)
        )
        db.session.add(row)
    db.session.commit()
    return "ok"


@app.post("/api/notifications/deliver-line")
@auth_required("manager")
def deliver_notification_line():
    if not LINE_CHANNEL_ACCESS_TOKEN:
        return jsonify({"error":"line_not_configured"}),503
    d=request.get_json() or {}
    text=f"{d.get('title','')}\n{d.get('text','')}".strip()
    user_id=d.get("userId")
    if not user_id:return jsonify({"error":"user_id_required"}),400
    try:
        r=requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={
                "Authorization":f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
                "Content-Type":"application/json"
            },
            json={"to":user_id,"messages":[{"type":"text","text":text}]},
            timeout=20
        )
        if r.status_code < 200 or r.status_code >= 300:
            db.session.add(DeliveryLog(queue_id=str(d.get("queueId","")),channel="line",
                recipient=user_id,status="failed",error=r.text))
            db.session.commit()
            return jsonify({"error":"line_send_failed","detail":r.text}),502
        db.session.add(DeliveryLog(queue_id=str(d.get("queueId","")),channel="line",
            recipient=user_id,status="sent",error=""))
        db.session.commit()
        return jsonify({"ok":True,"status":r.status_code})
    except Exception as e:
        db.session.add(DeliveryLog(queue_id=str(d.get("queueId","")),channel="line",
            recipient=user_id,status="failed",error=str(e)))
        db.session.commit()
        return jsonify({"error":"exception","detail":str(e)}),500

@app.get("/api/delivery-logs")
@auth_required("manager")
def get_delivery_logs():
    rows=DeliveryLog.query.order_by(DeliveryLog.created_at.desc()).limit(500).all()
    return jsonify([{
        "id":r.id,"queueId":r.queue_id,"channel":r.channel,"recipient":r.recipient,
        "status":r.status,"error":r.error,"createdAt":r.created_at.isoformat()
    } for r in rows])


@app.get("/api/notification-rules")
@auth_required("admin")
def get_notification_rules():
    return jsonify([{"id":r.id,"name":r.name,"minPriority":r.min_priority,"channel":r.channel,"assignee":r.assignee,"start":r.start_time,"end":r.end_time,"enabled":bool(r.enabled)} for r in NotificationRule.query.all()])

@app.post("/api/notification-rules")
@auth_required("admin")
def post_notification_rule():
    d=request.get_json() or {}
    r=NotificationRule(name=d.get("name"),min_priority=d.get("minPriority"),channel=d.get("channel"),assignee=d.get("assignee"),start_time=d.get("start"),end_time=d.get("end"),enabled=bool(d.get("enabled",True)))
    db.session.add(r);db.session.commit()
    return jsonify({"id":r.id}),201

@app.get("/api/notification-queue")
@auth_required("manager")
def get_notification_queue():
    rows=NotificationQueue.query.order_by(NotificationQueue.created_at.desc()).limit(500).all()
    return jsonify([{"id":r.id,"key":r.notification_key,"title":r.title,"text":r.text,"priority":r.priority,"channel":r.channel,"assignee":r.assignee,"status":r.status,"createdAt":r.created_at.isoformat()} for r in rows])


@app.get("/api/audit")
@auth_required("admin")
def get_audit():
    rows=AuditLog.query.order_by(AuditLog.created_at.desc()).limit(1000).all()
    return jsonify([{"id":r.id,"at":r.created_at.isoformat(),"user":r.user_name,"type":r.type,"target":r.target,"action":r.action,"detail":r.detail} for r in rows])

@app.post("/api/audit")
@auth_required("staff")
def post_audit():
    d=request.get_json() or {}
    r=AuditLog(user_name=g.user.name,type=d.get("type"),target=d.get("target"),action=d.get("action"),detail=d.get("detail"))
    db.session.add(r);db.session.commit()
    return jsonify({"id":r.id}),201


@app.get("/api/users")
@auth_required("admin")
def get_users():
    return jsonify([{"id":u.id,"name":u.name,"email":u.email,"role":u.role} for u in User.query.order_by(User.id).all()])

@app.post("/api/users")
@auth_required("admin")
def create_user():
    d=request.get_json() or {}
    if User.query.filter_by(email=d["email"].lower()).first():return jsonify({"error":"email_exists"}),409
    u=User(name=d["name"],email=d["email"].lower(),password_hash=generate_password_hash(d.get("password","demo")),role=d.get("role","staff"))
    db.session.add(u);db.session.commit();return jsonify({"id":u.id}),201

@app.patch("/api/users/<int:user_id>/role")
@auth_required("admin")
def update_role(user_id):
    d=request.get_json() or {};role=d.get("role")
    if role not in ROLE_ORDER:return jsonify({"error":"invalid_role"}),400
    u=db.session.get(User,user_id)
    if not u:return jsonify({"error":"not_found"}),404
    u.role=role;db.session.commit();return jsonify({"ok":True})

@app.get("/api/customers")
@auth_required("viewer")
def get_customers():
    return jsonify([{"id":r.id,"type":r.type,"name":r.name,"phone":r.phone,"address":r.address,"management":r.management,"owner":r.owner,"floor":r.floor,"warranty":r.warranty,"memberStatus":r.member_status,"next":r.next_inspection,"ref":r.referrer,"archived":bool(r.archived)} for r in Customer.query.all()])

@app.post("/api/customers")
@auth_required("manager")
def post_customer():
    d=request.get_json() or {}
    if d.get("id") and db.session.get(Customer,d["id"]):return jsonify({"id":d["id"],"exists":True})
    r=Customer(id=d.get("id"),type=d.get("type","個人"),name=d["name"],phone=d.get("phone"),address=d.get("address"),management=d.get("management"),owner=d.get("owner"),floor=d.get("floor"),warranty=d.get("warranty"),member_status=d.get("memberStatus"),next_inspection=d.get("next"),referrer=d.get("ref"),archived=bool(d.get("archived",False)))
    db.session.add(r);db.session.commit();return jsonify({"id":r.id}),201


@app.patch("/api/customers/<int:customer_id>")
@auth_required("manager")
def patch_customer(customer_id):
    c=db.session.get(Customer,customer_id)
    if not c:return jsonify({"error":"not_found"}),404
    d=request.get_json() or {}
    for key,attr in {
        "name":"name","phone":"phone","address":"address","management":"management",
        "owner":"owner","floor":"floor","warranty":"warranty","archived":"archived"
    }.items():
        if key in d:setattr(c,attr,d[key])
    db.session.commit()
    return jsonify({"ok":True})


@app.get("/api/projects")
@auth_required("viewer")
def get_projects():
    return jsonify([{"id":r.id,"customerId":r.customer_id,"name":r.name,"address":r.address,"status":r.status,"progress":r.progress,"owner":r.owner,"archived":bool(r.archived)} for r in Project.query.all()])

@app.post("/api/projects")
@auth_required("staff")
def post_project():
    d=request.get_json() or {}
    if d.get("id") and db.session.get(Project,d["id"]):return jsonify({"id":d["id"],"exists":True})
    r=Project(id=d.get("id"),customer_id=d.get("customerId"),name=d["name"],address=d.get("address"),status=d.get("status","予定"),progress=d.get("progress","搬入前"),owner=d.get("owner"),archived=bool(d.get("archived",False)))
    db.session.add(r);db.session.commit();return jsonify({"id":r.id}),201


@app.patch("/api/projects/<int:project_id>")
@auth_required("staff")
def patch_project(project_id):
    p=db.session.get(Project,project_id)
    if not p:return jsonify({"error":"not_found"}),404
    d=request.get_json() or {}
    for key,attr in {
        "name":"name","address":"address","owner":"owner",
        "status":"status","progress":"progress","archived":"archived"
    }.items():
        if key in d:setattr(p,attr,d[key])
    db.session.commit()
    return jsonify({"ok":True})


@app.get("/api/bookings")
@auth_required("viewer")
def get_bookings():
    return jsonify([{"id":r.id,"customerId":r.customer_id,"projectId":r.project_id,"name":r.name,"phone":r.phone,"date":r.date,"time":r.time,"address":r.address,"note":r.note,"status":r.status} for r in Booking.query.all()])

@app.post("/api/bookings")
@auth_required("manager")
def post_booking():
    d=request.get_json() or {}
    if d.get("id") and db.session.get(Booking,d["id"]):return jsonify({"id":d["id"],"exists":True})
    r=Booking(id=d.get("id"),customer_id=d.get("customerId"),project_id=d.get("projectId"),name=d.get("name"),phone=d.get("phone"),date=d.get("date"),time=d.get("time"),address=d.get("address"),note=d.get("note"),status=d.get("status"))
    db.session.add(r);db.session.commit();return jsonify({"id":r.id}),201

@app.get("/api/memberships")
@auth_required("viewer")
def get_memberships():
    return jsonify([{"customerId":r.customer_id,"memberStatus":r.member_status,"plan":r.plan,"contractStart":r.contract_start,"renewDate":r.renew_date,"billingStatus":r.billing_status,"paymentStatus":r.payment_status,"cancelDate":r.cancel_date,"repairBalance":r.repair_balance} for r in Membership.query.all()])

@app.post("/api/memberships")
@auth_required("manager")
def post_membership():
    d=request.get_json() or {}
    r=Membership.query.filter_by(customer_id=d["customerId"]).first()
    if not r:r=Membership(customer_id=d["customerId"]);db.session.add(r)
    r.member_status=d.get("memberStatus");r.plan=d.get("plan");r.contract_start=d.get("contractStart");r.renew_date=d.get("renewDate");r.billing_status=d.get("billingStatus");r.payment_status=d.get("paymentStatus");r.cancel_date=d.get("cancelDate");r.repair_balance=d.get("repairBalance")
    db.session.commit();return jsonify({"ok":True})

@app.get("/api/messages")
@auth_required("viewer")
def get_messages():
    return jsonify([{"id":r.id,"projectId":r.project_id,"body":r.body,"source":r.source} for r in Message.query.all()])

@app.post("/api/messages")
@auth_required("staff")
def post_message():
    d=request.get_json() or {}
    exists=Message.query.filter_by(project_id=d.get("projectId"),body=d.get("body"),source=d.get("source","ACE")).first()
    if exists:return jsonify({"id":exists.id,"exists":True})
    r=Message(project_id=d.get("projectId"),body=d.get("body"),source=d.get("source","ACE"))
    db.session.add(r);db.session.commit();return jsonify({"id":r.id}),201

@app.get("/api/photos")
@auth_required("viewer")
def get_photos():
    return jsonify([{"id":r.id,"customerId":r.customer_id,"projectId":r.project_id,"category":r.category,"name":r.name,"url":f"/uploads/{r.filename}"} for r in Photo.query.all()])

@app.post("/api/photos/upload")
@auth_required("staff")
def upload_photo():
    file=request.files.get("file")
    if not file:return jsonify({"error":"file required"}),400
    kind=request.form.get("category","資料");target_type=request.form.get("targetType");target_id=int(request.form.get("targetId"))
    ext=os.path.splitext(secure_filename(file.filename))[1].lower() or ".jpg"
    filename=f"{uuid.uuid4().hex}{ext}";file.save(os.path.join(UPLOAD_DIR,filename))
    r=Photo(category=kind,name=file.filename,filename=filename)
    if target_type=="project":r.project_id=target_id
    else:r.customer_id=target_id
    db.session.add(r);db.session.commit();return jsonify({"id":r.id,"url":f"/uploads/{filename}"}),201

@app.get("/uploads/<path:filename>")
def uploaded(filename):return send_from_directory(UPLOAD_DIR,filename)

def bootstrap():
    if not User.query.filter_by(email="admin@ace.local").first():
        db.session.add(User(name="ACE 管理者",email="admin@ace.local",password_hash=generate_password_hash("demo"),role="admin"))
        db.session.commit()

with app.app_context():
    _initialize_production_once()

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","8000")),debug=False)
