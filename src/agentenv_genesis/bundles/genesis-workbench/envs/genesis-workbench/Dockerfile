# The Genesis workbench env: an MCP server that simulates one sim-ready object in Genesis (CPU backend by default).
FROM public.ecr.aws/docker/library/python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 MPLBACKEND=Agg GENESIS_BACKEND=cpu
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libegl1 libegl-mesa0 libgl1-mesa-dri libglu1-mesa libglib2.0-0 libgomp1 \
    libx11-6 libxext6 libxrender1 libxi6 libxrandr2 libxcursor1 libxinerama1 libxfixes3 libxxf86vm1 libsm6 libice6 \
    && rm -rf /var/lib/apt/lists/*
ENV PYOPENGL_PLATFORM=egl
RUN pip install --index-url https://download.pytorch.org/whl/cpu "torch==2.15.0"
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY workbench.py assets.py warmup.py ./
COPY assets ./assets
RUN useradd --create-home --uid 10001 appuser
USER appuser
RUN python warmup.py
COPY session.py server.py workbench.html ./
CMD ["python", "server.py"]
