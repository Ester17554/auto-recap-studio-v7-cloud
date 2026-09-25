FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY server.py /app/server.py
COPY index.html /app/index.html
COPY app.js /app/app.js
COPY styles.css /app/styles.css
COPY manifest.json /app/manifest.json
ENV RECAP_DATA=/data/recap
RUN mkdir -p /data/recap
EXPOSE 10000
CMD ["uvicorn","server:app","--host","0.0.0.0","--port","10000"]
