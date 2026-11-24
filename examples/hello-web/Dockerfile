FROM public.ecr.aws/docker/library/python:3.12-slim

# Run as an unprivileged user; the server needs nothing else.
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY server.py .
USER app

ENV PORT=8080 PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "server.py"]
