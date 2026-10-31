# Start Sahay AI on Windows. Open http://127.0.0.1:8080
# Keys: copy .env.example to .env and fill GEMINI_API_KEY / SARVAM_API_KEY (optional).
python -m pip install -q -r requirements.txt
python -m uvicorn sahay.app:app --host 127.0.0.1 --port 8080
