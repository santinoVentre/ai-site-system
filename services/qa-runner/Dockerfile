FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

WORKDIR /app

# Install Node.js LTS (the base image no longer ships npm) and Lighthouse for
# performance / accessibility / SEO audits.
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    npm install -g lighthouse@12 chrome-launcher@1 && \
    npm cache clean --force && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -m -r qauser || true

EXPOSE 8001

CMD ["uvicorn", "qa.main:app", "--host", "0.0.0.0", "--port", "8001"]
