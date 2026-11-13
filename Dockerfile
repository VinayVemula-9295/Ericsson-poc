FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TF_PLUGIN_CACHE_DIR=/terraform-plugin-cache
ARG TERRAFORM_VERSION=1.17.1
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSLO "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip" \
    && curl -fsSL "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_SHA256SUMS" -o terraform_SHA256SUMS \
    && grep "terraform_${TERRAFORM_VERSION}_linux_amd64.zip$" terraform_SHA256SUMS | sha256sum -c - \
    && unzip -q "terraform_${TERRAFORM_VERSION}_linux_amd64.zip" -d /usr/local/bin \
    && chmod 0755 /usr/local/bin/terraform \
    && rm -f "terraform_${TERRAFORM_VERSION}_linux_amd64.zip" terraform_SHA256SUMS \
    && terraform -version
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -c "import tornado.httpclient, tornado.httpserver, tornado.ioloop, tornado.web, tornado.websocket"
COPY . .
EXPOSE 8501
CMD ["python", "runtime_entrypoint.py"]
