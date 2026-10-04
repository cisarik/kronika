"""Kernel constants for the stripped chatgpt-page ask bridge.

Runtime directories are not constants. Callers pass an explicit state
directory; the development default lives in :mod:`kronika_capture.paths`.
"""

APP_NAME = "kronika-capture"
STATE_DIR_NAME = "framenest-chatgpt-page"
DEFAULT_BRIDGE_HOST = "127.0.0.1"
DEFAULT_BRIDGE_PORT = 8765
PROTO_VERSION = 1
# systemd LoadCredential= name. The file appears as $CREDENTIALS_DIRECTORY/token.
SYSTEMD_BRIDGE_TOKEN_CREDENTIAL = "token"
SYSTEMD_BRIDGE_TOKEN_MAX_BYTES = 4096
# Same interval the runner's launch brake enforces before another browser start.
CAPTURE_RESTART_BRAKE_MS = 300_000
PACK_VERSION = 5
API_VERSION = 1
BRIDGE_VERSION = "0.1.0"

DEFAULT_ASK_TIMEOUT_S = 600
MAX_ASK_TIMEOUT_S = 3600
NEXT_WAIT_DEFAULT_S = 20
NEXT_WAIT_MAX_S = 25
EXTENSION_CONNECTED_WINDOW_S = 90
JOB_STALL_S = 120
CANCEL_GRACE_S = 10.0
CLIENT_POLL_INTERVAL_S = 0.5
CLIENT_CANCEL_WAIT_S = 10.0
CLIENT_WAITING_NOTICE_S = 5.0

BODY_MAX_BYTES = 2 * 1024 * 1024
# UTF-8 bytes of the entire serialized result envelope, not just answer text.
RESULT_MAX_BYTES = BODY_MAX_BYTES
LOG_MAX_BYTES = 2 * 1024 * 1024
JOB_RETENTION_MAX_ITEMS = 256
JOB_RETENTION_MAX_AGE_S = 24 * 3600
JOB_EVENT_MAX_ITEMS = 64
ADMIN_WAIT_MAX_S = 1800

UPLOAD_UNAVAILABLE = "file upload is not available in this build"
