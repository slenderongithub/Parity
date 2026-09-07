"""Print the Gemini models this API key can call.

Model availability changes; a name that worked last month may 404 for a new key.
Run this if extraction fails with NOT_FOUND, then set GEMINI_MODEL in .env.

    python backend/list_models.py
"""

import os

import config  # noqa: F401  - loads .env
from google import genai

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
for m in client.models.list():
    if "generateContent" in (m.supported_actions or []):
        print(m.name.replace("models/", ""))
