"""
notifier.py — 알림 모듈

매 거래마다 → 디스코드 (실시간)
매일 밤 10시 → 이메일 (데일리 리포트)

디스코드 웹훅 설정:
  디스코드 서버 → 채널 설정 → 연동 → 웹후크 → 새 웹후크 → URL 복사
  .env 파일에 DISCORD_WEBHOOK_URL 입력

이메일 설정:
  Google 계정 → 보안 → 2단계 인증 → 앱 비밀번호 생성
  .env 파일에 GMAIL_SENDER, GMAIL_APP_PASSWORD 입력
"""

import os
import logging
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

def now_kst() -> datetime:
    return datetime.now(KST)

logger = logging.getLogger(__name__)

COIN_NAME = {
    "BTC/USDT":        "비트코인",
    "ETH/USDT":        "이더리움",
    "XRP/USDT":        "리플",
    "DOGE/USDT":       "도지코인",
    "SOL/USDT":        "솔라나",
    "BNB/USDT":        "바이낸스코인",
    "ADA/USDT":        "에이다",
    "AVAX/USDT":       "아발란체",
    "LINK/USDT":       "체인링크",
    "LTC/USDT":        "라이트코인",
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

# 디스코드 embed 색상
COLOR_BUY      = 0x2ecc71   # 초록 (매수)
COLOR_SELL_WIN = 0x3498db   # 파랑 (익절)
COLOR_SELL_LOSS= 0xe74c3c   # 빨강 (손절/손실)
COLOR_INFO     = 0x95a5a6   # 회색 (정보)


# ─────────────────────────────────────────────────────────────
# 디스코드 알림
# ─────────────────────────────────────────────────────────────

def _send_discord(payload: dict):
    """디스코드 웹훅으로 메시지 전송"""
    url = os.getenv("DISCORD_WEBHOOK_URL")
    if not url:
        logger.warning("⚠️  DISCORD_WEBHOOK_URL 미설정 — 디스코드 알림 생략")
        return
    try:
        resp = requests.post(url, json=payload, timeout=5)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"❌ 디스코드 전송 실패: {e}")


def notify_buy(market: str, price: float, invest: float,
               stop_loss: float, take_profit: float,
               reason: str, trader,
               direction: str = 'LONG', leverage: int = 1) -> None:
    """
    매수 시 디스코드 알림

    어떤 코인을 왜 샀는지, 손절/목표가는 얼마인지,
    지금 내 자산은 어떤지 한눈에 보여줌
    """
    name    = COIN_NAME.get(market, market)
    total   = trader.get_total_value({market: price})
    pnl     = total - trader.initial_capital
    pnl_pct = pnl / trader.initial_capital * 100

    dir_label = "🔺 LONG (상승 베팅)" if direction == 'LONG' else "🔻 SHORT (하락 베팅)"

    # 이유를 줄별로 예쁘게
    reason_lines = "\n".join(
        f"> {part.strip()}"
        for part in reason.split("  ")
        if part.strip()
    )

    # 현재 보유 코인 목록
    holdings = []
    for m, pos in trader.positions.items():
        n   = COIN_NAME.get(m, m)
        tag = "🔺" if pos.get('direction', 'LONG') == 'LONG' else "🔻"
        lev = pos.get('leverage', 1)
        holdings.append(f"• {tag} {n}  x{lev}  |  진입가 {pos['entry_price']:,.4f} USDT")
    holdings_str = "\n".join(holdings) if holdings else "없음"

    payload = {
        "embeds": [{
            "title": f"{'🟢' if direction == 'LONG' else '🔴'}  진입!  {name}  {dir_label}",
            "description": (
                f"💵 **현금** {trader.capital:,.2f} USDT   "
                f"📊 **총자산** {total:,.2f} USDT   "
                f"{'📈' if pnl >= 0 else '📉'} **누적손익** {pnl:+,.2f} USDT ({pnl_pct:+.1f}%)"
            ),
            "color": COLOR_BUY,
            "fields": [
                {
                    "name": "💰 진입 정보",
                    "value": (
                        f"```\n"
                        f"방향     : {direction} ({leverage}배 레버리지)\n"
                        f"진입가   : {price:>15.4f} USDT\n"
                        f"투자금   : {invest:>15.2f} USDT\n"
                        f"목표가 🎯: {take_profit:>15.4f} USDT\n"
                        f"손절가 🛑: {stop_loss:>15.4f} USDT\n"
                        f"```"
                    ),
                    "inline": False,
                },
                {
                    "name": "✏️  산 이유",
                    "value": reason_lines,
                    "inline": False,
                },
                {
                    "name": "💼  거래 후 내 자산",
                    "value": (
                        f"```\n"
                        f"현금     : {trader.capital:>12.2f} USDT\n"
                        f"총 자산  : {total:>12.2f} USDT\n"
                        f"누적 손익: {pnl:>+12.2f} USDT  ({pnl_pct:+.1f}%)\n"
                        f"```"
                    ),
                    "inline": False,
                },
                {
                    "name": "📦  현재 보유 코인",
                    "value": holdings_str,
                    "inline": False,
                },
            ],
            "footer": {"text": f"coinTrader  |  {now_kst().strftime('%Y-%m-%d %H:%M:%S')}"},
        }]
    }
    _send_discord(payload)


def notify_sell(market: str, entry_price: float, exit_price: float,
                profit: float, profit_pct: float, reason: str,
                buy_reason: str, candles_held: int,
                stop_loss: float, take_profit: float,
                trader, current_prices: dict,
                direction: str = 'LONG', leverage: int = 1,
                peak_price: float = 0.0, initial_stop_loss: float = 0.0) -> None:
    """
    매도 시 디스코드 알림

    얼마에 사서 얼마에 팔았는지, 왜 팔았는지,
    지금 내 자산은 어떤지 보여줌
    """
    name      = COIN_NAME.get(market, market)
    is_profit = profit >= 0
    color     = COLOR_SELL_WIN if is_profit else COLOR_SELL_LOSS
    dir_tag   = "🔺 LONG" if direction == 'LONG' else "🔻 SHORT"
    result_emoji = "✅  익절" if reason == 'TAKE_PROFIT' else ("😢  손절" if reason == 'STOP_LOSS' else "⏰  시간초과")

    reason_simple = {
        'STOP_LOSS':    f"손절선({stop_loss:.4f} USDT)까지 떨어졌어 — 더 큰 손해 막으려고 팔았어",
        'TAKE_PROFIT':  f"목표가({take_profit:.4f} USDT)까지 올랐어 — 수익 챙겼어!",
        'TIME_STOP':    f"{candles_held}분 기다렸는데 목표까지 안 올라서 팔았어",
        'END_OF_PERIOD': "백테스트 기간 종료",
    }.get(reason, reason)

    total   = trader.get_total_value(current_prices)
    pnl     = total - trader.initial_capital
    pnl_pct_total = pnl / trader.initial_capital * 100

    # 남은 보유 코인
    holdings = []
    for m, pos in trader.positions.items():
        n    = COIN_NAME.get(m, m)
        p    = current_prices.get(m, pos['entry_price'])
        d    = pos.get('direction', 'LONG')
        lev  = pos.get('leverage', 1)
        tag  = "🔺" if d == 'LONG' else "🔻"
        pct  = (p - pos['entry_price']) / pos['entry_price'] * 100 * lev
        holdings.append(f"• {tag} {n}  x{lev}  |  {pct:+.1f}%")
    holdings_str = "\n".join(holdings) if holdings else "없음 (현금 대기 중)"

    payload = {
        "embeds": [{
            "title": f"{'🟢' if is_profit else '🔴'}  청산!  {name}  {dir_tag}  {result_emoji}",
            "description": (
                f"💵 **현금** {trader.capital:,.2f} USDT   "
                f"📊 **총자산** {total:,.2f} USDT   "
                f"{'📈' if pnl >= 0 else '📉'} **누적손익** {pnl:+,.2f} USDT ({pnl_pct_total:+.1f}%)"
            ),
            "color": color,
            "fields": [
                {
                    "name": "📊  거래 결과",
                    "value": (
                        f"```\n"
                        f"방향     : {direction} ({leverage}배 레버리지)\n"
                        f"진입가   : {entry_price:>15.4f} USDT\n"
                        f"{'최고가' if direction == 'LONG' else '최저가'}   : {peak_price:>15.4f} USDT\n"
                        f"초기손절  : {initial_stop_loss:>15.4f} USDT\n"
                        f"최종손절  : {stop_loss:>15.4f} USDT  ({stop_loss - initial_stop_loss:>+.4f})\n"
                        f"청산가   : {exit_price:>15.4f} USDT\n"
                        f"손익     : {profit:>+15.2f} USDT  ({profit_pct:+.1f}%)\n"
                        f"보유 시간: {candles_held}분\n"
                        f"```"
                    ),
                    "inline": False,
                },
                {
                    "name": "✏️  판 이유",
                    "value": f"> {reason_simple}",
                    "inline": False,
                },
                {
                    "name": "💼  거래 후 내 자산",
                    "value": (
                        f"```\n"
                        f"현금     : {trader.capital:>12.2f} USDT\n"
                        f"총 자산  : {total:>12.2f} USDT\n"
                        f"누적 손익: {pnl:>+12.2f} USDT  ({pnl_pct_total:+.1f}%)\n"
                        f"```"
                    ),
                    "inline": False,
                },
                {
                    "name": "📦  남은 보유 코인",
                    "value": holdings_str,
                    "inline": False,
                },
            ],
            "footer": {"text": f"coinTrader  |  {now_kst().strftime('%Y-%m-%d %H:%M:%S')}"},
        }]
    }
    _send_discord(payload)


# ─────────────────────────────────────────────────────────────
# 이메일 데일리 리포트
# ─────────────────────────────────────────────────────────────

def _build_html_report(trader, config: dict, current_prices: dict) -> str:
    today     = now_kst().strftime("%Y년 %m월 %d일")
    total     = trader.get_total_value(current_prices)
    total_pnl = total - trader.initial_capital
    total_pct = total_pnl / trader.initial_capital * 100
    day_pnl   = total - trader.daily_start
    day_pct   = day_pnl / trader.daily_start * 100 if trader.daily_start > 0 else 0

    today_str    = now_kst().strftime("%Y-%m-%d")
    today_trades = [t for t in trader.trades if t['exit_time'].strftime("%Y-%m-%d") == today_str]
    wins         = [t for t in today_trades if t['profit'] >= 0]
    losses       = [t for t in today_trades if t['profit'] <  0]
    win_rate     = len(wins) / len(today_trades) * 100 if today_trades else 0

    total_color = "#2ecc71" if total_pnl >= 0 else "#e74c3c"
    day_color   = "#2ecc71" if day_pnl   >= 0 else "#e74c3c"

    # 보유 코인 행
    holdings_rows = ""
    for market, pos in trader.positions.items():
        price    = current_prices.get(market, pos['entry_price'])
        cur_val  = price * pos['quantity']
        invested = pos['entry_price'] * pos['quantity']
        profit   = cur_val - invested
        pct      = profit / invested * 100
        color    = "#2ecc71" if profit >= 0 else "#e74c3c"
        name     = COIN_NAME.get(market, market)
        holdings_rows += f"""
        <tr>
          <td style="padding:8px;border-bottom:1px solid #eee;">{name}</td>
          <td style="padding:8px;border-bottom:1px solid #eee;text-align:right;">{pos['entry_price']:,.0f} USDT</td>
          <td style="padding:8px;border-bottom:1px solid #eee;text-align:right;">{price:,.0f} USDT</td>
          <td style="padding:8px;border-bottom:1px solid #eee;text-align:right;color:{color};">
              {profit:+,.0f} USDT ({pct:+.1f}%)
          </td>
        </tr>"""
    if not holdings_rows:
        holdings_rows = '<tr><td colspan="4" style="padding:8px;text-align:center;color:#999;">보유 중인 코인 없음</td></tr>'

    # 오늘 거래 내역 행 (최근 30건)
    trade_rows = ""
    for t in today_trades[-30:]:
        color = "#2ecc71" if t['profit'] >= 0 else "#e74c3c"
        emoji = "✅" if t['profit'] >= 0 else "❌"
        name  = COIN_NAME.get(t['market'], t['market'])
        reason_map = {'STOP_LOSS': '손절', 'TAKE_PROFIT': '익절', 'TIME_STOP': '시간초과'}
        reason_kr  = reason_map.get(t['reason'], t['reason'])
        trade_rows += f"""
        <tr>
          <td style="padding:5px;border-bottom:1px solid #eee;font-size:12px;">{t['exit_time'].strftime('%H:%M')}</td>
          <td style="padding:5px;border-bottom:1px solid #eee;">{emoji} {name}</td>
          <td style="padding:5px;border-bottom:1px solid #eee;font-size:12px;color:#666;">{reason_kr}</td>
          <td style="padding:5px;border-bottom:1px solid #eee;text-align:right;color:{color};">
              {t['profit']:+,.0f} USDT ({t['profit_pct']:+.1f}%)
          </td>
        </tr>"""
    if not trade_rows:
        trade_rows = '<tr><td colspan="4" style="padding:8px;text-align:center;color:#999;">오늘 거래 없음</td></tr>'

    return f"""
    <html><body style="font-family:Arial,sans-serif;background:#f5f5f5;margin:0;padding:20px;">
    <div style="max-width:600px;margin:0 auto;background:white;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1);">

      <div style="background:#1a1a2e;padding:24px;text-align:center;">
        <h1 style="color:white;margin:0;font-size:22px;">📊 코인 자동매매 데일리 리포트</h1>
        <p style="color:#aaa;margin:8px 0 0;">{today}</p>
      </div>

      <div style="padding:20px;border-bottom:1px solid #eee;">
        <h2 style="margin:0 0 16px;font-size:16px;color:#333;">📅 오늘 손익</h2>
        <div style="display:flex;gap:12px;">
          <div style="flex:1;background:#f8f9fa;border-radius:8px;padding:16px;text-align:center;">
            <div style="font-size:12px;color:#666;margin-bottom:4px;">오늘 수익</div>
            <div style="font-size:22px;font-weight:bold;color:{day_color};">{day_pnl:+,.0f} USDT</div>
            <div style="font-size:13px;color:{day_color};">{day_pct:+.2f}%</div>
          </div>
          <div style="flex:1;background:#f8f9fa;border-radius:8px;padding:16px;text-align:center;">
            <div style="font-size:12px;color:#666;margin-bottom:4px;">누적 수익</div>
            <div style="font-size:22px;font-weight:bold;color:{total_color};">{total_pnl:+,.0f} USDT</div>
            <div style="font-size:13px;color:{total_color};">{total_pct:+.2f}%</div>
          </div>
          <div style="flex:1;background:#f8f9fa;border-radius:8px;padding:16px;text-align:center;">
            <div style="font-size:12px;color:#666;margin-bottom:4px;">총 자산</div>
            <div style="font-size:22px;font-weight:bold;color:#333;">{total:,.0f} USDT</div>
            <div style="font-size:13px;color:#999;">시작: {trader.initial_capital:,.0f} USDT</div>
          </div>
        </div>
      </div>

      <div style="padding:20px;border-bottom:1px solid #eee;">
        <h2 style="margin:0 0 16px;font-size:16px;color:#333;">🔄 오늘 거래 통계</h2>
        <div style="display:flex;gap:12px;text-align:center;">
          <div style="flex:1;background:#f8f9fa;border-radius:8px;padding:12px;">
            <div style="font-size:11px;color:#666;">총 거래</div>
            <div style="font-size:20px;font-weight:bold;">{len(today_trades)}회</div>
          </div>
          <div style="flex:1;background:#e8f8f0;border-radius:8px;padding:12px;">
            <div style="font-size:11px;color:#666;">이긴 거래</div>
            <div style="font-size:20px;font-weight:bold;color:#2ecc71;">✅ {len(wins)}회</div>
          </div>
          <div style="flex:1;background:#fdf0f0;border-radius:8px;padding:12px;">
            <div style="font-size:11px;color:#666;">진 거래</div>
            <div style="font-size:20px;font-weight:bold;color:#e74c3c;">❌ {len(losses)}회</div>
          </div>
          <div style="flex:1;background:#f8f9fa;border-radius:8px;padding:12px;">
            <div style="font-size:11px;color:#666;">승률</div>
            <div style="font-size:20px;font-weight:bold;">{win_rate:.0f}%</div>
          </div>
        </div>
      </div>

      <div style="padding:20px;border-bottom:1px solid #eee;">
        <h2 style="margin:0 0 12px;font-size:16px;color:#333;">💼 현재 보유 코인</h2>
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
          <thead><tr style="background:#f8f9fa;">
            <th style="padding:8px;text-align:left;">코인</th>
            <th style="padding:8px;text-align:right;">매수가</th>
            <th style="padding:8px;text-align:right;">현재가</th>
            <th style="padding:8px;text-align:right;">손익</th>
          </tr></thead>
          <tbody>{holdings_rows}</tbody>
        </table>
        <div style="margin-top:8px;text-align:right;font-size:13px;color:#666;">
          💵 현금: {trader.capital:,.0f} USDT
        </div>
      </div>

      <div style="padding:20px;">
        <h2 style="margin:0 0 12px;font-size:16px;color:#333;">
          📋 오늘 거래 내역{"  (최근 30건)" if len(today_trades) > 30 else ""}
        </h2>
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
          <thead><tr style="background:#f8f9fa;">
            <th style="padding:6px;text-align:left;">시각</th>
            <th style="padding:6px;text-align:left;">코인</th>
            <th style="padding:6px;text-align:left;">이유</th>
            <th style="padding:6px;text-align:right;">손익</th>
          </tr></thead>
          <tbody>{trade_rows}</tbody>
        </table>
      </div>

      <div style="background:#f8f9fa;padding:16px;text-align:center;font-size:11px;color:#999;">
        coinTrader 자동매매 시스템  |  페이퍼 트레이딩
      </div>
    </div>
    </body></html>
    """


def send_daily_report(trader, config: dict, current_prices: dict):
    """매일 밤 10시 이메일 데일리 리포트"""
    sender    = os.getenv("GMAIL_SENDER")
    password  = os.getenv("GMAIL_APP_PASSWORD")
    recipient = config['notification']['recipient_email']

    if not sender or not password:
        logger.warning("⚠️  GMAIL_SENDER / GMAIL_APP_PASSWORD 미설정 — 이메일 생략")
        return

    today   = now_kst().strftime("%Y년 %m월 %d일")
    total   = trader.get_total_value(current_prices)
    day_pnl = total - trader.daily_start
    emoji   = "📈" if day_pnl >= 0 else "📉"

    msg            = MIMEMultipart("alternative")
    msg["Subject"] = f"{emoji} [{today}] 코인 자동매매 데일리 리포트 | 오늘 {day_pnl:+,.0f} USDT"
    msg["From"]    = sender
    msg["To"]      = recipient
    msg.attach(MIMEText(_build_html_report(trader, config, current_prices), "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(sender, password)
            smtp.sendmail(sender, recipient, msg.as_string())
        logger.info(f"📧 데일리 리포트 전송 완료 → {recipient}")
    except smtplib.SMTPAuthenticationError:
        logger.error("❌ Gmail 인증 실패. 앱 비밀번호를 확인해주세요.")
    except Exception as e:
        logger.error(f"❌ 이메일 전송 실패: {e}")
