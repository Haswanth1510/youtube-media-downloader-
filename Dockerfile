FROM python:3.11-slim

# Set the working directory
WORKDIR /app

# Install system dependencies (ffmpeg is required for yt-dlp)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Expose the port Uvicorn will run on
EXPOSE 8000

# Start the application
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
