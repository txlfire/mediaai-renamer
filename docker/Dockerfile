FROM node:22-alpine AS frontend-build

WORKDIR /frontend

COPY package.json package-lock.json /frontend/
RUN npm ci

COPY frontend /frontend/frontend
RUN npm run frontend:build

FROM python:3.11-slim

WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt
COPY backend/requirements.lock /app/backend/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /app/backend/requirements.lock

COPY backend /app/backend
COPY config/config.example.toml /app/config/config.example.toml
COPY --from=frontend-build /frontend/frontend/dist /app/frontend-dist

ENV PYTHONPATH=/app/backend
ENV MEDIAAI_FRONTEND_DIR=/app/frontend-dist
ENV MEDIAAI_RUNTIME=container
ENV MEDIAAI_MEDIA_ROOT=/app/media

RUN mkdir -p /app/media

EXPOSE 8970

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8970"]
