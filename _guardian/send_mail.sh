#!/bin/bash
# Usage: send_mail.sh <to_email> <subject> <body_file>
TO="$1"
SUBJECT="$2"
BODY_FILE="$3"
LOG="/www/wwwroot/quipper.mabdc.com/_guardian/mail.log"

if [ -z "$TO" ] || [ -z "$SUBJECT" ] || [ ! -f "$BODY_FILE" ]; then
    echo "Usage: send_mail.sh <to> <subject> <body_file>" >&2
    exit 1
fi

BODY=$(cat "$BODY_FILE")

MSG_FILE=$(mktemp)
cat > "$MSG_FILE" <<EOF
From: Quipper Admin <admin@mabdc.ae>
To: ${TO}
Subject: ${SUBJECT}
Content-Type: text/plain; charset=utf-8
MIME-Version: 1.0

${BODY}
EOF

curl --silent --show-error \
    --ssl-reqd \
    --url 'smtp://mail.mabdc.ae:587' \
    --user 'admin@mabdc.ae:Denskie123' \
    --mail-from 'admin@mabdc.ae' \
    --mail-rcpt "${TO}" \
    --upload-file "$MSG_FILE" >> "$LOG" 2>&1

RESULT=$?
rm -f "$MSG_FILE"
if [ $RESULT -ne 0 ]; then
    echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] FAIL to=${TO} subject=\"${SUBJECT}\" exit=${RESULT}" >> "$LOG"
fi
exit $RESULT
