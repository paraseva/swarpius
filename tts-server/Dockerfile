# The base image must match the host's installed CUDA version. Only certain
# PyTorch + CUDA combos might exist in the registry — check available tags at
# https://hub.docker.com/r/pytorch/pytorch/tags before changing.
# Tested: pytorch 2.10.0 + CUDA 12.8 (RTX 5090).
#
# Build stage uses -devel for CUDA kernel compilation. Runtime stage uses
# -runtime for a smaller final image. The venv is copied between stages.
#
# Pre-built F5-TTS images also exist on Docker Hub and GHCR — if one matches
# your setup you might skip this build entirely. Search for "f5-tts" on
# Docker Hub or check the upstream repo for community images.

ARG PYTORCH_TAG=2.10.0-cuda12.8-cudnn9

# ---------- build stage (devel: has CUDA compiler) ---------------------------
FROM pytorch/pytorch:${PYTORCH_TAG}-devel AS build

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y \
        git build-essential python3-venv \
        libsndfile1-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

SHELL ["/bin/bash", "-c"]

ARG F5_TTS_VERSION=1.1.16

RUN git clone --branch "${F5_TTS_VERSION}" --depth 1 https://github.com/SWivid/F5-TTS.git \
    && cd F5-TTS \
    && git submodule update --init --recursive \
    && python -m venv .venv \
    && source .venv/bin/activate \
    && python -m pip install --no-cache-dir torch==2.10.0 torchaudio==2.10.0 torchcodec==0.10.0 --index-url https://download.pytorch.org/whl/cu128 \
    && python -m pip install --no-cache-dir -e .

# ---------- runtime stage (minimal base, venv has everything Python needs) ---
FROM python:3.12-slim-bookworm

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg sox libsox-fmt-all libsox-fmt-mp3 libsndfile1 \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/local/bin/python3 /usr/bin/python3 \
    && ln -sf /usr/local/bin/python3 /usr/bin/python

ARG SWARPIUS_UID=1000
RUN useradd --create-home --uid ${SWARPIUS_UID} swarpius \
    && mkdir -p /home/swarpius/.cache/huggingface/hub \
                /home/swarpius/.cache/matplotlib \
    && chown -R swarpius:swarpius /home/swarpius

# Copy CUDA shared libraries that the runtime image lacks (nppicc, nvrtc, etc.)
COPY --from=build /usr/local/cuda-12.8/targets/x86_64-linux/lib/*.so* /usr/local/cuda/lib64/
ENV LD_LIBRARY_PATH="/usr/local/cuda/lib64:${LD_LIBRARY_PATH}"

COPY --from=build --chown=swarpius:swarpius /workspace/F5-TTS /workspace/F5-TTS

WORKDIR /workspace/F5-TTS

COPY --chown=swarpius:swarpius run_socket_server.py /workspace/F5-TTS/src/f5_tts/run_socket_server.py

ENV PATH="/workspace/F5-TTS/.venv/bin:$PATH"
ENV HOME=/home/swarpius
USER swarpius

VOLUME /home/swarpius/.cache/huggingface/hub/

WORKDIR /tmp
EXPOSE 9998

CMD ["python", "/workspace/F5-TTS/src/f5_tts/run_socket_server.py"]
