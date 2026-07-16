FROM python:3.12-slim

WORKDIR /app

# The backend talks to the host Docker daemon via the mounted socket to create
# sibling experiment containers. No Docker CLI needed — docker-py uses the API.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
