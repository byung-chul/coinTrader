"""
backtest.py — 여러 코인을 동시에 보면서 과거 데이터로 테스트 (OKX)

작동 방식:
  1. 감시 목록의 모든 코인 데이터를 다운로드
  2. 매 시간마다 모든 코인의 신호를 동시에 확인
  3. 신호가 여러 개면 → 가장 강한 신호의 코인을 우선 매수
  4. 최대 보유 개수(max_positions) 안에서만 매수
"""

import os
import time
import pickle
import yaml
import ccxt
import pandas as pd
from strategy import calculate_indicators, generate_signal
from paper_trader import COIN_NAME

CACHE_DIR = '.cache'


def load_config(path: str = 'config.yaml') -> dict:
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def _fetch_data(exchange: ccxt.okx, market: str, start_date: str, end_date: str, config: dict) -> dict:
    """
    캔들 데이터를 가져오는 함수.
    - 데이터가 많으면 여러 번 나눠서 요청 (페이지네이션)
    - 같은 조건으로 두 번째 실행부터는 저장된 파일을 바로 읽음 (캐시)
    """
    interval = config['trading']['interval']
    os.makedirs(CACHE_DIR, exist_ok=True)
    safe_market = market.replace('/', '_')
    cache_file  = f"{CACHE_DIR}/{safe_market}_{interval}_{start_date}_{end_date}.pkl"

    if os.path.exists(cache_file):
        print(f"   📦 캐시 사용: {COIN_NAME.get(market, market)}")
        with open(cache_file, 'rb') as f:
            return pickle.load(f)

    start_ts = pd.Timestamp(start_date, tz='UTC')
    end_ts   = pd.Timestamp(end_date, tz='UTC') + pd.Timedelta(hours=23, minutes=59)

    # 워밍업용 추가 시간 (EMA60 계산을 위해 이전 캔들 필요)
    interval_minutes = _interval_to_minutes(interval)
    fetch_from = start_ts - pd.Timedelta(minutes=interval_minutes * 250)

    dfs       = []
    since_ms  = int(fetch_from.timestamp() * 1000)
    end_ms    = int(end_ts.timestamp() * 1000)
    req_count = 0

    while since_ms < end_ms:
        ohlcv = None
        for retry in range(3):
            try:
                ohlcv = exchange.fetch_ohlcv(market, timeframe=interval, since=since_ms, limit=300)
                if ohlcv:
                    break
            except Exception:
                pass
            time.sleep(0.5 * (retry + 1))

        if not ohlcv:
            break

        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        df.set_index('timestamp', inplace=True)
        df = df.astype(float)
        dfs.append(df)

        req_count += 1
        last_ts = ohlcv[-1][0]

        if req_count % 10 == 0:
            ts_str = pd.Timestamp(last_ts, unit='ms', tz='UTC').strftime('%m월 %d일 %H:%M')
            print(f"   ⏳ {COIN_NAME.get(market, market)} 다운로드 중... "
                  f"({req_count}번째 요청 / {ts_str} 까지 수집)")

        since_ms = last_ts + 1
        time.sleep(0.2)

    if not dfs:
        raise ConnectionError(f"데이터 없음: {market}")

    full_df = pd.concat(dfs).drop_duplicates().sort_index()
    full_df = calculate_indicators(full_df, config)

    mask   = (full_df.index >= start_ts) & (full_df.index <= end_ts)
    result = {'df': full_df, 'bt_index': full_df[mask].index}

    with open(cache_file, 'wb') as f:
        pickle.dump(result, f)
    print(f"   💾 캐시 저장 완료: {COIN_NAME.get(market, market)} ({len(full_df):,}개 캔들)")

    return result


def _interval_to_minutes(interval: str) -> int:
    """'1m' → 1, '1h' → 60, '1d' → 1440"""
    unit = interval[-1]
    num  = int(interval[:-1])
    return num * {'m': 1, 'h': 60, 'd': 1440}.get(unit, 1)


def _portfolio_snapshot(capital: float, positions: dict, current_prices: dict) -> dict:
    total = capital
    holdings = {}
    for market, pos in positions.items():
        price        = current_prices.get(market, pos['entry_price'])
        current_val  = price * pos['quantity']
        invested     = pos['entry_price'] * pos['quantity']
        profit       = current_val - invested
        profit_pct   = profit / invested * 100
        total       += current_val
        holdings[market] = {
            'name':          COIN_NAME.get(market, market),
            'entry_price':   pos['entry_price'],
            'current_price': price,
            'quantity':      pos['quantity'],
            'invested':      invested,
            'current_val':   current_val,
            'profit':        profit,
            'profit_pct':    profit_pct,
        }
    return {'cash': capital, 'holdings': holdings, 'total': total}


def _print_portfolio(snap: dict, initial_capital: float, label: str = ""):
    total      = snap['total']
    total_pnl  = total - initial_capital
    total_pct  = total_pnl / initial_capital * 100
    cash_pct   = snap['cash'] / total * 100 if total > 0 else 0

    if label:
        print(f"  {'─'*60}")
        print(f"  💼 {label}")
    print(f"  {'─'*60}")

    print(f"  💵 현금        : {snap['cash']:>12.2f} USDT  ({cash_pct:.0f}%)")

    if snap['holdings']:
        for market, h in snap['holdings'].items():
            invested_pct = h['invested'] / snap['total'] * 100
            pnl_emoji    = "📈" if h['profit'] >= 0 else "📉"
            print(
                f"  {pnl_emoji} {h['name']:<8}: {h['current_val']:>12.2f} USDT  "
                f"({invested_pct:.0f}% 투자)  "
                f"손익 {h['profit']:>+.2f} USDT ({h['profit_pct']:>+.1f}%)"
            )
    else:
        print(f"  📭 보유 코인   : 없음")

    pnl_emoji = "📈" if total_pnl >= 0 else "📉"
    print(f"  {'─'*60}")
    print(
        f"  {pnl_emoji} 총 자산      : {total:>12.2f} USDT  "
        f"(시작 대비 {total_pnl:>+.2f} USDT / {total_pct:>+.1f}%)"
    )


def run_backtest(
    markets: list          = None,
    start_date: str        = "2025-02-01",
    end_date: str          = "2025-02-28",
    initial_capital: float = 1500.0,
    config: dict           = None,
):
    if config is None:
        config = load_config()
    if markets is None:
        markets = config['trading']['markets']

    exchange      = ccxt.okx({'enableRateLimit': True})
    max_positions = config['trading']['max_positions']
    names         = [COIN_NAME.get(m, m) for m in markets]

    print(f"\n{'='*64}")
    print(f"  멀티코인 백테스트  [OKX]")
    print(f"  감시 코인  : {', '.join(names)}")
    print(f"  기간       : {start_date} ~ {end_date}")
    print(f"  초기 자금  : {initial_capital:,.2f} USDT")
    print(f"  최대 동시 보유: {max_positions}개")
    print(f"{'='*64}\n")

    # ── 1. 데이터 다운로드 ────────────────────────────────────────
    print("📥 데이터 다운로드 중...")
    all_data = {}
    for market in markets:
        name = COIN_NAME.get(market, market)
        try:
            all_data[market] = _fetch_data(exchange, market, start_date, end_date, config)
            print(f"   ✅ {name} ({len(all_data[market]['bt_index'])}개 캔들)")
        except Exception as e:
            print(f"   ❌ {name} 실패: {e}")

    if not all_data:
        print("데이터를 하나도 불러오지 못했습니다.")
        return

    bt_index = all_data[markets[0]]['bt_index']
    for market in markets[1:]:
        if market in all_data:
            bt_index = bt_index.intersection(all_data[market]['bt_index'])

    print(f"\n📊 공통 백테스트 구간: {len(bt_index)}개 캔들")

    # ── 2. 시뮬레이션 ─────────────────────────────────────────────
    capital   = float(initial_capital)
    positions = {}
    trades    = []
    equity    = []
    events    = []

    for timestamp in bt_index:

        current_prices = {}
        for market in all_data:
            df = all_data[market]['df']
            if timestamp in df.index:
                current_prices[market] = float(df.loc[timestamp, 'close'])

        # ── 청산 조건 확인 ────────────────────────────────────────
        for market in list(positions.keys()):
            pos       = positions[market]
            price     = current_prices.get(market)
            direction = pos.get('direction', 'LONG')
            if price is None:
                continue

            pos['candles_held'] += 1
            exit_reason = None

            if direction == 'LONG':
                if price <= pos.get('liq_price', 0):        exit_reason = 'LIQUIDATION'
                elif price <= pos['stop_loss']:              exit_reason = 'STOP_LOSS'
                elif price >= pos['take_profit']:            exit_reason = 'TAKE_PROFIT'
            else:  # SHORT
                if price >= pos.get('liq_price', float('inf')): exit_reason = 'LIQUIDATION'
                elif price >= pos['stop_loss']:              exit_reason = 'STOP_LOSS'
                elif price <= pos['take_profit']:            exit_reason = 'TAKE_PROFIT'

            if exit_reason is None and pos['candles_held'] >= config['risk']['time_stop_candles']:
                exit_reason = 'TIME_STOP'

            if exit_reason:
                if exit_reason == 'LIQUIDATION':
                    profit = -pos['invest']
                elif direction == 'LONG':
                    profit = (price - pos['entry_price']) * pos['quantity']
                else:
                    profit = (pos['entry_price'] - price) * pos['quantity']

                capital += pos['invest'] + profit

                reason_simple = {
                    'STOP_LOSS':   f"손절선({pos['stop_loss']:.4f} USDT)까지 {'떨어' if direction=='LONG' else '올라'}서 팔았어 😢",
                    'TAKE_PROFIT': f"목표가({pos['take_profit']:.4f} USDT)까지 {'올라' if direction=='LONG' else '내려'}서 수익 챙겼어! 🎉",
                    'TIME_STOP':   f"{pos['candles_held']}분 기다렸는데 목표까지 안 가서 팔았어 ⏰",
                    'LIQUIDATION': f"⚡ 청산가({pos.get('liq_price',0):.4f} USDT)까지 가서 강제 청산됐어! 마진 날렸어 💸",
                }.get(exit_reason, exit_reason)

                trade = {
                    'market': market, 'name': COIN_NAME.get(market, market),
                    'direction': direction,
                    'entry_time': pos['entry_time'], 'exit_time': timestamp,
                    'entry_price': pos['entry_price'], 'exit_price': price,
                    'profit': profit, 'profit_pct': profit / pos['invest'] * 100,
                    'reason': exit_reason, 'reason_simple': reason_simple,
                    'buy_reason': pos['buy_reason'], 'candles_held': pos['candles_held'],
                }
                trades.append(trade)
                del positions[market]

                snap = _portfolio_snapshot(capital, positions, current_prices)
                events.append({'type': 'SELL', 'time': timestamp, 'trade': trade, 'snap': snap})

        # ── 매수 신호 탐색 ────────────────────────────────────────
        if len(positions) < max_positions:
            buy_signals = []
            for market, data in all_data.items():
                if market in positions:
                    continue
                df  = data['df']
                loc = df.index.get_loc(timestamp)
                window = df.iloc[max(0, loc - 99): loc + 1]
                if len(window) <= config['strategy']['ema_long']:
                    continue
                sig = generate_signal(window, config)
                if sig['action'] in ('LONG', 'SHORT'):
                    buy_signals.append((sig['score'], market, sig))

            buy_signals.sort(key=lambda x: x[0], reverse=True)
            slots = max_positions - len(positions)

            leverage   = config['trading'].get('leverage', 1)
            trade_mode = config['trading'].get('trade_mode', 'spot')

            for score, market, sig in buy_signals[:slots]:
                price     = current_prices.get(market)
                direction = sig.get('direction', 'LONG')
                if price is None:
                    continue

                total_val   = capital + sum(
                    (p['invest'] + ((current_prices.get(m, p['entry_price']) - p['entry_price']) * p['quantity']
                                    if p.get('direction','LONG') == 'LONG'
                                    else (p['entry_price'] - current_prices.get(m, p['entry_price'])) * p['quantity']))
                    for m, p in positions.items()
                )
                risk_amount = total_val * config['risk']['risk_per_trade']
                stop_dist   = sig['atr'] * config['strategy']['atr_stop_mult']
                quantity    = (risk_amount * leverage) / stop_dist
                invest      = quantity * price / leverage
                max_invest  = total_val * config['risk']['max_position_ratio']
                invest      = min(invest, max_invest, capital)

                if invest <= 0:
                    continue

                quantity = invest * leverage / price

                if direction == 'LONG':
                    stop_loss   = price - sig['atr'] * config['strategy']['atr_stop_mult']
                    take_profit = price + sig['atr'] * config['strategy']['atr_target_mult']
                    liq_price   = price * (1 - 0.9 / leverage)
                else:
                    stop_loss   = price + sig['atr'] * config['strategy']['atr_stop_mult']
                    take_profit = price - sig['atr'] * config['strategy']['atr_target_mult']
                    liq_price   = price * (1 + 0.9 / leverage)

                positions[market] = {
                    'direction':    direction,
                    'entry_price':  price,
                    'entry_time':   timestamp,
                    'quantity':     quantity,
                    'invest':       invest,
                    'stop_loss':    stop_loss,
                    'take_profit':  take_profit,
                    'liq_price':    liq_price,
                    'candles_held': 0,
                    'buy_reason':   sig['reason'],
                    'score':        score,
                }
                capital -= invest

                snap = _portfolio_snapshot(capital, positions, current_prices)
                events.append({
                    'type': 'BUY', 'time': timestamp, 'market': market,
                    'direction': direction,
                    'price': price, 'invest': invest,
                    'stop_loss':   positions[market]['stop_loss'],
                    'take_profit': positions[market]['take_profit'],
                    'reason': sig['reason'], 'snap': snap,
                })

        total = capital
        for m, pos in positions.items():
            p   = current_prices.get(m, pos['entry_price'])
            if pos.get('direction', 'LONG') == 'LONG':
                pnl = (p - pos['entry_price']) * pos['quantity']
            else:
                pnl = (pos['entry_price'] - p) * pos['quantity']
            total += pos['invest'] + pnl
        equity.append({'time': timestamp, 'total': total})

    # 기간 종료 강제 청산
    for market, pos in list(positions.items()):
        price     = current_prices.get(market, pos['entry_price'])
        direction = pos.get('direction', 'LONG')
        if direction == 'LONG':
            profit = (price - pos['entry_price']) * pos['quantity']
        else:
            profit = (pos['entry_price'] - price) * pos['quantity']
        capital += pos['invest'] + profit
        trade = {
            'market': market, 'name': COIN_NAME.get(market, market),
            'direction': direction,
            'entry_time': pos['entry_time'], 'exit_time': bt_index[-1],
            'entry_price': pos['entry_price'], 'exit_price': price,
            'profit': profit, 'profit_pct': profit / pos['invest'] * 100,
            'reason': 'END_OF_PERIOD',
            'reason_simple': f"백테스트 기간이 끝나서 마지막 가격({price:.4f} USDT)에 청산했어 📅",
            'buy_reason': pos['buy_reason'], 'candles_held': pos['candles_held'],
        }
        trades.append(trade)

    # ── 3. 결과 출력 ──────────────────────────────────────────────
    _print_timeline(events, initial_capital)
    _print_summary(trades, equity, initial_capital, capital, markets, start_date, end_date)


def _print_timeline(events: list, initial_capital: float):
    print(f"\n\n{'━'*64}")
    print(f"  📅 거래 타임라인")
    print(f"{'━'*64}")

    for event in events:
        ts = event['time'].strftime('%m월 %d일 %H시')

        if event['type'] == 'BUY':
            name      = COIN_NAME.get(event['market'], event['market'])
            direction = event.get('direction', 'LONG')
            dir_label = "🔺 롱 (오를 때 수익)" if direction == 'LONG' else "🔻 숏 (내릴 때 수익)"
            print(f"\n  ┌{'─'*60}┐")
            print(f"  │  🕐 {ts}")
            print(f"  │  🟢 진입: {name}  {dir_label}")
            print(f"  │     가격    : {event['price']:>15.4f} USDT")
            print(f"  │     마진    : {event['invest']:>15.2f} USDT")
            print(f"  │     목표가  : {event['take_profit']:>15.4f} USDT  🎯")
            print(f"  │     손절가  : {event['stop_loss']:>15.4f} USDT  🛑")
            print(f"  │  ✏️  산 이유:")
            for part in event['reason'].split('  '):
                part = part.strip()
                if part:
                    print(f"  │     {part}")
            _print_portfolio(event['snap'], initial_capital, "거래 후 자산 현황")

        elif event['type'] == 'SELL':
            t     = event['trade']
            emoji = "✅" if t['profit'] >= 0 else "❌"
            print(f"\n  ┌{'─'*60}┐")
            print(f"  │  🕐 {ts}")
            print(f"  │  🔴 매도: {t['name']}  {emoji}")
            print(f"  │     판 가격 : {t['exit_price']:>15.4f} USDT")
            print(f"  │     산 가격 : {t['entry_price']:>15.4f} USDT")
            print(f"  │     손익   : {t['profit']:>+15.2f} USDT  ({t['profit_pct']:>+.1f}%)")
            print(f"  │  ✏️  판 이유: {t['reason_simple']}")
            _print_portfolio(event['snap'], initial_capital, "거래 후 자산 현황")


def _print_summary(trades, equity, initial_capital, final_capital,
                   markets, start_date, end_date):
    total_profit = final_capital - initial_capital
    total_return = total_profit / initial_capital * 100

    wins     = [t for t in trades if t['profit'] >= 0]
    losses   = [t for t in trades if t['profit'] <  0]
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    avg_win  = sum(t['profit_pct'] for t in wins)   / len(wins)   if wins   else 0
    avg_loss = sum(t['profit_pct'] for t in losses) / len(losses) if losses else 0

    peak, max_dd = initial_capital, 0.0
    for e in equity:
        peak   = max(peak, e['total'])
        max_dd = max(max_dd, (peak - e['total']) / peak * 100)

    coin_stats = {}
    for t in trades:
        m = t['market']
        if m not in coin_stats:
            coin_stats[m] = {'profit': 0, 'count': 0, 'wins': 0}
        coin_stats[m]['profit'] += t['profit']
        coin_stats[m]['count']  += 1
        if t['profit'] >= 0:
            coin_stats[m]['wins'] += 1

    print(f"\n\n{'━'*64}")
    print(f"  📊 최종 요약  |  {start_date} ~ {end_date}")
    print(f"{'━'*64}")
    print(f"  💰 초기 자금       : {initial_capital:>12.2f} USDT")
    print(f"  💰 최종 자금       : {final_capital:>12.2f} USDT")
    emoji = "📈" if total_profit >= 0 else "📉"
    print(f"  {emoji} 총 손익           : {total_profit:>+12.2f} USDT  ({total_return:>+.2f}%)")
    print(f"  ⛰️  최대 낙폭 (MDD) : {max_dd:>12.2f}%")
    print(f"  {'─'*58}")
    print(f"  🔄 총 거래 횟수    : {len(trades):>3} 회")
    print(f"  ✅ 이긴 거래       : {len(wins):>3} 회  (평균 +{avg_win:.2f}%)")
    print(f"  ❌ 진 거래         : {len(losses):>3} 회  (평균 {avg_loss:.2f}%)")
    print(f"  🎯 승률            : {win_rate:>8.1f}%")

    print(f"\n  🏆 코인별 성적표")
    print(f"  {'─'*58}")
    for market, stat in sorted(coin_stats.items(), key=lambda x: -x[1]['profit']):
        name = COIN_NAME.get(market, market)
        wr   = stat['wins'] / stat['count'] * 100 if stat['count'] else 0
        e    = "📈" if stat['profit'] >= 0 else "📉"
        print(f"  {e} {name:<10} : {stat['profit']:>+10.2f} USDT  ({stat['count']}회 / 승률 {wr:.0f}%)")

    print(f"{'━'*64}\n")


if __name__ == '__main__':
    config = load_config()
    run_backtest(
        start_date      = "2025-02-01",
        end_date        = "2025-02-28",
        initial_capital = config['risk']['initial_capital'],
        config          = config,
    )
