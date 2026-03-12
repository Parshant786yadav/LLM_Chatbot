from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
import os
import io
import re
import time
from sqlalchemy import or_, text, create_engine
from sqlalchemy.exc import OperationalError
from dotenv import load_dotenv
from groq import Groq
from database import engine, SessionLocal, DATABASE_URL
from models import Base, User, Chat, Message
from fastapi import UploadFile, File, Form
from pypdf import PdfReader
from rag import split_text, create_embedding
from authlib.integrations.starlette_client import OAuth
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
from models import Document, DocumentChunk, Company, Admin
import json
from rag import cosine_similarity

# Create all tables (users, chats, messages, documents, document_chunks) as soon as app loads
# so "no such table: users" never happens when handling /chat requests
Base.metadata.create_all(bind=engine)

# Run DB setup at app startup (avoids "database is locked" when uvicorn --reload spawns subprocess)
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "document_storage")
os.makedirs(UPLOAD_DIR, exist_ok=True)

def _run_db_migrations():
    # Use a short-timeout engine so we fail in seconds, not minutes
    migration_engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False, "timeout": 3},
    )
    last_error = None
    for attempt in range(3):
        try:
            # Create all tables (users, chats, messages, documents, document_chunks) so User ID 1, 2, 3... work
            Base.metadata.create_all(bind=migration_engine)
            with migration_engine.begin() as conn:
                conn.execute(text("PRAGMA journal_mode=WAL"))
                try:
                    r = conn.execute(text("PRAGMA table_info(documents)"))
                    cols = [row[1] for row in r.fetchall()]
                    if "chat_id" not in cols:
                        conn.execute(text("ALTER TABLE documents ADD COLUMN chat_id INTEGER REFERENCES chats(id)"))
                    if "file_path" not in cols:
                        conn.execute(text("ALTER TABLE documents ADD COLUMN file_path VARCHAR"))
                except Exception:
                    pass
                try:
                    r = conn.execute(text("PRAGMA table_info(users)"))
                    cols = [row[1] for row in r.fetchall()]
                    if "display_id" not in cols:
                        conn.execute(text("ALTER TABLE users ADD COLUMN display_id VARCHAR"))
                except Exception:
                    pass
                for table, col in [("chats", "display_id"), ("messages", "display_id"), ("documents", "display_id")]:
                    try:
                        r = conn.execute(text(f"PRAGMA table_info({table})"))
                        cols = [row[1] for row in r.fetchall()]
                        if col not in cols:
                            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} VARCHAR"))
                    except Exception:
                        pass
                # Company support: users.user_type, users.company_id, documents.company_id, companies.show_doc_count_to_employees
                for table, col in [("users", "user_type"), ("users", "company_id"), ("documents", "company_id")]:
                    try:
                        r = conn.execute(text(f"PRAGMA table_info({table})"))
                        cols = [row[1] for row in r.fetchall()]
                        if col not in cols:
                            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} INTEGER" if col == "company_id" else f"ALTER TABLE {table} ADD COLUMN {col} VARCHAR"))
                    except Exception:
                        pass
                try:
                    r = conn.execute(text("PRAGMA table_info(companies)"))
                    cols = [row[1] for row in r.fetchall()]
                    if "show_doc_count_to_employees" not in cols:
                        conn.execute(text("ALTER TABLE companies ADD COLUMN show_doc_count_to_employees INTEGER DEFAULT 0"))
                except Exception:
                    pass
                # Admins table (for admin dashboard / database view)
                conn.execute(text(
                    "CREATE TABLE IF NOT EXISTS admins (id INTEGER PRIMARY KEY, email VARCHAR UNIQUE)"
                ))
                conn.execute(text(
                    "INSERT OR IGNORE INTO admins (email) VALUES ('parshant786yadav@gmail.com')"
                ))
            migration_engine.dispose()
            return
        except OperationalError as e:
            last_error = e
            if "locked" not in str(e).lower() and "busy" not in str(e).lower():
                migration_engine.dispose()
                raise
            time.sleep(1)
    migration_engine.dispose()
    raise RuntimeError(
        "Database is locked. Close any other app using chatbot.db (other terminals, DB browser), then restart."
    ) from last_error

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

app = FastAPI(title="Enterprise AI Assistant Backend")


@app.on_event("startup")
def startup():
    _run_db_migrations()
    # Ensure main engine (used by requests) has tables; same file as migration
    Base.metadata.create_all(bind=engine)


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
    """Send OTP via Gmail SMTP. Raises on failure."""
    sender = os.getenv("GMAIL_OTP_EMAIL", "").strip()
    password = os.getenv("GMAIL_OTP_APP_PASSWORD", "").strip()
    if not sender or not password:
        raise ValueError("GMAIL_OTP_EMAIL and GMAIL_OTP_APP_PASSWORD must be set in .env")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Your login OTP"
    msg["From"] = sender
    msg["To"] = to_email
    text = f"Your one-time password is: {otp}\n\nIt expires in 10 minutes.\n\nIf you didn't request this, ignore this email."
    msg.attach(MIMEText(text, "plain"))
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
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            display_id = _get_next_display_id(db, (body.mode or "personal").strip().lower())
            user = User(email=email, display_id=display_id)
            if (body.mode or "").strip().lower() == "company":
                domain = _extract_domain(email)
                if domain:
                    company = _get_or_create_company(db, domain)
                    if company:
                        user.user_type = "company"
                        user.company_id = company.id
            db.add(user)
            db.commit()
    finally:
        db.close()
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


def _is_admin(db, email: str) -> bool:
    """True if email is in admins table (can see Database)."""
    if not email or not str(email).strip():
        return False
    return db.query(Admin).filter(Admin.email == email.strip().lower()).first() is not None


def _is_super_admin(email: str) -> bool:
    """True if email is the super admin (only one who can add/remove admins)."""
    return (email or "").strip().lower() == SUPER_ADMIN_EMAIL


def _get_or_create_company(db, domain: str):
    """Get or create Company by domain. Returns Company or None."""
    if not domain:
        return None
    company = db.query(Company).filter(Company.domain == domain).first()
    if not company:
        company = Company(domain=domain)
        db.add(company)
        db.commit()
        db.refresh(company)
    return company


def _get_next_display_id(db, mode: str) -> str:
    """Next user ID: personal -> A1, A2,... ; company -> C1, C2,..."""
    prefix = "A" if (mode or "").strip().lower() == "personal" else "C"
    result = db.execute(
        text(
            "SELECT MAX(CAST(SUBSTR(display_id, 2) AS INTEGER)) FROM users WHERE display_id LIKE :pat"
        ),
        {"pat": prefix + "%"},
    )
    row = result.scalar()
    next_num = (row or 0) + 1
    return prefix + str(next_num)


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

    db = SessionLocal()

    try:
        # 1️⃣ Get or create user
        email = req.email or "guest"

        mode = (req.mode or "personal").strip().lower()
        user = db.query(User).filter(User.email == email).first()
        if not user:
            display_id = _get_next_display_id(db, req.mode or "personal")
            user = User(email=email, display_id=display_id)
            if mode == "company":
                domain = _extract_domain(email)
                if domain:
                    company = _get_or_create_company(db, domain)
                    if company:
                        user.user_type = "company"
                        user.company_id = company.id
            db.add(user)
            db.commit()
            db.refresh(user)
        elif mode == "company" and (getattr(user, "company_id", None) is None):
            domain = _extract_domain(email)
            if domain:
                company = _get_or_create_company(db, domain)
                if company:
                    user.user_type = "company"
                    user.company_id = company.id
                    db.commit()
                    db.refresh(user)

        # 2️⃣ Get or create chat
        chat_name = req.chat or "default"

        chat = (
            db.query(Chat)
            .filter(Chat.user_id == user.id, Chat.name == chat_name)
            .first()
        )

        if not chat:
            chat = Chat(name=chat_name, user_id=user.id, display_id=user.display_id)
            db.add(chat)
            db.commit()
            db.refresh(chat)

        # 3️⃣ Save user message (with user id so you can find which user sent it)
        user_message = Message(
            role="user",
            content=req.message,
            chat_id=chat.id,
            display_id=user.display_id,
        )
        db.add(user_message)
        db.commit()

        # 4️⃣ Reconstruct conversation history (all messages for full memory)
        history = (
            db.query(Message)
            .filter(Message.chat_id == chat.id)
            .order_by(Message.id)
            .all()
        )
        # Full dialogue for LLM: last N turns (user + assistant), excluding the current user message (it goes in final_prompt)
        history_excluding_current = history[:-1] if len(history) > 1 else []
        history_tail = history_excluding_current[-(_MAX_CHAT_HISTORY_TURNS * 2) :]
        history_messages = [{"role": msg.role, "content": msg.content or ""} for msg in history_tail]
        history_user_contents = [msg.content for msg in history if msg.role == "user"]

        # ---------------- RAG PART (global docs + this chat's docs, or company docs) ----------------
        if user.company_id is not None:
            # Company user: use only company-shared documents (all users @same domain access these)
            chunks = (
                db.query(DocumentChunk)
                .join(Document)
                .filter(Document.company_id == user.company_id)
                .all()
            )
        else:
            # Personal: user's global docs + this chat's docs
            chunks = (
                db.query(DocumentChunk)
                .join(Document)
                .filter(
                    Document.user_id == user.id,
                    or_(Document.chat_id.is_(None), Document.chat_id == chat.id),
                )
                .all()
            )

        context = ""
        if chunks:
            # Use many recent user messages for RAG so follow-ups and long threads still retrieve the right doc context
            rag_query_parts = history_user_contents[-10:] if history_user_contents else [req.message]
            rag_query = " ".join(rag_query_parts).strip() or req.message
            query_embedding = create_embedding(rag_query)
            scored_chunks = []
            for chunk in chunks:
                chunk_embedding = json.loads(chunk.embedding)
                score = cosine_similarity(query_embedding, chunk_embedding)
                scored_chunks.append((score, chunk.content))
            scored_chunks.sort(reverse=True)
            # Only use chunks that are somewhat relevant (e.g. score > 0.2)
            top_chunks = [c[1] for c in scored_chunks[:5] if c[0] > 0.2]
            context = "\n\n".join(top_chunks) if top_chunks else ""

        # 5️⃣ Call Groq: natural chat when no context, use docs when context exists
        reply = None
        last_error = None

        system_instruction = (
            "You are a friendly, helpful AI assistant. Talk naturally like a human—warm, conversational, and engaging. "
            "For greetings (e.g. hello, hi, how are you), small talk, or general questions, respond in a natural way. "
            "When the user has provided 'Relevant context from documents' below, use that context to answer questions about the documents when relevant; "
            "otherwise answer from your knowledge or chat normally. Never say you don't know for simple greetings or chitchat."
        )

        if context.strip():
            final_prompt = f"""Relevant context from the user's uploaded documents:

{context}

---

User: {req.message}"""
        else:
            final_prompt = req.message

        for model in (CHAT_MODEL_PRIMARY, CHAT_MODEL_FALLBACK):
            try:
                reply = _call_groq_with_system(
                    client, model, system_instruction, history_messages, final_prompt
                )
                break
            except Exception as e:
                last_error = e
                if _is_quota_error(e):
                    continue  # try fallback model
                raise

        if reply is None and last_error and _is_quota_error(last_error):
            return {
                "reply": "Rate limit reached. Please try again in a few minutes or check https://console.groq.com/docs/rate-limits"
            }
        if reply is None:
            raise last_error or RuntimeError("No reply from model")

        # 6️⃣ Save model reply (with user id so you can find which user's chat it belongs to)
        model_message = Message(
            role="model",
            content=reply,
            chat_id=chat.id,
            display_id=user.display_id,
        )
        db.add(model_message)
        db.commit()

        return {"reply": reply}

    except Exception as e:
        if _is_quota_error(e):
            return {
                "reply": "Groq rate limit exceeded. Please try again in a few minutes or check https://console.groq.com/docs/rate-limits"
            }
        return {"reply": f"Error: {str(e)}"}

    finally:
        db.close()

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

    db = SessionLocal()

    try:
        content_type = (file.content_type or "").strip().lower()
        if content_type not in _ALLOWED_CONTENT_TYPES:
            return {"error": "Only PDF and images (e.g. JPG, PNG, GIF, WebP) are supported"}

        content = await file.read()

        if content_type == "application/pdf":
            reader = PdfReader(io.BytesIO(content))
            full_text = ""
            for page in reader.pages:
                full_text += page.extract_text() or ""
        else:
            full_text = _extract_text_from_image(content, file.filename or "image")

        is_company = (mode or "").strip().lower() == "company"
        # Company uploads: only hr@companyname can upload; others get 403
        if is_company and not _is_hr_email(email):
            raise HTTPException(
                status_code=403,
                detail="Only HR (hr@yourcompany) can upload company documents. You can ask questions in chat.",
            )
        user = db.query(User).filter(User.email == email).first()
        if not user:
            display_id = _get_next_display_id(db, "company" if is_company else "personal")
            user = User(email=email, display_id=display_id)
            if is_company:
                domain = _extract_domain(email)
                if domain:
                    company = _get_or_create_company(db, domain)
                    if company:
                        user.user_type = "company"
                        user.company_id = company.id
            db.add(user)
            db.commit()
            db.refresh(user)
        elif is_company and user.company_id is None:
            domain = _extract_domain(email)
            if domain:
                company = _get_or_create_company(db, domain)
                if company:
                    user.user_type = "company"
                    user.company_id = company.id
                    db.commit()
                    db.refresh(user)

        # Company uploads: document is shared with all users @same domain (no chat)
        # Personal: resolve chat_id if this is a chat document
        chat_id = None
        company_id = None
        if is_company and user.company_id:
            company_id = user.company_id
        elif chat:
            chat_row = (
                db.query(Chat)
                .filter(Chat.user_id == user.id, Chat.name == chat)
                .first()
            )
            if not chat_row:
                chat_row = Chat(name=chat, user_id=user.id, display_id=user.display_id)
                db.add(chat_row)
                db.commit()
                db.refresh(chat_row)
            chat_id = chat_row.id

        document = Document(
            name=file.filename,
            user_id=user.id,
            chat_id=chat_id,
            company_id=company_id,
            display_id=user.display_id,
        )
        db.add(document)
        db.commit()
        db.refresh(document)

        # store PDF on disk for preview
        user_dir = os.path.join(UPLOAD_DIR, str(user.id))
        os.makedirs(user_dir, exist_ok=True)
        safe_name = _sanitize_filename(file.filename)
        stored_name = f"{document.id}_{safe_name}"
        file_path = os.path.join(user_dir, stored_name)
        with open(file_path, "wb") as f:
            f.write(content)
        document.file_path = file_path
        db.commit()

        # split and embed (ensure at least one chunk so doc is findable)
        chunks = split_text(full_text) if full_text.strip() else [f"Document: {file.filename or 'upload'}"]

        for chunk in chunks:
            embedding = create_embedding(chunk)

            doc_chunk = DocumentChunk(
                document_id=document.id,
                content=chunk,
                embedding=json.dumps(embedding)
            )
            db.add(doc_chunk)

        db.commit()

        return {"message": "Document uploaded and processed", "document_id": document.id}

    finally:
        db.close()

@app.get("/chats/{email}")
def get_chats(email: str):

    db = SessionLocal()

    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            return {"chats": []}

        chats = db.query(Chat).filter(Chat.user_id == user.id).all()

        return {
            "chats": [{"name": c.name, "display_id": c.display_id} for c in chats]
        }

    finally:
        db.close()


@app.post("/chats")
def create_chat(body: CreateChatRequest):
    """Create a chat (and user if needed) so the chat exists in DB before first message. Fixes rename on new accounts."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == body.email).first()
        if not user:
            display_id = _get_next_display_id(db, body.mode)
            user = User(email=body.email, display_id=display_id)
            if (body.mode or "").strip().lower() == "company":
                domain = _extract_domain(body.email)
                if domain:
                    company = _get_or_create_company(db, domain)
                    if company:
                        user.user_type = "company"
                        user.company_id = company.id
            db.add(user)
            db.commit()
            db.refresh(user)
        existing = db.query(Chat).filter(Chat.user_id == user.id, Chat.name == body.name).first()
        if existing:
            return {"ok": True, "name": body.name}
        chat = Chat(name=body.name, user_id=user.id, display_id=user.display_id)
        db.add(chat)
        db.commit()
        return {"ok": True, "name": body.name}
    finally:
        db.close()


@app.patch("/chats/rename")
def rename_chat(body: RenameChatRequest):
    """Rename a chat for the given user. new_name must be unique for that user."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == body.email).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        chat = db.query(Chat).filter(Chat.user_id == user.id, Chat.name == body.old_name).first()
        if not chat:
            raise HTTPException(status_code=404, detail="Chat not found")
        new_name = (body.new_name or "").strip()
        if not new_name:
            raise HTTPException(status_code=400, detail="New name cannot be empty")
        if new_name == body.old_name:
            return {"ok": True, "name": new_name}
        existing = db.query(Chat).filter(Chat.user_id == user.id, Chat.name == new_name).first()
        if existing:
            raise HTTPException(status_code=400, detail="A chat with this name already exists")
        chat.name = new_name
        db.commit()
        return {"ok": True, "name": new_name}
    finally:
        db.close()


@app.get("/user-info")
def get_user_info(email: str = ""):
    """Get user's user_id (A1, C2, etc.), email, and is_admin for profile."""
    if not email:
        return {"email": "", "user_id": None, "is_admin": False}
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        is_admin = _is_admin(db, email)
        if not user:
            return {"email": email, "user_id": None, "is_admin": is_admin}
        return {"email": user.email, "user_id": user.display_id, "is_admin": is_admin}
    finally:
        db.close()


def _row_to_dict(row, keys):
    return {k: getattr(row, k, None) for k in keys}


@app.get("/admin/database")
def get_admin_database(email: str = ""):
    """Return all DB data (users, chats, messages, documents, companies, document_chunks). Admin only."""
    db = SessionLocal()
    try:
        if not _is_admin(db, email):
            raise HTTPException(status_code=403, detail="Admin only")
        users = [_row_to_dict(u, ["id", "email", "display_id", "user_type", "company_id"]) for u in db.query(User).all()]
        chats = [_row_to_dict(c, ["id", "name", "user_id", "display_id"]) for c in db.query(Chat).all()]
        messages = [_row_to_dict(m, ["id", "role", "content", "chat_id", "display_id"]) for m in db.query(Message).all()]
        documents = [_row_to_dict(d, ["id", "name", "file_path", "user_id", "company_id", "chat_id", "display_id"]) for d in db.query(Document).all()]
        chunks = [_row_to_dict(c, ["id", "document_id", "content"]) for c in db.query(DocumentChunk).all()]
        return {
            "users": users,
            "chats": chats,
            "messages": messages,
            "documents": documents,
            "document_chunks": chunks,
        }
    finally:
        db.close()


@app.get("/admin/admins")
def get_admin_list(email: str = ""):
    """List all admin emails. Only super admin (parshant786yadav@gmail.com) can see the list."""
    db = SessionLocal()
    try:
        if not _is_super_admin(email):
            raise HTTPException(status_code=403, detail="Only super admin can view admin list")
        admins = [a.email for a in db.query(Admin).all()]
        return {"admins": admins}
    finally:
        db.close()


@app.post("/admin/admins")
def add_admin(body: AddAdminRequest):
    """Add an email as admin. Only super admin (parshant786yadav@gmail.com) can add."""
    db = SessionLocal()
    try:
        if not _is_super_admin(body.email):
            raise HTTPException(status_code=403, detail="Only the super admin can add admins")
        email_to_add = (body.new_admin_email or "").strip().lower()
        if not email_to_add or "@" not in email_to_add:
            raise HTTPException(status_code=400, detail="Valid email required")
        existing = db.query(Admin).filter(Admin.email == email_to_add).first()
        if existing:
            return {"message": "Already an admin", "admins": [a.email for a in db.query(Admin).all()]}
        admin = Admin(email=email_to_add)
        db.add(admin)
        db.commit()
        return {"message": "Admin added", "admins": [a.email for a in db.query(Admin).all()]}
    finally:
        db.close()


@app.post("/admin/admins/remove")
def remove_admin(body: RemoveAdminRequest):
    """Remove an email from admins. Only super admin (parshant786yadav@gmail.com) can remove."""
    db = SessionLocal()
    try:
        if not _is_super_admin(body.email):
            raise HTTPException(status_code=403, detail="Only the super admin can remove admins")
        email_to_remove = (body.remove_admin_email or "").strip().lower()
        if not email_to_remove:
            raise HTTPException(status_code=400, detail="Email required")
        existing = db.query(Admin).filter(Admin.email == email_to_remove).first()
        if not existing:
            return {"message": "Not an admin", "admins": [a.email for a in db.query(Admin).all()]}
        db.delete(existing)
        db.commit()
        return {"message": "Admin removed", "admins": [a.email for a in db.query(Admin).all()]}
    finally:
        db.close()


@app.get("/documents/file/{document_id}")
def get_document_file(document_id: int, email: str):

    db = SessionLocal()

    try:
        # Admin can view/download any document
        if _is_admin(db, email):
            doc = db.query(Document).filter(Document.id == document_id).first()
            if not doc or not doc.file_path or not os.path.isfile(doc.file_path):
                raise HTTPException(status_code=404, detail="Document not found")
            media_type = _media_type_for_path(doc.file_path)
            return FileResponse(
                doc.file_path,
                media_type=media_type,
                filename=doc.name,
            )

        user = db.query(User).filter(User.email == email).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc or not doc.file_path or not os.path.isfile(doc.file_path):
            raise HTTPException(status_code=404, detail="Document not found")
        # Allow: owner (personal doc) or same-company user (company doc)
        is_owner = doc.user_id == user.id
        same_company = (
            doc.company_id is not None
            and getattr(user, "company_id", None) is not None
            and user.company_id == doc.company_id
        )
        if not is_owner and not same_company:
            raise HTTPException(status_code=403, detail="Document not found")

        media_type = _media_type_for_path(doc.file_path)
        return FileResponse(
            doc.file_path,
            media_type=media_type,
            filename=doc.name,
        )

    finally:
        db.close()


@app.delete("/documents/{document_id}")
def delete_document(document_id: int, email: str):
    """Delete one document by id. Allowed if user owns it (personal) or is HR and it's a company doc."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        # Allow: owner (personal doc) or HR deleting a company doc of their company
        is_owner = doc.user_id == user.id
        is_hr_company_doc = (
            doc.company_id is not None
            and user.company_id == doc.company_id
            and _is_hr_email(email)
        )
        if not is_owner and not is_hr_company_doc:
            raise HTTPException(status_code=403, detail="Not allowed to delete this document")
        # Delete chunks first (no cascade in model)
        db.query(DocumentChunk).filter(DocumentChunk.document_id == document_id).delete()
        if doc.file_path and os.path.isfile(doc.file_path):
            try:
                os.remove(doc.file_path)
            except OSError:
                pass
        db.delete(doc)
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@app.get("/company/settings")
def get_company_settings(email: str = ""):
    """Get company setting show_doc_count_to_employees (for HR to load checkbox)."""
    if not email:
        return {"show_doc_count_to_employees": False}
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user or not user.company_id:
            return {"show_doc_count_to_employees": False}
        company = db.query(Company).filter(Company.id == user.company_id).first()
        if not company:
            return {"show_doc_count_to_employees": False}
        return {"show_doc_count_to_employees": bool(getattr(company, "show_doc_count_to_employees", 0))}
    finally:
        db.close()


@app.patch("/company/settings")
def update_company_settings(body: CompanySettingsUpdate):
    """HR only: set whether employees can see company document count."""
    db = SessionLocal()
    try:
        if not _is_hr_email(body.email):
            raise HTTPException(status_code=403, detail="Only HR can update this setting")
        user = db.query(User).filter(User.email == body.email).first()
        if not user or not user.company_id:
            raise HTTPException(status_code=404, detail="Company not found")
        company = db.query(Company).filter(Company.id == user.company_id).first()
        if not company:
            raise HTTPException(status_code=404, detail="Company not found")
        company.show_doc_count_to_employees = 1 if body.show_doc_count_to_employees else 0
        db.commit()
        return {"show_doc_count_to_employees": body.show_doc_count_to_employees}
    finally:
        db.close()


@app.get("/documents/company/count")
def get_company_documents_count(email: str = ""):
    """Return company document count. visible=True only when HR enabled 'show count to employees'.
    If employee has no company_id yet, link them to company by email domain so count can be shown."""
    if not email:
        return {"count": 0, "visible": False}
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            # First time employee loading: create user and attach to company by domain
            domain = _extract_domain(email)
            if not domain:
                return {"count": 0, "visible": False}
            company = _get_or_create_company(db, domain)
            if not company:
                return {"count": 0, "visible": False}
            display_id = _get_next_display_id(db, "company")
            user = User(email=email, display_id=display_id, user_type="company", company_id=company.id)
            db.add(user)
            db.commit()
            db.refresh(user)
        elif getattr(user, "company_id", None) is None:
            # Existing user (e.g. from personal) logging in as company: attach to company by domain
            domain = _extract_domain(email)
            if domain:
                company = _get_or_create_company(db, domain)
                if company:
                    user.user_type = "company"
                    user.company_id = company.id
                    db.commit()
                    db.refresh(user)
        if not user.company_id:
            return {"count": 0, "visible": False}
        company = db.query(Company).filter(Company.id == user.company_id).first()
        if not company or not getattr(company, "show_doc_count_to_employees", 0):
            return {"count": 0, "visible": False}
        n = db.query(Document).filter(Document.company_id == user.company_id).count()
        return {"count": n, "visible": True}
    finally:
        db.close()


@app.get("/documents/company/{email}")
def get_company_documents(email: str):
    """List company documents (for HR only). Same-domain users access via chat only."""
    db = SessionLocal()
    try:
        if not _is_hr_email(email):
            return {"documents": []}
        user = db.query(User).filter(User.email == email).first()
        if not user or not user.company_id:
            return {"documents": []}
        docs = (
            db.query(Document)
            .filter(Document.company_id == user.company_id)
            .all()
        )
        return {
            "documents": [
                {"id": doc.id, "name": doc.name, "has_preview": bool(doc.file_path and os.path.isfile(doc.file_path))}
                for doc in docs
            ]
        }
    finally:
        db.close()


@app.get("/documents/{email}")
def get_documents(email: str):

    db = SessionLocal()

    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            return {"documents": []}

        # Only global documents (chat_id is None, not company docs)
        docs = (
            db.query(Document)
            .filter(
                Document.user_id == user.id,
                Document.chat_id.is_(None),
                Document.company_id.is_(None),
            )
            .all()
        )

        return {
            "documents": [
                {"id": doc.id, "name": doc.name, "user_id": doc.display_id, "has_preview": bool(doc.file_path and os.path.isfile(doc.file_path))}
                for doc in docs
            ]
        }

    finally:
        db.close()


@app.get("/documents/{email}/{chat_name}")
def get_chat_documents(email: str, chat_name: str):

    db = SessionLocal()

    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            return {"documents": []}

        chat = (
            db.query(Chat)
            .filter(Chat.user_id == user.id, Chat.name == chat_name)
            .first()
        )
        if not chat:
            return {"documents": []}

        docs = (
            db.query(Document)
            .filter(Document.user_id == user.id, Document.chat_id == chat.id)
            .all()
        )

        return {
            "documents": [
                {"id": doc.id, "name": doc.name, "user_id": doc.display_id, "has_preview": bool(doc.file_path and os.path.isfile(doc.file_path))}
                for doc in docs
            ]
        }

    finally:
        db.close()

@app.get("/messages/{email}/{chat_name}")
def get_messages(email: str, chat_name: str):

    db = SessionLocal()

    try:
        # Get user
        user = db.query(User).filter(User.email == email).first()
        if not user:
            return {"messages": []}

        # Get chat
        chat = (
            db.query(Chat)
            .filter(Chat.user_id == user.id, Chat.name == chat_name)
            .first()
        )

        if not chat:
            return {"messages": []}

        # Get messages
        messages = (
            db.query(Message)
            .filter(Message.chat_id == chat.id)
            .order_by(Message.id)
            .all()
        )

        return {
            "messages": [
                {"role": msg.role, "content": msg.content, "user_id": msg.display_id}
                for msg in messages
            ]
        }

    finally:
        db.close()



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

    token = await oauth.google.authorize_access_token(request)
    user = token["userinfo"]

    email = user["email"]

    db = SessionLocal()

    try:
        user_db = db.query(User).filter(User.email == email).first()

        if not user_db:
            display_id = _get_next_display_id(db, "personal")
            user_db = User(email=email, display_id=display_id)
            db.add(user_db)
            db.commit()

        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:8000")
        return RedirectResponse(frontend_url.rstrip("/") + "/?email=" + urllib.parse.quote(email))

    finally:
        db.close()


# Serve frontend static files at / (index.html, script.js, style.css). API routes above take precedence.
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")



