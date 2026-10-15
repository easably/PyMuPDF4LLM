FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts/fetch_tessdata.py /tmp/fetch_tessdata.py
ENV TESSDATA_PREFIX=/usr/share/tessdata
RUN python /tmp/fetch_tessdata.py && rm /tmp/fetch_tessdata.py

COPY src ./src

ENV PYTHONPATH=/app/src
ENV HOME=/tmp
ENV TMPDIR=/tmp
EXPOSE 8080

USER nobody
CMD ["uvicorn", "pymupdf4llm_service.app:app", "--host", "0.0.0.0", "--port", "8080"]
