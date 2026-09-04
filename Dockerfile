FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml /tmp/pyproject.toml
RUN python -c "import tomllib; p=tomllib.load(open('/tmp/pyproject.toml','rb')); print('\\n'.join(p['project']['dependencies']))" \
    > /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /app

RUN pip install --no-cache-dir --no-deps --no-build-isolation .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
