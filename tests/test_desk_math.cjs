const assert = require('node:assert/strict');
const m = require('../kronos/static/desk-math.js');
assert.equal(m.entryPrice({bid:11.95,ask:12.10,ltp:12},1),12.10);
assert.equal(m.entryPrice({bid:11.95,ask:12.10,ltp:12},-1),11.95);
assert.equal(m.entryPrice({bid:0,ask:12.10,ltp:12},1),null);
assert.equal(m.entryPrice({bid:13,ask:12,ltp:12},-1),null);
const row = {samples: [90, 100, 110, 140], q10: 93, median: 105, q90: 131};
assert.equal(m.below(row, 105), 50);
assert.equal(m.below(row, 150), 100);
assert.equal(m.below({q10: 90, median: 100, q90: 110}, 100), null);
assert.deepEqual(m.outcomes(row, s => s - 105), {win: 50, bad: -12, mid: 0, good: 26.000000000000007});
// For a put, the low stock-price quantile gives high P&L. Sort outcomes themselves.
const put = m.outcomes(row, s => 105 - s);
assert(put.bad < put.mid && put.mid < put.good);
assert.equal(m.fees(10000, 20000, 2, 20), 90.11302);
assert.deepEqual(m.bounds([{type:'PE', strike:1000, qty:-1, lot:100}], s => 480 - 100 * Math.max(0,1000-s)),
  {maxProfit:480, maxLoss:-99520, be:[995.2]});
assert.deepEqual(m.bounds([{type:'CE', strike:100, qty:1, lot:1}], s => Math.max(0,s-100)-1000),
  {maxProfit:Infinity, maxLoss:-1000, be:[1100]});
assert.equal(m.bounds([{type:'CE',strike:100,qty:-1,lot:100}],s=>500-100*Math.max(0,s-100)).maxLoss,-Infinity);
console.log('Desk maths: sample shares, P&L quantiles, fees and exact payoff bounds passed');
