FROM python:3.14-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 FINCRIME_LLM=0 FINCRIME_MODEL=/opt/model
COPY system/fincrime/training/bert/requirements-docker.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.14.1+cpu \
    && pip install --no-cache-dir -r /tmp/requirements.txt
COPY system/ /app/system/
COPY dist/portable-hybrid/ /opt/model/
RUN chmod -R a+rX /opt/model
ENV PYTHONPATH=/app/system
RUN useradd --create-home --uid 10001 inference
USER inference
ENTRYPOINT ["python", "-m", "fincrime.training.bert.infer"]
