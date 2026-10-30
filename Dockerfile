FROM python:3.12-slim
# POMODO_PRODUCTION: a container is a deployed board unless someone sets it to "" on purpose (no DEV_USER).
ENV PYTHONUNBUFFERED=1 PORT=8080 POMODO_PRODUCTION=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Every module main.py imports, then the settings. pomodo*.toml matches pomodo.example.toml (always there) and your
# own pomodo.toml (copied in when it exists, so the image knows your team and repo). It holds no secrets: the token,
# the OAuth secret and SECRET_KEY are passed in as environment variables when the container runs, never baked in.
COPY main.py board.py github.py auth.py config.py demo.py pomodo*.toml ./
COPY templates ./templates
COPY static ./static
RUN useradd --system --create-home app
USER app
EXPOSE 8080
CMD exec gunicorn --bind ":$PORT" --workers 1 --threads 8 --timeout 60 main:app
