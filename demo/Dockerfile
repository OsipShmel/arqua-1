# One image per demo service: docker build --build-arg SERVICE=petstore demo/
FROM python:3.14-slim
ARG SERVICE
RUN pip install --no-cache-dir fastapi==0.142.2 uvicorn==0.54.0 pyyaml==6.0.3
WORKDIR /app
COPY common.py ./
COPY ${SERVICE}/ ./service/
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1
USER nobody
EXPOSE 8080
CMD ["uvicorn", "--app-dir", "service", "app:app", "--host", "0.0.0.0", "--port", "8080"]
