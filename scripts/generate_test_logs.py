#!/usr/bin/env python3
"""Generate deterministic log fixtures and optional live append streams for QA."""

from __future__ import annotations

import argparse
import json
import random
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path


LEVELS = ("TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "FATAL")


def timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def text_event(value: datetime, index: int, level: str) -> str:
    component = ("source.main.agent", "ce.link.tcp.control", "worker.pool", "api.gateway")[index % 4]
    messages = (
        "Starting program",
        "Building dependencies",
        f"Retry connect to 10.20.{index % 255}.{(index * 7) % 255}",
        f"Request completed request_id={1000 + index} duration_ms={index % 47}",
        f"Request to JSON failed request_id={1000 + index}",
        "Unicode payload: Привет мир — 東京 — café",
    )
    message = messages[index % len(messages)]
    body = f"{timestamp(value)} {level:<7} {component}: {message}\n"
    if level in {"ERROR", "CRITICAL", "FATAL"}:
        body += (
            f"Traceback (most recent call last):\n"
            f"  stack frame: example.py:{10 + index}\n"
            f"  stack frame: worker.py:{22 + index}\n"
            f"ValueError: generated failure {index}\n"
        )
    return body


def trading_event(value: datetime, index: int) -> str:
    iso = value.isoformat(timespec="microseconds")
    side = "BUY" if index % 2 == 0 else "SELL"
    return (
        f"{timestamp(value)} INFO  tll.channel._test: Post message: type: Data, "
        f"msgid: {3001 + index % 10}, name: price_info, seq: {index}, size: 276\n"
        "header:\n"
        f"  id: \"quote-lite-{index % 3}\"\n"
        f"  local_time: {iso}\n"
        "  market_time: 1970-01-01T00:00:00\n"
        f"  op_id: {2400 + index}\n"
        "prices:\n"
        f"  - price: {5 + index / 10:.5f}\n"
        f"    market_time: {iso}\n"
        f"    type: {side}\n"
        "    source: BASE_M\n"
        f"  - price: {-2.3 + index / 100:.5f}\n"
        "    market_time: 1970-01-01T00:00:00\n"
        "    type: DEFAULT\n"
        "    source: D\n"
    )


def json_event(value: datetime, index: int, level: str) -> str:
    payload = {
        "timestamp": value.isoformat().replace("+00:00", "Z"),
        "level": level.lower(),
        "message": f"structured event {index}",
        "request": {"id": 5000 + index, "path": f"/v1/items/{index % 11}"},
        "duration": index % 83,
        "success": level not in {"ERROR", "CRITICAL", "FATAL"},
        "nullable": None,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


def write_initial_set(output: Path, count: int, seed: int) -> None:
    rng = random.Random(seed)
    output.mkdir(parents=True, exist_ok=True)
    base = datetime(2026, 9, 21, 13, 46, 33, 138000, tzinfo=UTC)

    text_lines = ["startup banner without timestamp\n"]
    trading_lines: list[str] = []
    json_lines: list[str] = []
    syslog_lines: list[str] = []
    custom_lines: list[str] = []
    for index in range(count):
        value = base + timedelta(milliseconds=index * 7)
        level = rng.choice(LEVELS)
        text_lines.append(text_event(value, index, level))
        trading_lines.append(trading_event(value, index))
        json_lines.append(json_event(value, index, level))
        syslog_lines.append(value.strftime("Sep %d %H:%M:%S") + f" host service[{100 + index}]: event-{index}\n")
        custom_lines.append(value.strftime("time=%d/%m/%Y %H:%M:%S") + f" level={level} custom-{index}\n")

    (output / "application.log").write_text("".join(text_lines), encoding="utf-8")
    (output / "trading.log").write_text("".join(trading_lines), encoding="utf-8")
    (output / "structured.jsonl").write_text("".join(json_lines), encoding="utf-8")
    (output / "syslog.log").write_text("".join(syslog_lines), encoding="utf-8")
    (output / "custom.log").write_text("".join(custom_lines), encoding="utf-8")
    (output / "no-timestamp.log").write_text(
        "plain startup line\ncontinuation only\nUnicode: Привет — 東京\n", encoding="utf-8"
    )
    (output / "malformed-json.log").write_text(
        '{"timestamp":"not-a-date","level":"info","message":"bad timestamp"}\n'
        "{this is not valid json}\n"
        '["JSON array is not an object"]\n',
        encoding="utf-8",
    )


def stream(output: Path, iterations: int, interval: float, seed: int) -> None:
    rng = random.Random(seed)
    base = datetime.now(UTC)
    targets = {
        "application.log": lambda value, index, level: text_event(value, index, level),
        "trading.log": lambda value, index, level: trading_event(value, index),
        "structured.jsonl": json_event,
    }
    for index in range(iterations):
        value = base + timedelta(milliseconds=index)
        level = rng.choice(LEVELS)
        for name, factory in targets.items():
            with (output / name).open("a", encoding="utf-8") as handle:
                handle.write(factory(value, index, level))
                handle.flush()
        if interval:
            time.sleep(interval)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("generated-logs"))
    parser.add_argument("--count", type=int, default=40, help="events per timestamped fixture")
    parser.add_argument("--seed", type=int, default=20260921)
    parser.add_argument("--stream", action="store_true", help="append live events after fixture creation")
    parser.add_argument("--iterations", type=int, default=20, help="live append iterations")
    parser.add_argument("--interval", type=float, default=0.25, help="seconds between live appends")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.count < 1 or args.iterations < 0 or args.interval < 0:
        raise SystemExit("count must be positive; iterations and interval must be non-negative")
    write_initial_set(args.output, args.count, args.seed)
    if args.stream:
        stream(args.output, args.iterations, args.interval, args.seed + 1)
    print(args.output.resolve())


if __name__ == "__main__":
    main()
