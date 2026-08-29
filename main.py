# main.py

import io
import os

from flask import Flask, jsonify, request, send_file
from PIL import Image
from rembg import remove


app = Flask(__name__)

MAX_FILE_SIZE = 10 * 1024 * 1024

ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}

EDGE_SECRET = os.environ.get("REMOVEUR_EDGE_SECRET", "")


@app.get("/")
def root():
    return jsonify({
        "status": "healthy",
        "service": "removeur-api"
    })


@app.get("/health")
def health():
    return jsonify({
        "status": "healthy",
        "service": "removeur-api"
    })


@app.post("/remove-background")
def remove_background():

    if not EDGE_SECRET:
        return jsonify({
            "error": "Server security configuration is missing."
        }), 500

    supplied_secret = request.headers.get(
        "X-REMOVEUR-EDGE-SECRET",
        ""
    )

    if supplied_secret != EDGE_SECRET:
        return jsonify({
            "error": "Unauthorized."
        }), 401

    source_header = request.headers.get(
        "X-REMOVEUR-SOURCE",
        ""
    )

    if source_header != "removeur-cloudflare":
        return jsonify({
            "error": "Unauthorized."
        }), 401

    uploaded_file = request.files.get("image")

    if uploaded_file is None:
        return jsonify({
            "error": "No image uploaded."
        }), 400

    if not uploaded_file.filename:
        return jsonify({
            "error": "Invalid filename."
        }), 400

    content_type = (
        uploaded_file.content_type or ""
    ).lower()

    if content_type not in ALLOWED_MIME_TYPES:
        return jsonify({
            "error":
                "Only JPG, PNG and WebP images are supported."
        }), 415

    uploaded_file.stream.seek(0)

    raw_data = uploaded_file.stream.read(
        MAX_FILE_SIZE + 1
    )

    if len(raw_data) > MAX_FILE_SIZE:
        return jsonify({
            "error":
                "Image is too large. Maximum allowed size is 10 MB."
        }), 413

    if not raw_data:
        return jsonify({
            "error": "Empty image."
        }), 400

    try:
        image = Image.open(
            io.BytesIO(raw_data)
        )

        image.verify()

    except Exception:
        return jsonify({
            "error": "Invalid image file."
        }), 400

    try:
        image = Image.open(
            io.BytesIO(raw_data)
        ).convert("RGBA")

    except Exception:
        return jsonify({
            "error": "Unable to read image."
        }), 400

    try:
        output = remove(image)

    except Exception:
        app.logger.exception(
            "Background removal failed"
        )

        return jsonify({
            "error":
                "Background removal failed."
        }), 500

    output_buffer = io.BytesIO()

    output.save(
        output_buffer,
        format="PNG",
        optimize=True
    )

    output_buffer.seek(0)

    return send_file(
        output_buffer,
        mimetype="image/png",
        as_attachment=True,
        download_name=(
            "removeur-background-removed.png"
        ),
        max_age=0
    )


@app.errorhandler(413)
def request_entity_too_large(_error):
    return jsonify({
        "error":
            "Uploaded image is too large."
    }), 413


@app.errorhandler(404)
def not_found(_error):
    return jsonify({
        "error": "Not found."
    }), 404


@app.errorhandler(500)
def internal_error(_error):
    return jsonify({
        "error":
            "Internal server error."
    }), 500


if __name__ == "__main__":
    port = int(
        os.environ.get(
            "PORT",
            "10000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
