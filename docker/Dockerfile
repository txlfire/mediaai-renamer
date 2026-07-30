FROM node:22-alpine AS frontend-build

WORKDIR /frontend

COPY frontend/package.json /frontend/package.json
RUN npm install

COPY frontend /frontend
RUN npm run build

FROM python:3.11-slim

WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend /app/backend
COPY config/config.example.toml /app/config/config.example.toml
COPY --from=frontend-build /frontend/dist /app/frontend-dist

ENV PYTHONPATH=/app/backend
ENV MEDIAAI_FRONTEND_DIR=/app/frontend-dist

EXPOSE 8970

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8970"]
