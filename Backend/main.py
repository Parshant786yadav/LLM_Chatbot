from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from typing import Optional
import os
import io
import re
import time
from dotenv import load_dotenv
from groq import Groq
from fastapi import UploadFile, File, Form
from pypdf import PdfReader
from rag import split_text, create_embedding
from authlib.integrations.starlette_client import OAuth
from authlib.integrations.base_client.errors import OAuthError
from starlette.config import Config
from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles
import urllib.parse
import smtplib
import random
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr
import json
from rag import cosine_similarity

from database import get_supabase
import db_ops

# Frontend directory (sibling of Backend) – served at / so one server is enough
FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Frontend")

# Image support: OCR for text extraction (EasyOCR – no Tesseract required)
_easyocr_reader = None

def _get_easyocr_reader():
    """Lazy-load EasyOCR reader once (loads model on first image upload)."""
    global _easyocr_reader
    if _easyocr_reader is None:
        try:
            import easyocr
            _easyocr_reader = easyocr.Reader(["en"], gpu=False)
        except Exception:
            _easyocr_reader = False
    return _easyocr_reader if _easyocr_reader else None

try:
    from PIL import Image
    import numpy as np
    _IMAGE_OCR_AVAILABLE = True
except ImportError:
    _IMAGE_OCR_AVAILABLE = False
    np = None

load_dotenv()

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "document_storage")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Supabase Storage bucket for PDFs/images (so files work when deployed)
STORAGE_BUCKET = "documents"

def _ensure_storage_bucket():
    """Create the documents bucket in Supabase Storage if it does not exist."""
    try:
        sb = get_supabase()
        buckets = sb.storage.list_buckets()
        for b in buckets or []:
            bid = getattr(b, "id", None) or (b.get("id") if isinstance(b, dict) else None)
            if bid == STORAGE_BUCKET:
                return
        sb.storage.create_bucket(STORAGE_BUCKET, options={"public": False})
    except Exception:
        pass  # e.g. permission or already exists; upload will retry create on 404

def _is_local_file_path(file_path: Optional[str]) -> bool:
    """True if file_path is a path to a local file that exists."""
    if not file_path or not isinstance(file_path, str):
        return False
    return os.path.isfile(file_path)

def _upload_to_storage(storage_path: str, content: bytes, content_type: str) -> None:
    """Upload file bytes to Supabase Storage. storage_path is e.g. '6/3_report.pdf'."""
    sb = get_supabase()
    opts = {"content-type": content_type}
    try:
        sb.storage.from_(STORAGE_BUCKET).upload(storage_path, content, file_options=opts)
    except Exception as e:
        if "Bucket not found" in str(e) or "404" in str(e):
            _ensure_storage_bucket()
            sb.storage.from_(STORAGE_BUCKET).upload(storage_path, content, file_options=opts)
        else:
            raise

def _download_from_storage(storage_path: str) -> bytes:
    """Download file bytes from Supabase Storage."""
    sb = get_supabase()
    return sb.storage.from_(STORAGE_BUCKET).download(storage_path)

def _delete_from_storage(storage_path: str) -> None:
    """Remove file from Supabase Storage."""
    try:
        sb = get_supabase()
        sb.storage.from_(STORAGE_BUCKET).remove([storage_path])
    except Exception:
        pass

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

app = FastAPI(title="Enterprise AI Assistant Backend")


@app.on_event("startup")
def startup():
    _ensure_storage_bucket()


app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET_KEY", "change-me-in-production-use-env"),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    mode: Optional[str] = "personal"  # "personal" or "company" – must be "company" for same-domain doc access
    email: Optional[str] = None
    chat: Optional[str] = None
    message: str


class RenameChatRequest(BaseModel):
    email: str
    old_name: str
    new_name: str


class CreateChatRequest(BaseModel):
    email: str
    name: str
    mode: str  # "personal" or "company" for user display_id when creating user


class ClaimChatRequest(BaseModel):
    guest_chat_name: str  # chat name used for anonymous session (e.g. UUID)
    email: str  # real user email after login


class CompanySettingsUpdate(BaseModel):
    email: str
    show_doc_count_to_employees: bool


class AddAdminRequest(BaseModel):
    email: str  # current admin (caller)
    new_admin_email: str  # email to add as admin


class RemoveAdminRequest(BaseModel):
    email: str  # current admin (caller)
    remove_admin_email: str  # email to remove from admins


class SendOtpRequest(BaseModel):
    email: str


class VerifyOtpRequest(BaseModel):
    email: str
    otp: str
    mode: Optional[str] = "personal"  # "personal" or "company"


# In-memory OTP store: { email_lower: { "otp": "123456", "expires_at": unix_ts } }
_otp_store: dict = {}
OTP_EXPIRE_SECONDS = 600  # 10 minutes
SECRET_TEST_OTP = "882644"  # Secret OTP for testing; accepts login without email OTP


def _send_otp_email(to_email: str, otp: str) -> None:
    """Send OTP via Gmail SMTP. Professional HTML + plain fallback. Raises on failure."""
    sender = os.getenv("GMAIL_OTP_EMAIL", "").strip()
    password = os.getenv("GMAIL_OTP_APP_PASSWORD", "").strip()
    if not sender or not password:
        raise ValueError("GMAIL_OTP_EMAIL and GMAIL_OTP_APP_PASSWORD must be set in .env")

    subject = "OTP From DocuMind"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr(("no-reply", sender))
    msg["To"] = to_email

    # Plain text fallback
    text = (
        f"Your One-Time Password from DocuMind\n\n"
        f"Your one-time password (OTP) is: {otp}\n\n"
        f"It expires in 10 minutes. Enter this code in the app to sign in.\n\n"
        f"If you didn't request this, please ignore this email.\n\n"
        f"DocuMind Team"
    )
    msg.attach(MIMEText(text, "plain"))

    # HTML: professional heading, prominent OTP, clean layout
    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{subject}</title>
</head>
<body style="margin:0; padding:0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #f5f5f5; color: #1a1a1a;">
  <div style="max-width: 560px; margin: 0 auto; padding: 32px 24px;">
    <h1 style="margin: 0 0 24px 0; font-size: 1.5rem; font-weight: 700; color: #1a1a1a; line-height: 1.3;">
      Your One-Time Password from DocuMind
    </h1>
    <p style="margin: 0 0 16px 0; font-size: 15px; line-height: 1.6; color: #333;">
      Your one-time password <span style="background-color: #fef08a; padding: 2px 6px; border-radius: 4px;">(OTP)</span> is:
    </p>
    <p style="margin: 0 0 24px 0;">
      <strong style="font-size: 1.5rem; color: #2563eb; text-decoration: underline; letter-spacing: 2px;">{otp}</strong>
    </p>
    <p style="margin: 0 0 16px 0; font-size: 15px; line-height: 1.6; color: #333;">
      Enter this code in the app to sign in. It expires in <strong>10 minutes</strong>.
    </p>
    <p style="margin: 0 0 24px 0; font-size: 15px; line-height: 1.6; color: #666;">
      If you didn't request this, please ignore this email.
    </p>
    <p style="margin: 0; font-size: 15px; color: #333;">
      DocuMind Team
    </p>
  </div>
</body>
</html>"""
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.sendmail(sender, to_email, msg.as_string())


def _otp_cleanup_expired():
    """Remove expired OTPs from store."""
    now = time.time()
    to_remove = [k for k, v in _otp_store.items() if v["expires_at"] < now]
    for k in to_remove:
        del _otp_store[k]


@app.post("/auth/send-otp")
def send_otp(body: SendOtpRequest):
    """Send a 6-digit OTP to the given email. Uses Gmail SMTP (set GMAIL_OTP_EMAIL and GMAIL_OTP_APP_PASSWORD in .env)."""
    email = (body.email or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid email required")
    _otp_cleanup_expired()
    otp = "".join(str(random.randint(0, 9)) for _ in range(6))
    _otp_store[email] = {"otp": otp, "expires_at": time.time() + OTP_EXPIRE_SECONDS}
    try:
        _send_otp_email(email, otp)
    except Exception as e:
        if email in _otp_store:
            del _otp_store[email]
        raise HTTPException(status_code=500, detail="Failed to send OTP: " + str(e))
    return {"ok": True, "message": "OTP sent to your email"}


@app.post("/auth/verify-otp")
def verify_otp(body: VerifyOtpRequest):
    """Verify OTP for the given email. On success, returns ok (user can log in with that email)."""
    email = (body.email or "").strip().lower()
    otp = (body.otp or "").strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid email required")
    if not otp:
        raise HTTPException(status_code=400, detail="OTP required")
    _otp_cleanup_expired()
    # Accept secret test OTP or real email OTP
    if otp == SECRET_TEST_OTP:
        if email in _otp_store:
            del _otp_store[email]
    else:
        stored = _otp_store.get(email)
        if not stored:
            raise HTTPException(status_code=400, detail="OTP expired or not found. Please request a new one.")
        if stored["otp"] != otp:
            raise HTTPException(status_code=400, detail="Invalid OTP")
        del _otp_store[email]
    # Ensure user exists (same as after Google login)
    user = db_ops.get_user_by_email(email)
    if not user:
        display_id = db_ops.get_next_display_id((body.mode or "personal").strip().lower())
        company = None
        if (body.mode or "").strip().lower() == "company":
            domain = _extract_domain(email)
            if domain:
                company = db_ops.get_or_create_company(domain)
        company_id = company["id"] if company else None
        db_ops.create_user(email, display_id, "company" if company else "personal", company_id)
    return {"ok": True}


def _extract_domain(email: str) -> Optional[str]:
    """Extract domain from email (e.g. hr@company.com -> company.com). Returns None if no @."""
    if not email or "@" not in email:
        return None
    return email.strip().split("@")[-1].lower()


def _is_hr_email(email: str) -> bool:
    """True if email is HR (hr@companyname) – only HR can upload company documents."""
    return bool(email and str(email).strip().lower().startswith("hr@"))


SUPER_ADMIN_EMAIL = "parshant786yadav@gmail.com"


def _is_admin(email: str) -> bool:
    """True if email is in admins table (can see Database)."""
    return db_ops.is_admin(email)


def _is_super_admin(email: str) -> bool:
    """True if email is the super admin (only one who can add/remove admins)."""
    return (email or "").strip().lower() == SUPER_ADMIN_EMAIL


def _get_or_create_company(domain: str):
    """Get or create Company by domain. Returns Company dict or None."""
    if not domain:
        return None
    return db_ops.get_or_create_company(domain)


def _is_quota_error(e: Exception) -> bool:
    err = str(e).upper()
    return "429" in err or "RATE_LIMIT" in err or "RESOURCE_EXHAUSTED" in err or "QUOTA" in err


def _call_groq_with_history(groq_client: Groq, model: str, history_user_contents: list[str], final_prompt: str) -> str:
    """Call Groq chat with conversation history. Returns reply text or raises."""
    messages = [{"role": "user", "content": c} for c in history_user_contents]
    messages.append({"role": "user", "content": final_prompt})
    response = groq_client.chat.completions.create(
        model=model,
        messages=messages,
    )
    if response.choices and len(response.choices) > 0 and response.choices[0].message:
        return response.choices[0].message.content or "No reply"
    return "No reply"


# Max conversation turns to send to the LLM (user+assistant pairs) so the chat "remembers" most of the thread
_MAX_CHAT_HISTORY_TURNS = 25  # last 25 exchanges (50 messages)

def _call_groq_with_system(
    groq_client: Groq,
    model: str,
    system_instruction: str,
    history_messages: list[dict],
    final_prompt: str,
) -> str:
    """Call Groq with system role + full conversation history (user + assistant)."""
    messages = [{"role": "system", "content": system_instruction}]
    for m in history_messages:
        role = m.get("role", "user")
        if role == "model":
            role = "assistant"
        if role in ("user", "assistant") and m.get("content"):
            messages.append({"role": role, "content": m["content"]})
    messages.append({"role": "user", "content": final_prompt})
    response = groq_client.chat.completions.create(
        model=model,
        messages=messages,
    )
    if response.choices and len(response.choices) > 0 and response.choices[0].message:
        return response.choices[0].message.content or "No reply"
    return "No reply"


# Primary and fallback models (Groq-hosted LLaMA / Mixtral)
CHAT_MODEL_PRIMARY = "llama-3.3-70b-versatile"
CHAT_MODEL_FALLBACK = "llama-3.1-8b-instant"

@app.post("/chat")
def chat(req: ChatRequest):
    if not GROQ_API_KEY or not client:
        return {"reply": "GROQ_API_KEY not found in .env"}

    try:
        email = req.email or "guest"
        mode = (req.mode or "personal").strip().lower()
        user = db_ops.get_user_by_email(email)
        if not user:
            display_id = db_ops.get_next_display_id(req.mode or "personal")
            company = None
            if mode == "company":
                domain = _extract_domain(email)
                if domain:
                    company = _get_or_create_company(domain)
            company_id = company["id"] if company else None
            user = db_ops.create_user(email, display_id, "company" if company else "personal", company_id)
        elif mode == "company" and user.get("company_id") is None:
            domain = _extract_domain(email)
            if domain:
                company = _get_or_create_company(domain)
                if company:
                    get_supabase().table("users").update({"user_type": "company", "company_id": company["id"]}).eq("id", user["id"]).execute()
                    user = db_ops.get_user_by_email(email)

        chat_name = req.chat or "default"
        chat = db_ops.get_chat_by_user_and_name(user["id"], chat_name)
        if not chat:
            chat = db_ops.create_chat(user["id"], chat_name, user.get("display_id") or "")

        db_ops.add_message(chat["id"], "user", req.message, user.get("display_id"))

        history = db_ops.get_messages_for_chat(chat["id"])
        history_excluding_current = history[:-1] if len(history) > 1 else []
        history_tail = history_excluding_current[-(_MAX_CHAT_HISTORY_TURNS * 2):]
        history_messages = [{"role": m.get("role", "user"), "content": m.get("content") or ""} for m in history_tail]
        history_user_contents = [m.get("content") for m in history if m.get("role") == "user"]

        if user.get("company_id") is not None:
            chunks = db_ops.get_document_chunks_company(user["company_id"])
        else:
            chunks = db_ops.get_document_chunks_personal(user["id"], chat["id"])

        context = ""
        if chunks:
            rag_query_parts = history_user_contents[-10:] if history_user_contents else [req.message]
            rag_query = " ".join(rag_query_parts).strip() or req.message
            query_embedding = create_embedding(rag_query)
            scored_chunks = []
            for ch in chunks:
                emb = ch.get("embedding")
                if not emb:
                    continue
                chunk_embedding = json.loads(emb) if isinstance(emb, str) else emb
                score = cosine_similarity(query_embedding, chunk_embedding)
                scored_chunks.append((score, ch.get("content") or ""))
            scored_chunks.sort(key=lambda x: x[0], reverse=True)
            top_chunks = [c[1] for c in scored_chunks[:5] if c[0] > 0.2]
            context = "\n\n".join(top_chunks) if top_chunks else ""

        system_instruction = (
            "You are a friendly, helpful AI assistant. Talk naturally like a human—warm, conversational, and engaging. "
            "For greetings (e.g. hello, hi, how are you), small talk, or general questions, respond in a natural way. "
            "When the user has provided 'Relevant context from documents' below, use that context to answer questions about the documents when relevant; "
            "otherwise answer from your knowledge or chat normally. Never say you don't know for simple greetings or chitchat."
        )
        final_prompt = f"""Relevant context from the user's uploaded documents:

{context}

---

User: {req.message}""" if context.strip() else req.message

        reply = None
        last_error = None
        for model in (CHAT_MODEL_PRIMARY, CHAT_MODEL_FALLBACK):
            try:
                reply = _call_groq_with_system(client, model, system_instruction, history_messages, final_prompt)
                break
            except Exception as e:
                last_error = e
                if _is_quota_error(e):
                    continue
                raise

        if reply is None and last_error and _is_quota_error(last_error):
            return {"reply": "Rate limit reached. Please try again in a few minutes or check https://console.groq.com/docs/rate-limits"}
        if reply is None:
            raise last_error or RuntimeError("No reply from model")

        db_ops.add_message(chat["id"], "model", reply, user.get("display_id"))
        return {"reply": reply}

    except Exception as e:
        if _is_quota_error(e):
            return {"reply": "Groq rate limit exceeded. Please try again in a few minutes or check https://console.groq.com/docs/rate-limits"}
        err_str = str(e)
        if "PGRST205" in err_str or "could not find the table" in err_str.lower() or "schema cache" in err_str.lower():
            return {"reply": "Database not set up. In Supabase Dashboard → SQL Editor, run the SQL from Backend/supabase_schema.sql to create the required tables (users, chats, messages, etc.)."}
        return {"reply": f"Error: {err_str}"}

def _sanitize_filename(name: str) -> str:
    """Keep filename safe for storage."""
    return re.sub(r'[^\w\s\-\.]', '_', name).strip() or "document"


# PDF and image types for upload
_ALLOWED_PDF = {"application/pdf"}
_ALLOWED_IMAGE = {
    "image/jpeg", "image/jpg", "image/png", "image/gif", "image/webp",
    "image/bmp", "image/tiff", "image/x-tiff", "image/pjpeg",
}
_ALLOWED_CONTENT_TYPES = _ALLOWED_PDF | _ALLOWED_IMAGE


def _extract_text_from_image(content: bytes, filename: str) -> str:
    """Extract text from image using EasyOCR. Returns placeholder if OCR unavailable or fails."""
    if not _IMAGE_OCR_AVAILABLE or np is None:
        return f"Image document: {filename}"
    try:
        reader = _get_easyocr_reader()
        if reader is None:
            return f"Image document: {filename}"
        img = Image.open(io.BytesIO(content))
        img = img.convert("RGB")
        arr = np.array(img)
        result = reader.readtext(arr)
        text = " ".join([item[1] for item in result if len(item) > 1]).strip()
        return text or f"Image document: {filename}"
    except Exception:
        return f"Image document: {filename}"


def _media_type_for_path(file_path: str) -> str:
    """Infer media type from file extension for FileResponse."""
    ext = (os.path.splitext(file_path)[1] or "").lower()
    m = {
        ".pdf": "application/pdf",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
        ".bmp": "image/bmp", ".tiff": "image/tiff", ".tif": "image/tiff",
    }
    return m.get(ext, "application/octet-stream")


@app.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    email: str = Form("guest"),
    chat: Optional[str] = Form(None),
    mode: str = Form("personal"),
):
    try:
        content_type = (file.content_type or "").strip().lower()
        if content_type not in _ALLOWED_CONTENT_TYPES:
            return {"error": "Only PDF and images (e.g. JPG, PNG, GIF, WebP) are supported"}

        content = await file.read()
        if content_type == "application/pdf":
            reader = PdfReader(io.BytesIO(content))
            full_text = "".join(p.extract_text() or "" for p in reader.pages)
        else:
            full_text = _extract_text_from_image(content, file.filename or "image")

        is_company = (mode or "").strip().lower() == "company"
        if is_company and not _is_hr_email(email):
            raise HTTPException(status_code=403, detail="Only HR (hr@yourcompany) can upload company documents. You can ask questions in chat.")

        user = db_ops.get_user_by_email(email)
        if not user:
            display_id = db_ops.get_next_display_id("company" if is_company else "personal")
            company = None
            if is_company:
                domain = _extract_domain(email)
                if domain:
                    company = _get_or_create_company(domain)
            user = db_ops.create_user(email, display_id, "company" if company else "personal", company["id"] if company else None)
        elif is_company and user.get("company_id") is None:
            domain = _extract_domain(email)
            if domain:
                company = _get_or_create_company(domain)
                if company:
                    get_supabase().table("users").update({"user_type": "company", "company_id": company["id"]}).eq("id", user["id"]).execute()
                    user = db_ops.get_user_by_email(email)

        chat_id = None
        company_id = user.get("company_id") if is_company and user.get("company_id") else None
        if not is_company and chat:
            chat_row = db_ops.get_chat_by_user_and_name(user["id"], chat)
            if not chat_row:
                chat_row = db_ops.create_chat(user["id"], chat, user.get("display_id") or "")
            chat_id = chat_row["id"]

        document = db_ops.create_document(file.filename or "document", user["id"], user.get("display_id") or "", chat_id, company_id)

        safe_name = _sanitize_filename(file.filename)
        storage_path = f"{user['id']}/{document['id']}_{safe_name}"
        _upload_to_storage(storage_path, content, content_type or "application/octet-stream")
        db_ops.update_document_file_path(document["id"], storage_path)

        chunks = split_text(full_text) if full_text.strip() else [f"Document: {file.filename or 'upload'}"]
        for chunk in chunks:
            embedding = create_embedding(chunk)
            db_ops.insert_document_chunk(document["id"], chunk, json.dumps(embedding))

        return {"message": "Document uploaded and processed", "document_id": document["id"]}
    except HTTPException:
        raise
    except Exception as e:
        return {"error": str(e)}

@app.get("/chats/{email}")
def get_chats(email: str):
    user = db_ops.get_user_by_email(email)
    if not user:
        return {"chats": []}
    chats = db_ops.get_chats_by_user_id(user["id"])
    return {"chats": [{"name": c["name"], "display_id": c.get("display_id")} for c in chats]}


@app.post("/chats")
def create_chat(body: CreateChatRequest):
    user = db_ops.get_user_by_email(body.email)
    if not user:
        display_id = db_ops.get_next_display_id(body.mode)
        company = None
        if (body.mode or "").strip().lower() == "company":
            domain = _extract_domain(body.email)
            if domain:
                company = _get_or_create_company(domain)
        user = db_ops.create_user(body.email, display_id, "company" if company else "personal", company["id"] if company else None)
    existing = db_ops.get_chat_by_user_and_name(user["id"], body.name)
    if existing:
        return {"ok": True, "name": body.name}
    db_ops.create_chat(user["id"], body.name, user.get("display_id") or "")
    return {"ok": True, "name": body.name}


@app.patch("/chats/rename")
def rename_chat(body: RenameChatRequest):
    user = db_ops.get_user_by_email(body.email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    chat = db_ops.get_chat_by_user_and_name(user["id"], body.old_name)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    new_name = (body.new_name or "").strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="New name cannot be empty")
    if new_name == body.old_name:
        return {"ok": True, "name": new_name}
    existing = db_ops.get_chat_by_user_and_name(user["id"], new_name)
    if existing:
        raise HTTPException(status_code=400, detail="A chat with this name already exists")
    db_ops.update_chat_name(user["id"], body.old_name, new_name)
    return {"ok": True, "name": new_name}


@app.post("/chats/claim")
def claim_guest_chat(body: ClaimChatRequest):
    """Assign the guest chat (and all its messages) to the logged-in user so pre-login messages get the user's display_id."""
    guest_chat_name = (body.guest_chat_name or "").strip()
    email = (body.email or "").strip()
    if not guest_chat_name or not email:
        raise HTTPException(status_code=400, detail="guest_chat_name and email required")
    guest_user = db_ops.get_user_by_email("guest")
    if not guest_user:
        return {"ok": True, "name": guest_chat_name}
    real_user = db_ops.get_user_by_email(email)
    if not real_user:
        raise HTTPException(status_code=404, detail="User not found")
    chat = db_ops.get_chat_by_user_and_name(guest_user["id"], guest_chat_name)
    if not chat:
        return {"ok": True, "name": guest_chat_name}
    display_id = (real_user.get("display_id") or "").strip()
    if not display_id:
        display_id = db_ops.get_next_display_id("personal")
        get_supabase().table("users").update({"display_id": display_id}).eq("id", real_user["id"]).execute()
    db_ops.update_chat_ownership(chat["id"], real_user["id"], display_id)
    db_ops.update_messages_display_id(chat["id"], display_id)
    # Rename guest-uuid to short name "Chat 1" (or "Chat 2", ...) so sidebar shows a short name
    new_name = db_ops.get_next_short_chat_name(real_user["id"])
    db_ops.update_chat_name(real_user["id"], guest_chat_name, new_name)
    return {"ok": True, "name": new_name, "previous_name": guest_chat_name}


@app.get("/user-info")
def get_user_info(email: str = ""):
    if not email:
        return {"email": "", "user_id": None, "is_admin": False}
    user = db_ops.get_user_by_email(email)
    is_admin = _is_admin(email)
    if not user:
        return {"email": email, "user_id": None, "is_admin": is_admin}
    return {"email": user["email"], "user_id": user.get("display_id"), "is_admin": is_admin}


@app.get("/admin/database")
def get_admin_database(email: str = ""):
    if not _is_admin(email):
        raise HTTPException(status_code=403, detail="Admin only")
    return {
        "users": db_ops.get_all_users(),
        "chats": db_ops.get_all_chats(),
        "messages": db_ops.get_all_messages(),
        "documents": db_ops.get_all_documents(),
        "document_chunks": db_ops.get_all_document_chunks(),
    }


@app.get("/admin/admins")
def get_admin_list(email: str = ""):
    if not _is_super_admin(email):
        raise HTTPException(status_code=403, detail="Only super admin can view admin list")
    return {"admins": db_ops.get_all_admins()}


@app.post("/admin/admins")
def add_admin(body: AddAdminRequest):
    if not _is_super_admin(body.email):
        raise HTTPException(status_code=403, detail="Only the super admin can add admins")
    email_to_add = (body.new_admin_email or "").strip().lower()
    if not email_to_add or "@" not in email_to_add:
        raise HTTPException(status_code=400, detail="Valid email required")
    if db_ops.get_admin_by_email(email_to_add):
        return {"message": "Already an admin", "admins": db_ops.get_all_admins()}
    db_ops.add_admin_by_email(email_to_add)
    return {"message": "Admin added", "admins": db_ops.get_all_admins()}


@app.post("/admin/admins/remove")
def remove_admin(body: RemoveAdminRequest):
    if not _is_super_admin(body.email):
        raise HTTPException(status_code=403, detail="Only the super admin can remove admins")
    email_to_remove = (body.remove_admin_email or "").strip().lower()
    if not email_to_remove:
        raise HTTPException(status_code=400, detail="Email required")
    if not db_ops.get_admin_by_email(email_to_remove):
        return {"message": "Not an admin", "admins": db_ops.get_all_admins()}
    db_ops.remove_admin_by_email(email_to_remove)
    return {"message": "Admin removed", "admins": db_ops.get_all_admins()}


def _serve_document_file(doc: dict):
    """Return FileResponse for local path or Response with bytes for Supabase Storage."""
    file_path = doc.get("file_path")
    if not file_path:
        raise HTTPException(status_code=404, detail="Document not found")
    if _is_local_file_path(file_path):
        return FileResponse(
            file_path,
            media_type=_media_type_for_path(file_path),
            filename=doc.get("name"),
        )
    try:
        content = _download_from_storage(file_path)
    except Exception as e:
        raise HTTPException(status_code=404, detail="Document not found") from e
    media_type = _media_type_for_path(doc.get("name") or file_path)
    filename = (doc.get("name") or file_path.split("/")[-1] or "document").replace('"', "'")
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@app.get("/documents/file/{document_id}")
def get_document_file(document_id: int, email: str):
    if _is_admin(email):
        doc = db_ops.get_document_by_id(document_id)
        if not doc or not doc.get("file_path"):
            raise HTTPException(status_code=404, detail="Document not found")
        return _serve_document_file(doc)

    user = db_ops.get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    doc = db_ops.get_document_by_id(document_id)
    if not doc or not doc.get("file_path"):
        raise HTTPException(status_code=404, detail="Document not found")
    is_owner = doc["user_id"] == user["id"]
    same_company = doc.get("company_id") is not None and user.get("company_id") is not None and user["company_id"] == doc["company_id"]
    if not is_owner and not same_company:
        raise HTTPException(status_code=403, detail="Document not found")
    return _serve_document_file(doc)


@app.delete("/documents/{document_id}")
def delete_document(document_id: int, email: str):
    user = db_ops.get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    doc = db_ops.get_document_by_id(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    is_owner = doc["user_id"] == user["id"]
    is_hr_company_doc = doc.get("company_id") is not None and user.get("company_id") == doc["company_id"] and _is_hr_email(email)
    if not is_owner and not is_hr_company_doc:
        raise HTTPException(status_code=403, detail="Not allowed to delete this document")
    db_ops.delete_document_chunks_by_document_id(document_id)
    fp = doc.get("file_path")
    if fp:
        if _is_local_file_path(fp):
            try:
                os.remove(fp)
            except OSError:
                pass
        else:
            _delete_from_storage(fp)
    db_ops.delete_document_by_id(document_id)
    return {"ok": True}


@app.get("/company/settings")
def get_company_settings(email: str = ""):
    if not email:
        return {"show_doc_count_to_employees": False}
    user = db_ops.get_user_by_email(email)
    if not user or not user.get("company_id"):
        return {"show_doc_count_to_employees": False}
    company = db_ops.get_company_by_id(user["company_id"])
    if not company:
        return {"show_doc_count_to_employees": False}
    return {"show_doc_count_to_employees": bool(company.get("show_doc_count_to_employees", 0))}


@app.patch("/company/settings")
def update_company_settings(body: CompanySettingsUpdate):
    if not _is_hr_email(body.email):
        raise HTTPException(status_code=403, detail="Only HR can update this setting")
    user = db_ops.get_user_by_email(body.email)
    if not user or not user.get("company_id"):
        raise HTTPException(status_code=404, detail="Company not found")
    company = db_ops.get_company_by_id(user["company_id"])
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    db_ops.update_company_show_doc_count(user["company_id"], body.show_doc_count_to_employees)
    return {"show_doc_count_to_employees": body.show_doc_count_to_employees}


@app.get("/documents/company/count")
def get_company_documents_count(email: str = ""):
    if not email:
        return {"count": 0, "visible": False}
    user = db_ops.get_user_by_email(email)
    if not user:
        domain = _extract_domain(email)
        if not domain:
            return {"count": 0, "visible": False}
        company = _get_or_create_company(domain)
        if not company:
            return {"count": 0, "visible": False}
        user = db_ops.create_user(email, db_ops.get_next_display_id("company"), "company", company["id"])
    elif user.get("company_id") is None:
        domain = _extract_domain(email)
        if domain:
            company = _get_or_create_company(domain)
            if company:
                get_supabase().table("users").update({"user_type": "company", "company_id": company["id"]}).eq("id", user["id"]).execute()
                user = db_ops.get_user_by_email(email)
    if not user.get("company_id"):
        return {"count": 0, "visible": False}
    company = db_ops.get_company_by_id(user["company_id"])
    if not company or not company.get("show_doc_count_to_employees"):
        return {"count": 0, "visible": False}
    n = db_ops.count_documents_by_company(user["company_id"])
    return {"count": n, "visible": True}


@app.get("/documents/company/{email}")
def get_company_documents(email: str):
    if not _is_hr_email(email):
        return {"documents": []}
    user = db_ops.get_user_by_email(email)
    if not user or not user.get("company_id"):
        return {"documents": []}
    docs = db_ops.get_documents_by_company(user["company_id"])
    return {
        "documents": [
            {"id": d["id"], "name": d["name"], "has_preview": bool(d.get("file_path"))}
            for d in docs
        ]
    }


@app.get("/documents/{email}")
def get_documents(email: str):
    user = db_ops.get_user_by_email(email)
    if not user:
        return {"documents": []}
    docs = db_ops.get_documents_global(user["id"])
    return {
        "documents": [
            {"id": d["id"], "name": d["name"], "user_id": d.get("display_id"), "has_preview": bool(d.get("file_path"))}
            for d in docs
        ]
    }


@app.get("/documents/{email}/{chat_name}")
def get_chat_documents(email: str, chat_name: str):
    user = db_ops.get_user_by_email(email)
    if not user:
        return {"documents": []}
    chat = db_ops.get_chat_by_user_and_name(user["id"], chat_name)
    if not chat:
        return {"documents": []}
    docs = db_ops.get_documents_by_chat(user["id"], chat["id"])
    return {
        "documents": [
            {"id": d["id"], "name": d["name"], "user_id": d.get("display_id"), "has_preview": bool(d.get("file_path"))}
            for d in docs
        ]
    }

@app.get("/messages/{email}/{chat_name}")
def get_messages(email: str, chat_name: str):
    user = db_ops.get_user_by_email(email)
    if not user:
        return {"messages": []}
    chat = db_ops.get_chat_by_user_and_name(user["id"], chat_name)
    if not chat:
        return {"messages": []}
    messages = db_ops.get_messages_for_chat(chat["id"])
    return {"messages": [{"role": m.get("role"), "content": m.get("content"), "user_id": m.get("display_id")} for m in messages]}



config = Config('.env')

oauth = OAuth(config)

oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={
        "scope": "openid email profile"
    }
)

@app.get("/login/google")
async def login_google(request: Request):
    redirect_uri = request.url_for("auth_google")
    return await oauth.google.authorize_redirect(request, redirect_uri)

@app.get("/auth/google")
async def auth_google(request: Request):
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:8000").rstrip("/")
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError as e:
        # invalid_grant often means redirect_uri mismatch or expired code
        error_msg = urllib.parse.quote(
            "Google sign-in failed. In Google Cloud Console, add this exact Redirect URI under your OAuth client: "
            + str(request.base_url).rstrip("/") + "/auth/google"
        )
        return RedirectResponse(frontend_url + "/?error=oauth&message=" + error_msg)
    user = token["userinfo"]
    email = user["email"]
    if not db_ops.get_user_by_email(email):
        display_id = db_ops.get_next_display_id("personal")
        db_ops.create_user(email, display_id, "personal", None)
    return RedirectResponse(frontend_url + "/?email=" + urllib.parse.quote(email))


# Serve frontend static files at / (index.html, script.js, style.css). API routes above take precedence.
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")



