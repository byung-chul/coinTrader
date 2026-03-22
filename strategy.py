"""
strategy.py — 볼린저 밴드 추세 추종 전략

[핵심 개념]

볼린저 밴드:
  가격이 보통 움직이는 범위를 세 줄로 표시해.
  ─────── 위 선: 가격이 여기를 뚫고 올라가면 "강한 상승 모멘텀"
  ─────── 중간선: 평균 가격 (EMA20)
  ─────── 아래 선: 가격이 여기를 뚫고 내려가면 "강한 하락 모멘텀"

추세 추종 전략:
  평균으로 회귀하는 게 아니라, 강한 방향으로 편승하는 전략.
  → 위 선 돌파 + RSI 강세 + 거래량 급증 = LONG  (상승 추세에 올라탐)
  → 아래 선 돌파 + RSI 약세 + 거래량 급증 = SHORT (하락 추세에 올라탐)

진입 조건 (3가지 모두 충족):
  1. 볼린저 밴드 상단/하단 돌파 (강한 모멘텀 확인)
  2. RSI가 추세 방향 확인 (50 이상이면 상승력, 이하면 하락력)
  3. 거래량 급증 (많은 사람이 같은 방향으로 움직임)
"""

import pandas as pd


def calculate_indicators(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    s = config['strategy']

    # ── 볼린저 밴드 ──────────────────────────────────────────────
    period  = s.get('bb_period', 20)
    std_mul = s.get('bb_std', 2.0)
    df['bb_mid']   = df['close'].rolling(window=period).mean()
    bb_std         = df['close'].rolling(window=period).std()
    df['bb_upper'] = df['bb_mid'] + bb_std * std_mul
    df['bb_lower'] = df['bb_mid'] - bb_std * std_mul
    band_width     = (df['bb_upper'] - df['bb_lower']).replace(0, 1e-10)
    df['bb_pct']   = (df['close'] - df['bb_lower']) / band_width

    # ── ATR (변동폭, 손절/익절 계산에 사용) ─────────────────────
    high_low   = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift(1)).abs()
    low_close  = (df['low']  - df['close'].shift(1)).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['atr']  = true_range.ewm(span=s.get('atr_period', 14), adjust=False).mean()

    # ── RSI (힘 측정기) ──────────────────────────────────────────
    delta    = df['close'].diff()
    gain     = delta.where(delta > 0, 0.0)
    loss     = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(span=s.get('rsi_period', 14), adjust=False).mean()
    avg_loss = loss.ewm(span=s.get('rsi_period', 14), adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, 1e-10)
    df['rsi'] = 100 - (100 / (1 + rs))

    # ── 거래량 이동평균 ─────────────────────────────────────────
    df['volume_ma'] = df['volume'].rolling(window=20).mean()

    return df


def generate_signal(df: pd.DataFrame, config: dict) -> dict:
    s          = config['strategy']
    trade_mode = config['trading'].get('trade_mode', 'spot')

    cur  = df.iloc[-1]
    prev = df.iloc[-2]
    atr  = cur['atr']

    vol_ratio = cur['volume'] / cur['volume_ma'] if cur['volume_ma'] > 0 else 0
    vol_surge = vol_ratio >= s.get('volume_mult', 1.5)

    # ── 최소 ATR 체크 (너무 좁은 시장 진입 스킵) ────────────────
    min_atr_pct = s.get('min_atr_pct', 0.003)
    if cur['close'] > 0 and atr / cur['close'] < min_atr_pct:
        return {'action': 'HOLD', 'direction': None,
                'reason': f"ATR이 너무 작음 ({atr/cur['close']*100:.3f}% < {min_atr_pct*100:.1f}%) — 변동성 부족",
                'atr': atr, 'score': 0}

    # ── 추세 필터 (볼린저 밴드 중간선 기준) ─────────────────────
    above_mid = cur['close'] > cur['bb_mid']   # True = 단기 상승 추세

    # ── LONG: 하단 돌파 + 하락 추세일 때만 ───────────────────────
    break_lower = cur['bb_pct'] < s.get('bb_entry_lower', 0.25)
    rsi_weak    = cur['rsi'] < 50

    if break_lower and rsi_weak and vol_surge and not above_mid:
        score = (0.25 - cur['bb_pct']) * (100 - cur['rsi']) * vol_ratio
        reason = (
            f"① 볼린저 밴드 하단 돌파 — 과매도 구간 반등 베팅 📉→📈 (밴드 위치 {cur['bb_pct']*100:.0f}%)  "
            f"② RSI 과매도 ({cur['rsi']:.0f}) — 반등 가능성 확인  "
            f"③ 평소보다 {vol_ratio:.1f}배 많이 거래되고 있어 🔥"
        )
        return {'action': 'LONG', 'direction': 'LONG', 'reason': reason, 'atr': atr, 'score': score}

    # ── SHORT: 상단 돌파 + 상승 추세일 때만 (선물 모드에서만) ────
    if trade_mode == 'futures':
        break_upper = cur['bb_pct'] > s.get('bb_entry_upper', 0.75)
        rsi_strong  = cur['rsi'] > 50

        if break_upper and rsi_strong and vol_surge and above_mid:
            score = (cur['bb_pct'] - 0.75) * cur['rsi'] * vol_ratio
            reason = (
                f"① 볼린저 밴드 상단 돌파 — 과매수 구간 되돌림 베팅 📈→📉 (밴드 위치 {cur['bb_pct']*100:.0f}%)  "
                f"② RSI 과매수 ({cur['rsi']:.0f}) — 되돌림 가능성 확인  "
                f"③ 평소보다 {vol_ratio:.1f}배 많이 거래되고 있어 🔥"
            )
            return {'action': 'SHORT', 'direction': 'SHORT', 'reason': reason, 'atr': atr, 'score': score}

    # ── 신호 없음 ────────────────────────────────────────────────
    failed = []
    failed.append(f"추세: {'상승' if above_mid else '하락'} (BB중간선 {'위' if above_mid else '아래'})")
    if not break_lower: failed.append(f"하단 돌파 안 됨 (밴드 위치 {cur['bb_pct']*100:.0f}%)")
    if not rsi_weak:    failed.append(f"RSI 과매도 아님 ({cur['rsi']:.0f})")
    if not vol_surge:   failed.append(f"거래량 부족 ({vol_ratio:.1f}배)")

    return {'action': 'HOLD', 'direction': None, 'reason': ' / '.join(failed), 'atr': atr, 'score': 0}
