#!/bin/bash
cd "$(dirname "$0")" || exit 1
if ! command -v npm >/dev/null 2>&1; then
  echo "Node.js and npm are required. See README.md."
  read -r -p "Press Return to close."
  exit 1
fi
if [ ! -x node_modules/.bin/electron ]; then
  echo "Dependencies are missing. Run npm install in this folder first. See README.md."
  read -r -p "Press Return to close."
  exit 1
fi
npm start
