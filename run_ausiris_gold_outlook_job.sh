#!/bin/sh

set -eu

# Debian cron starts with a minimal environment. Copy only Telegram settings
# required by this independent Ausiris text notification worker.
cron_environment=$(tr '\000' '\n' </proc/1/environ)
while IFS= read -r entry; do
    case "$entry" in
        TELEGRAM_BOT_TOKEN=*|TELEGRAM_CHAT_ID=*)
            export "$entry"
            ;;
    esac
done <<EOF
$cron_environment
EOF

exec su -s /bin/sh app -c 'exec /usr/local/bin/python ausiris_gold_outlook.py'
