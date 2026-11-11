// Accuracy-first qualification and sizing. This module never fetches market data
// and never sends broker orders. Missing context is a hard NO TRADE.
const INTERVAL_MS = {
  '1m': 60_000, '3m': 180_000, '5m': 300_000, '10m': 600_000,
  '15m': 900_000, '30m': 1_800_000, '1h': 3_600_000,
  '1d': 86_400_000, '1w': 604_800_000, '1mo': 2_678_400_000
};

function familyOf(name = '') {
  const n = name.toLowerCase();
  if (/vwap/.test(n)) return 'vwap';
  if (/ema|moving average|higher high|lower high/.test(n)) return 'trend';
  if (/rsi|macd|roc|momentum|stochastic/.test(n)) return 'momentum';
  if (/volume|obv|mfi|cmf/.test(n)) return 'volume';
  if (/breakout|donchian|inside bar|nr4|nr7|opening range|initial balance/.test(n)) return 'breakout';
  if (/bollinger|squeeze|atr expansion|volatility/.test(n)) return 'volatility';
  if (/engulf|hammer|star|pin bar|doji|candlestick|support rejection|resistance rejection/.test(n)) return 'price-action';
  if (/supertrend|parabolic|ichimoku|adx|dmi/.test(n)) return 'trend-strength';
  if (/gap/.test(n)) return 'gap';
  return 'other';
}

function round2(value) {
  return Number.isFinite(value) ? Math.round(value * 100) / 100 : null;
}

export function calculateIntradayRiskPlan({
  capital = 1000, riskPct = 0.5, entry, stop, target,
  estimatedRoundTripCosts
} = {}) {
  const reasons = [];
  const cap = Number(capital), pct = Number(riskPct);
  const en = Number(entry), st = Number(stop), tg = Number(target);
  const costs = Number(estimatedRoundTripCosts);
  if (!(Number.isFinite(cap) && cap > 0)) reasons.push('Capital must be positive.');
  if (!(Number.isFinite(pct) && pct > 0 && pct <= 1)) reasons.push('Risk per trade must be between 0% and 1%.');
  if (!(Number.isFinite(en) && en > 0 && Number.isFinite(st) && st > 0 && st !== en)) reasons.push('A valid entry and stop are required.');
  if (!Number.isFinite(costs) || costs < 0) reasons.push('Verified round-trip charges must be supplied; costs are not guessed.');
  if (Number.isFinite(en) && Number.isFinite(st) && en > 0 && st > 0 && en !== st && Number.isFinite(tg) && tg > 0) {
    const isLong = st < en;
    if ((isLong && tg <= en) || (!isLong && tg >= en)) reasons.push('Target must be on the profitable side of entry.');
    const grossRiskPerShare = Math.abs(en - st);
    const grossRewardPerShare = Math.abs(tg - en);
    if (grossRewardPerShare / grossRiskPerShare < 2) reasons.push('Gross reward/risk must be at least 1:2.');
  } else {
    reasons.push('A target is required to verify the 1:2 reward/risk rule.');
  }
  if (reasons.length) return {
    qualified: false, quantity: 0, capital: round2(cap), riskBudget: round2(cap * pct / 100),
    estimatedRoundTripCosts: Number.isFinite(costs) ? round2(costs) : null,
    reasons
  };

  const riskBudget = cap * pct / 100;
  const riskPerShare = Math.abs(en - st);
  const rewardPerShare = Math.abs(tg - en);
  const quantityByRisk = Math.floor(Math.max(0, riskBudget - costs) / riskPerShare);
  const quantityByCapital = Math.floor(cap / en);
  const quantity = Math.max(0, Math.min(quantityByRisk, quantityByCapital));
  if (quantity < 1) reasons.push('₹1,000 capital and the risk limit do not permit even one share after estimated charges.');
  const totalRisk = quantity * riskPerShare + costs;
  const netReward = quantity * rewardPerShare - costs;
  if (quantity > 0 && netReward / totalRisk < 2) reasons.push('Net reward/risk after estimated charges is below 1:2.');
  return {
    qualified: reasons.length === 0, quantity,
    capital: round2(cap), riskPct: pct, riskBudget: round2(riskBudget),
    entry: round2(en), stop: round2(st), target: round2(tg),
    estimatedRoundTripCosts: round2(costs),
    capitalRequired: round2(quantity * en),
    estimatedMaxLoss: round2(totalRisk),
    estimatedNetReward: round2(netReward),
    netRewardRisk: totalRisk > 0 ? round2(netReward / totalRisk) : null,
    reasons
  };
}

export function assessAccuracyFirst({ bars = [], interval = '5m', strategyResults = [], capital = 1000,
  entry, stop, target, estimatedRoundTripCosts, higherTimeframe = null,
  indexTrend = null, sectorTrend = null, now = Date.now() } = {}) {
  const reasons = [];
  const last = bars.at(-1);
  const intervalMs = INTERVAL_MS[interval] || INTERVAL_MS['5m'];
  if (bars.length < 50) reasons.push('At least 50 candles are required.');
  if (!last || !Number.isFinite(Date.parse(last.timestamp))) reasons.push('Latest candle timestamp is missing or invalid.');
  else {
    const age = now - Date.parse(last.timestamp);
    if (age < -60_000 || age > intervalMs * 2) reasons.push('Market data is stale or timestamp is in the future.');
  }
  const validVolumes = bars.filter(x => Number.isFinite(Number(x.volume)) && Number(x.volume) > 0).length;
  if (validVolumes < 20) reasons.push('At least 20 positive-volume candles are required for volume confirmation.');

  const votes = { BUY: new Map(), SELL: new Map() };
  for (const result of strategyResults) {
    if (!['BUY', 'SELL'].includes(result?.side)) continue;
    const family = familyOf(result.strategy);
    if (family !== 'other') votes[result.side].set(family, result.strategy);
  }
  const buyFamilies = [...votes.BUY.keys()];
  const sellFamilies = [...votes.SELL.keys()];
  const direction = buyFamilies.length > sellFamilies.length ? 'BUY' :
    sellFamilies.length > buyFamilies.length ? 'SELL' : 'WAIT';
  const agreeing = direction === 'BUY' ? buyFamilies : direction === 'SELL' ? sellFamilies : [];
  const opposing = direction === 'BUY' ? sellFamilies : direction === 'SELL' ? buyFamilies : [];
  if (direction === 'WAIT') reasons.push('Strategy families are tied or have no directional consensus.');
  if (agreeing.length < 3) reasons.push('At least three independent strategy families must agree.');
  if (opposing.length > 1) reasons.push('Too many opposing strategy families; conflict filter failed.');
  if (!higherTimeframe || higherTimeframe !== direction) reasons.push('A matching, source-verified higher-timeframe trend is unavailable.');
  if (!indexTrend || indexTrend !== direction) reasons.push('A matching, source-verified NIFTY/index trend is unavailable.');
  if (!sectorTrend || sectorTrend !== direction) reasons.push('A matching, source-verified sector trend is unavailable.');

  const riskPlan = calculateIntradayRiskPlan({
    capital, riskPct: 0.5, entry, stop, target, estimatedRoundTripCosts
  });
  if (!riskPlan.qualified) reasons.push(...riskPlan.reasons);
  const qualified = reasons.length === 0;
  return {
    decision: qualified ? direction : 'NO TRADE',
    direction, qualified, mode: 'ACCURACY_FIRST',
    capital: Number(capital), interval, candleCount: bars.length,
    independentConfirmations: agreeing.length, opposingFamilies: opposing.length,
    confirmingFamilies: agreeing,
    riskPlan, reasons: [...new Set(reasons)],
    execution: 'PAPER_ONLY',
    note: 'No live provider is implied by this assessment. No broker order is sent.'
  };
}
