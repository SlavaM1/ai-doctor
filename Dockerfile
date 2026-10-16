FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock && useradd --uid 1000 --create-home doctor \
    && mkdir -p /data && chown doctor:doctor /data
COPY doctor ./doctor
USER doctor
ENV DATABASE_URL=sqlite:////data/doctor.db PYTHONUNBUFFERED=1
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "doctor.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
