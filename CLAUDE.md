# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

Upbit 거래소를 이용한 파이썬 자동 매매 시스템. 현재 v0.1 — 페이퍼 트레이딩(가짜 돈) 단계.

## 실행 방법

```bash
# 최초 1회: 의존성 설치
pip install -r requirements.txt

# API 키 설정
cp .env.example .env
# .env 파일에 Upbit API 키 입력

# 실행
python main.py

# 종료: Ctrl+C
```

## 파일 구조

| 파일 | 역할 |
|------|------|
| `main.py` | 진입점. 메인 루프, 킬스위치, 로깅 설정 |
| `strategy.py` | 기술 지표 계산(EMA/ATR/RSI/거래량) + 매수 신호 생성 |
| `paper_trader.py` | 가상 자금으로 매수/매도/손익 추적 |
| `config.yaml` | 전략 파라미터, 리스크 한도 등 모든 설정값 |
| `.env` | Upbit API 키 (gitignore됨) |

## 아키텍처

데이터 흐름:
```
Upbit REST API → fetch_candles() → calculate_indicators() → generate_signal() → PaperTrader.buy/sell()
```

메인 루프 순서 (check_interval마다 반복):
1. 캔들 데이터 수집
2. 기술 지표 계산 (EMA20/60, ATR14, RSI14, 거래량MA)
3. 일일 손실 한도 체크 (초과 시 당일 거래 중단)
4. 포지션 있으면 청산 조건 확인 (손절/익절/시간손절)
5. 포지션 없으면 매수 신호 확인 (4가지 AND 조건)

## 전략 로직

매수 조건 (4가지 모두 충족해야):
- EMA20 > EMA60 (상승 추세)
- 현재가 > 전봉 종가 + ATR×0.5 (변동성 돌파)
- RSI > 50
- 거래량 > 거래량MA × 1.5

청산 조건 (하나라도 해당 시):
- STOP_LOSS: 현재가 < 진입가 - ATR×1.5
- TAKE_PROFIT: 현재가 > 진입가 + ATR×3.0
- TIME_STOP: 12봉 보유 후 미청산

## 개발 단계

- **v0.1 (현재)**: 페이퍼 트레이딩, 단일 마켓, REST polling
- **v0.2**: 모듈 분리 심화, 소액 실거래 연동
- **v0.3**: 리스크 레이어 강화, 멀티 마켓, WebSocket
