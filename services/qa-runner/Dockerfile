FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

WORKDIR /app

# Lighthouse for performance/a11y/seo audits (playwright image already has Node)
RUN npm install -g lighthouse@12 chrome-launcher@1 && \
    npm cache clean --force

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -m -r qauser || true

EXPOSE 8001

CMD ["uvicorn", "qa.main:app", "--host", "0.0.0.0", "--port", "8001"]
