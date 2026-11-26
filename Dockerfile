FROM python:3.12-slim
WORKDIR /srv
COPY app app
COPY kb kb
COPY static static
RUN python3 app/seed_data.py
ENV PORT=8000 COOKIE_SECURE=1
EXPOSE 8000
CMD ["python3", "app/server.py"]
