#!/bin/sh
# bot-team を裏で起動してブラウザを開く。すでに起動済みならブラウザを開くだけ。終了は画面左上の ⏻。
cd "$(dirname "$0")"
nohup python app/server.py --open >> app/server.log 2>&1 &
