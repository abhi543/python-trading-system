"""
1-2-3 Reversal Map [AGPro Series] Strategy Engine
Python implementation translated faithfully from Pine Script v6 (AGPro Series v2.1)

Author: AGProLabs | AGPro Series v2.1
Features:
- Confirmed pivot swing sequencing (Point 1, Point 2, Point 3)
- Point 3 retracement validation (15% - 92% Fibonacci bounds)
- Neckline breakout detection with ATR buffer validation
- Measured-Move Target projection (Point 1 to Point 2 height)
- Point 3 Invalidation Stop Loss buffer
- Dynamic 0-100 Reversal Score & Quality Grading (A+, A, B, C)
- Break Edge & Retest Pocket context
"""

import pandas as pd
import numpy as np


class Reversal123Engine:
    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        tr1 = df['high'] - df['low']
        tr2 = (df['high'] - df['close'].shift(1)).abs()
        tr3 = (df['low'] - df['close'].shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(period).mean().bfill()

    @staticmethod
    def evaluate_123_reversal(df: pd.DataFrame, left_bars: int = 2, right_bars: int = 2,
                              min_leg_atr: float = 0.65, min_p3_hold_atr: float = 0.05,
                              min_retrace: float = 0.15, max_retrace: float = 0.92,
                              break_buffer_atr: float = 0.05, invalid_buffer_atr: float = 0.15,
                              max_candidate_age: int = 160, use_volume: bool = True) -> dict:
        """
        Evaluates the active 1-2-3 Reversal Map for a given OHLCV dataframe.
        Returns full pattern telemetry, breakout stage, reversal score, targets, and trade signals.
        """
        if df is None or len(df) < (left_bars + right_bars + 15):
            return {
                "has_pattern": False,
                "signal": "HOLD",
                "stage": "Insufficient Bars",
                "reason": "Need at least 25 historical candles for pivot sequencing"
            }

        # Ensure required columns exist
        df = df.copy().reset_index(drop=True)
        if 'atr' not in df.columns:
            df['atr'] = Reversal123Engine.calculate_atr(df, 14)
        if 'avg_vol' not in df.columns:
            df['avg_vol'] = df['volume'].rolling(20).mean().bfill()

        n = len(df)
        curr_idx = n - 1
        curr_close = float(df.loc[curr_idx, 'close'])
        curr_high = float(df.loc[curr_idx, 'high'])
        curr_low = float(df.loc[curr_idx, 'low'])
        curr_open = float(df.loc[curr_idx, 'open']) if 'open' in df.columns else curr_close
        curr_vol = float(df.loc[curr_idx, 'volume']) if 'volume' in df.columns else 1.0
        avg_vol = float(df.loc[curr_idx, 'avg_vol']) if 'avg_vol' in df.columns else 1.0
        atr = float(df.loc[curr_idx, 'atr']) or (curr_close * 0.02)

        # -------------------------------------------------
        # 1. PIVOT CHAIN DETECTION (Pine Script Pivot Logic)
        # -------------------------------------------------
        pivot_types = []   # 1 = High, -1 = Low
        pivot_bars = []
        pivot_prices = []

        for i in range(left_bars, n - right_bars):
            h = float(df.loc[i, 'high'])
            l = float(df.loc[i, 'low'])

            is_ph = all(df.loc[i - lb, 'high'] <= h for lb in range(1, left_bars + 1)) and \
                    all(df.loc[i + rb, 'high'] <= h for rb in range(1, right_bars + 1))
            is_pl = all(df.loc[i - lb, 'low'] >= l for lb in range(1, left_bars + 1)) and \
                    all(df.loc[i + rb, 'low'] >= l for rb in range(1, right_bars + 1))

            if is_ph:
                if pivot_types and pivot_types[-1] == 1:
                    # Better pivot high
                    if h > pivot_prices[-1]:
                        pivot_prices[-1] = h
                        pivot_bars[-1] = i
                else:
                    pivot_types.append(1)
                    pivot_bars.append(i)
                    pivot_prices.append(h)

            if is_pl:
                if pivot_types and pivot_types[-1] == -1:
                    # Better pivot low
                    if l < pivot_prices[-1]:
                        pivot_prices[-1] = l
                        pivot_bars[-1] = i
                else:
                    pivot_types.append(-1)
                    pivot_bars.append(i)
                    pivot_prices.append(l)

            # Limit pivot buffer to 12 as in Pine Script
            if len(pivot_types) > 14:
                pivot_types.pop(0)
                pivot_bars.pop(0)
                pivot_prices.pop(0)

        if len(pivot_types) < 3:
            return {
                "has_pattern": False,
                "signal": "HOLD",
                "stage": "Scanning",
                "reason": "Scanning - awaiting 1-2-3 pivot structure"
            }

        # -------------------------------------------------
        # 2. EVALUATE RECENT 1-2-3 STRUCTURES
        # -------------------------------------------------
        active_pattern = None

        # Search backward from newest pivots to find the active validated 1-2-3 structure
        for idx in range(len(pivot_types) - 3, max(-1, len(pivot_types) - 8), -1):
            t1, t2, t3 = pivot_types[idx], pivot_types[idx + 1], pivot_types[idx + 2]
            b1, b2, b3 = pivot_bars[idx], pivot_bars[idx + 1], pivot_bars[idx + 2]
            p1, p2, p3 = pivot_prices[idx], pivot_prices[idx + 1], pivot_prices[idx + 2]

            bull_chain = (t1 == -1 and t2 == 1 and t3 == -1)
            bear_chain = (t1 == 1 and t2 == -1 and t3 == 1)

            if not (bull_chain or bear_chain):
                continue

            direction = 1 if bull_chain else -1
            leg = abs(p2 - p1)
            safe_leg = max(leg, 1e-6)
            hold = (p3 - p1) if direction == 1 else (p1 - p3)
            retrace = (p2 - p3) / safe_leg if direction == 1 else (p3 - p2) / safe_leg

            # Validation checks per Pine Script
            valid_leg = leg >= (atr * min_leg_atr)
            valid_hold = hold >= (atr * min_p3_hold_atr)
            valid_retrace = (min_retrace <= retrace <= max_retrace)

            if not (valid_leg and valid_hold and valid_retrace):
                continue

            # Check expiration
            bars_since_p3 = curr_idx - b3
            if bars_since_p3 > max_candidate_age:
                continue

            # Check pre-break invalidation
            # Bullish: price should not drop below Point 1 minus invalidation buffer
            # Bearish: price should not rise above Point 1 plus invalidation buffer
            post_p3_slice = df.loc[b3:]
            min_post_p3 = float(post_p3_slice['low'].min())
            max_post_p3 = float(post_p3_slice['high'].max())

            if direction == 1 and min_post_p3 < (p1 - atr * invalid_buffer_atr):
                continue
            if direction == -1 and max_post_p3 > (p1 + atr * invalid_buffer_atr):
                continue

            active_pattern = {
                "direction": direction,
                "dir_label": "Bullish" if direction == 1 else "Bearish",
                "p1": round(p1, 2), "p2": round(p2, 2), "p3": round(p3, 2),
                "b1": b1, "b2": b2, "b3": b3,
                "leg": leg, "hold": hold, "retrace": retrace,
                "neckline": round(p2, 2)
            }
            break

        if not active_pattern:
            return {
                "has_pattern": False,
                "signal": "HOLD",
                "stage": "Scanning",
                "reason": "1-2-3 Scanning - no valid swing pattern active"
            }

        # -------------------------------------------------
        # 3. STRUCTURE SCORING & BREAKOUT ENGINE
        # -------------------------------------------------
        p1 = active_pattern["p1"]
        p2 = active_pattern["p2"]
        p3 = active_pattern["p3"]
        b1, b2, b3 = active_pattern["b1"], active_pattern["b2"], active_pattern["b3"]
        direction = active_pattern["direction"]
        neckline = active_pattern["neckline"]
        leg = active_pattern["leg"]
        hold = active_pattern["hold"]
        retrace = active_pattern["retrace"]

        # Base structure score components (0-100)
        leg_score = min(25.0, (leg / atr) / max(min_leg_atr, 0.10) * 25.0)
        hold_score = min(20.0, (hold / atr) / max(min_p3_hold_atr, 0.05) * 20.0)
        retrace_score = max(0.0, 25.0 - abs(retrace - 0.55) * 70.0)
        left_span = max(1.0, float(b2 - b1))
        right_span = max(1.0, float(b3 - b2))
        symmetry = min(left_span, right_span) / max(left_span, right_span)
        symmetry_score = symmetry * 20.0
        spacing_score = min(10.0, (min(left_span, right_span) / max(left_bars + right_bars, 1)) * 10.0)
        base_score = min(100.0, max(0.0, leg_score + hold_score + retrace_score + symmetry_score + spacing_score))

        # Neckline Breakout Validation
        neckline_break = (curr_close > neckline + atr * break_buffer_atr) if direction == 1 else \
                         (curr_close < neckline - atr * break_buffer_atr)

        # Breakout addition to score
        dist = abs(curr_close - neckline) / atr
        body = abs(curr_close - curr_open) / atr
        close_loc = (curr_close - curr_low) / max(curr_high - curr_low, 1e-6) if direction == 1 else \
                    (curr_high - curr_close) / max(curr_high - curr_low, 1e-6)
        vol_score = 10.0 if (curr_vol > avg_vol) else 3.0
        break_add = min(10.0, dist * 18.0) + min(8.0, body * 10.0) + min(8.0, close_loc * 8.0) + vol_score
        final_score = min(100.0, max(0.0, (base_score * 0.75 + break_add) if neckline_break else base_score))

        # Quality Grade
        if final_score >= 85:
            grade = "A+"
        elif final_score >= 75:
            grade = "A"
        elif final_score >= 62:
            grade = "B"
        else:
            grade = "C"

        # -------------------------------------------------
        # 4. TARGET & STOP LOSS ENGINE (v2.1 Pine Script)
        # -------------------------------------------------
        entry_price = curr_close

        # Stop Loss: Point 3 Buffer (P3 +/- 0.15 * ATR)
        stop_loss_price = round((p3 - atr * invalid_buffer_atr) if direction == 1 else (p3 + atr * invalid_buffer_atr), 2)

        # Target: Measured Move (projects P1-P2 height from breakout)
        target_price = round((entry_price + leg) if direction == 1 else (entry_price - leg), 2)

        risk_distance = max(abs(entry_price - stop_loss_price), 0.05)
        reward_distance = abs(target_price - entry_price)
        rr_multiple = round(reward_distance / risk_distance, 1)

        # Retest Pocket (Neckline +/- 0.30 ATR)
        pocket_top = round(neckline + atr * 0.30, 2)
        pocket_bottom = round(neckline - atr * 0.30, 2)
        in_retest_pocket = (pocket_bottom <= curr_close <= pocket_top)

        if neckline_break:
            stage = "Retest Pocket Active" if in_retest_pocket else "Neckline Break Confirmed"
            signal = "BUY" if direction == 1 else "SELL"
        else:
            stage = "Structure Armed"
            signal = "HOLD"

        dir_tag = "Bullish" if direction == 1 else "Bearish"
        action_name = f"1-2-3 {dir_tag} Breakout" if neckline_break else f"1-2-3 {dir_tag} Armed"
        reason = f"{action_name} [Score: {final_score:.0f} {grade}] | Neckline: ₹{neckline:.2f} | Target: ₹{target_price:.2f} ({rr_multiple}R) | Stop: ₹{stop_loss_price:.2f}"

        return {
            "has_pattern": True,
            "direction": direction,
            "dir_label": dir_tag,
            "stage": stage,
            "neckline_broken": neckline_break,
            "signal": signal,
            "reason": reason,
            "score": round(final_score),
            "grade": grade,
            "neckline": neckline,
            "p1": p1,
            "p2": p2,
            "p3": p3,
            "entry_price": entry_price,
            "target_price": target_price,
            "stop_loss_price": stop_loss_price,
            "risk_reward": rr_multiple,
            "retest_pocket": {"top": pocket_top, "bottom": pocket_bottom},
            "in_retest_pocket": in_retest_pocket,
            "atr_14": round(atr, 2)
        }

