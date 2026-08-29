FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Bangkok

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y cron tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

RUN addgroup --system app \
    && adduser --system --ingroup app app \
    && chown app:app /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app . .
COPY --chown=root:root trade-paxg.cron /etc/cron.d/trade-paxg

RUN chmod 0644 /etc/cron.d/trade-paxg
RUN chmod 0755 /app/run_cron_job.sh /app/run_exit_profit_job.sh /app/run_liquidity_sweep_job.sh /app/run_ai_daily_outlook_job.sh

USER app

EXPOSE 8002

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "80"]
