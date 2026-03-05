# models.py

from sqlalchemy import Column, Integer, String, Text, ForeignKey
from sqlalchemy.orm import relationship
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)

    display_id = Column(String, unique=True, nullable=True, index=True)

    # personal or company
    user_type = Column(String, default="personal")

    # company relation
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True)

    company = relationship("Company", back_populates="users")

    chats = relationship("Chat", back_populates="user")
    documents = relationship("Document", back_populates="user")


class Chat(Base):
    __tablename__ = "chats"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    user_id = Column(Integer, ForeignKey("users.id"))
    display_id = Column(String, nullable=True, index=True)  # A1, C2... who created this chat

    user = relationship("User", back_populates="chats")
    messages = relationship("Message", back_populates="chat")


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    role = Column(String)
    content = Column(Text)
    chat_id = Column(Integer, ForeignKey("chats.id"))
    display_id = Column(String, nullable=True, index=True)  # A1, C2... user who sent this message

    chat = relationship("Chat", back_populates="messages")



class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)

    name = Column(String)
    file_path = Column(String, nullable=True)

    user_id = Column(Integer, ForeignKey("users.id"))

    # NEW (company support)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True)

    chat_id = Column(Integer, ForeignKey("chats.id"), nullable=True)

    display_id = Column(String, nullable=True, index=True)

    user = relationship("User", back_populates="documents")
    company = relationship("Company", back_populates="documents")

    chunks = relationship("DocumentChunk", back_populates="document")


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"))
    content = Column(Text)
    embedding = Column(Text)

    document = relationship("Document", back_populates="chunks")


class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, index=True)
    domain = Column(String, unique=True, index=True)
    show_doc_count_to_employees = Column(Integer, default=0)  # 0=False, 1=True; when True, employees see "Company documents: N"

    users = relationship("User", back_populates="company")
    documents = relationship("Document", back_populates="company")