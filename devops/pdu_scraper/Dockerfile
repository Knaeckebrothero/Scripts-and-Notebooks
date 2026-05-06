FROM python:3.12-slim

RUN pip install --no-cache-dir \
      requests==2.32.3 \
      "psycopg[binary]==3.2.3" \
      urllib3==2.2.3

WORKDIR /app
COPY scraper.py /app/scraper.py

USER 1000
ENTRYPOINT ["python", "-u", "/app/scraper.py"]
