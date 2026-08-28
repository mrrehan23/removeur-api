import io
import os
import secrets
from typing import Optional

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from PIL import Image, UnidentifiedImageError
from rembg import remove


app = FastAPI(
    title="REMOVEUR Background Removal API",
    version="1.0.0"
)


ALLOWED_ORIGINS = {
    "https://removeur.pages.dev",
    "https://removeur.com",
    "https://www.removeur.com",
}

INTERNAL_SECRET = os.environ.get("REMOVEUR_INTERNAL_SECRET", "")

MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000

ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}


app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ALLOWED_ORIGINS),
    allow_credentials=False,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["Content-Type", "X-REMOVEUR-SECRET"],
)


@app.get("/")
async def root():
    return {
        "service": "REMOVEUR Background Removal API",
        "status": "online",
    }


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "removeur-api",
    }


def verify_origin(origin: Optional[str], referer: Optional[str]) -> bool:
    if origin in ALLOWED_ORIGINS:
        return True

    if referer:
        for allowed_origin in ALLOWED_ORIGINS:
            if referer.startswith(allowed_origin + "/"):
                return True

    return False


def verify_internal_secret(secret: Optional[str]) -> bool:
    if not INTERNAL_SECRET:
        return False

    if not secret:
        return False

    return secrets.compare_digest(secret, INTERNAL_SECRET)


@app.post("/api/remove-background")
async def remove_background(
    file: UploadFile = File(...),
    x_removeur_secret: Optional[str] = Header(default=None),
    origin: Optional[str] = Header(default=None),
    referer: Optional[str] = Header(default=None),
):
    if not verify_origin(origin, referer):
        raise HTTPException(
            status_code=403,
            detail="Forbidden origin.",
        )

    if not verify_internal_secret(x_removeur_secret):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized request.",
        )

    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail="Unsupported image format.",
        )

    data = await file.read()

    if not data:
        raise HTTPException(
            status_code=400,
            detail="Empty file.",
        )

    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail="Image exceeds the 10 MB limit.",
        )

    try:
        image = Image.open(io.BytesIO(data))
        image.load()

        width, height = image.size

        if width * height > MAX_IMAGE_PIXELS:
            raise HTTPException(
                status_code=413,
                detail="Image resolution is too large.",
            )

    except HTTPException:
        raise

    except UnidentifiedImageError:
        raise HTTPException(
            status_code=400,
            detail="Invalid image file.",
        )

    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Unable to read image.",
        )

    try:
        output = remove(data)

    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Background removal failed.",
        )

    finally:
        del data

    return Response(
        content=output,
        media_type="image/png",
        headers={
            "Content-Disposition": 'attachment; filename="removeur-result.png"',
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )
