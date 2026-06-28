from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Float, Enum, Index, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base

class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index('ix_users_username', 'username'),
        Index('ix_users_email', 'email'),
        Index('ix_users_created_at', 'created_at'),
    )
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False)
    email = Column(String(100), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    first_name = Column(String(50))
    last_name = Column(String(50))
    avatar_url = Column(String(500), nullable=True)
    birth_date = Column(String(10), nullable=True)
    is_active = Column(Boolean, default=True)
    is_bot = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    premium_until = Column(DateTime, nullable=True)
    last_login = Column(DateTime, nullable=True)
    emoji_status = Column(String(20), nullable=True)
    emoji_status_expires = Column(DateTime, nullable=True)
    
    is_invisible = Column(Boolean, default=False)
    is_2fa_enabled = Column(Boolean, default=False)
    totp_secret = Column(String(100), nullable=True)
    backup_codes = Column(Text, nullable=True)
    profile_id = Column(Integer, nullable=True)
    
    settings = relationship("UserSettings", back_populates="user", uselist=False)
    bots = relationship("Bot", back_populates="owner")
    profile = relationship("Profile", back_populates="user", uselist=False)
    messages = relationship("Message", back_populates="sender")
    memberships = relationship("Membership", back_populates="user")

class Profile(Base):
    __tablename__ = "profiles"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    bio = Column(Text, nullable=True)
    theme = Column(String(20), default="light")
    notifications_enabled = Column(Boolean, default=True)
    custom_status = Column(String(100), nullable=True)
    custom_status_emoji = Column(String(10), nullable=True)
    company = Column(String(100), nullable=True)
    position = Column(String(100), nullable=True)
    working_hours = Column(String(100), nullable=True)
    website = Column(String(500), nullable=True)
    
    user = relationship("User", back_populates="profile")

class Chat(Base):
    __tablename__ = "chats"
    __table_args__ = (
        Index('ix_chats_type', 'type'),
        Index('ix_chats_created_at', 'created_at'),
    )
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    type = Column(Enum('private', 'group', 'channel', name='chat_type'), nullable=False)
    name = Column(String(100), nullable=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    wallpaper = Column(String(500), nullable=True)
    pinned_message_id = Column(Integer, nullable=True)
    slow_mode_delay = Column(Integer, default=0)
    invite_link = Column(String(100), nullable=True, unique=True)
    auto_delete_after = Column(Integer, default=0)  # seconds, 0=off
    transfers_enabled = Column(Boolean, default=False)
    
    messages = relationship("Message", back_populates="chat")
    memberships = relationship("Membership", back_populates="chat")

class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index('ix_messages_chat_timestamp', 'chat_id', 'timestamp'),
        Index('ix_messages_sender_id', 'sender_id'),
        Index('ix_messages_timestamp', 'timestamp'),
    )
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    content = Column(Text, nullable=False)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    chat_id = Column(Integer, ForeignKey("chats.id"), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    edited = Column(Boolean, default=False)
    bot_id = Column(Integer, ForeignKey("bots.id"), nullable=True)
    is_deleted = Column(Boolean, default=False)
    is_forwarded = Column(Boolean, default=False)
    original_sender = Column(Integer, nullable=True)
    forwarded_from_message_id = Column(Integer, nullable=True)
    original_chat_id = Column(Integer, nullable=True)
    scheduled_for = Column(DateTime, nullable=True)
    reply_to_id = Column(Integer, ForeignKey("messages.id"), nullable=True)
    read_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    edited_at = Column(DateTime, nullable=True)
    reactions_json = Column(Text, default="{}")
    
    bot = relationship("Bot", back_populates="messages")
    sender = relationship("User", back_populates="messages")
    chat = relationship("Chat", back_populates="messages")
    edit_history = relationship("MessageEditHistory", back_populates="message")

class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        Index('ix_memberships_user_chat', 'user_id', 'chat_id', unique=True),
    )
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    chat_id = Column(Integer, ForeignKey("chats.id"), nullable=False)
    role = Column(String(20), default="member")
    joined_at = Column(DateTime, default=datetime.utcnow)
    is_archived = Column(Boolean, default=False)
    muted_until = Column(DateTime, nullable=True)
    custom_notification_sound = Column(String(100), nullable=True)
    
    user = relationship("User", back_populates="memberships")
    chat = relationship("Chat", back_populates="memberships")

class MessageEditHistory(Base):
    __tablename__ = "message_edit_history"
    
    id = Column(Integer, primary_key=True)
    message_id = Column(Integer, ForeignKey("messages.id"), nullable=False)
    old_content = Column(Text, nullable=False)
    edited_at = Column(DateTime, default=datetime.utcnow)
    edited_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    message = relationship("Message", back_populates="edit_history")

class Bot(Base):
    __tablename__ = "bots"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    username = Column(String(50), unique=True, nullable=False)
    avatar_url = Column(String(500), nullable=True)
    api_key = Column(String(64), unique=True, nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    owner = relationship("User", back_populates="bots")
    messages = relationship("Message", back_populates="bot")

class BotWebhook(Base):
    __tablename__ = "bot_webhooks"

    id = Column(Integer, primary_key=True)
    bot_id = Column(Integer, ForeignKey("bots.id"), nullable=False, unique=True)
    url = Column(String(500), nullable=False)
    max_connections = Column(Integer, default=40)
    allowed_updates = Column(Text, default="[]")
    last_error_date = Column(DateTime, nullable=True)
    last_error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class BotCommand(Base):
    __tablename__ = "bot_commands"

    id = Column(Integer, primary_key=True)
    bot_id = Column(Integer, ForeignKey("bots.id"), nullable=False)
    command = Column(String(32), nullable=False)
    description = Column(String(256), nullable=False)


class Admin(Base):
    __tablename__ = "admins"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    added_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class StickerPack(Base):
    __tablename__ = "sticker_packs"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    code = Column(String(8), unique=True, nullable=False)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    is_public = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    stickers = relationship("Sticker", back_populates="pack")
    author = relationship("User")

class Sticker(Base):
    __tablename__ = "stickers"
    
    id = Column(Integer, primary_key=True, index=True)
    pack_id = Column(Integer, ForeignKey("sticker_packs.id"), nullable=False)
    emoji = Column(String(10), nullable=True)
    type = Column(String(20), default="image")
    url = Column(String(500), nullable=False)
    order = Column(Integer, default=0)
    
    pack = relationship("StickerPack", back_populates="stickers")

class Report(Base):
    __tablename__ = "reports"
    
    id = Column(Integer, primary_key=True)
    reporter_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    reported_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    reason = Column(String(200), nullable=False)
    message_id = Column(Integer, ForeignKey("messages.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String(20), default="pending")

class UserSettings(Base):
    __tablename__ = "user_settings"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    
    theme = Column(String(20), default="dark")
    language = Column(String(10), default="ru")
    fontSize = Column(Integer, default=14)
    notifications_enabled = Column(Boolean, default=True)
    sound_enabled = Column(Boolean, default=True)
    wallpaper = Column(String(100), default="default")
    
    who_can_see_photo = Column(String(20), default="all")
    who_can_see_bio = Column(String(20), default="all")
    who_can_see_last_seen = Column(String(20), default="all")
    who_can_add_to_groups = Column(String(20), default="all")
    
    show_stats = Column(Boolean, default=True)
    
    mute_when_online = Column(Boolean, default=False)
    smart_notifications = Column(Boolean, default=False)
    custom_sounds = Column(Text, default="{}")  # JSON
    
    user = relationship("User", back_populates="settings")


class Poll(Base):
    __tablename__ = "polls"
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(Integer, ForeignKey("chats.id"), nullable=False)
    question = Column(String(500), nullable=False)
    options = Column(Text, nullable=False)  # JSON array
    is_anonymous = Column(Boolean, default=True)
    is_closed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class PollVote(Base):
    __tablename__ = "poll_votes"
    id = Column(Integer, primary_key=True, index=True)
    poll_id = Column(Integer, ForeignKey("polls.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    option_index = Column(Integer, nullable=False)
    __table_args__ = (UniqueConstraint("poll_id", "user_id", name="uq_poll_user"),)

class Draft(Base):
    __tablename__ = "drafts"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    chat_id = Column(Integer, ForeignKey("chats.id"), nullable=False)
    content = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (UniqueConstraint("user_id", "chat_id", name="uq_draft"),)

class Archive(Base):
    __tablename__ = "archives"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    chat_id = Column(Integer, ForeignKey("chats.id"), nullable=False)
    archived_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("user_id", "chat_id", name="uq_archive"),)

class Folder(Base):
    __tablename__ = "folders"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(100), nullable=False)
    chat_ids = Column(Text, default="[]")  # JSON
    icon = Column(String(50), default="folder")

class SavedMessage(Base):
    __tablename__ = "saved_messages"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    message_id = Column(Integer, ForeignKey("messages.id"), nullable=False)
    saved_at = Column(DateTime, default=datetime.utcnow)


class BlockedUser(Base):
    __tablename__ = 'blocked_users'

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    blocked_user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship('User', foreign_keys=[user_id])
    blocked_user = relationship('User', foreign_keys=[blocked_user_id])

class ScheduledMessage(Base):
    __tablename__ = "scheduled_messages"
    
    id = Column(Integer, primary_key=True)
    content = Column(Text, nullable=False)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    chat_id = Column(Integer, ForeignKey("chats.id"), nullable=False)
    scheduled_for = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String(20), default="pending")

class Notification(Base):
    __tablename__ = "notifications"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    type = Column(String(50), nullable=False)
    message = Column(Text, nullable=False)
    from_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    chat_id = Column(Integer, ForeignKey("chats.id"), nullable=True)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

# ============================================
# ГЕЙМИФИКАЦИЯ
# ============================================

class UserXP(Base):
    __tablename__ = "user_xp"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    total_xp = Column(Integer, default=0)
    daily_xp = Column(Integer, default=0)
    daily_xp_date = Column(String(10))
    level = Column(Integer, default=1)
    rank_title = Column(String(50), default="Основатель")
    show_level = Column(Boolean, default=True)
    show_achievements = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class UserStreak(Base):
    __tablename__ = "user_streaks"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    chat_id = Column(Integer, ForeignKey("chats.id"), nullable=False)
    streak_days = Column(Integer, default=0)
    last_message_date = Column(String(10))
    restores_used = Column(Integer, default=0)
    restores_reset_date = Column(String(10))
    created_at = Column(DateTime, default=datetime.utcnow)

class Achievement(Base):
    __tablename__ = "achievements"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=False)
    icon = Column(String(50))
    category = Column(String(50))
    xp_reward = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

class UserAchievement(Base):
    __tablename__ = "user_achievements"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    achievement_id = Column(Integer, ForeignKey("achievements.id"), nullable=False)
    unlocked_at = Column(DateTime, default=datetime.utcnow)

class UserStats(Base):
    __tablename__ = "user_stats"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    messages_sent = Column(Integer, default=0)
    messages_received = Column(Integer, default=0)
    photos_shared = Column(Integer, default=0)
    videos_shared = Column(Integer, default=0)
    calls_made = Column(Integer, default=0)
    calls_duration = Column(Integer, default=0)
    chats_joined = Column(Integer, default=0)
    quarkpay_integrated = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class ChatStreak(Base):
    __tablename__ = "chat_streaks"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    chat_id = Column(Integer, ForeignKey("chats.id"), nullable=False)
    streak_days = Column(Integer, default=0)
    last_message_date = Column(String(10))
    restores_used = Column(Integer, default=0)
    restores_reset_date = Column(String(10))
    created_at = Column(DateTime, default=datetime.utcnow)

# ============================================
# STORIES
# ============================================

class Story(Base):
    __tablename__ = "stories"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    content_type = Column(String(20), nullable=False)
    content_url = Column(String(500), nullable=True)
    text_content = Column(Text, nullable=True)
    bg_color = Column(String(20), nullable=True)
    font_color = Column(String(20), nullable=True)
    poll_question = Column(String(500), nullable=True)
    poll_options = Column(Text, nullable=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    user = relationship("User")

class StoryReaction(Base):
    __tablename__ = "story_reactions"
    id = Column(Integer, primary_key=True, index=True)
    story_id = Column(Integer, ForeignKey("stories.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    emoji = Column(String(10), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class PaymentToken(Base):
    __tablename__ = "payment_tokens"
    id = Column(Integer, primary_key=True, index=True)
    token = Column(String(64), unique=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    plan = Column(String(20), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)

class PendingTransfer(Base):
    __tablename__ = "pending_transfers"
    id = Column(Integer, primary_key=True, index=True)
    verify_code = Column(String(32), unique=True, nullable=False)
    from_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    from_username = Column(String(50), nullable=False)
    to_username = Column(String(50), nullable=False)
    amount = Column(Float, nullable=False)
    description = Column(String(500), default="")
    pin_code = Column(String(4), nullable=False)
    status = Column(String(20), default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)

class BlacklistedToken(Base):
    __tablename__ = "blacklisted_tokens"
    id = Column(Integer, primary_key=True, index=True)
    token = Column(String(500), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class UserSession(Base):
    __tablename__ = "user_sessions"
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(64), unique=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    device = Column(String(200), default="")
    ip = Column(String(50), default="")
    is_current = Column(Boolean, default=False)
    is_new = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
