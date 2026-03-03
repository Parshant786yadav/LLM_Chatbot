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
from models import Document, DocumentChunk
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
    mode: str
    email: Optional[str] = None
    chat: Optional[str] = None
    message: str


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

        user = db.query(User).filter(User.email == email).first()
        if not user:
            display_id = _get_next_display_id(db, req.mode)
            user = User(email=email, display_id=display_id)
            db.add(user)
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
            chat = Chat(name=chat_name, user_id=user.id)
            db.add(chat)
            db.commit()
            db.refresh(chat)

        # 3️⃣ Save user message
        user_message = Message(
            role="user",
            content=req.message,
            chat_id=chat.id
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

        # ---------------- RAG PART (global docs + this chat's docs) ----------------
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

        # 6️⃣ Save model reply
        model_message = Message(
            role="model",
            content=reply,
            chat_id=chat.id
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


@app.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    email: str = Form("guest"),
    chat: Optional[str] = Form(None),
):

    db = SessionLocal()

    try:
        if file.content_type != "application/pdf":
            return {"error": "Only PDF supported"}

        content = await file.read()
        reader = PdfReader(io.BytesIO(content))

        full_text = ""
        for page in reader.pages:
            full_text += page.extract_text() or ""

        # get user (upload is personal-only, so new users get A1, A2, ...)
        user = db.query(User).filter(User.email == email).first()
        if not user:
            display_id = _get_next_display_id(db, "personal")
            user = User(email=email, display_id=display_id)
            db.add(user)
            db.commit()
            db.refresh(user)

        # resolve chat_id if this is a chat document
        chat_id = None
        if chat:
            chat_row = (
                db.query(Chat)
                .filter(Chat.user_id == user.id, Chat.name == chat)
                .first()
            )
            if not chat_row:
                chat_row = Chat(name=chat, user_id=user.id)
                db.add(chat_row)
                db.commit()
                db.refresh(chat_row)
            chat_id = chat_row.id

        # create document record first to get id
        document = Document(name=file.filename, user_id=user.id, chat_id=chat_id)
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

        # split and embed
        chunks = split_text(full_text)

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
            "chats": [chat.name for chat in chats]
        }

    finally:
        db.close()


@app.get("/user-info")
def get_user_info(email: str = ""):
    """Get user's display_id (A1, C2, etc.) and email for profile."""
    if not email:
        return {"email": "", "display_id": None}
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            return {"email": email, "display_id": None}
        return {"email": user.email, "display_id": user.display_id}
    finally:
        db.close()


@app.get("/documents/file/{document_id}")
def get_document_file(document_id: int, email: str):

    db = SessionLocal()

    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        doc = db.query(Document).filter(
            Document.id == document_id,
            Document.user_id == user.id,
        ).first()
        if not doc or not doc.file_path or not os.path.isfile(doc.file_path):
            raise HTTPException(status_code=404, detail="Document not found")

        return FileResponse(
            doc.file_path,
            media_type="application/pdf",
            filename=doc.name,
        )

    finally:
        db.close()


@app.get("/documents/{email}")
def get_documents(email: str):

    db = SessionLocal()

    try:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            return {"documents": []}

        # Only global documents (chat_id is None)
        docs = (
            db.query(Document)
            .filter(Document.user_id == user.id, Document.chat_id.is_(None))
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
                {"id": doc.id, "name": doc.name, "has_preview": bool(doc.file_path and os.path.isfile(doc.file_path))}
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
                {"role": msg.role, "content": msg.content}
                for msg in messages
            ]
        }

    finally:
        db.close()
