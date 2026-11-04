'use strict';
// Pure review arithmetic, shared with the offline tests. No trade orders or forecasts.
const ShortlistModel = (() => {
  const positive = n => typeof n === 'number' && Number.isFinite(n) && n > 0;
  function assess(row, quote, marketOpen, now = Date.now()) {
    const livePrice = positive(quote?.ltp), price = livePrice ? quote.ltp : row.close;
    const age = quote?.quote_time ? (now - Number(quote.quote_time) * 1000) / 1000 : Infinity;
    const fetchedAge = quote?.fetched_at ? (now - Date.parse(quote.fetched_at)) / 1000 : Infinity;
    const fresh = livePrice && marketOpen === true && quote.fresh === true && age >= 0 && age <= 60 && fetchedAge >= 0 && fetchedAge <= 60;
    const side = row.direction === 'short' ? -1 : 1;
    const valid = positive(row.pivot) && positive(row.support) && side * (row.pivot - row.support) > 0;
    let state = 'No setup', distance = null, stopDistance = null;
    if (valid && positive(price)) {
      distance = side * (row.pivot - price) / row.pivot * 100;
      stopDistance = side * (price - row.support) / price * 100;
      state = stopDistance <= 0 ? 'Past stop' : distance <= 0 ? 'Past trigger' : distance <= 1 ? 'Near trigger' : 'Waiting';
    } else if (row.scanner) state = 'Check levels';
    return {price: positive(price) ? price : null, state, distance, stopDistance, fresh,
      source: livePrice ? (fresh ? 'Current quote' : 'Unverified quote') : 'Saved close',
      review: fresh ? state : (state === 'No setup' || state === 'Check levels' ? state : 'Check quote')};
  }
  function csvCell(value) {
    let text = String(value ?? '');
    if (/^[\s]*[=+\-@]/.test(text)) text = "'" + text;
    return '"' + text.replace(/"/g, '""') + '"';
  }
  return {assess, csvCell};
})();
if (typeof module !== 'undefined') module.exports = ShortlistModel;
