(function () {
  function smartMoneyBadge(
    hasRatioData,
    isInst,
    oiChgDominant,
    totalOI,
    volRatio,
    th
  ) {
    if (!hasRatioData)
      return { dot: "⚪", label: "RETAIL", color: "var(--txt3)" };

    if (isInst) {
      if (oiChgDominant > totalOI * 0.02)
        return { dot: "🟢", label: "ACC", color: "var(--green)" };

      if (oiChgDominant < -totalOI * 0.02)
        return { dot: "🔴", label: "DIST", color: "var(--red)" };

      return { dot: "🟡", label: "HEDGE", color: "var(--amber)" };
    }

    if (volRatio >= th.volRatioMax && Math.abs(oiChgDominant) < totalOI * 0.05) {
      return { dot: "🔵", label: "ROLL", color: "#3b82f6" };
    }

    return { dot: "⚪", label: "RETAIL", color: "var(--retail)" };
  }

  window.smartMoneyBadge = smartMoneyBadge;
})();