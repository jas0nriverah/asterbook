#!/bin/bash
cd "$(dirname "$0")" || exit 1
./start.sh
status=$?
echo
if [ "$status" -ne 0 ]; then echo "Setup failed ($status). See messages above."; fi
read -r -p "Press Return to close this window. "
exit "$status"
