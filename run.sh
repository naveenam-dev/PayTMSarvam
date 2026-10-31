#!/usr/bin/env sh
# Start Sahay AI on macOS / Linux. Open http://127.0.0.1:8080
# Keys: copy .env.example to .env and fill GEMINI_API_KEY / SARVAM_API_KEY (optional).
set -e
python3 -m pip install -q -r requirements.txt
exec python3 -m uvicorn sahay.app:app --host 127.0.0.1 --port 8080
