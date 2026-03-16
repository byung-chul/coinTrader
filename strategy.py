"""
strategy.py — 무릎에서 사고 어깨에서 파는 전략

[핵심 개념]

볼린저 밴드:
  가격이 보통 움직이는 범위를 세 줄로 표시해.
  ─────── 위 선 (어깨): 가격이 여기까지 오면 "많이 올랐다"
  ─────── 중간 선 (허리): 평균 가격
  ─────── 아래 선 (무릎): 가격이 여기까지 내려오면 "많이 떨어졌다"

  평상시 가격은 위아래 선 사이를 왔다갔다 함.
  → 아래 선 근처 = 싸게 살 기회 (무릎)
  → 위 선 근처   = 비싸게 팔 기회 (어깨)

RSI (힘 측정기):
  0~100 사이 숫자.
  30 이하 = "너무 많이 팔렸어, 곧 오를 수도" → 사기 좋음
  70 이상 = "너무 많이 샀어, 곧 내릴 수도" → 팔기 좋음
  숫자가 반등하기 시작하면 = "이제 방향이 바뀌는 중"

전략 요약:
  LONG  (롱): 가격이 아래 선 근처에서 올라오기 시작 → 매수 (무릎에서 삼)
  SHORT (숏): 가격이 위 선 근처에서 내려오기 시작 → 공매도 (어깨에서 팜)
"""

import pandas as pd


def calculate_indicators(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    s = config['strategy']

    # ── 볼린저 밴드 ──────────────────────────────────────────────
    # 최근 N개 캔들의 평균과 표준편차로 위/아래 선을 그림
    period  = s.get('bb_period', 20)
    std_mul = s.get('bb_std', 2.0)
    df['bb_mid']   = df['close'].rolling(window=period).mean()      # 중간선 (이동평균)
    bb_std         = df['close'].rolling(window=period).std()
    df['bb_upper'] = df['bb_mid'] + bb_std * std_mul                # 위 선 (어깨)
    df['bb_lower'] = df['bb_mid'] - bb_std * std_mul                # 아래 선 (무릎)
    # 가격이 밴드 내 어디쯤 있는지 0~1로 표현 (0=아래선, 1=위선)
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

    # ── 추세 필터 (EMA200) ──────────────────────────────────────
    # EMA200 위 = 상승 추세 (LONG만 허용)
    # EMA200 아래 = 하락 추세 (SHORT만 허용)
    df['ema200'] = df['close'].ewm(span=s.get('ema200_period', 200), adjust=False).mean()

    return df


def generate_signal(df: pd.DataFrame, config: dict) -> dict:
    s          = config['strategy']
    trade_mode = config['trading'].get('trade_mode', 'spot')

    cur  = df.iloc[-1]   # 지금 캔들
    prev = df.iloc[-2]   # 바로 전 캔들
    atr  = cur['atr']

    rsi_oversold  = s.get('rsi_oversold',  35)   # 이 아래면 "너무 많이 떨어진 상태"
    rsi_overbought= s.get('rsi_overbought', 65)   # 이 위면  "너무 많이 오른 상태"

    vol_ratio = cur['volume'] / cur['volume_ma'] if cur['volume_ma'] > 0 else 0
    vol_surge = vol_ratio >= s.get('volume_mult', 1.5)

    # ── 추세 판단 (EMA200 기준) ──────────────────────────────────
    above_ema200 = cur['close'] > cur['ema200']   # True = 상승 추세
    trend_label  = "상승추세" if above_ema200 else "하락추세"

    # ── LONG: 무릎에서 매수 ──────────────────────────────────────
    # 조건 1: 가격이 볼린저 밴드 아래 선 근처까지 떨어졌었음
    near_lower   = cur['bb_pct'] < s.get('bb_entry_lower', 0.25)  # 밴드 하위 25% 이하
    # 조건 2: RSI가 과매도 구간에서 반등 시작 (내리다가 올라오는 중)
    rsi_bouncing = prev['rsi'] < rsi_oversold and cur['rsi'] > prev['rsi']
    # 조건 3: 거래량 증가 (사람들이 사기 시작)

    if near_lower and rsi_bouncing and vol_surge and above_ema200:
        score = (rsi_oversold - prev['rsi']) * vol_ratio
        reason = (
            f"① [{trend_label}] EMA200 위 — 상승 추세 확인  "
            f"② 가격이 볼린저 밴드 아래 선까지 떨어졌다가 올라오는 중 📉→📈  "
            f"③ 너무 많이 팔렸다가 다시 사는 사람들이 늘고 있어 (RSI {prev['rsi']:.0f}→{cur['rsi']:.0f})  "
            f"④ 평소보다 {vol_ratio:.1f}배나 많이 거래되고 있어 🔥"
        )
        return {'action': 'LONG', 'direction': 'LONG', 'reason': reason, 'atr': atr, 'score': score}

    # ── SHORT: 어깨에서 공매도 (선물 모드 + 하락 추세에서만) ────
    if trade_mode == 'futures':
        near_upper    = cur['bb_pct'] > s.get('bb_entry_upper', 0.75)
        rsi_dropping  = prev['rsi'] > rsi_overbought and cur['rsi'] < prev['rsi']

        if near_upper and rsi_dropping and vol_surge and not above_ema200:
            score = (prev['rsi'] - rsi_overbought) * vol_ratio
            reason = (
                f"① [{trend_label}] EMA200 아래 — 하락 추세 확인  "
                f"② 가격이 볼린저 밴드 위 선까지 올랐다가 내려오는 중 📈→📉  "
                f"③ 너무 많이 샀다가 다시 파는 사람들이 늘고 있어 (RSI {prev['rsi']:.0f}→{cur['rsi']:.0f})  "
                f"④ 평소보다 {vol_ratio:.1f}배나 많이 거래되고 있어 🔥"
            )
            return {'action': 'SHORT', 'direction': 'SHORT', 'reason': reason, 'atr': atr, 'score': score}

    # ── 신호 없음 ────────────────────────────────────────────────
    failed = []
    failed.append(f"추세: {trend_label} (EMA200 {'위' if above_ema200 else '아래'})")
    if not near_lower:    failed.append(f"아직 충분히 안 떨어졌어 (밴드 위치 {cur['bb_pct']*100:.0f}%)")
    if not rsi_bouncing:  failed.append(f"RSI가 아직 바닥에서 반등하지 않았어 ({cur['rsi']:.0f})")
    if not vol_surge:     failed.append(f"거래량이 아직 부족해 ({vol_ratio:.1f}배)")

    return {'action': 'HOLD', 'direction': None, 'reason': ' / '.join(failed), 'atr': atr, 'score': 0}
