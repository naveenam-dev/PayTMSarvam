FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY sahay ./sahay
COPY static ./static
EXPOSE 8080
# Pass keys at runtime: docker run --env-file .env -p 8080:8080 sahay-ai
CMD ["uvicorn", "sahay.app:app", "--host", "0.0.0.0", "--port", "8080"]
