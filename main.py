"""
main.py — 프로그램 시작점

작동 순서:
1. 설정 불러오기
2. 1분마다 모든 코인 시세 확인 (OKX 거래소)
3. 전략에 따라 사고 팔기
4. 매일 밤 10시에 이메일 리포트 발송
5. Ctrl+C 누르면 안전하게 종료
"""

import os
import sys
import time
import signal
import logging
import pandas as pd
from datetime import datetime

import yaml
import ccxt
from dotenv import load_dotenv

from strategy import calculate_indicators, generate_signal
from paper_trader import PaperTrader, COIN_NAME
from live_trader import LiveTrader
from notifier import send_daily_report, notify_buy, notify_sell


# ─────────────────────────────────────────────────────────────
# 로깅 설정
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('trading.log', encoding='utf-8'),
    ],
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# 킬 스위치 (긴급 정지)
# ─────────────────────────────────────────────────────────────
_kill_switch = False

def _handle_shutdown(sig, frame):
    global _kill_switch
    logger.info("")
    logger.info("🛑 종료 신호를 받았습니다. 안전하게 마무리 중...")
    _kill_switch = True

signal.signal(signal.SIGINT,  _handle_shutdown)
signal.signal(signal.SIGTERM, _handle_shutdown)


# ─────────────────────────────────────────────────────────────
# 헬퍼 함수들
# ─────────────────────────────────────────────────────────────
def load_config(path: str = 'config.yaml') -> dict:
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def create_exchange() -> ccxt.okx:
    """OKX 거래소 연결 — 시세 조회는 인증 없이 공개 API 사용"""
    return ccxt.okx({'enableRateLimit': True})


def fetch_candles(exchange: ccxt.okx, market: str, interval: str, count: int) -> pd.DataFrame:
    """OKX에서 캔들 데이터 가져오기"""
    ohlcv = exchange.fetch_ohlcv(market, timeframe=interval, limit=count)
    if not ohlcv:
        raise ConnectionError(f"캔들 데이터를 불러오지 못했습니다: {market}")
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    return df.astype(float)


# ─────────────────────────────────────────────────────────────
# 메인 루프
# ─────────────────────────────────────────────────────────────
def main():
    load_dotenv()
    config = load_config()

    markets        = config['trading']['markets']
    interval       = config['trading']['interval']
    candle_count   = config['trading']['candle_count']
    check_interval = config['trading']['check_interval']
    max_positions  = config['trading']['max_positions']
    report_time    = config['notification']['daily_report_time']  # "22:00"

    execution = config['trading'].get('execution', 'paper')
    exchange  = create_exchange()

    if execution == 'live':
        trader     = LiveTrader(config)
        mode_label = "💸 실거래 (진짜 돈)"
    else:
        trader     = PaperTrader(config)
        mode_label = "📄 페이퍼 트레이딩 (가짜 돈)"

    names = [COIN_NAME.get(m, m) for m in markets]

    logger.info("=" * 60)
    logger.info("🚀 자동매매 시작  [OKX]")
    logger.info(f"   감시 코인 : {', '.join(names)}")
    logger.info(f"   봉 단위  : {interval}")
    logger.info(f"   모드     : {mode_label}")
    logger.info(f"   리포트   : 매일 {report_time} 이메일 발송")
    logger.info(f"   종료     : Ctrl+C 를 누르세요")
    logger.info("=" * 60)

    last_day         = datetime.now().date()
    last_report_date = None
    current_prices   = {}

    while not _kill_switch:
        now = datetime.now()

        try:
            # ── 자정 지나면 일일 기준 리셋 ──────────────────────
            today = now.date()
            if today != last_day:
                trader.reset_daily(current_prices)
                last_day = today
                logger.info("🌅 새 날이 밝았습니다. 일일 손실 한도 초기화.")

            # ── 매일 밤 10시에 이메일 리포트 발송 ────────────────
            report_h, report_m = map(int, report_time.split(":"))
            is_report_time = (
                now.hour == report_h and
                now.minute == report_m and
                now.second < 30 and
                last_report_date != today
            )
            if is_report_time:
                logger.info("📧 데일리 리포트 작성 중...")
                send_daily_report(trader, config, current_prices)
                last_report_date = today

            # ── 모든 코인 데이터 수집 & 지표 계산 ───────────────
            all_dfs = {}
            for market in markets:
                try:
                    df = fetch_candles(exchange, market, interval, candle_count)
                    all_dfs[market] = calculate_indicators(df, config)
                    current_prices[market] = float(df.iloc[-1]['close'])
                except ConnectionError:
                    pass

            if not current_prices:
                raise ConnectionError("모든 코인 데이터 수집 실패")

            # ── 일일 손실 한도 체크 ───────────────────────────────
            if trader.is_daily_loss_exceeded(current_prices):
                logger.warning("⚠️  오늘 손실이 너무 큽니다. 오늘 거래를 중단합니다.")
                trader.print_status(current_prices)
                time.sleep(check_interval)
                continue

            # ── 보유 코인 청산 조건 확인 ──────────────────────────
            exits = trader.check_exits(current_prices)
            for market, reason in exits.items():
                pos          = trader.positions[market]
                buy_reason   = pos.get('buy_reason', '')
                entry_price  = pos['entry_price']
                stop_loss         = pos['stop_loss']
                initial_stop_loss = pos.get('initial_stop_loss', stop_loss)
                take_profit       = pos['take_profit']
                peak_price        = pos.get('peak_price', entry_price)
                candles_held = pos['candles_held']
                exit_price   = current_prices[market]
                profit       = (exit_price - entry_price) * pos['quantity']
                profit_pct   = (exit_price - entry_price) / entry_price * 100
                trader.sell(market, exit_price, reason, buy_reason)
                notify_sell(
                    market, entry_price, exit_price,
                    profit, profit_pct, reason, buy_reason, candles_held,
                    stop_loss, take_profit, trader, current_prices,
                    direction=pos.get('direction', 'LONG'),
                    leverage=pos.get('leverage', 1),
                    peak_price=peak_price,
                    initial_stop_loss=initial_stop_loss,
                )

            # ── 빈 슬롯 있으면 매수 신호 탐색 ────────────────────
            if trader.can_buy():
                entry_signals = []
                for market, df in all_dfs.items():
                    if market in trader.positions:
                        continue
                    sig = generate_signal(df, config)
                    if sig['action'] in ('LONG', 'SHORT'):
                        entry_signals.append((sig['score'], market, sig))

                entry_signals.sort(key=lambda x: x[0], reverse=True)
                slots = max_positions - len(trader.positions)

                for score, market, sig in entry_signals[:slots]:
                    price = current_prices.get(market)
                    if price:
                        trader.buy(market, price, sig['atr'], sig['reason'], sig['direction'], sig['score'])
                        pos = trader.positions.get(market)
                        if pos:
                            notify_buy(
                                market, price, pos['invest'],
                                pos['stop_loss'], pos['take_profit'],
                                sig['reason'], trader,
                                direction=sig['direction'],
                                leverage=pos.get('leverage', 1),
                            )

            # ── 현재 상태 출력 ────────────────────────────────────
            trader.print_status(current_prices)

        except ConnectionError as e:
            logger.error(f"🌐 네트워크 오류: {e} — {check_interval}초 후 재시도")

        except Exception as e:
            logger.error(f"❌ 예상치 못한 오류: {e}")
            import traceback
            logger.error(traceback.format_exc())

        for _ in range(check_interval):
            if _kill_switch:
                break
            time.sleep(1)

    # ── 종료 시 최종 결과 ────────────────────────────────────
    logger.info("=" * 60)
    logger.info("🏁 최종 결과")
    trader.print_status(current_prices)
    logger.info("=" * 60)


if __name__ == '__main__':
    main()
