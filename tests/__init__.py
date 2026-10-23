"""The tests never use your real keys or your real settings, even when a .env file exists next to them."""

from leadrouter import settings

settings.GEMINI_API_KEY = ""
settings.SERPER_API_KEY = ""
settings.APOLLO_API_KEY = ""
settings.DRAFT_RECIPIENT = ""
settings.MAX_LEADS = 0
settings.SENDER_COMPANY = "Acme Connect"
settings.PRODUCT_PITCH = settings.build_pitch(settings.SENDER_COMPANY)
