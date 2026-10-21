FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl lib32gcc-s1 \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /opt/steamcmd \
 && curl -sSL https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz | tar -xz -C /opt/steamcmd \
 && (/opt/steamcmd/steamcmd.sh +quit || true)

ENV STEAMCMD=/opt/steamcmd/steamcmd.sh \
    GMOD2NANOS_DATA=/data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY gmod2nanos gmod2nanos
COPY web web

EXPOSE 8000
CMD ["uvicorn", "gmod2nanos.app:app", "--host", "0.0.0.0", "--port", "8000"]
