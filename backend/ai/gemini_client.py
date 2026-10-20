"""
ONE place that decides how the backend reaches Gemini (Ishu, 20 Oct 2026).

Two backends, chosen by `GEMINI_BACKEND`:
  * `vertex`  : Vertex AI. Authenticates as the Cloud Run service account
                (Application Default Credentials), no API key, billed
                POST-PAID to the GCP billing account like BigQuery and Cloud
                Run. `GEMINI_VERTEX_PROJECT` (defaults to the BigQuery project)
                and `GEMINI_VERTEX_LOCATION` (default `global`, where the
                2.5 models are served).
  * anything else: the Gemini Developer API with `GEMINI_API_KEY`. Since
                23 Mar 2026 that API bills from a PREPAID wallet; when the
                wallet is empty every call returns 402 and the tool goes
                blind (that is how 20 Oct 2026 started). Kept as the fallback
                for local runs.

Every module gets its client from `make_client()` and asks `credential()`
whether AI is available at all, instead of reading GEMINI_API_KEY itself.
Model names stay where they are; both backends serve the same ids.
"""
from __future__ import annotations

import base64
import logging
import os
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.5-flash"


def backend() -> str:
    return (os.getenv("GEMINI_BACKEND", "") or "").strip().lower()


def use_vertex() -> bool:
    return backend() == "vertex"


def vertex_project() -> str:
    return (os.getenv("GEMINI_VERTEX_PROJECT", "") or os.getenv("GCP_PROJECT_ID", "")
            or os.getenv("GOOGLE_CLOUD_PROJECT", "") or "averroes-deal-origination")


def vertex_location() -> str:
    return os.getenv("GEMINI_VERTEX_LOCATION", "") or "global"


def credential() -> str:
    """A truthy marker when Gemini is reachable, '' when it is not. Call
    sites that used to do `api_key = os.getenv("GEMINI_API_KEY"); if not
    api_key: return ...` keep exactly that shape with this in place of the
    getenv, so the no-AI path is unchanged under both backends."""
    if use_vertex():
        return "vertex"
    return os.getenv("GEMINI_API_KEY", "") or ""


def configured() -> bool:
    return bool(credential())


def make_client():
    """The google-genai client for the configured backend."""
    from google import genai
    if use_vertex():
        return genai.Client(vertexai=True, project=vertex_project(), location=vertex_location())
    key = os.getenv("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("Gemini not configured: set GEMINI_BACKEND=vertex or GEMINI_API_KEY")
    return genai.Client(api_key=key)


def pdf_part(data: bytes):
    """A PDF as an inline part, for the document-reading calls that used the
    old SDK's {"mime_type": ..., "data": base64} dict."""
    from google.genai import types
    return types.Part.from_bytes(data=data, mime_type="application/pdf")


def generate_json(contents: Any, model: str = DEFAULT_MODEL, client=None) -> str:
    """generate_content with a JSON response, returning the text. Replaces
    the old `GenerativeModel(...).generate_content(..., generation_config=
    {"response_mime_type": "application/json"})` call shape one for one."""
    from google.genai.types import GenerateContentConfig
    c = client or make_client()
    resp = c.models.generate_content(
        model=model, contents=contents,
        config=GenerateContentConfig(response_mime_type="application/json"))
    return (resp.text or "") if resp is not None else ""
