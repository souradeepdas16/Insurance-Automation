"""AI client — sends vision requests through OpenRouter."""

from __future__ import annotations

import base64
import io
import json
import os
import re as _re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image

from src.paths import APP_DIR

load_dotenv(APP_DIR / ".env")

# ── OpenRouter setup ───────────────────────────────────────────────────────────
OPENROUTER_API_KEY: str = os.environ.get("OPENROUTER_API_KEY", "")
client = OpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1",
)


def _get_model() -> str:
    """Return the active AI model from DB settings, falling back to a default."""
    try:
        from src.database import get_setting

        db_model = get_setting("ai_model")
        if db_model:
            return db_model
    except Exception:
        pass
    return "openai/gpt-4.1"


# ── Debug logging ────────────────────────────────────────────────────────────
AI_DEBUG: bool = os.environ.get("AI_DEBUG", "1").strip() != "0"
_LOG_DIR = APP_DIR / "logs" / "ai_raw"
_log_counter = 0
_log_lock_debug = threading.Lock()


def _log_raw(label: str, raw: str, finish_reason: str = "") -> None:
    """Write raw API response to logs/ai_raw/<timestamp>_<label>.txt"""
    if not AI_DEBUG:
        return
    global _log_counter
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    with _log_lock_debug:
        _log_counter += 1
        ts = datetime.now().strftime("%H%M%S")
        fname = f"{ts}_{_log_counter:03d}_{label[:40]}.txt"
    with open(_LOG_DIR / fname, "w", encoding="utf-8") as f:
        f.write(f"# finish_reason: {finish_reason}\n")
        f.write(f"# chars: {len(raw)}\n\n")
        f.write(raw)


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_DIM = 2048

# Maximum output tokens — defaults to model hard limit; override via AI_MAX_OUTPUT_TOKENS env var.
_MAX_OUTPUT_TOKENS: int = int(os.environ.get("AI_MAX_OUTPUT_TOKENS", "65536"))

# Maximum PDF pages to send per API call (avoids huge payloads / provider limits).
MAX_PAGES_PER_CALL: int = int(os.environ.get("AI_MAX_PAGES_PER_CALL", "10"))


def _resize_image_to_base64(file_path: str, max_dim: int = MAX_DIM) -> str:
    """Open an image, resize if needed, return base64 JPEG string."""
    with Image.open(file_path) as img:
        img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode("ascii")


def pdf_pages_to_base64(
    file_path: str,
    max_dim: int = MAX_DIM,
    start_page: int = 0,
    end_page: int | None = None,
) -> list[str]:
    """Render PDF pages to JPEG base64 strings using PyMuPDF.

    Args:
        start_page: 0-based first page index (inclusive).
        end_page:   0-based last page index (exclusive). None = all pages.
    """
    import gc
    import fitz  # PyMuPDF

    pages_b64: list[str] = []
    with fitz.open(file_path) as doc:
        stop = end_page if end_page is not None else len(doc)
        for i in range(start_page, min(stop, len(doc))):
            page = doc[i]
            pix = page.get_pixmap(dpi=150)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            del pix
            img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            pages_b64.append(base64.b64encode(buf.getvalue()).decode("ascii"))
            img.close()
            buf.close()
            gc.collect()
    return pages_b64


def _file_to_content_items(file_path: str) -> list[dict[str, Any]]:
    """Convert a file to one or more chat-completions content items.

    Images -> single inline base64 image_url (JPEG)
    PDFs   -> one image_url per page (rendered to JPEG via PyMuPDF)
    """
    ext = Path(file_path).suffix.lower()

    if ext in IMAGE_EXTS:
        b64 = _resize_image_to_base64(file_path)
        return [
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"},
            }
        ]

    # PDF — render each page to JPEG for universal model compatibility
    pages = pdf_pages_to_base64(file_path)
    return [
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"},
        }
        for b64 in pages
    ]


def _b64_images_to_content(images_b64: list[str]) -> list[dict[str, Any]]:
    """Convert a list of base64 JPEG strings to content items."""
    return [
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"},
        }
        for b64 in images_b64
    ]


def vision_extract_json_from_images(
    images_b64: list[str],
    prompt: str,
    max_output_tokens: int = _MAX_OUTPUT_TOKENS,
    label: str = "chunk",
) -> dict[str, Any]:
    """Like vision_extract_json but takes pre-rendered base64 JPEG images.

    Used when the caller has already split a PDF into page chunks.
    """
    content: list[dict[str, Any]] = _b64_images_to_content(images_b64)
    content.append({"type": "text", "text": prompt})

    response = client.chat.completions.create(
        model=_get_model(),
        messages=[
            {"role": "system", "content": SYSTEM_JSON},
            {"role": "user", "content": content},
        ],
        response_format={"type": "json_object"},
        max_tokens=max_output_tokens,
        temperature=0,
    )
    choice = response.choices[0]
    raw = (choice.message.content or "").strip()
    finish_reason = getattr(choice, "finish_reason", "") or ""

    _log_raw(label, raw, finish_reason)

    if finish_reason == "length":
        raise ValueError(
            f"Output truncated (finish_reason=length, {len(raw)} chars). "
            f"Increase max_output_tokens (currently {max_output_tokens}). "
            f"Raw saved to logs/ai_raw/"
        )

    parsed = _safe_json_loads(raw)
    if isinstance(parsed, list) and len(parsed) == 1 and isinstance(parsed[0], dict):
        parsed = parsed[0]
    return parsed


def _strip_json_fences(text: str) -> str:
    """Remove markdown code fences from a JSON response."""
    text = text.strip()
    m = _re.search(r"```(?:json)?\s*\n?(.*?)```", text, _re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


def _repair_json(raw: str) -> str:
    """Attempt to repair common JSON issues from AI responses."""
    # 1. Remove control characters (except \n, \r, \t which are valid in JSON strings
    #    but should be escaped — we'll handle that below)
    cleaned = _re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", raw)

    # 2. Fix unescaped newlines/tabs inside JSON string values
    #    Walk through and escape any literal newlines/tabs within strings
    result: list[str] = []
    in_string = False
    i = 0
    while i < len(cleaned):
        ch = cleaned[i]
        if ch == "\\" and in_string and i + 1 < len(cleaned):
            result.append(ch)
            result.append(cleaned[i + 1])
            i += 2
            continue
        if ch == '"':
            in_string = not in_string
        if in_string and ch == "\n":
            result.append("\\n")
            i += 1
            continue
        if in_string and ch == "\r":
            result.append("\\r")
            i += 1
            continue
        if in_string and ch == "\t":
            result.append("\\t")
            i += 1
            continue
        result.append(ch)
        i += 1
    cleaned = "".join(result)

    # 3. Remove trailing commas before } or ]
    cleaned = _re.sub(r",\s*([}\]])", r"\1", cleaned)

    return cleaned


def _close_json(partial: str) -> str:
    """Try to close an incomplete JSON structure by adding missing brackets/braces."""
    # Count unclosed braces/brackets
    stack: list[str] = []
    in_str = False
    i = 0
    while i < len(partial):
        ch = partial[i]
        if ch == "\\" and in_str and i + 1 < len(partial):
            i += 2
            continue
        if ch == '"':
            in_str = not in_str
        if not in_str:
            if ch in ("{", "["):
                stack.append(ch)
            elif ch == "}" and stack and stack[-1] == "{":
                stack.pop()
            elif ch == "]" and stack and stack[-1] == "[":
                stack.pop()
        i += 1

    # If we're inside an unclosed string, close it
    if in_str:
        partial += '"'

    # Close any remaining open brackets/braces
    for opener in reversed(stack):
        partial += "]" if opener == "[" else "}"

    return partial


def _safe_json_loads(raw: str) -> Any:
    """Parse JSON with repair attempts for malformed AI responses."""
    # 1. Try direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 2. Try repairing common issues (control chars, unescaped newlines, trailing commas)
    repaired = _repair_json(raw)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # 3. Handle "Extra data" — truncate at the first valid JSON boundary
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        if "Extra data" in str(exc) and exc.pos:
            truncated = raw[: exc.pos].strip()
            print(
                f"    ⚠ Extra data after valid JSON at char {exc.pos}, truncating and retrying…"
            )
            return json.loads(truncated)

    # 4. Try truncating the repaired JSON at the error position and closing the structure
    try:
        json.loads(repaired)
    except json.JSONDecodeError as exc:
        if exc.pos and exc.pos > 100:
            # Truncate at the error position, back up to a safe boundary
            truncated = repaired[: exc.pos]
            # Strip trailing partial tokens (incomplete key/value)
            truncated = _re.sub(r"[,:\s]+$", "", truncated)
            # Remove a trailing partial string value (unclosed quote)
            truncated = _re.sub(r',\s*"[^"]*$', "", truncated)
            closed = _close_json(truncated)
            try:
                parsed = json.loads(closed)
                print(
                    f"    ⚠ Repaired malformed JSON (truncated at char {exc.pos} of {len(repaired)}, closed structure)"
                )
                return parsed
            except json.JSONDecodeError:
                pass

    # 5. All repair attempts failed — raise the original error for diagnostics
    return json.loads(raw)


def vision_request(file_paths: list[str], prompt: str) -> str:
    """Send files to AI provider, return raw text response."""
    content: list[dict[str, Any]] = []
    for fp in file_paths:
        content.extend(_file_to_content_items(fp))
    content.append({"type": "text", "text": prompt})

    response = client.chat.completions.create(
        model=_get_model(),
        messages=[{"role": "user", "content": content}],
        max_tokens=_MAX_OUTPUT_TOKENS,
    )
    return (response.choices[0].message.content or "").strip()


SYSTEM_JSON = (
    "You are a structured data extraction assistant. "
    "You MUST respond with a single, valid JSON object and absolutely nothing else. "
    "Rules: no markdown fences, no comments, no trailing commas, no explanation text. "
    "Every string value must be properly closed. Output ONLY valid JSON."
)


def vision_extract_json(
    file_paths: list[str],
    prompt: str,
    max_output_tokens: int = _MAX_OUTPUT_TOKENS,
) -> dict[str, Any]:
    """Send files to AI provider with JSON response format, return parsed dict."""
    content: list[dict[str, Any]] = []
    for fp in file_paths:
        content.extend(_file_to_content_items(fp))
    content.append({"type": "text", "text": prompt})

    response = client.chat.completions.create(
        model=_get_model(),
        messages=[
            {"role": "system", "content": SYSTEM_JSON},
            {"role": "user", "content": content},
        ],
        response_format={"type": "json_object"},
        max_tokens=max_output_tokens,
        temperature=0,
    )
    choice = response.choices[0]
    raw = (choice.message.content or "").strip()
    finish_reason = getattr(choice, "finish_reason", "") or ""

    label = Path(file_paths[0]).stem if file_paths else "nofile"
    _log_raw(label, raw, finish_reason)

    if finish_reason == "length":
        raise ValueError(
            f"Output truncated (finish_reason=length, {len(raw)} chars). "
            f"Increase max_output_tokens (currently {max_output_tokens}). "
            f"Raw saved to logs/ai_raw/"
        )

    parsed = _safe_json_loads(raw)
    if isinstance(parsed, list) and len(parsed) == 1 and isinstance(parsed[0], dict):
        parsed = parsed[0]
    return parsed


def vision_extract_json_labeled(
    labeled_files: list[tuple[str, str]],
    prompt: str,
    max_output_tokens: int = _MAX_OUTPUT_TOKENS,
) -> dict[str, Any]:
    """Send labeled files (label, path) with JSON response format.

    Each file is preceded by a text label so the model knows which file is which.
    Returns parsed JSON dict.
    """
    content: list[dict[str, Any]] = []

    for label, file_path in labeled_files:
        content.append({"type": "text", "text": f"[{label}]"})
        content.extend(_file_to_content_items(file_path))

    content.append({"type": "text", "text": prompt})

    response = client.chat.completions.create(
        model=_get_model(),
        messages=[
            {"role": "system", "content": SYSTEM_JSON},
            {"role": "user", "content": content},
        ],
        response_format={"type": "json_object"},
        max_tokens=max_output_tokens,
        temperature=0,
    )
    choice = response.choices[0]
    raw = (choice.message.content or "").strip()
    finish_reason = getattr(choice, "finish_reason", "") or ""

    _log_raw("labeled", raw, finish_reason)

    if finish_reason == "length":
        raise ValueError(
            f"Output truncated (finish_reason=length, {len(raw)} chars). "
            f"Increase max_output_tokens (currently {max_output_tokens}). "
            f"Raw saved to logs/ai_raw/"
        )

    return _safe_json_loads(raw)
