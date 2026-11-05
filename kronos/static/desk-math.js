'use strict';
// Small, testable calculations shared by the desk's views.
(function (root) {
  const samples = row => (row?.samples || []).filter(x => Number.isFinite(x) && x > 0);
  const entryPrice = (quote, side) => {
    if (!Number.isFinite(quote?.bid) || !Number.isFinite(quote?.ask) || quote.bid <= 0 || quote.ask < quote.bid || !side) return null;
    return side > 0 ? quote.ask : quote.bid;
  };
  const quantile = (values, q) => {
    if (!values.length) return null;
    const x = [...values].sort((a, b) => a - b), i = (x.length - 1) * q, lo = Math.floor(i);
    return x[lo] + (x[Math.ceil(i)] - x[lo]) * (i - lo);
  };
  const below = (row, price) => {
    const x = samples(row);
    return x.length ? x.filter(s => s < price).length / x.length * 100 : null;
  };
  const outcomes = (row, value) => {
    const pnl = samples(row).map(value);
    if (!pnl.length || pnl.some(x => !Number.isFinite(x))) return {win: null, bad: null, mid: null, good: null};
    return {win: pnl.filter(x => x > 0).length / pnl.length * 100,
      bad: quantile(pnl, .1), mid: quantile(pnl, .5), good: quantile(pnl, .9)};
  };
  const fees = (buy, sell, orders, brokerage) => {
    const exchange = (buy + sell) * .0003553, sebi = (buy + sell) * .000001, broker = orders * brokerage;
    return broker + sell * .0015 + exchange + sebi + (broker + exchange + sebi) * .18 + buy * .00003;
  };
  const reserve = (legs, prefs = {}) => {
    const buy = legs.filter(l => l.qty > 0).reduce((s, l) => s + l.qty * l.lot * l.entry, 0);
    const sell = legs.filter(l => l.qty < 0).reduce((s, l) => s - l.qty * l.lot * l.entry, 0);
    const brokerage = prefs.brokerage ?? 20, turnover = buy + sell;
    return fees(buy, sell, legs.length, brokerage) + fees(turnover, turnover, legs.length, brokerage) + turnover * (prefs.slippage ?? .01);
  };
  const bounds = (legs, value) => {
    const knots = [...new Set([0, ...legs.map(l => l.strike)])].sort((a, b) => a - b);
    const vals = knots.map(value), slope = legs.reduce((s, l) => s + (l.type === 'CE' ? l.qty * l.lot : 0), 0);
    const be = knots.filter((k, i) => Math.abs(vals[i]) < 1e-9);
    for (let i = 1; i < knots.length; i++) {
      const a = vals[i - 1], b = vals[i];
      if (a * b < 0) be.push(knots[i - 1] + (knots[i] - knots[i - 1]) * (-a) / (b - a));
    }
    if (slope && vals.at(-1) * slope < 0) be.push(knots.at(-1) - vals.at(-1) / slope);
    return {maxProfit: slope > 0 ? Infinity : Math.max(...vals),
      maxLoss: slope < 0 ? -Infinity : Math.min(0, ...vals), be: [...new Set(be)].sort((a, b) => a - b)};
  };
  const api = {entryPrice, below, outcomes, fees, reserve, bounds};
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.DeskMath = api;
})(typeof window === 'undefined' ? globalThis : window);
