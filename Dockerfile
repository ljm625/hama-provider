FROM python:3.12-slim

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir .

ENV HAMA_HOST=0.0.0.0
ENV HAMA_PORT=34567
EXPOSE 34567

CMD ["hama-provider"]
