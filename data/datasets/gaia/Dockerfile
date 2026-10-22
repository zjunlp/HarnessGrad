# GAIA's task image: the only dataset here whose environment is *networked on purpose*.
#
# Built once and pinned by content address (`eval/container.py` resolves a tag to its image ID);
# the platform never builds a task image during a run. Recorded here so the image can be rebuilt:
#
#     docker build -t harnessgrad/gaia:20261008 data/datasets/gaia/
#
# What is in it, and why each piece is a *capability the benchmark needs* rather than a
# convenience: `curl` and `ca-certificates` for the live web the questions are about;
# `numpy` because the official scorer imports it (`scorer.py`); `poppler-utils` for the PDF
# attachments (`pdftotext`); `openpyxl` + `pillow` for the xlsx and image attachments. The
# attachments also include audio (`.mp3`, `.MOV`) and video, and nothing here can transcribe
# them -- those tasks are solvable only through the web or not at all, and the dataset does not
# pretend otherwise: the oracle control submits the released answer and proves the *check* is
# reachable, not that a harness can obtain the answer.
FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates poppler-utils file \
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir numpy openpyxl pillow requests beautifulsoup4 lxml

WORKDIR /app
