FROM python:3.9-slim

WORKDIR /app

COPY application.py /app/application.py

COPY requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r requirements.txt

RUN apt-get update && apt-get install -y \
    stress-ng \
    && rm -rf /var/lib/apt/lists/*

RUN pip install kubernetes

ENTRYPOINT ["python", "application.py"]
