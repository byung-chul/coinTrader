"""
live_trader.py — OKX 실제 선물 거래 모듈

PaperTrader와 동일한 인터페이스 (buy/sell/check_exits 등)를 가지지만
실제 OKX 계좌에 주문을 넣음.

주의사항:
  - config.yaml의 trade_mode: "live" 일 때만 사용
  - .env에 OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE 필수
  - 실제 돈이 움직이므로 처음엔 소액으로 테스트 권장
"""

import os
import logging
from datetime import datetime
import ccxt

logger = logging.getLogger(__name__)

COIN_NAME = {
    "BTC/USDT:USDT":   "비트코인",
    "ETH/USDT:USDT":   "이더리움",
    "XRP/USDT:USDT":   "리플",
    "DOGE/USDT:USDT":  "도지코인",
    "SOL/USDT:USDT":   "솔라나",
    "BNB/USDT:USDT":   "바이낸스코인",
    "ADA/USDT:USDT":   "에이다",
    "AVAX/USDT:USDT":  "아발란체",
    "LINK/USDT:USDT":  "체인링크",
    "LTC/USDT:USDT":   "라이트코인",
}


class LiveTrader:
    """
    실제 OKX 선물 거래 클래스

    PaperTrader와 인터페이스가 동일해서 main.py에서 교체만 하면 됨.
    내부적으로는 ccxt를 통해 OKX에 실제 주문을 전송.
    """

    def __init__(self, config: dict):
        self.config       = config
        self.max_positions = config['trading']['max_positions']
        self.leverage      = min(config['trading'].get('leverage', 1), 5)
        self.positions     = {}   # 내가 추적하는 포지션 (OKX 포지션과 동기화)
        self.trades        = []
        self.daily_start   = None

        # OKX 연결
        self.exchange = ccxt.okx({
            'apiKey':    os.getenv('OKX_API_KEY'),
            'secret':    os.getenv('OKX_SECRET_KEY'),
            'password':  os.getenv('OKX_PASSPHRASE'),
            'enableRateLimit': True,
            'options':   {'defaultType': 'swap'},   # 선물(영구계약) 기본값
        })

        # 마켓 정보 로드 (계약 단위 등)
        self.exchange.load_markets()

        # 레버리지 설정
        for market in config['trading']['markets']:
            try:
                self.exchange.set_leverage(self.leverage, market, params={'mgnMode': 'cross'})
                logger.info(f"   레버리지 설정: {COIN_NAME.get(market, market)} x{self.leverage}")
            except Exception as e:
                logger.warning(f"   레버리지 설정 실패 ({market}): {e}")

        # 초기 자산 조회
        balance = self._get_balance()
        self.initial_capital = balance
        self.daily_start     = balance

        logger.info("=" * 50)
        logger.info("💰 실거래 모드 시작  [OKX 선물]")
        logger.info(f"   잔고: {balance:,.2f} USDT")
        logger.info(f"   레버리지: x{self.leverage}")
        logger.info(f"   최대 포지션: {self.max_positions}개")
        logger.info("=" * 50)

    # ─────────────────────────────────────────
    # 잔고 조회
    # ─────────────────────────────────────────
    def _get_balance(self) -> float:
        """OKX 계좌의 USDT 가용 잔고"""
        try:
            bal = self.exchange.fetch_balance({'type': 'swap'})
            return float(bal.get('USDT', {}).get('free', 0))
        except Exception as e:
            logger.error(f"잔고 조회 실패: {e}")
            return 0.0

    @property
    def capital(self) -> float:
        """현재 가용 현금 (OKX 실시간 조회)"""
        return self._get_balance()

    # ─────────────────────────────────────────
    # 계약 수량 계산
    # ─────────────────────────────────────────
    def _calc_contracts(self, market: str, usdt_amount: float, price: float) -> float:
        """
        투자할 USDT → OKX 계약 수량 변환
        OKX 선물은 '계약' 단위로 주문해야 함
        """
        mkt         = self.exchange.market(market)
        contract_sz = float(mkt.get('contractSize', 1))   # 1계약 = 몇 개의 코인
        # 투자금 × 레버리지 = 실효 포지션
        # 실효 포지션 ÷ (가격 × 계약크기) = 계약 수
        contracts = (usdt_amount * self.leverage) / (price * contract_sz)
        return max(round(contracts, 0), 1)   # 최소 1계약

    # ─────────────────────────────────────────
    # 매수 / 진입
    # ─────────────────────────────────────────
    def can_buy(self) -> bool:
        return len(self.positions) < self.max_positions

    def buy(self, market: str, price: float, atr: float, reason: str, direction: str = 'LONG'):
        if market in self.positions:
            return

        balance     = self._get_balance()
        total_value = self.get_total_value({market: price})
        risk_amount = total_value * self.config['risk']['risk_per_trade']
        stop_dist   = atr * self.config['strategy']['atr_stop_mult']
        quantity    = (risk_amount * self.leverage) / stop_dist
        invest      = quantity * price / self.leverage
        max_invest  = total_value * self.config['risk']['max_position_ratio']
        invest      = min(invest, max_invest, balance)

        if invest <= 0:
            logger.warning(f"투자금 계산 오류 (invest={invest:.2f})")
            return

        contracts = self._calc_contracts(market, invest, price)
        side      = 'buy' if direction == 'LONG' else 'sell'
        pos_side  = 'long' if direction == 'LONG' else 'short'

        try:
            order = self.exchange.create_order(
                symbol  = market,
                type    = 'market',
                side    = side,
                amount  = contracts,
                params  = {'tdMode': 'cross', 'posSide': pos_side},
            )
            filled_price = float(order.get('average') or order.get('price') or price)
            filled_cost  = contracts * filled_price * float(self.exchange.market(market).get('contractSize', 1)) / self.leverage

            if direction == 'LONG':
                stop_loss   = filled_price - atr * self.config['strategy']['atr_stop_mult']
                take_profit = filled_price + atr * self.config['strategy']['atr_target_mult']
                liq_price   = filled_price * (1 - 0.9 / self.leverage)
            else:
                stop_loss   = filled_price + atr * self.config['strategy']['atr_stop_mult']
                take_profit = filled_price - atr * self.config['strategy']['atr_target_mult']
                liq_price   = filled_price * (1 + 0.9 / self.leverage)

            self.positions[market] = {
                'direction':    direction,
                'entry_price':  filled_price,
                'entry_time':   datetime.now(),
                'contracts':    contracts,
                'invest':       filled_cost,
                'quantity':     filled_cost * self.leverage / filled_price,
                'stop_loss':    stop_loss,
                'take_profit':  take_profit,
                'liq_price':    liq_price,
                'candles_held': 0,
                'buy_reason':   reason,
            }

            name = COIN_NAME.get(market, market)
            dir_emoji = "🔺롱" if direction == 'LONG' else "🔻숏"
            logger.info(f"{'🟢'} {dir_emoji} 실거래 진입! [{name}]")
            logger.info(f"   체결가: {filled_price:.4f} | 계약: {contracts} | 마진: {filled_cost:.2f} USDT")
            logger.info(f"   손절: {stop_loss:.4f} | 익절: {take_profit:.4f}")

        except ccxt.InsufficientFunds:
            logger.error(f"잔고 부족 — 진입 불가 ({market})")
        except Exception as e:
            logger.error(f"주문 실패 ({market}): {e}")

    # ─────────────────────────────────────────
    # 청산 조건 확인
    # ─────────────────────────────────────────
    def check_exits(self, prices: dict) -> dict:
        exits = {}
        for market, pos in self.positions.items():
            price     = prices.get(market)
            direction = pos.get('direction', 'LONG')
            if price is None:
                continue
            pos['candles_held'] += 1

            if direction == 'LONG':
                if price <= pos.get('liq_price', 0):       exits[market] = 'LIQUIDATION'
                elif price <= pos['stop_loss']:             exits[market] = 'STOP_LOSS'
                elif price >= pos['take_profit']:           exits[market] = 'TAKE_PROFIT'
            else:
                if price >= pos.get('liq_price', 1e18):    exits[market] = 'LIQUIDATION'
                elif price >= pos['stop_loss']:             exits[market] = 'STOP_LOSS'
                elif price <= pos['take_profit']:           exits[market] = 'TAKE_PROFIT'

            if market not in exits and pos['candles_held'] >= self.config['risk']['time_stop_candles']:
                exits[market] = 'TIME_STOP'

        return exits

    # ─────────────────────────────────────────
    # 청산 / 매도
    # ─────────────────────────────────────────
    def sell(self, market: str, price: float, reason: str, buy_reason: str = ""):
        pos = self.positions.get(market)
        if not pos:
            return

        direction = pos.get('direction', 'LONG')
        contracts = pos['contracts']
        # 청산 방향은 진입과 반대
        close_side = 'sell' if direction == 'LONG' else 'buy'
        pos_side   = 'long' if direction == 'LONG' else 'short'

        try:
            order = self.exchange.create_order(
                symbol  = market,
                type    = 'market',
                side    = close_side,
                amount  = contracts,
                params  = {'tdMode': 'cross', 'posSide': pos_side, 'reduceOnly': True},
            )
            exit_price = float(order.get('average') or order.get('price') or price)

            if direction == 'LONG':
                profit = (exit_price - pos['entry_price']) * pos['quantity']
            else:
                profit = (pos['entry_price'] - exit_price) * pos['quantity']

            if reason == 'LIQUIDATION':
                profit = -pos['invest']

            profit_pct = profit / pos['invest'] * 100
            name       = COIN_NAME.get(market, market)

            self.trades.append({
                'market':       market,
                'name':         name,
                'direction':    direction,
                'entry_time':   pos['entry_time'],
                'exit_time':    datetime.now(),
                'entry_price':  pos['entry_price'],
                'exit_price':   exit_price,
                'profit':       profit,
                'profit_pct':   profit_pct,
                'reason':       reason,
                'buy_reason':   buy_reason,
                'candles_held': pos['candles_held'],
                'leverage':     self.leverage,
            })
            del self.positions[market]

            emoji = "✅" if profit >= 0 else "❌"
            logger.info(f"{emoji} 실거래 청산! [{name}]  {profit:+.2f} USDT ({profit_pct:+.2f}%)")

        except Exception as e:
            logger.error(f"청산 주문 실패 ({market}): {e}")

    # ─────────────────────────────────────────
    # 자산 조회
    # ─────────────────────────────────────────
    def get_total_value(self, current_prices: dict) -> float:
        """잔고 + 열린 포지션 평가액"""
        total = self._get_balance()
        for market, pos in self.positions.items():
            price     = current_prices.get(market, pos['entry_price'])
            direction = pos.get('direction', 'LONG')
            if direction == 'LONG':
                pnl = (price - pos['entry_price']) * pos['quantity']
            else:
                pnl = (pos['entry_price'] - price) * pos['quantity']
            total += pos['invest'] + pnl
        return total

    def is_daily_loss_exceeded(self, current_prices: dict) -> bool:
        if not self.daily_start:
            return False
        total    = self.get_total_value(current_prices)
        loss_pct = (self.daily_start - total) / self.daily_start
        return loss_pct > self.config['risk']['daily_loss_limit']

    def reset_daily(self, current_prices: dict):
        self.daily_start = self.get_total_value(current_prices)

    def print_status(self, current_prices: dict):
        total      = self.get_total_value(current_prices)
        profit     = total - self.initial_capital
        profit_pct = profit / self.initial_capital * 100 if self.initial_capital else 0
        trades     = len(self.trades)
        wins       = sum(1 for t in self.trades if t['profit'] >= 0)
        win_rate   = (wins / trades * 100) if trades else 0.0

        holding_str = "없음"
        if self.positions:
            parts = []
            for market, pos in self.positions.items():
                name      = COIN_NAME.get(market, market)
                price     = current_prices.get(market, pos['entry_price'])
                direction = pos.get('direction', 'LONG')
                if direction == 'LONG':
                    pct = (price - pos['entry_price']) / pos['entry_price'] * 100 * self.leverage
                else:
                    pct = (pos['entry_price'] - price) / pos['entry_price'] * 100 * self.leverage
                tag = "🔺" if direction == 'LONG' else "🔻"
                parts.append(f"{tag}{name}({pct:+.1f}%)")
            holding_str = ", ".join(parts)

        logger.info(
            f"💰 [실거래] 잔고: {self._get_balance():,.2f} USDT | "
            f"총자산: {total:,.2f} USDT | 수익: {profit:+.2f} USDT ({profit_pct:+.2f}%) | "
            f"거래: {trades}회 | 승률: {win_rate:.0f}% | 보유: {holding_str}"
        )
