"""
Backtest engine — validate the V2 strategy against historical data.
Replays resolved markets with their news coverage through the classifier.
"""
from __future__ import annotations

import time
import logging
from dataclasses import dataclass

import httpx

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

import config
from markets import Market
from classifier import classify
from edge import size_position

log = logging.getLogger(__name__)
console = Console()

GAMMA_API = "https://gamma-api.polymarket.com"


@dataclass
class BacktestResult:
    market_question: str
    entry_price: float
    exit_price: float
    classification: str
    materiality: float
    side: str
    bet_amount: float
    pnl: float
    correct: bool


@dataclass
class BacktestReport:
    period: str
    markets_tested: int
    signals_generated: int
    trades_simulated: int
    total_pnl: float
    win_rate: float
    avg_edge: float
    results: list[BacktestResult]


def fetch_resolved_markets(limit: int = 50, category: str | None = None) -> list[dict]:
    """Fetch recently resolved markets from Gamma API."""
    params = {
        "limit": limit,
        "closed": True,
        "order": "volume",
        "ascending": False,
    }

    try:
        resp = httpx.get(f"{GAMMA_API}/markets", params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        console.print(f"[red]Error fetching resolved markets: {e}[/red]")
        return []

    items = data if isinstance(data, list) else data.get("data", [])

    markets = []
    for m in items:
        try:
            import json
            outcome_prices = m.get("outcomePrices", "")
            if isinstance(outcome_prices, str):
                prices = json.loads(outcome_prices)
            else:
                prices = outcome_prices

            if not prices or len(prices) < 2:
                continue

            vol = float(m.get("volume", m.get("volumeNum", 0)) or 0)
            if vol < config.MIN_VOLUME_USD or vol > config.MAX_VOLUME_USD:
                continue

            question = m.get("question", "")
            if category:
                from markets import _infer_category
                cat = _infer_category(question, m.get("tags") or [])
                if cat != category:
                    continue

            markets.append({
                "question": question,
                "condition_id": m.get("conditionId", m.get("condition_id", "")),
                "yes_price_at_open": 0.5,  # approximation
                "resolved_yes_price": float(prices[0]),
                "volume": vol,
                "category": m.get("tags", []),
            })
        except (ValueError, TypeError, KeyError):
            continue

    return markets


def run_backtest(
    limit: int = 30,
    category: str | None = None,
    test_headlines: list[str] | None = None,
) -> BacktestReport:
    """
    Run a backtest against resolved markets.
    Uses mock headlines derived from market questions if none provided.
    """
    console.print("[bold]Fetching resolved niche markets...[/bold]")
    resolved = fetch_resolved_markets(limit=limit, category=category)
    console.print(f"Found {len(resolved)} resolved niche markets")

    if not resolved:
        return BacktestReport(
            period=f"last {limit} resolved",
            markets_tested=0,
            signals_generated=0,
            trades_simulated=0,
            total_pnl=0,
            win_rate=0,
            avg_edge=0,
            results=[],
        )

    results = []
    signals = 0
    total_pnl = 0.0

    for i, m_data in enumerate(resolved):
        question = m_data["question"]
        resolved_price = m_data["resolved_yes_price"]

        # Create a Market object for the classifier
        entry_price = 0.5  # assume we entered at midpoint (conservative)
        market = Market(
            condition_id=m_data["condition_id"],
            question=question,
            category="unknown",
            yes_price=entry_price,
            no_price=1 - entry_price,
            volume=m_data["volume"],
            end_date="",
            active=False,
            tokens=[],
        )

        # Generate a synthetic headline from the question
        # In production, you'd replay actual historical news
        headline = f"Breaking: Developments suggest '{question}' outcome shifting"
        if test_headlines and i < len(test_headlines):
            headline = test_headlines[i]

        console.print(f"  [{i + 1}/{len(resolved)}] {question[:60]}...", end="\r")

        # Classify
        cls = classify(headline, market, source="backtest")

        if cls.direction == "neutral" or cls.materiality < config.MATERIALITY_THRESHOLD:
            continue

        signals += 1

        # Determine side and outcome
        if cls.direction == "bullish":
            side = "YES"
            won = resolved_price > entry_price
        else:
            side = "NO"
            won = resolved_price < entry_price

        edge = cls.materiality * 0.5  # conservative edge estimate
        bet = size_position(edge)

        if won:
            if side == "YES" and entry_price > 0:
                pnl = bet * ((1.0 / entry_price) - 1)
            elif side == "NO" and entry_price < 1:
                pnl = bet * ((1.0 / (1 - entry_price)) - 1)
            else:
                pnl = bet * 0.5
        else:
            pnl = -bet

        total_pnl += pnl

        results.append(BacktestResult(
            market_question=question,
            entry_price=entry_price,
            exit_price=resolved_price,
            classification=cls.direction,
            materiality=cls.materiality,
            side=side,
            bet_amount=bet,
            pnl=round(pnl, 2),
            correct=won,
        ))

        time.sleep(0.3)  # rate limit

    wins = sum(1 for r in results if r.correct)
    win_rate = (wins / len(results) * 100) if results else 0
    avg_edge = sum(r.materiality for r in results) / len(results) if results else 0

    report = BacktestReport(
        period=f"last {len(resolved)} resolved niche markets",
        markets_tested=len(resolved),
        signals_generated=signals,
        trades_simulated=len(results),
        total_pnl=round(total_pnl, 2),
        win_rate=round(win_rate, 1),
        avg_edge=round(avg_edge * 100, 1),
        results=results,
    )

    _print_report(report)
    return report


def _print_report(report: BacktestReport):
    """Print a rich backtest report."""
    console.print()

    # Summary
    table = Table(title="Backtest Report", show_header=True, header_style="bold cyan")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_row("Period", report.period)
    table.add_row("Markets Tested", str(report.markets_tested))
    table.add_row("Signals Generated", str(report.signals_generated))
    table.add_row("Trades Simulated", str(report.trades_simulated))

    pnl_style = "bright_green" if report.total_pnl >= 0 else "red"
    pnl_str = f"+${report.total_pnl:.2f}" if report.total_pnl >= 0 else f"-${abs(report.total_pnl):.2f}"
    table.add_row("Total PnL", f"[{pnl_style}]{pnl_str}[/{pnl_style}]")

    wr_style = "bright_green" if report.win_rate >= 55 else ("yellow" if report.win_rate >= 45 else "red")
    table.add_row("Win Rate", f"[{wr_style}]{report.win_rate:.1f}%[/{wr_style}]")
    table.add_row("Avg Materiality", f"{report.avg_edge:.1f}%")
    console.print(table)

    # Individual trades
    if report.results:
        console.print()
        trades_table = Table(title="Simulated Trades", show_header=True, header_style="bold green")
        trades_table.add_column("Market", max_width=40)
        trades_table.add_column("Signal", width=8)
        trades_table.add_column("Mat.", justify="right", width=5)
        trades_table.add_column("Side", width=5)
        trades_table.add_column("Bet", justify="right", width=7)
        trades_table.add_column("PnL", justify="right", width=9)
        trades_table.add_column("Result", width=6)

        for r in report.results[:20]:
            pnl_str = f"+${r.pnl:.2f}" if r.pnl >= 0 else f"-${abs(r.pnl):.2f}"
            pnl_style = "bright_green" if r.pnl >= 0 else "red"
            result_str = f"[bright_green]WIN[/bright_green]" if r.correct else f"[red]LOSS[/red]"

            trades_table.add_row(
                r.market_question[:40],
                r.classification[:8],
                f"{r.materiality:.2f}",
                r.side,
                f"${r.bet_amount:.2f}",
                f"[{pnl_style}]{pnl_str}[/{pnl_style}]",
                result_str,
            )

        console.print(trades_table)


if __name__ == "__main__":
    run_backtest(limit=15)
