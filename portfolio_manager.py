#!/usr/bin/env python3
"""Interactive portfolio manager. Run locally with DATABASE_URL set.

Usage:
    python portfolio_manager.py

Commands at the prompt:
    list                          — show all holdings
    add TICKER SHARES AVG_COST    — add or update a holding
    remove TICKER                 — remove a holding
    quit                          — exit
"""
from __future__ import annotations
import os
import sys


def connect():
    try:
        import psycopg
    except ImportError:
        print("psycopg not installed. Run: pip install 'psycopg[binary]'")
        sys.exit(1)
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL not set. Export it first:\n"
              "  export DATABASE_URL='postgresql://...'")
        sys.exit(1)
    return psycopg.connect(url)


def cmd_list(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ticker, shares, avg_cost, purchase_date "
            "FROM portfolio ORDER BY ticker"
        )
        rows = cur.fetchall()
    if not rows:
        print("  (no holdings)")
        return
    print(f"\n  {'TICKER':<8} {'SHARES':>10} {'AVG COST':>10} {'DATE'}")
    print(f"  {'-'*8} {'-'*10} {'-'*10} {'-'*10}")
    for ticker, shares, avg_cost, purchase_date in rows:
        date_str = str(purchase_date) if purchase_date else "—"
        print(f"  {ticker:<8} {shares:>10.4f} {avg_cost:>10.2f} {date_str}")
    print()


def cmd_add(conn, parts):
    if len(parts) < 3:
        print("  Usage: add TICKER SHARES AVG_COST  (e.g. add AAPL 10 150.00)")
        return
    ticker = parts[0].upper()
    try:
        shares = float(parts[1])
        avg_cost = float(parts[2])
    except ValueError:
        print("  SHARES and AVG_COST must be numbers.")
        return
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO portfolio (ticker, shares, avg_cost)
            VALUES (%s, %s, %s)
            ON CONFLICT DO NOTHING
        """, (ticker, shares, avg_cost))
        if cur.rowcount == 0:
            # already exists — update it
            cur.execute("""
                UPDATE portfolio SET shares = %s, avg_cost = %s
                WHERE ticker = %s
            """, (shares, avg_cost, ticker))
            print(f"  Updated {ticker}: {shares} shares @ ${avg_cost:.2f}")
        else:
            print(f"  Added {ticker}: {shares} shares @ ${avg_cost:.2f}")
    conn.commit()


def cmd_remove(conn, parts):
    if not parts:
        print("  Usage: remove TICKER  (e.g. remove AAPL)")
        return
    ticker = parts[0].upper()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM portfolio WHERE ticker = %s", (ticker,))
        if cur.rowcount == 0:
            print(f"  {ticker} not found in portfolio.")
        else:
            print(f"  Removed {ticker}.")
    conn.commit()


def main():
    print("\n=== Portfolio Manager ===")
    print("Commands: list | add TICKER SHARES AVG_COST | remove TICKER | quit\n")
    conn = connect()
    cmd_list(conn)
    while True:
        try:
            line = input("portfolio> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break
        if not line:
            continue
        parts = line.split()
        cmd = parts[0].lower()
        args = parts[1:]
        if cmd in ("quit", "exit", "q"):
            print("Bye.")
            break
        elif cmd == "list":
            cmd_list(conn)
        elif cmd == "add":
            cmd_add(conn, args)
        elif cmd == "remove":
            cmd_remove(conn, args)
        else:
            print(f"  Unknown command '{cmd}'. Try: list, add, remove, quit")
    conn.close()


if __name__ == "__main__":
    main()
