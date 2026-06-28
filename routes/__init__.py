from .auth import router as auth_router
from .users import router as users_router
from .messages import router as messages_router
from .chats import router as chats_router
from .polls import router as polls_router
from .drafts import router as drafts_router
from .archive_folders import router as archive_folders_router
from .saved_messages import router as saved_router
from .admin import router as admin_router
from .bots import router as bots_router
from .settings import router as settings_router
from .payment import router as payment_router
from .e2ee import router as e2ee_router
from .premium import router as premium_router
from .stickers import router as stickers_router
from .search import router as search_router
from .bot_api import router as bot_api_router
from .bot_management import router as bot_management_router
from .calls import router as calls_router

__all__ = [
    'auth_router', 'users_router', 'messages_router', 'chats_router',
    'polls_router', 'drafts_router', 'saved_router', 'archive_folders_router',
    'admin_router', 'bots_router', 'settings_router', 'payment_router',
    'e2ee_router', 'premium_router', 'stickers_router', 'search_router',
    'bot_api_router', 'bot_management_router', 'calls_router',
]
