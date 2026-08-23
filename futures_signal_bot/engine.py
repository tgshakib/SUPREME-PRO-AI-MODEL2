"""Future Signal • TG signal generator.

This is isolated from the Binary/Forex engines.  It preserves the upstream
project's 11-indicator, multi-timeframe style and Telegram HTML output flow.
When the locally running combined OTC feed has fresh candles, those candles are
used.  The copied upstream source's algorithmic candle generator is retained as
a clearly labelled fallback because its Pocket Option adapter has no live API
implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import random
import re
import time
from typing import Iterable

from .config import DEFAULT_UTC_OFFSET


@dataclass(frozen=True)
class Quality:
    direction: str
    confidence: int
    confirmed: bool


def _clean_symbol(asset: str) -> str:
    symbol = re.sub(r"\s*\(OTC\)\s*$", "", asset, flags=re.I).replace("/", "")
    return re.sub(r"\s+", "", symbol).upper()


def _otc_key(asset: str) -> str:
    symbol = _clean_symbol(asset)
    aliases = {"GOLD": "XAUUSD", "SILVER": "XAGUSD", "BITCOIN": "BTCUSD"}
    return f"{aliases.get(symbol, symbol)}-OTC"


def _algorithmic_candles(asset: str, count: int = 60) -> list[dict]:
    """Faithful local fallback style from the copied upstream adapter.

    It is deliberately not presented as a broker API.  The seed changes every
    fifteen minutes, which is how the upstream repository avoided permanent
    deterministic output.
    """
    seed = hash(f"{asset}:1:{int(time.time() // 900)}") & 0xFFFFFFFF
    rng = random.Random(seed)
    base = 0.9 + rng.random() * 1.5
    volatility = 0.0008 + rng.random() * 0.0015
    price = base
    momentum = 0.0
    volume_base = 800 + rng.random() * 1500
    now = int(time.time())
    out: list[dict] = []
    for index in range(count, 0, -1):
        noise = (rng.random() - 0.5) * volatility * 2
        reversion = (base - price) * 0.03
        momentum = momentum * 0.72 + noise * 0.28 + reversion
        change = momentum + (rng.random() - 0.5) * volatility * 0.4
        opening = price
        closing = round(price + change, 5)
        body = abs(closing - opening)
        upper = round(max(opening, closing) + body * (0.3 + rng.random() * 0.9) * rng.random(), 5)
        lower = round(min(opening, closing) - body * (0.3 + rng.random() * 0.9) * rng.random(), 5)
        volume_base = volume_base * 0.88 + (400 + rng.random() * 1800) * 0.12
        out.append({
            "time": now - index * 60,
            "open": round(opening, 5),
            "high": upper,
            "low": lower,
            "close": closing,
            "volume": int(volume_base * (0.6 + abs(change) / volatility)),
        })
        price = closing
    return out


def _local_otc_candles(asset: str, market: str) -> tuple[list[dict], str] | None:
    try:
        from otc_feed_combined import otc_feed
        key = _otc_key(asset)
        if market == "po":
            candles = otc_feed.po.get_candles(key, "1m", 60)
            label = "LOCAL PO OTC FEED"
        elif market == "quotex":
            candles = otc_feed.qx.get_candles(key, "1m", 60)
            label = "LOCAL QUOTEX OTC FEED"
        else:
            candles = otc_feed.get_candles(key, "1m", 60)
            label = "LOCAL OTC FEED"
        if candles and len(candles) >= 20:
            return candles[-60:], label
    except Exception:
        # The upstream behaviour also drops to its local generator when a
        # broker connection is unavailable.
        pass
    return None


def get_candles(asset: str, market: str) -> tuple[list[dict], str]:
    if market != "real":
        live = _local_otc_candles(asset, market)
        if live:
            return live
    return _algorithmic_candles(asset), "UPSTREAM ADAPTER FALLBACK"


def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    period = max(1, min(period, len(values)))
    value = sum(values[:period]) / period
    factor = 2 / (period + 1)
    for item in values[period:]:
        value = item * factor + value * (1 - factor)
    return value


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) < 3:
        return 50
    values = closes[-(period + 1):]
    gains = sum(max(values[i] - values[i - 1], 0) for i in range(1, len(values)))
    losses = sum(max(values[i - 1] - values[i], 0) for i in range(1, len(values)))
    if losses == 0:
        return 100
    return 100 - 100 / (1 + gains / losses)


def _vote_quality(candles: list[dict]) -> Quality:
    if len(candles) < 12:
        return Quality("CALL", 42, False)
    closes = [float(c["close"]) for c in candles]
    votes: list[tuple[int, float]] = []
    vote = lambda direction, weight: votes.append((direction, weight))
    n = len(closes)

    # Same eleven signal families as the copied source: EMA fast/macro, RSI,
    # stochastic, momentum, consistency, MACD, Bollinger, volume, candle and
    # EMA slope.  The output is intentionally isolated from the main engines.
    vote(1 if _ema(closes, 5) > _ema(closes, 13) else -1, 2)
    vote(1 if _ema(closes, min(21, n)) > _ema(closes, min(55, n)) else -1, 2)
    rsi = _rsi(closes)
    vote(1 if rsi > 55 and rsi < 75 or rsi <= 25 else -1 if rsi < 45 or rsi >= 75 else 0, 1.5)
    recent = candles[-14:]
    high, low, last = max(c["high"] for c in recent), min(c["low"] for c in recent), recent[-1]
    stoch = 50 if high == low else (last["close"] - low) / (high - low) * 100
    vote(1 if 65 < stoch < 85 or stoch <= 15 else -1 if stoch < 35 or stoch >= 85 else 0, 1.5)
    last_three = candles[-3:]
    momentum = sum(1 if c["close"] > c["open"] else -1 if c["close"] < c["open"] else 0 for c in last_three)
    vote(1 if momentum > 0 else -1 if momentum < 0 else 0, 1)
    moves = [closes[i] - closes[i - 1] for i in range(n - 4, n)]
    consistency = sum(1 if change > 0 else -1 if change < 0 else 0 for change in moves)
    vote(1 if consistency >= 2 else -1 if consistency <= -2 else 0, 1.5)
    macd = _ema(closes, 12) - _ema(closes, 26)
    previous_macd = _ema(closes[:-1], 12) - _ema(closes[:-1], 26)
    vote(1 if macd > 0 else -1 if macd < 0 else 0, 2 if abs(macd) > abs(previous_macd) else 1)
    sample = closes[-20:]
    mean = sum(sample) / len(sample)
    std = math.sqrt(sum((v - mean) ** 2 for v in sample) / len(sample))
    bb_position = .5 if std == 0 else (closes[-1] - (mean - 2 * std)) / (4 * std)
    vote(1 if bb_position > .62 else -1 if bb_position < .38 else 0, 1)
    volumes = [float(c.get("volume", 1000)) for c in candles]
    average_volume = sum(volumes[:-1]) / max(1, len(volumes) - 1)
    vote(1 if last["close"] >= last["open"] else -1, 1.5 if volumes[-1] > average_volume * 1.3 else .75 if volumes[-1] > average_volume * 1.1 else 0)
    body, full_range = abs(last["close"] - last["open"]), last["high"] - last["low"]
    upper_wick = last["high"] - max(last["open"], last["close"])
    lower_wick = min(last["open"], last["close"]) - last["low"]
    if full_range and body / full_range >= .10:
        vote(1 if lower_wick > body * 1.5 else -1 if upper_wick > body * 1.5 else 1 if last["close"] >= last["open"] else -1, 1.5)
    ema_now = _ema(closes, 5)
    ema_then = _ema(closes[:-3], 5)
    vote(1 if ema_now > ema_then else -1 if ema_now < ema_then else 0, 1)

    call_weight = sum(weight for direction, weight in votes if direction == 1)
    put_weight = sum(weight for direction, weight in votes if direction == -1)
    direction = "CALL" if call_weight >= put_weight else "PUT"
    winner = call_weight if direction == "CALL" else put_weight
    total = sum(weight for _, weight in votes) or 1
    confidence = min(99, round(winner / total * 100))
    winner_votes = sum(1 for item, _ in votes if item == (1 if direction == "CALL" else -1))
    ranges = [c["high"] - c["low"] for c in candles[-7:]]
    movement = sum(abs(v) for v in moves) or 1
    trend = abs(sum(moves)) / movement * 100
    return Quality(direction, confidence, winner_votes >= 7 and sum(ranges) / len(ranges) > 0 and trend >= 15 and confidence >= 65)


def _compress(candles: list[dict], step: int) -> list[dict]:
    return [{
        "time": group[0]["time"], "open": group[0]["open"],
        "high": max(c["high"] for c in group), "low": min(c["low"] for c in group),
        "close": group[-1]["close"], "volume": sum(c.get("volume", 0) for c in group),
    } for group in (candles[index:index + step] for index in range(0, len(candles) - step + 1, step))]


def _triple_timeframe(candles: list[dict]) -> Quality:
    short = _vote_quality(candles)
    middle = _vote_quality(_compress(candles, 3))
    macro = _vote_quality(_compress(candles, 6))
    confidence = short.confidence
    confirmed = short.confirmed
    if middle.direction == short.direction:
        confidence = min(99, confidence + 3)
        confirmed = confirmed and middle.confidence >= 58
    else:
        return Quality(short.direction, max(35, confidence - 16), False)
    if macro.direction == short.direction:
        return Quality(short.direction, min(99, confidence + 3), confirmed and macro.confidence >= 52)
    return Quality(short.direction, max(40, confidence - 8), False)


def _directions(count: int, preferred: str, forced: str, otc: bool) -> Iterable[str]:
    if forced in {"CALL", "PUT"}:
        return [forced] * count
    other = "PUT" if preferred == "CALL" else "CALL"
    output: list[str] = []
    for index in range(count):
        # OTC max streak of one reproduces the source's conservative mixing.
        if otc and output and output[-1] == preferred:
            output.append(other)
        else:
            output.append(preferred if index % 3 != 1 else other)
    return output


def generate_message(assets: list[str], market: str, direction: str, count: int,
                     utc_offset: int = DEFAULT_UTC_OFFSET) -> str:
    now = datetime.now(timezone.utc) + timedelta(hours=utc_offset)
    market_label = {
        "real": "Real Market", "quotex": "Quotex OTC", "po": "Pocket Option OTC",
        "iq": "IQ Option OTC", "olymp": "Olymp Trade OTC",
    }.get(market, "OTC Market")
    otc = market != "real"
    header = (
        "<b>━━━━━━━━━・━━━━━━━━━</b>\n"
        f"<b>        𝗗𝗮𝘁𝗲: {now:%d/%m/%Y}</b>\n"
        f"<b>  𝗧𝗶𝗺𝗲 𝗭𝗼𝗻𝗲: UTC{utc_offset:+d}</b>\n"
        "<b>      𝗠𝗼𝗱𝗲: FUTURE SIGNAL • TG</b>\n"
        f"<b>    𝗠𝗮𝗿𝗸𝗲𝘁: {market_label}</b>\n"
        f"<b>  𝗗𝗶𝗿𝗲𝗰𝘁𝗶𝗼𝗻: {direction}</b>\n"
        "<b>•••••••••••••••••••••••••••••••••••••••</b>\n"
        "<b> Community @TRADERGUIDE_BOT</b>\n"
        "<b>•••••••••••••••••••••••••••••••••••••••</b>\n"
    )
    blocks: list[str] = []
    sources: set[str] = set()
    for asset in assets:
        candles, source = get_candles(asset, market)
        sources.add(source)
        quality = _triple_timeframe(candles) if otc else _vote_quality(candles)
        name = _clean_symbol(asset) + ("-OTC" if otc else "")
        lines = [f"<b>▎{name} — 🎯 {quality.confidence}%</b>"]
        signal_time = now + timedelta(minutes=1)
        one_minute_gaps = (5, 6, 3, 7, 12)
        for index, signal_direction in enumerate(_directions(count, quality.direction, direction, otc)):
            lines.append(f"<b>{signal_time:%H:%M} {name} {signal_direction}</b>")
            gap_minutes = one_minute_gaps[index % len(one_minute_gaps)]
            signal_time += timedelta(minutes=gap_minutes if otc else max(1, gap_minutes - 2))
        blocks.append("\n".join(lines))
    source_label = " + ".join(sorted(sources))
    return f"{header}\n\n" + "\n\n".join(blocks) + f"\n\n<i>Data mode: {source_label}</i>"