import threading

from routes._base import APIRouter

PIN_LOCK = threading.Lock()

router = APIRouter(tags=["fun"], prefix="/api/fun")
