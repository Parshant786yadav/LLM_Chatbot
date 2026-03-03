from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import os
from sqlalchemy import or_, text
from dotenv import load_dotenv
from groq import Groq
from database import engine, SessionLocal
from models import Base, User, Chat, Message
from fastapi import UploadFile, File, Form
from pypdf import PdfReader
from rag import split_text, create_embedding
from models import Document, DocumentChunk
import json
from rag import cosine_similarity

Base.metadata.create_all(bind=engine)

# Ensure documents.chat_id exists (for existing DBs created before chat-scoped docs)
with engine.connect() as conn:
    r = conn.execute(text("PRAGMA table_info(documents)"))
    cols = [row[1] for row in r.fetchall()]
    if "chat_id" not in cols:
        conn.execute(text("ALTER TABLE documents ADD COLUMN chat_id INTEGER REFERENCES chats(id)"))
        conn.commit()

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

app = FastAPI(title="Enterprise AI Assistant Backend")

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
            user = User(email=email)
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
            top_chunks = [c[1] for c in scored_chunks[:3]]
            context = "\n\n".join(top_chunks)

        final_prompt = f"""
        Answer the question using ONLY the context below.
        If answer not in context, say you don't know.

        Context:
        {context}

        Question:
        {req.message}
        """

        # 5️⃣ Call Groq: try primary model, fallback on quota error
        reply = None
        last_error = None

        for model in (CHAT_MODEL_PRIMARY, CHAT_MODEL_FALLBACK):
            try:
                reply = _call_groq_with_history(
                    client, model, history_user_contents, final_prompt
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

        reader = PdfReader(file.file)

        full_text = ""
        for page in reader.pages:
            full_text += page.extract_text() or ""

        # get user
        user = db.query(User).filter(User.email == email).first()
        if not user:
            user = User(email=email)
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

        # create document record (chat_id=None for global, set for chat docs)
        document = Document(name=file.filename, user_id=user.id, chat_id=chat_id)
        db.add(document)
        db.commit()
        db.refresh(document)

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

        return {"message": "Document uploaded and processed"}

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
            "documents": [doc.name for doc in docs]
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
            "documents": [doc.name for doc in docs]
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