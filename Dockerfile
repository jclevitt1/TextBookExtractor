FROM public.ecr.aws/lambda/python:3.12

# Install poppler for pdf2image
RUN dnf install -y poppler-utils && dnf clean all

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY textbook_extraction/ ${LAMBDA_TASK_ROOT}/textbook_extraction/

# Lambda handler
CMD ["textbook_extraction.handlers.worker_handler.handler"]
