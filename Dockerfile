# Imagen oficial, estable y compatible
FROM python:3.10.14-slim

# Configuración para evitar errores
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV MISE_PYTHON_GITHUB_ATTESTATIONS=false

# Instalar dependencias del sistema necesarias
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Carpeta de trabajo
WORKDIR /app

# Copiar lista de paquetes
COPY requirements.txt .

# Instalar paquetes de Python
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copiar todo el código
COPY . .

# Comando de inicio
CMD ["python", "bot.py"]

