# Backend API only — see docker-compose.yml for the full stack.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
EXPOSE 4173
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "4173"]
