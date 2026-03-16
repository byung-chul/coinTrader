"""
paper_trader.py — 가짜 돈으로 현물/선물 거래를 시뮬레이션하는 모듈

[현물 vs 선물 차이]
현물: 실제 코인을 사고 팜. 오를 때만 수익.
선물: 레버리지 사용. 오를 때도(롱), 내릴 때도(숏) 수익 가능.
      단, 레버리지만큼 손해도 빠름 → 청산 주의!

OKX 기준: 가격/수익은 USDT 단위
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

COIN_NAME = {
    # 현물
    "BTC/USDT":   "비트코인",
    "ETH/USDT":   "이더리움",
    "XRP/USDT":   "리플",
    "DOGE/USDT":  "도지코인",
    "SOL/USDT":   "솔라나",
    "BNB/USDT":   "바이낸스코인",
    "ADA/USDT":   "에이다",
    "AVAX/USDT":  "아발란체",
    "LINK/USDT":  "체인링크",
    "LTC/USDT":   "라이트코인",
    # 선물
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


class PaperTrader:
    """
    가상 트레이더

    현물 모드: LONG만 가능
    선물 모드: LONG(오를 때 수익) + SHORT(내릴 때 수익) 모두 가능
               레버리지로 적은 돈으로 큰 포지션 운용
    """

    def __init__(self, config: dict):
        self.capital         = float(config['risk']['initial_capital'])
        self.initial_capital = self.capital
        self.positions       = {}
        self.trades          = []
        self.daily_start     = self.capital
        self.config          = config
        self.max_positions   = config['trading']['max_positions']
        self.trade_mode      = config['trading'].get('trade_mode', 'spot')
        self.leverage_min    = config['trading'].get('leverage_min', config['trading'].get('leverage', 1)) if self.trade_mode == 'futures' else 1
        self.leverage_max    = min(config['trading'].get('leverage_max', config['trading'].get('leverage', 1)), 5) if self.trade_mode == 'futures' else 1
        self.leverage        = self.leverage_max   # 로그 표시용 기본값

        logger.info(f"📄 페이퍼 트레이딩 시작")
        logger.info(f"   초기 자금: {self.capital:,.2f} USDT")
        logger.info(f"   모드: {'🔮 선물 (레버리지 x' + str(self.leverage) + ')' if self.trade_mode == 'futures' else '💵 현물'}")
        r = config['risk']
        risk_min = r.get('risk_per_trade_min', r.get('risk_per_trade', 0.02)) * 100
        risk_max = r.get('risk_per_trade_max', r.get('risk_per_trade', 0.02)) * 100
        logger.info(f"   최대 포지션: {self.max_positions}개  |  거래당 위험: {risk_min:.0f}%~{risk_max:.0f}% (신호 강도에 따라 동적)")

    # ─────────────────────────────────────────
    # 매수 / 진입
    # ─────────────────────────────────────────
    def can_buy(self) -> bool:
        return len(self.positions) < self.max_positions

    def _calc_risk_ratio(self, score: float) -> float:
        """신호 강도(score)에 따라 risk_per_trade를 min~max 사이에서 동적 결정"""
        r = self.config['risk']
        mn    = r.get('risk_per_trade_min', r.get('risk_per_trade', 0.02))
        mx    = r.get('risk_per_trade_max', r.get('risk_per_trade', 0.02))
        cap   = r.get('score_cap', 30.0)
        ratio = min(score / cap, 1.0)
        return mn + (mx - mn) * ratio

    def _calc_leverage(self, score: float) -> int:
        """신호 강도(score)에 따라 레버리지를 min~max 사이에서 동적 결정"""
        cap   = self.config['risk'].get('score_cap', 30.0)
        ratio = min(score / cap, 1.0)
        lev   = self.leverage_min + (self.leverage_max - self.leverage_min) * ratio
        return max(1, round(lev))

    def buy(self, market: str, price: float, atr: float, reason: str, direction: str = 'LONG', score: float = 0.0):
        """포지션 진입 (LONG 또는 SHORT)"""
        if market in self.positions:
            return

        leverage    = self._calc_leverage(score)
        total_value = self.get_total_value({market: price})
        risk_amount = total_value * self._calc_risk_ratio(score)
        stop_dist   = atr * self.config['strategy']['atr_stop_mult']

        # 레버리지 적용: 같은 마진으로 더 큰 포지션 운용
        # 수량 = (위험금액 × 레버리지) / 손절거리
        quantity = (risk_amount * leverage) / stop_dist
        invest   = quantity * price / leverage   # 실제 필요한 증거금(마진)

        # 안전장치: 마진은 자산의 max_position_ratio 이하
        max_invest = total_value * self.config['risk']['max_position_ratio']
        invest     = min(invest, max_invest, self.capital)

        if invest <= 0:
            return

        quantity = invest * leverage / price

        # 방향에 따라 손절/익절 위치가 반대
        if direction == 'LONG':
            stop_loss   = price - atr * self.config['strategy']['atr_stop_mult']
            take_profit = price + atr * self.config['strategy']['atr_target_mult']
            # 청산가: 마진 90% 소진 시점 (레버리지 높을수록 가격 변동 조금만 해도 청산)
            liq_price   = price * (1 - 0.9 / leverage)
        else:  # SHORT
            stop_loss   = price + atr * self.config['strategy']['atr_stop_mult']
            take_profit = price - atr * self.config['strategy']['atr_target_mult']
            liq_price   = price * (1 + 0.9 / leverage)

        name = COIN_NAME.get(market, market)
        self.positions[market] = {
            'direction':    direction,
            'entry_price':  price,
            'entry_time':   datetime.now(),
            'quantity':     quantity,
            'invest':       invest,
            'stop_loss':    stop_loss,
            'take_profit':  take_profit,
            'liq_price':    liq_price,
            'leverage':     leverage,
            'atr':               atr,
            'peak_price':        price,
            'initial_stop_loss': stop_loss,
            'candles_held': 0,
            'buy_reason':   reason,
        }
        self.capital -= invest

        dir_emoji = "🟢🔺" if direction == 'LONG' else "🔴🔻"
        logger.info(f"{dir_emoji} {direction} 진입! [{name}]")
        risk_ratio = self._calc_risk_ratio(score)
        logger.info(f"   가격: {price:.4f} | 마진: {invest:.2f} USDT | 레버리지: x{leverage} | 위험비율: {risk_ratio*100:.1f}% (점수 {score:.1f})")
        logger.info(f"   손절: {stop_loss:.4f} | 익절: {take_profit:.4f} | 청산가: {liq_price:.4f}")
        logger.info(f"   이유: {reason}")

    # ─────────────────────────────────────────
    # 청산 조건 확인
    # ─────────────────────────────────────────
    def check_exits(self, prices: dict) -> dict:
        exits = {}
        for market, pos in self.positions.items():
            price = prices.get(market)
            if price is None:
                continue
            pos['candles_held'] += 1
            direction   = pos.get('direction', 'LONG')
            trail_dist  = pos['atr'] * self.config['strategy']['atr_stop_mult']

            # ── 트레일링 스탑: 최고가/최저가 갱신 시 손절선 이동 ──
            if direction == 'LONG' and price > pos['peak_price']:
                pos['peak_price'] = price
                new_stop = price - trail_dist
                if new_stop > pos['stop_loss']:
                    pos['stop_loss'] = new_stop
            elif direction == 'SHORT' and price < pos['peak_price']:
                pos['peak_price'] = price
                new_stop = price + trail_dist
                if new_stop < pos['stop_loss']:
                    pos['stop_loss'] = new_stop

            # 청산 (강제 종료 — 손해가 너무 커서 거래소가 강제로 닫음)
            if direction == 'LONG' and price <= pos['liq_price']:
                exits[market] = 'LIQUIDATION'
            elif direction == 'SHORT' and price >= pos['liq_price']:
                exits[market] = 'LIQUIDATION'

            # 손절 (트레일링 스탑 포함)
            elif direction == 'LONG' and price <= pos['stop_loss']:
                exits[market] = 'STOP_LOSS'
            elif direction == 'SHORT' and price >= pos['stop_loss']:
                exits[market] = 'STOP_LOSS'

            # 익절
            elif direction == 'LONG' and price >= pos['take_profit']:
                exits[market] = 'TAKE_PROFIT'
            elif direction == 'SHORT' and price <= pos['take_profit']:
                exits[market] = 'TAKE_PROFIT'

            # 시간 초과
            elif pos['candles_held'] >= self.config['risk']['time_stop_candles']:
                exits[market] = 'TIME_STOP'

        return exits

    # ─────────────────────────────────────────
    # 청산 / 매도
    # ─────────────────────────────────────────
    def sell(self, market: str, price: float, reason: str, buy_reason: str = ""):
        pos = self.positions.get(market)
        if not pos:
            return

        direction  = pos.get('direction', 'LONG')
        quantity   = pos['quantity']
        entry      = pos['entry_price']

        # 방향에 따라 손익 계산
        if direction == 'LONG':
            profit = (price - entry) * quantity
        else:  # SHORT
            profit = (entry - price) * quantity

        # 청산 시 마진 전체 손실 처리
        if reason == 'LIQUIDATION':
            profit = -pos['invest']

        profit_pct = profit / pos['invest'] * 100
        name       = COIN_NAME.get(market, market)

        # 마진 반환 + 손익 반영
        self.capital += pos['invest'] + profit

        reason_simple = {
            'STOP_LOSS':    f"손절선({pos['stop_loss']:.4f} USDT)에 걸려서 팔았어 😢 더 큰 손해 막으려고",
            'TAKE_PROFIT':  f"목표가({pos['take_profit']:.4f} USDT)에 도달해서 수익 챙겼어! 🎉",
            'TIME_STOP':    f"{pos['candles_held']}분 기다렸는데 목표까지 안 가서 팔았어 ⏰",
            'LIQUIDATION':  f"⚡ 가격이 청산가({pos['liq_price']:.4f} USDT)까지 가서 강제로 닫혔어! 마진 날렸어 💸",
        }.get(reason, reason)

        self.trades.append({
            'market':        market,
            'name':          name,
            'direction':     direction,
            'entry_time':    pos['entry_time'],
            'exit_time':     datetime.now(),
            'entry_price':   entry,
            'exit_price':    price,
            'profit':        profit,
            'profit_pct':    profit_pct,
            'reason':        reason,
            'reason_simple': reason_simple,
            'buy_reason':    buy_reason,
            'candles_held':  pos['candles_held'],
            'leverage':      self.leverage,
        })
        del self.positions[market]

        emoji = "✅" if profit >= 0 else "❌"
        dir_tag = "🔺롱" if direction == 'LONG' else "🔻숏"
        logger.info(f"{emoji} {dir_tag} 청산! [{name}]  {profit:+.2f} USDT ({profit_pct:+.2f}%)")
        logger.info(f"   {reason_simple}")

    # ─────────────────────────────────────────
    # 안전장치
    # ─────────────────────────────────────────
    def is_daily_loss_exceeded(self, current_prices: dict) -> bool:
        total    = self.get_total_value(current_prices)
        loss_pct = (self.daily_start - total) / self.daily_start
        return loss_pct > self.config['risk']['daily_loss_limit']

    def reset_daily(self, current_prices: dict):
        self.daily_start = self.get_total_value(current_prices)

    # ─────────────────────────────────────────
    # 현황 계산/출력
    # ─────────────────────────────────────────
    def get_total_value(self, current_prices: dict) -> float:
        """현금(마진 제외) + 열린 포지션의 현재 평가액"""
        total = self.capital
        for market, pos in self.positions.items():
            price     = current_prices.get(market, pos['entry_price'])
            direction = pos.get('direction', 'LONG')
            if direction == 'LONG':
                pnl = (price - pos['entry_price']) * pos['quantity']
            else:
                pnl = (pos['entry_price'] - price) * pos['quantity']
            total += pos['invest'] + pnl
        return total

    def print_status(self, current_prices: dict):
        total      = self.get_total_value(current_prices)
        profit     = total - self.initial_capital
        profit_pct = profit / self.initial_capital * 100
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
                lev = pos.get('leverage', self.leverage_max)
                if direction == 'LONG':
                    pct = (price - pos['entry_price']) / pos['entry_price'] * 100 * lev
                else:
                    pct = (pos['entry_price'] - price) / pos['entry_price'] * 100 * lev
                tag = "🔺" if direction == 'LONG' else "🔻"
                parts.append(f"{tag}{name}({pct:+.1f}%)")
            holding_str = ", ".join(parts)

        logger.info(
            f"💰 총자산: {total:,.2f} USDT | 수익: {profit:+.2f} USDT ({profit_pct:+.2f}%) | "
            f"거래: {trades}회 | 승률: {win_rate:.0f}% | 보유: {holding_str}"
        )
