# -------- Stage 1: builder (instala dependencias) --------
FROM public.ecr.aws/docker/library/python:3.11-slim AS builder

WORKDIR /app

# Evita archivos .pyc y buffers
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Solo copiamos requirements.txt para que Docker cachee esta capa
COPY requirements.txt .

# Instalamos dependencias en /install
RUN pip install --prefix=/install -r requirements.txt

# -------- Stage 2: runtime --------
FROM public.ecr.aws/docker/library/python:3.11-slim 

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Copiamos solo las dependencias ya instaladas
COPY --from=builder /install /usr/local

# Copiamos el resto del código de la aplicación
COPY . .
 
EXPOSE 8000
 
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
