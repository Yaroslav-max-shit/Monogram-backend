from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class UserBase(BaseModel):
    username: str
    email: str

class UserCreate(UserBase):
    password: str
    first_name: str
    last_name: Optional[str] = None

class UserResponse(UserBase):
    id: int
    profile_id: Optional[int] = None
    first_name: str
    last_name: Optional[str] = None
    avatar_url: Optional[str] = None
    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str

class LoginRequest(BaseModel):
    username: str
    password: str

class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str

class EmailChangeRequest(BaseModel):
    email: str

class TwoFAVerifyRequest(BaseModel):
    code: str

class SetUsernameRequest(BaseModel):
    username: str

class BotCreateRequest(BaseModel):
    name: str
    username: str
    description: str = ""

class BotSendRequest(BaseModel):
    chat_id: int
    text: str

class ResetPasswordRequest(BaseModel):
    email: str

class ResetPasswordConfirmRequest(BaseModel):
    token: str
    new_password: str

class MessageCreate(BaseModel):
    content: str
    chat_id: int

class MessageResponse(BaseModel):
    id: int
    content: str
    sender_id: int
    chat_id: int
    timestamp: datetime
    edited: bool
    class Config:
        from_attributes = True

class ChatCreate(BaseModel):
    type: str
    name: Optional[str] = None
    description: Optional[str] = None

class ChatResponse(BaseModel):
    id: int
    type: str
    name: Optional[str] = None
    description: Optional[str] = None
    avatar_url: Optional[str] = None

    class Config:
        from_attributes = True