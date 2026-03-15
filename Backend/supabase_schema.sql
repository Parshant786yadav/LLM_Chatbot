-- Run this in Supabase Dashboard → SQL Editor to create tables for the chatbot.
-- Order matters (foreign keys).
--
-- Storage (for PDFs): In Dashboard → Storage, create a bucket named exactly "documents"
-- (private is fine). If you get "Bucket not found" on upload, create it manually.

-- Companies (referenced by users and documents)
CREATE TABLE IF NOT EXISTS companies (
  id BIGSERIAL PRIMARY KEY,
  domain TEXT UNIQUE NOT NULL,
  show_doc_count_to_employees INTEGER DEFAULT 0
);

-- Users
CREATE TABLE IF NOT EXISTS users (
  id BIGSERIAL PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  display_id TEXT UNIQUE,
  user_type TEXT DEFAULT 'personal',
  company_id BIGINT REFERENCES companies(id)
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_display_id ON users(display_id);
CREATE INDEX IF NOT EXISTS idx_users_company_id ON users(company_id);

-- Chats
CREATE TABLE IF NOT EXISTS chats (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  user_id BIGINT NOT NULL REFERENCES users(id),
  display_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_chats_user_id ON chats(user_id);

-- Messages
CREATE TABLE IF NOT EXISTS messages (
  id BIGSERIAL PRIMARY KEY,
  role TEXT NOT NULL,
  content TEXT,
  chat_id BIGINT NOT NULL REFERENCES chats(id),
  display_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages(chat_id);

-- Documents
CREATE TABLE IF NOT EXISTS documents (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  file_path TEXT,
  user_id BIGINT NOT NULL REFERENCES users(id),
  company_id BIGINT REFERENCES companies(id),
  chat_id BIGINT REFERENCES chats(id),
  display_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents(user_id);
CREATE INDEX IF NOT EXISTS idx_documents_company_id ON documents(company_id);
CREATE INDEX IF NOT EXISTS idx_documents_chat_id ON documents(chat_id);

-- Document chunks (for RAG embeddings)
CREATE TABLE IF NOT EXISTS document_chunks (
  id BIGSERIAL PRIMARY KEY,
  document_id BIGINT NOT NULL REFERENCES documents(id),
  content TEXT,
  embedding TEXT
);

CREATE INDEX IF NOT EXISTS idx_document_chunks_document_id ON document_chunks(document_id);

-- Admins (for admin dashboard)
CREATE TABLE IF NOT EXISTS admins (
  id BIGSERIAL PRIMARY KEY,
  email TEXT UNIQUE NOT NULL
);

-- Seed super admin (optional)
INSERT INTO admins (email) VALUES ('parshant786yadav@gmail.com')
ON CONFLICT (email) DO NOTHING;
