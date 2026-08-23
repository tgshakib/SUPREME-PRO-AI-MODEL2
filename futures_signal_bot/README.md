# Future Signal • TG

This directory is deliberately isolated from the existing Binary, Forex, and
Funded Pass systems.

## Local source copy

`source/api-server/` is a local source/configuration copy of
`tgshakib/FUTURE-SIGNAL-GENERATE-TGLAB`'s API server.  It contains no `.git`
history, executable, or embedded archive.  The original TypeScript bot is not
started: starting a second Telegram polling process with the same token would
cause update conflicts.

## Runtime path

The existing aiogram bot invokes `engine.py` through
`handlers/futures_signal.py`.  The engine tries the main project's already
running combined OTC feed first.  If it does not have sufficient candles, it
uses the local algorithmic adapter behaviour preserved from the copied source.

The imported repository has no implemented Pocket Option HTTP/WebSocket API
call; its Pocket Option adapter also falls back to generated candles.  This
feature therefore does not claim that fallback signals are broker-live.