#!/bin/sh

set -eu

# Debian cron starts jobs with a minimal environment. Copy only values needed
# by this independent Twelve Data and Telegram notification worker.
cron_environment=$(tr '\000' '\n' </proc/1/environ)
while IFS= read -r entry; do
    case "$entry" in
        TELEGRAM_BOT_TOKEN=*|TELEGRAM_CHAT_ID=*|TWELVEDATA_API_KEY=*)
            export "$entry"
            ;;
    esac
done <<EOF
$cron_environment
EOF

exec su -s /bin/sh app -c 'exec /usr/local/bin/python bollinger_wick_alert.py'
