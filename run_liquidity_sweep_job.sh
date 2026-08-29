#!/bin/sh

set -eu

cron_environment=$(tr '\000' '\n' </proc/1/environ)
while IFS= read -r entry; do
    case "$entry" in
        TELEGRAM_BOT_TOKEN=*|TELEGRAM_CHAT_ID=*|TRADING_SYMBOL=*|PRICE_SOURCE=*)
            export "$entry"
            ;;
    esac
done <<EOF
$cron_environment
EOF

exec su -s /bin/sh app -c \
    'exec /usr/local/bin/python liquidity_sweep.py --symbol "${TRADING_SYMBOL:-PAXG/USDT}"'
