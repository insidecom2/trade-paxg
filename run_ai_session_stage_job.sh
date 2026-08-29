#!/bin/sh

set -eu

# Runs one of the four session-time AI analysis stages (session_preparation,
# setup_detection, setup_confirmation, final_session_decision). The
# 08:00 DAILY_OUTLOOK stage has its own launcher (run_ai_daily_outlook_job.sh)
# since it predates this one and needs no stage argument.

cron_environment=$(tr '\000' '\n' </proc/1/environ)
while IFS= read -r entry; do
    case "$entry" in
        TELEGRAM_BOT_TOKEN=*|TELEGRAM_CHAT_ID=*|TRADING_SYMBOL=*|PRICE_SOURCE=*|OPENAI_API_KEY=*|OPENAI_MODEL=*|AI_ANALYSIS_ENABLED=*|AI_PRICE_SOURCE=*|AI_TRADING_SYMBOL=*|TWELVEDATA_API_KEY=*|FRED_API_KEY=*|AI_NEWS_CALENDAR_ENABLED=*)
            export "$entry"
            ;;
    esac
done <<EOF
$cron_environment
EOF

# A positional stage name is required — each cron entry passes its own stage
# explicitly rather than relying on an environment default.
stage="${1:?usage: run_ai_session_stage_job.sh <session_preparation|setup_detection|setup_confirmation|final_session_decision>}"

exec su -s /bin/sh app -c \
    "exec /usr/local/bin/python ai_analysis.py --symbol \"\${TRADING_SYMBOL:-PAXG/USDT}\" --stage '$stage'"
