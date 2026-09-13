# syntax=docker/dockerfile:1.7

FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf AS deps

ENV HOME=/data \
    MEM0_DIR=/data/mem0 \
    MEM0_TELEMETRY=false \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN --mount=type=cache,id=berry-pip,target=/root/.cache/pip,sharing=shared \
    pip install -r /app/requirements.txt

FROM deps AS app
COPY server.py /app/server.py
COPY brain.py /app/brain.py
COPY brain_learning.py /app/brain_learning.py
RUN groupadd --gid 1000 berry-memory \
    && useradd --uid 1000 --gid 1000 --home-dir /data --no-create-home --shell /usr/sbin/nologin berry-memory \
    && install -d -o 1000 -g 1000 -m 0700 /data /data/mem0

USER 1000:1000

FROM app AS test
COPY test_ownership.py migrate_ownership.py /app/
COPY test_brain.py /app/test_brain.py
COPY brain_client.py test_brain_api.py /app/
COPY brain_local.py configure_client.py test_brain_local.py /app/
COPY test_configure_client.py /app/
COPY evaluate_brain.py test_evaluate_brain.py /app/
RUN python /app/server.py --self-check \
    && python /app/server.py --api-self-check \
    && python /app/test_ownership.py \
    && python /app/test_brain.py \
    && python /app/test_brain_api.py \
    && python /app/test_brain_local.py \
    && python /app/test_configure_client.py \
    && python /app/test_evaluate_brain.py

FROM app AS runtime
EXPOSE 8080
CMD ["python", "/app/server.py"]
