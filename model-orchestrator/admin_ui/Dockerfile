# Admin UI - Streamlit app for API key management + usage monitoring
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Non-root user with a writable home (Streamlit keeps state under ~/.streamlit)
RUN groupadd -r ui && useradd -r -m -g ui ui

COPY app.py /app/app.py

USER ui

# Address/port are env-overridable: the Quadlet pins the UI to 127.0.0.1:8502
# behind the nginx TLS terminator; compose keeps the in-container defaults.
ENV STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_PORT=8501

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import os,sys,urllib.request; port=os.environ.get('STREAMLIT_SERVER_PORT','8501'); r=urllib.request.urlopen('http://127.0.0.1:'+port+'/_stcore/health', timeout=5); sys.exit(0 if r.status==200 else 1)"

CMD ["streamlit", "run", "app.py"]
