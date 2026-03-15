# db_ops.py – Supabase CRUD helpers (replaces SQLAlchemy session usage)

from typing import Optional, Any
from database import get_supabase


def _one(data: list) -> Optional[dict]:
    return data[0] if data else None


def get_user_by_email(email: str) -> Optional[dict]:
    r = get_supabase().table("users").select("*").eq("email", email).execute()
    return _one(r.data)


def create_user(email: str, display_id: str, user_type: str = "personal", company_id: Optional[int] = None) -> dict:
    payload = {"email": email, "display_id": display_id, "user_type": user_type or "personal"}
    if company_id is not None:
        payload["company_id"] = company_id
    r = get_supabase().table("users").insert(payload).execute()
    return r.data[0]


def get_or_create_company(domain: str) -> Optional[dict]:
    r = get_supabase().table("companies").select("*").eq("domain", domain).execute()
    if r.data:
        return r.data[0]
    ins = get_supabase().table("companies").insert({"domain": domain}).execute()
    return ins.data[0] if ins.data else None


def get_next_display_id(mode: str) -> str:
    prefix = "A" if (mode or "").strip().lower() == "personal" else "C"
    r = get_supabase().table("users").select("display_id").like("display_id", f"{prefix}%").execute()
    max_num = 0
    for row in r.data or []:
        did = row.get("display_id") or ""
        if did.startswith(prefix) and len(did) > 1:
            try:
                n = int(did[1:])
                if n > max_num:
                    max_num = n
            except ValueError:
                pass
    return prefix + str(max_num + 1)


def get_chat_by_user_and_name(user_id: int, chat_name: str) -> Optional[dict]:
    r = get_supabase().table("chats").select("*").eq("user_id", user_id).eq("name", chat_name).execute()
    return _one(r.data)


def create_chat(user_id: int, name: str, display_id: str) -> dict:
    r = get_supabase().table("chats").insert({"user_id": user_id, "name": name, "display_id": display_id}).execute()
    return r.data[0]


def add_message(chat_id: int, role: str, content: str, display_id: Optional[str] = None) -> dict:
    payload = {"chat_id": chat_id, "role": role, "content": content}
    if display_id is not None:
        payload["display_id"] = display_id
    r = get_supabase().table("messages").insert(payload).execute()
    return r.data[0]


def get_messages_for_chat(chat_id: int) -> list:
    r = get_supabase().table("messages").select("*").eq("chat_id", chat_id).order("id").execute()
    return r.data or []


def update_messages_display_id(chat_id: int, display_id: str) -> None:
    get_supabase().table("messages").update({"display_id": display_id}).eq("chat_id", chat_id).execute()


def get_chats_by_user_id(user_id: int) -> list:
    r = get_supabase().table("chats").select("*").eq("user_id", user_id).execute()
    return r.data or []


def get_next_short_chat_name(user_id: int) -> str:
    """Return next available name like 'Chat 1', 'Chat 2', ... for the user's chats."""
    chats = get_chats_by_user_id(user_id)
    max_n = 0
    prefix = "Chat "
    for c in chats:
        name = (c.get("name") or "").strip()
        if name.startswith(prefix) and len(name) > len(prefix):
            try:
                n = int(name[len(prefix):])
                if n > max_n:
                    max_n = n
            except ValueError:
                pass
    return prefix + str(max_n + 1)


def update_chat_name(user_id: int, old_name: str, new_name: str) -> None:
    get_supabase().table("chats").update({"name": new_name}).eq("user_id", user_id).eq("name", old_name).execute()


def update_chat_ownership(chat_id: int, user_id: int, display_id: str) -> None:
    get_supabase().table("chats").update({"user_id": user_id, "display_id": display_id}).eq("id", chat_id).execute()


# ---------- Documents & RAG ----------
def get_document_chunks_company(company_id: int) -> list:
    docs = get_supabase().table("documents").select("id").eq("company_id", company_id).execute()
    doc_ids = [d["id"] for d in (docs.data or [])]
    if not doc_ids:
        return []
    r = get_supabase().table("document_chunks").select("*").in_("document_id", doc_ids).execute()
    return r.data or []


def get_document_chunks_personal(user_id: int, chat_id: Optional[int]) -> list:
    # User's docs where chat_id is null (global) or chat_id = this chat
    docs = get_supabase().table("documents").select("id, chat_id").eq("user_id", user_id).execute()
    rows = docs.data or []
    doc_ids = [d["id"] for d in rows if d.get("chat_id") is None or d.get("chat_id") == chat_id]
    if not doc_ids:
        return []
    r = get_supabase().table("document_chunks").select("*").in_("document_id", doc_ids).execute()
    return r.data or []


def create_document(name: str, user_id: int, display_id: str, chat_id: Optional[int] = None, company_id: Optional[int] = None) -> dict:
    payload = {"name": name, "user_id": user_id, "display_id": display_id}
    if chat_id is not None:
        payload["chat_id"] = chat_id
    if company_id is not None:
        payload["company_id"] = company_id
    r = get_supabase().table("documents").insert(payload).execute()
    return r.data[0]


def update_document_file_path(document_id: int, file_path: str) -> None:
    get_supabase().table("documents").update({"file_path": file_path}).eq("id", document_id).execute()


def insert_document_chunk(document_id: int, content: str, embedding: str) -> dict:
    r = get_supabase().table("document_chunks").insert({"document_id": document_id, "content": content, "embedding": embedding}).execute()
    return r.data[0]


def get_document_by_id(document_id: int) -> Optional[dict]:
    r = get_supabase().table("documents").select("*").eq("id", document_id).execute()
    return _one(r.data)


def delete_document_chunks_by_document_id(document_id: int) -> None:
    get_supabase().table("document_chunks").delete().eq("document_id", document_id).execute()


def delete_document_by_id(document_id: int) -> None:
    get_supabase().table("documents").delete().eq("id", document_id).execute()


def get_documents_global(user_id: int) -> list:
    r = get_supabase().table("documents").select("*").eq("user_id", user_id).is_("chat_id", "null").is_("company_id", "null").execute()
    return r.data or []


def get_documents_by_chat(user_id: int, chat_id: int) -> list:
    r = get_supabase().table("documents").select("*").eq("user_id", user_id).eq("chat_id", chat_id).execute()
    return r.data or []


def get_documents_by_company(company_id: int) -> list:
    r = get_supabase().table("documents").select("*").eq("company_id", company_id).execute()
    return r.data or []


# ---------- Companies ----------
def get_company_by_id(company_id: int) -> Optional[dict]:
    r = get_supabase().table("companies").select("*").eq("id", company_id).execute()
    return _one(r.data)


def update_company_show_doc_count(company_id: int, show: bool) -> None:
    get_supabase().table("companies").update({"show_doc_count_to_employees": 1 if show else 0}).eq("id", company_id).execute()


def count_documents_by_company(company_id: int) -> int:
    r = get_supabase().table("documents").select("id").eq("company_id", company_id).execute()
    return len(r.data or [])


# ---------- Admins ----------
def is_admin(email: str) -> bool:
    if not (email or str(email).strip()):
        return False
    r = get_supabase().table("admins").select("id").eq("email", email.strip().lower()).execute()
    return len(r.data or []) > 0


def get_all_admins() -> list:
    r = get_supabase().table("admins").select("email").execute()
    return [a["email"] for a in (r.data or [])]


def add_admin_by_email(email: str) -> None:
    get_supabase().table("admins").insert({"email": email.strip().lower()}).execute()


def get_admin_by_email(email: str) -> Optional[dict]:
    r = get_supabase().table("admins").select("*").eq("email", email.strip().lower()).execute()
    return _one(r.data)


def remove_admin_by_email(email: str) -> None:
    get_supabase().table("admins").delete().eq("email", email.strip().lower()).execute()


# ---------- Admin database dump ----------
def get_all_users() -> list:
    r = get_supabase().table("users").select("id, email, display_id, user_type, company_id").execute()
    return r.data or []


def get_all_chats() -> list:
    r = get_supabase().table("chats").select("id, name, user_id, display_id").execute()
    return r.data or []


def get_all_messages() -> list:
    r = get_supabase().table("messages").select("id, role, content, chat_id, display_id").execute()
    return r.data or []


def get_all_documents() -> list:
    r = get_supabase().table("documents").select("id, name, file_path, user_id, company_id, chat_id, display_id").execute()
    return r.data or []


def get_all_document_chunks() -> list:
    r = get_supabase().table("document_chunks").select("id, document_id, content").execute()
    return r.data or []
