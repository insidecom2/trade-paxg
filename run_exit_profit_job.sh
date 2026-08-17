#!/bin/sh

set -eu

# Debian cron starts jobs with a minimal environment. Copy only the settings
# this job needs from the cron daemon (PID 1), which inherited Docker env_file.
cron_environment=$(tr '\000' '\n' </proc/1/environ)
while IFS= read -r entry; do
    case "$entry" in
        TELEGRAM_BOT_TOKEN=*|TELEGRAM_CHAT_ID=*|TRADING_SYMBOL=*|PRICE_SOURCE=*|MYSQL_HOST=*|MYSQL_PORT=*|MYSQL_DATABASE=*|MYSQL_USER=*|MYSQL_PASSWORD=*|MYSQL_PRICE_TABLE=*)
            export "$entry"
            ;;
    esac
done <<EOF
$cron_environment
EOF

exec su -s /bin/sh app -c \
    'exec /usr/local/bin/python exit_profit.py --symbol "${TRADING_SYMBOL:-PAXG/USDT}"'
