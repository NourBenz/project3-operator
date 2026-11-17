# Trusted dependency image for network-disabled Level 2 test execution.
# Repository source is never copied into this image; it is mounted read-only at run time.
FROM python:3.12-slim

RUN python -m pip install --no-cache-dir \
    pytest==9.0.2 \
    pytest-cov==7.0.0 \
    pytest-httpbin==2.1.0 \
    pytest-mock==3.15.1 \
    trustme==1.2.1 \
    urllib3==2.6.3 \
    charset-normalizer==3.4.4 \
    idna==3.11 \
    certifi==2026.1.4

RUN useradd --create-home --uid 10001 runner
USER runner
