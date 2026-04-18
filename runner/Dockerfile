FROM python:3.12-slim

RUN pip install --no-cache-dir pytest==8.3.3

# Hardening: run as non-root, no network at runtime.
RUN useradd --create-home --shell /usr/sbin/nologin runner
USER runner
WORKDIR /work

ENTRYPOINT ["python", "/runner/run_test.py"]
