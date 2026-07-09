"""Market analyst — market map, macro sensitivity, and demand-driver context."""
from __future__ import annotations

from src.agents.base import BaseAgent


class MarketAnalyst(BaseAgent):
    name = "market_analyst"
    role = "Assesses market structure, demand drivers, and macro sensitivity."
