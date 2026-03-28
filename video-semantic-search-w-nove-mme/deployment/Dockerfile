FROM public.ecr.aws/lambda/python:3.11

# Copy Step Functions pipeline handlers (Docker-based)
COPY lambda/functions/shot_segmentation_function.py ${LAMBDA_TASK_ROOT}/
COPY lambda/functions/celebrity_detection_function.py ${LAMBDA_TASK_ROOT}/
COPY lambda/functions/caption_function.py ${LAMBDA_TASK_ROOT}/

# Copy shared library modules
COPY lib/vector_store.py ${LAMBDA_TASK_ROOT}/
COPY lib/dynamodb_store.py ${LAMBDA_TASK_ROOT}/
COPY lib/search_engine.py ${LAMBDA_TASK_ROOT}/
COPY lib/prompt_analyzer.py ${LAMBDA_TASK_ROOT}/
COPY lib/nova_embeddings.py ${LAMBDA_TASK_ROOT}/

RUN yum install -y tar xz && \
    curl -L https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz -o /tmp/ffmpeg.tar.xz && \
    tar -xf /tmp/ffmpeg.tar.xz -C /tmp && \
    mv /tmp/ffmpeg-*-amd64-static/ffmpeg /usr/local/bin/ && \
    mv /tmp/ffmpeg-*-amd64-static/ffprobe /usr/local/bin/ && \
    rm -rf /tmp/ffmpeg* && \
    pip install --no-cache-dir opensearch-py requests-aws4auth

CMD ["shot_segmentation_function.lambda_handler"]
