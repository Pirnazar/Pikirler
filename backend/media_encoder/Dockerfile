FROM python:3.12-slim
WORKDIR /srv/encoder
ENV PYTHONUNBUFFERED=1 HF_HOME=/models/huggingface TOKENIZERS_PARALLELISM=false
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py ./
EXPOSE 8010
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8010", "--no-access-log"]
