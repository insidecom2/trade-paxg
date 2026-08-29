#!/bin/sh

set -eu

# Debian cron starts jobs with a minimal environment. Copy only the settings
# this job needs from the cron daemon (PID 1), which inherited Docker env_file.
cron_environment=$(tr '\000' '\n' </proc/1/environ)
while IFS= read -r entry; do
    case "$entry" in
        TELEGRAM_BOT_TOKEN=*|TELEGRAM_CHAT_ID=*|TRADING_SYMBOL=*|TRADING_TIMEFRAME=*|PRICE_SOURCE=*)
            export "$entry"
            ;;
    esac
done <<EOF
$cron_environment
EOF

# A positional timeframe takes precedence over the container environment. This
# makes the cron entry explicit while retaining the environment fallback for
# manual invocations without an argument.
export TRADING_TIMEFRAME="${1:-${TRADING_TIMEFRAME:-4h}}"

exec su -s /bin/sh app -c \
    'exec /usr/local/bin/python main.py --symbol "${TRADING_SYMBOL:-PAXG/USDT}" --timeframe "${TRADING_TIMEFRAME:-4h}"'
