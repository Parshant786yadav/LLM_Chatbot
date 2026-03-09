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
from models import Document, DocumentChunk, Company
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
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def home():
    return {"status": "Backend running successfully 🚀"}

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


def _extract_domain(email: str) -> Optional[str]:
    """Extract domain from email (e.g. hr@company.com -> company.com). Returns None if no @."""
    if not email or "@" not in email:
        return None
    return email.strip().split("@")[-1].lower()


def _is_hr_email(email: str) -> bool:
    """True if email is HR (hr@companyname) – only HR can upload company documents."""
    return bool(email and str(email).strip().lower().startswith("hr@"))


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


def _call_groq_with_system(
    groq_client: Groq,
    model: str,
    system_instruction: str,
    history_user_contents: list[str],
    final_prompt: str,
) -> str:
    """Call Groq with system role + history. Talks naturally; uses doc context when provided."""
    messages = [{"role": "system", "content": system_instruction}]
    for c in history_user_contents:
        messages.append({"role": "user", "content": c})
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

        # 4️⃣ Reconstruct conversation history (user messages only, for context)
        history = (
            db.query(Message)
            .filter(Message.chat_id == chat.id)
            .order_by(Message.id)
            .all()
        )
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
            query_embedding = create_embedding(req.message)
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
                    client, model, system_instruction, history_user_contents, final_prompt
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
    """Get user's user_id (A1, C2, etc.) and email for profile."""
    if not email:
        return {"email": "", "user_id": None}
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            return {"email": email, "user_id": None}
        return {"email": user.email, "user_id": user.display_id}
    finally:
        db.close()


@app.get("/documents/file/{document_id}")
def get_document_file(document_id: int, email: str):

    db = SessionLocal()

    try:
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
