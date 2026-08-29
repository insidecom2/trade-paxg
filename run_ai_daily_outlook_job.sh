#!/bin/sh

set -eu

cron_environment=$(tr '\000' '\n' </proc/1/environ)
while IFS= read -r entry; do
    case "$entry" in
        TELEGRAM_BOT_TOKEN=*|TELEGRAM_CHAT_ID=*|TRADING_SYMBOL=*|PRICE_SOURCE=*|OPENAI_API_KEY=*|OPENAI_MODEL=*|AI_ANALYSIS_ENABLED=*|AI_PRICE_SOURCE=*|AI_TRADING_SYMBOL=*|TWELVEDATA_API_KEY=*|FRED_API_KEY=*)
            export "$entry"
            ;;
    esac
done <<EOF
$cron_environment
EOF

exec su -s /bin/sh app -c \
    'exec /usr/local/bin/python ai_analysis.py --symbol "${TRADING_SYMBOL:-PAXG/USDT}"'
