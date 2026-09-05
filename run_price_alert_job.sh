#!/bin/sh

set -eu

# Debian cron starts jobs with a minimal environment. Copy only the values
# required by this independent XAU/USD alert worker from the container.
cron_environment=$(tr '\000' '\n' </proc/1/environ)
while IFS= read -r entry; do
    case "$entry" in
        TELEGRAM_BOT_TOKEN=*|TELEGRAM_CHAT_ID=*|TWELVEDATA_API_KEY=*|MYSQL_HOST=*|MYSQL_PORT=*|MYSQL_USER=*|MYSQL_PASSWORD=*|MYSQL_DATABASE=*)
            export "$entry"
            ;;
    esac
done <<EOF
$cron_environment
EOF

exec su -s /bin/sh app -c 'exec /usr/local/bin/python price_alert.py'
