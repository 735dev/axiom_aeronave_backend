"""Rules of the digital signature image."""

SIGNATURE_MAX_BYTES = 300 * 1024
SIGNATURE_MIME_TYPES = ("image/png", "image/jpeg")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
# Magic number -> mime type: the declared mime type is never trusted on its own.
SIGNATURE_MAGIC_BY_MIME = {"image/png": PNG_MAGIC, "image/jpeg": JPEG_MAGIC}

SIGNATURE_MESSAGES = {
    "format": "La firma debe ser una imagen PNG o JPEG válida.",
    "type": "La firma debe ser PNG o JPEG.",
    "empty": "La imagen de la firma está vacía.",
    "size": f"La firma no puede superar {SIGNATURE_MAX_BYTES // 1024} KB.",
    "content": "El archivo no es una imagen PNG o JPEG válida.",
}
