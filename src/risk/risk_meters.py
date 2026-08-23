"""
risk/risk_meters.py
--------------------
Risk-meter summary computed once per EngineResult pass: seven 0-100
gauges (Delta, Gamma, Vega, Theta, Liquidity, Event, Concentration risk)
derived from the ATM Greeks, lot size, DTE, and total PCR.

Moved from engine.py (Step 4c of the v4 migration plan). Pure move +
rename only: no behavioral changes, no signature changes.
"""

from __future__ import annotations

__all__ = [
    "_build_risk_meters",
]


# ===========================================================================
# Risk meters (was inline inside dashboard_modules.render_risk_dashboard)
# ===========================================================================

def _build_risk_meters(atm_delta: float, atm_gamma: float, base_iv: float,
                         atm_theta: float, lot_size: int, dte: int,
                         pcr: float) -> list[dict]:
    return [
        {'name': "Delta Risk",     'pct': int(min(abs(atm_delta) * 100, 100))},
        {'name': "Gamma Risk",     'pct': int(min(abs(atm_gamma) * 100_000, 100))},
        {'name': "Vega Risk",      'pct': int(min(base_iv * 333, 100))},
        {'name': "Theta Decay",    'pct': int(min(abs(atm_theta) * 5, 100))},
        {'name': "Liquidity Risk", 'pct': 60 if lot_size > 100 else 25},
        {'name': "Event Risk",     'pct': 85 if dte <= 3 else 55 if dte <= 7 else 30},
        {'name': "Concentration",  'pct': int(min(40 / max(pcr, 0.5), 90))},
    ]
