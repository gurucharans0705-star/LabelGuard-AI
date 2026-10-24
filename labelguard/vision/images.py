"""Small OpenCV helpers. OpenCV is imported lazily so the rest of the app works without it."""
from __future__ import annotations

INSTALL_HINT = "Install it with: pip install opencv-python-headless"


def vision_status() -> tuple[bool, str]:
    """Whether OpenCV is usable right now, and a plain-language message if not."""
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
    except ImportError as exc:
        return False, f"OpenCV is not installed ({exc.name} could not be imported). {INSTALL_HINT}"
    return True, "OpenCV is ready."


def decode_png(data: bytes):
    """PNG bytes -> BGR image array. Raises ValueError if the bytes are not a readable image."""
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR) if data else None
    if image is None:
        raise ValueError("the image data could not be decoded")
    return image


def encode_png(image) -> bytes:
    import cv2

    ok, buffer = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("the image could not be encoded")
    return buffer.tobytes()
