FROM python:3.14-alpine

WORKDIR /app
COPY server.py .

EXPOSE 9091

ENV MOCK_TOOL_SERVER_ADDR=0.0.0.0
ENV MOCK_TOOL_SERVER_PORT=9091

CMD ["python", "server.py"]
