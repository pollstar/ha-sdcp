"""Constants for the SDCP printer integration."""

DOMAIN = "sdcp"
MANUFACTURER_FALLBACK = "Chitu / SDCP"

CONF_POLL_ACTIVE = "poll_active"
CONF_POLL_IDLE = "poll_idle"
DEFAULT_POLL_ACTIVE = 3
DEFAULT_POLL_IDLE = 15

EVENT_PRINTER = f"{DOMAIN}_event"

SERVICE_START_PRINT = "start_print"
SERVICE_DELETE_FILES = "delete_files"

# URL of the card bundled by versions <= 0.1.x (removed from Lovelace resources on start)
LEGACY_CARD_PATH = "/sdcp_static/sdcp-printer-card.js"
