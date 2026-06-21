"""
S013 - Viana Mini-Indice

Price Action limpo com VWAP intradiaria:
  1. Define o vies pelo primeiro candle da sessao.
  2. Opera somente a favor do lado defendido pela VWAP.
  3. Exige fechamento de forca.
  4. Confirma por FVG ou barra ignorada.
  5. Consulta players quando o contexto de mercado fornece saldos/pressao.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd

from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class VianaMiniIndiceStrategy(BaseStrategy):
    needs_market_players_context = True

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="SC002",
            name="SC002 - Viana Mini-Índice",
            description=(
                "Estrategia especializada de Price Action + VWAP inspirada na "
                "leitura operacional do Viana para Mini-Indice. Usa a maxima e "
                "a minima do primeiro candle da sessao como contexto, aceita "
                "apenas compras quando a VWAP opera acima desse patamar e "
                "apenas vendas quando a VWAP opera abaixo. O gatilho exige "
                "fechamento de forca e confirmacao por Fair Value Gap ou barra "
                "ignorada, consulta a divergencia entre varejo e top traders "
                "da OKX, e calcula stop/alvo por ATR."
            ),
            tags=[
                "price_action",
                "vwap",
                "institutional",
                "scalping",
                "structure",
                "mini_indice",
            ],
            recommended_timeframe="5m",
            criteria=[
                {"id": "c1_vwap_bias", "label": "C1 Viés VWAP", "description": "VWAP define direção fora do candle de abertura."},
                {"id": "c2_rejection", "label": "C2 Rejeição", "description": "Preço rejeita a VWAP na direção do viés."},
                {"id": "c3_force", "label": "C3 Força", "description": "Fechamento rompe máxima/mínima anterior."},
                {"id": "c4_confirmation", "label": "C4 Confirmação", "description": "FVG ou barra ignorada confirma o gatilho."},
                {"id": "c5_players", "label": "C5 Players", "description": "Dados de players/posicionamento confirmam contexto quando exigidos."},
                {"id": "c6_volume", "label": "C6 Volume", "description": "Volume confirma participação."},
                {"id": "c7_risk", "label": "C7 Risco", "description": "Risco técnico calculado de forma válida."},
            ],
            params={
                "session_start_hour_utc": ParamDef(
                    type="int", default=12, min=0, max=23, step=1,
                    description="Hora UTC de inicio da sessao (B3: 12 UTC = 09h BRT).",
                ),
                "session_start_minute_utc": ParamDef(
                    type="int", default=0, min=0, max=59, step=5,
                    description="Minuto UTC de inicio da sessao.",
                ),
                "min_body_break_pct": ParamDef(
                    type="float", default=0.02, min=0.0, max=1.0, step=0.01,
                    description="Folga minima do fechamento sobre a maxima/minima anterior (%).",
                ),
                "vwap_rejection_max_dist_pct": ParamDef(
                    type="float", default=0.80, min=0.05, max=5.0, step=0.05,
                    description="Distancia maxima do preco ate a VWAP para validar rejeicao (%).",
                ),
                "require_confirmation": ParamDef(
                    type="int", default=1, min=0, max=1, step=1,
                    description="Exigir FVG ou barra ignorada apos o fechamento de forca.",
                ),
                "use_fvg": ParamDef(
                    type="int", default=1, min=0, max=1, step=1,
                    description="Usar Fair Value Gap de 3 candles como confirmacao.",
                ),
                "use_ignored_bar": ParamDef(
                    type="int", default=1, min=0, max=1, step=1,
                    description="Usar barra ignorada/inside candle como confirmacao.",
                ),
                "require_market_players": ParamDef(
                    type="int", default=0, min=0, max=1, step=1,
                    description="Exigir confirmacao de players presos quando houver fonte integrada.",
                ),
                "min_volume_ratio": ParamDef(
                    type="float", default=1.0, min=0.0, max=5.0, step=0.1,
                    description="Volume minimo relativo a media (0 desliga filtro).",
                ),
                "volume_period": ParamDef(
                    type="int", default=20, min=5, max=100, step=1,
                    description="Periodo da media de volume.",
                ),
                "atr_period": ParamDef(
                    type="int", default=14, min=5, max=50, step=1,
                    description="Periodo do ATR para stop tecnico.",
                ),
                "sl_atr_buffer": ParamDef(
                    type="float", default=0.25, min=0.0, max=3.0, step=0.05,
                    description="Buffer de ATR alem da minima/maxima do gatilho.",
                ),
                "rr_ratio": ParamDef(
                    type="float", default=1.5, min=0.5, max=5.0, step=0.1,
                    description="Risco-retorno do TP1.",
                ),
            },
        )

    def __init__(self) -> None:
        params = self.info().params
        for key, definition in params.items():
            setattr(self, key, definition.default)
        self._market_players = None

    def set_params(self, params: dict) -> None:
        for key, value in params.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def compute_with_context(
        self,
        candles: list,
        context: dict | None = None,
    ) -> Optional[StrategyResult]:
        context = context or {}
        self._market_players = (
            context.get("market_players")
            or context.get("player_balances")
            or context.get("institutional_players")
        )
        return self.compute(candles)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        min_len = max(int(self.volume_period), int(self.atr_period)) + 5
        if len(candles) < min_len:
            return None

        df = self._to_dataframe(candles)
        if df.empty:
            return None

        df = self._with_session(df)
        current_session = df["session_id"].iloc[-1]
        session_df = df[df["session_id"] == current_session].copy()
        if len(session_df) < 4:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason="Aguardando candles suficientes da sessao",
                indicators=self._base_indicators(df, None),
                criteria_met=0,
                criteria_total=7,
            )

        session_df["vwap"] = self._session_vwap(session_df)
        df.loc[session_df.index, "vwap"] = session_df["vwap"]
        df["atr"] = self._atr(df, int(self.atr_period))
        df["vol_avg"] = df["volume"].rolling(int(self.volume_period), min_periods=1).mean()

        opening = session_df.iloc[0]
        current = df.iloc[-1]
        previous = df.iloc[-2]

        opening_high = float(opening["high"])
        opening_low = float(opening["low"])
        close = float(current["close"])
        high = float(current["high"])
        low = float(current["low"])
        vwap = float(current["vwap"])
        atr = float(current["atr"]) if np.isfinite(current["atr"]) else 0.0
        vol_avg = float(current["vol_avg"]) if np.isfinite(current["vol_avg"]) else 0.0
        volume_ratio = float(current["volume"]) / vol_avg if vol_avg > 0 else 0.0

        bias = 0
        if vwap > opening_high:
            bias = 1
        elif vwap < opening_low:
            bias = -1

        force_buy = close > float(previous["high"]) * (1 + float(self.min_body_break_pct) / 100)
        force_sell = close < float(previous["low"]) * (1 - float(self.min_body_break_pct) / 100)

        fvg_buy, fvg_sell = self._fvg_flags(df)
        ignored_buy, ignored_sell = self._ignored_bar_flags(df)
        rejection_buy, rejection_sell = self._vwap_rejection_flags(current, previous, vwap)

        confirmation_buy = (bool(self.use_fvg) and fvg_buy) or (bool(self.use_ignored_bar) and ignored_buy)
        confirmation_sell = (bool(self.use_fvg) and fvg_sell) or (bool(self.use_ignored_bar) and ignored_sell)
        if not bool(self.require_confirmation):
            confirmation_buy = confirmation_buy or force_buy
            confirmation_sell = confirmation_sell or force_sell

        volume_ok = volume_ratio >= float(self.min_volume_ratio) if float(self.min_volume_ratio) > 0 else True
        players_available, player_confirmation, player_pressure = self._market_player_flags(bias)
        players_required = bool(int(self.require_market_players))
        players_ok = player_confirmation or (not players_required)

        buy_ok = bias == 1 and rejection_buy and force_buy and confirmation_buy and volume_ok and players_ok
        sell_ok = bias == -1 and rejection_sell and force_sell and confirmation_sell and volume_ok and players_ok

        indicators = self._base_indicators(
            df,
            {
                "opening_high": round(opening_high, 4),
                "opening_low": round(opening_low, 4),
                "vwap": round(vwap, 4),
                "daily_bias": bias,
                "price_vs_vwap_pct": round((close - vwap) / vwap * 100, 4) if vwap else 0.0,
                "vwap_rejection": 1 if (rejection_buy or rejection_sell) else 0,
                "force_close": 1 if force_buy else -1 if force_sell else 0,
                "fvg_signal": 1 if fvg_buy else -1 if fvg_sell else 0,
                "ignored_bar_signal": 1 if ignored_buy else -1 if ignored_sell else 0,
                "volume_ratio": round(volume_ratio, 3),
                "market_players_available": 1 if players_available else 0,
                "market_players_confirmation": 1 if player_confirmation else 0,
                "market_players_pressure": round(player_pressure, 4),
                "market_players_required": 1 if players_required else 0,
                "atr": round(atr, 4),
            },
        )

        criteria_total = 7
        criteria_met = sum([
            1 if bias != 0 else 0,
            1 if (rejection_buy or rejection_sell) else 0,
            1 if (force_buy or force_sell) else 0,
            1 if (confirmation_buy or confirmation_sell) else 0,
            1 if (players_available and player_confirmation) else 0,
            1 if volume_ok else 0,
            1 if (buy_ok or sell_ok) else 0,
        ])

        if buy_ok:
            return self._signal_result(
                Signal.BUY, indicators, close, low, high, previous, atr,
                criteria_met, criteria_total, "viana_vwap_bull",
            )
        if sell_ok:
            return self._signal_result(
                Signal.SELL, indicators, close, low, high, previous, atr,
                criteria_met, criteria_total, "viana_vwap_bear",
            )

        reasons = []
        if bias == 0:
            reasons.append("VWAP dentro do candle de abertura")
        if bias == 1 and not rejection_buy:
            reasons.append("sem rejeicao compradora na VWAP")
        if bias == -1 and not rejection_sell:
            reasons.append("sem rejeicao vendedora na VWAP")
        if bias == 1 and not force_buy:
            reasons.append("sem fechamento acima da maxima anterior")
        if bias == -1 and not force_sell:
            reasons.append("sem fechamento abaixo da minima anterior")
        if bias == 1 and not confirmation_buy:
            reasons.append("sem FVG/barra ignorada de compra")
        if bias == -1 and not confirmation_sell:
            reasons.append("sem FVG/barra ignorada de venda")
        if not volume_ok:
            reasons.append("volume abaixo do minimo")
        if players_required and not player_confirmation:
            reasons.append("players nao confirmaram o lado")

        return StrategyResult(
            signal=Signal.HOLD,
            indicators=indicators,
            hold_reason="; ".join(reasons) or "Aguardando gatilho Viana",
            metadata={
                "regime_state": "viana_waiting",
                "opening_range": [round(opening_low, 4), round(opening_high, 4)],
                "market_players_source": "context" if players_available else "unavailable",
            },
            criteria_met=criteria_met,
            criteria_total=criteria_total,
        )

    def _to_dataframe(self, candles: list) -> pd.DataFrame:
        rows = []
        for idx, candle in enumerate(candles):
            epoch = getattr(candle, "epoch", None)
            if epoch is None:
                epoch = getattr(candle, "timestamp", None)
            rows.append({
                "epoch": int(epoch) if epoch is not None else idx,
                "open": float(candle.open),
                "high": float(candle.high),
                "low": float(candle.low),
                "close": float(candle.close),
                "volume": float(getattr(candle, "volume", 0.0) or 0.0),
            })
        return pd.DataFrame(rows)

    def _with_session(self, df: pd.DataFrame) -> pd.DataFrame:
        start_delta = timedelta(
            hours=int(self.session_start_hour_utc),
            minutes=int(self.session_start_minute_utc),
        )

        def session_id(epoch: int) -> str:
            seconds = epoch / 1000 if epoch > 10_000_000_000 else epoch
            dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
            return (dt - start_delta).date().isoformat()

        df = df.copy()
        df["session_id"] = df["epoch"].apply(session_id)
        return df

    @staticmethod
    def _session_vwap(session_df: pd.DataFrame) -> pd.Series:
        typical = (session_df["high"] + session_df["low"] + session_df["close"]) / 3
        volume = session_df["volume"].replace(0, np.nan)
        vol_sum = volume.cumsum()
        pv_sum = (typical * volume).cumsum()
        vwap = pv_sum / vol_sum
        return vwap.ffill().fillna(typical.expanding().mean())

    @staticmethod
    def _atr(df: pd.DataFrame, period: int) -> pd.Series:
        prev_close = df["close"].shift(1)
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ], axis=1).max(axis=1)
        return tr.rolling(period, min_periods=1).mean()

    def _vwap_rejection_flags(self, current, previous, vwap: float) -> tuple[bool, bool]:
        close = float(current["close"])
        high = float(current["high"])
        low = float(current["low"])
        prev_high = float(previous["high"])
        prev_low = float(previous["low"])
        max_dist = float(self.vwap_rejection_max_dist_pct) / 100
        dist_ok = abs(close - vwap) / vwap <= max_dist if vwap else False

        sell_reject = (high >= vwap or prev_high >= vwap or dist_ok) and close < vwap
        buy_reject = (low <= vwap or prev_low <= vwap or dist_ok) and close > vwap
        return buy_reject, sell_reject

    @staticmethod
    def _fvg_flags(df: pd.DataFrame) -> tuple[bool, bool]:
        if len(df) < 3:
            return False, False
        c1 = df.iloc[-3]
        c3 = df.iloc[-1]
        bull = float(c1["high"]) < float(c3["low"])
        bear = float(c1["low"]) > float(c3["high"])
        return bull, bear

    @staticmethod
    def _ignored_bar_flags(df: pd.DataFrame) -> tuple[bool, bool]:
        if len(df) < 3:
            return False, False
        force = df.iloc[-3]
        ignored = df.iloc[-2]
        current = df.iloc[-1]

        force_bear = float(force["close"]) < float(force["open"])
        ignored_bull = float(ignored["close"]) >= float(ignored["open"])
        bear_mid = (float(force["open"]) + float(force["close"])) / 2
        bear = (
            force_bear
            and ignored_bull
            and float(ignored["close"]) <= bear_mid
            and float(current["close"]) < float(ignored["low"])
        )

        force_bull = float(force["close"]) > float(force["open"])
        ignored_bear = float(ignored["close"]) <= float(ignored["open"])
        bull_mid = (float(force["open"]) + float(force["close"])) / 2
        bull = (
            force_bull
            and ignored_bear
            and float(ignored["close"]) >= bull_mid
            and float(current["close"]) > float(ignored["high"])
        )
        return bull, bear

    def _market_player_flags(self, bias: int) -> tuple[bool, bool, float]:
        pressure = self._extract_player_pressure(self._market_players)
        if pressure is None:
            return False, False, 0.0

        # Pressao positiva = players comprados/presos; quando o vies e vendedor,
        # a estrategia espera stops desses comprados acelerando a queda.
        # Pressao negativa = players vendidos/presos; quando o vies e comprador,
        # a recompra desses vendidos pode acelerar a alta.
        confirmation = (bias == -1 and pressure > 0) or (bias == 1 and pressure < 0)
        return True, confirmation, float(pressure)

    def _extract_player_pressure(self, source) -> float | None:
        if source is None:
            return None

        if isinstance(source, (int, float)):
            return float(source)

        if isinstance(source, list):
            values = [self._extract_player_pressure(item) for item in source]
            clean = [value for value in values if value is not None]
            return float(sum(clean)) if clean else None

        if not isinstance(source, dict):
            return None

        if source.get("confirms_sell") is True:
            return abs(float(source.get("pressure") or 1.0))
        if source.get("confirms_buy") is True:
            return -abs(float(source.get("pressure") or 1.0))

        if "players" in source:
            return self._extract_player_pressure(source.get("players"))

        trapped = str(source.get("trapped_side") or "").lower()
        if trapped in {"long", "buy", "comprado", "comprados"}:
            return 1.0
        if trapped in {"short", "sell", "vendido", "vendidos"}:
            return -1.0

        direction = str(
            source.get("direction")
            or source.get("side")
            or source.get("dominant_side")
            or source.get("net_side")
            or ""
        ).lower()

        numeric = None
        for key in ("pressure", "net_pressure", "net_flow", "net_balance", "balance", "position", "qty"):
            value = source.get(key)
            if isinstance(value, (int, float)):
                numeric = abs(float(value))
                break
        if numeric is None:
            numeric = 1.0 if direction else None

        if numeric is None:
            return None
        if direction in {"sell", "short", "vendido", "venda", "bear"}:
            return -numeric
        if direction in {"buy", "long", "comprado", "compra", "bull"}:
            return numeric
        value = source.get("pressure") or source.get("net_pressure") or source.get("net_flow")
        return float(value) if isinstance(value, (int, float)) else None

    def _signal_result(
        self,
        signal: Signal,
        indicators: dict,
        close: float,
        low: float,
        high: float,
        previous,
        atr: float,
        criteria_met: int,
        criteria_total: int,
        regime_state: str,
    ) -> StrategyResult:
        atr_buffer = atr * float(self.sl_atr_buffer)
        if signal == Signal.BUY:
            stop = min(low, float(previous["low"])) - atr_buffer
            risk = close - stop
            target = close + risk * float(self.rr_ratio)
        else:
            stop = max(high, float(previous["high"])) + atr_buffer
            risk = stop - close
            target = close - risk * float(self.rr_ratio)

        if risk <= 0:
            return StrategyResult(
                signal=Signal.HOLD,
                indicators=indicators,
                hold_reason="Risco invalido para SL/TP",
                criteria_met=criteria_met,
                criteria_total=criteria_total,
            )

        enriched = dict(indicators)
        enriched.update({
            "signal": signal.value,
            "sl_price": round(stop, 4),
            "tp1_price": round(target, 4),
            "risk_points": round(risk, 4),
        })

        return StrategyResult(
            signal=signal,
            indicators=enriched,
            metadata={
                "sl_price": round(stop, 4),
                "tp1_price": round(target, 4),
                "sl_pct": round(risk / close, 5) if close else 0.0,
                "tp1_pct": round(abs(target - close) / close, 5) if close else 0.0,
                "ts_pct": round(abs(target - close) / close, 5) if close else 0.0,
                "rr": float(self.rr_ratio),
                "regime_state": regime_state,
                "strategy_pattern": "vwap_force_close_fvg_or_ignored_bar",
            },
            criteria_met=criteria_met,
            criteria_total=criteria_total,
        )

    @staticmethod
    def _base_indicators(df: pd.DataFrame, extra: dict | None) -> dict:
        current = df.iloc[-1]
        indicators = {
            "open": round(float(current["open"]), 4),
            "high": round(float(current["high"]), 4),
            "low": round(float(current["low"]), 4),
            "close": round(float(current["close"]), 4),
            "volume": round(float(current["volume"]), 4),
        }
        if extra:
            indicators.update(extra)
        return indicators
