import io
import os
import time
import hashlib
import secrets
from collections import defaultdict
from threading import Lock

from fastapi import FastAPI, File, UploadFile, Header, HTTPException, Request
from fastapi.responses import Response, JSONResponse
from PIL import Image, UnidentifiedImageError
from rembg import remove, new_session

app = FastAPI(
    title="Removeur API",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
)

BACKEND_SECRET = os.getenv("REMOVEUR_BACKEND_SECRET", "")
ALLOWED_ORIGIN = os.getenv(
    "REMOVEUR_FRONTEND_ORIGIN",
    "https://removeur.pages.dev"
)

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "10"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "50"))
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "5"))

ALLOWED_FORMATS = {
    "image/jpeg",
    "image/png",
    "image/webp",
}

ALLOWED_OUTPUTS = {
    "png": "PNG",
    "webp": "WEBP",
}

rate_lock = Lock()
daily_lock = Lock()

minute_requests = defaultdict(list)
daily_requests = defaultdict(list)

# One persistent model session.
# This avoids reloading the model for every request.
MODEL_NAME = os.getenv("REMBG_MODEL", "u2net")
MODEL_SESSION = None


def get_model_session():
    global MODEL_SESSION

    if MODEL_SESSION is None:
        MODEL_SESSION = new_session(MODEL_NAME)

    return MODEL_SESSION


def get_client_ip(request: Request) -> str:
    # Cloudflare supplies the connecting client IP.
    # Never trust arbitrary forwarded headers directly.
    cf_ip = request.headers.get("CF-Connecting-IP")

    if cf_ip:
        return cf_ip.strip()

    return request.client.host if request.client else "unknown"


def day_key(ip: str) -> str:
    current_day = time.strftime("%Y-%m-%d", time.gmtime())
    return hashlib.sha256(
        f"{current_day}:{ip}".encode()
    ).hexdigest()


def check_limits(ip: str):
    now = time.time()

    with rate_lock:
        entries = minute_requests[ip]

        minute_requests[ip] = [
            timestamp for timestamp in entries
            if now - timestamp < 60
        ]

        if len(minute_requests[ip]) >= RATE_LIMIT_PER_MINUTE:
            raise HTTPException(
                status_code=429,
                detail={
                    "success": False,
                    "code": "RATE_LIMIT",
                    "message": "Too many requests. Please wait and try again."
                },
            )

        minute_requests[ip].append(now)

    key = day_key(ip)

    with daily_lock:
        entries = daily_requests[key]

        daily_requests[key] = [
            timestamp for timestamp in entries
            if now - timestamp < 86400
        ]

        if len(daily_requests[key]) >= DAILY_LIMIT:
            raise HTTPException(
                status_code=429,
                detail={
                    "success": False,
                    "code": "DAILY_LIMIT",
                    "message": "Daily processing limit reached. Please try again tomorrow."
                },
            )

        daily_requests[key].append(now)


def verify_backend_secret(value: str | None):
    if not BACKEND_SECRET:
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "code": "SERVER_CONFIGURATION_ERROR"
            },
        )

    if not value or not secrets.compare_digest(value, BACKEND_SECRET):
        raise HTTPException(
            status_code=401,
            detail={
                "success": False,
                "code": "UNAUTHORIZED"
            },
        )


@app.get("/health")
async def health():
    return {
        "status": "online",
        "service": "Removeur API",
        "version": "1.0.0"
    }


@app.get("/")
async def root():
    return {
        "status": "online",
        "service": "Removeur API"
    }


@app.post("/api/remove")
async def remove_background(
    request: Request,
    file: UploadFile = File(...),
    x_removeur_secret: str | None = Header(default=None),
    output: str = "png",
):
    verify_backend_secret(x_removeur_secret)

    ip = get_client_ip(request)

    check_limits(ip)

    if file.content_type not in ALLOWED_FORMATS:
        raise HTTPException(
            status_code=415,
            detail={
                "success": False,
                "code": "UNSUPPORTED_FILE_TYPE",
                "message": "Supported files are JPG, PNG and WEBP."
            },
        )

    if output not in ALLOWED_OUTPUTS:
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "code": "INVALID_OUTPUT",
                "message": "Output must be png or webp."
            },
        )

    data = await file.read()

    if not data:
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "code": "EMPTY_FILE"
            },
        )

    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "success": False,
                "code": "FILE_TOO_LARGE",
                "message": f"Maximum file size is {MAX_UPLOAD_MB} MB."
            },
        )

    try:
        source_image = Image.open(io.BytesIO(data))

        source_image.verify()

        source_image = Image.open(io.BytesIO(data))

        if source_image.width < 20 or source_image.height < 20:
            raise HTTPException(
                status_code=400,
                detail={
                    "success": False,
                    "code": "IMAGE_TOO_SMALL"
                },
            )

        if source_image.width > 6000 or source_image.height > 6000:
            raise HTTPException(
                status_code=413,
                detail={
                    "success": False,
                    "code": "IMAGE_DIMENSIONS_TOO_LARGE",
                    "message": "Maximum image dimensions are 6000×6000."
                },
            )

    except UnidentifiedImageError:
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "code": "INVALID_IMAGE"
            },
        )

    try:
        session = get_model_session()

        result = remove(
            data,
            session=session,
        )

    except Exception:
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "code": "PROCESSING_FAILED",
                "message": "Background removal failed. Please try another image."
            },
        )

    try:
        result_image = Image.open(io.BytesIO(result))

        output_buffer = io.BytesIO()

        if output == "webp":
            result_image.save(
                output_buffer,
                format="WEBP",
                lossless=True,
                method=6,
            )

            media_type = "image/webp"
            filename = "removeur-background-removed.webp"

        else:
            result_image.save(
                output_buffer,
                format="PNG",
                optimize=True,
            )

            media_type = "image/png"
            filename = "removeur-background-removed.png"

        output_bytes = output_buffer.getvalue()

    except Exception:
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "code": "OUTPUT_FAILED"
            },
        )

    return Response(
        content=output_bytes,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, X-Removeur-Secret",
        },
    )


@app.options("/api/remove")
async def options_remove():
    return Response(
        status_code=204,
        headers={
            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, X-Removeur-Secret",
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.detail,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
