const {test} = require('node:test');
const assert = require('node:assert/strict');
const {assess,csvCell} = require('../kronos/static/shortlist-model.js');
const now = Date.parse('2026-11-04T05:00:00Z');
const long = {direction:'long',scanner:'darvas',close:99.5,pivot:100,support:95};
const short = {...long,direction:'short',support:105,close:100.5};
const q = ltp => ({ltp,fresh:true,quote_time:now/1000,fetched_at:new Date(now).toISOString()});
test('bullish trigger, invalidation and distance use the correct side',()=>{
  assert.equal(assess(long,q(99.5),true,now).review,'Near trigger');
  assert.equal(assess(long,q(100),true,now).review,'Past trigger');
  assert.equal(assess(long,q(95),true,now).review,'Past stop');
  assert.equal(assess(long,q(97),true,now).review,'Waiting');
  assert.equal(assess(long,q(101),true,now).distance,-1);
});
test('bearish trigger and invalidation reverse the inequalities',()=>{
  assert.equal(assess(short,q(100.5),true,now).review,'Near trigger');
  assert.equal(assess(short,q(99),true,now).review,'Past trigger');
  assert.equal(assess(short,q(105),true,now).review,'Past stop');
  assert.equal(assess(short,q(103),true,now).review,'Waiting');
});
test('a cached close must not be described as a fresh signal',()=>{
  const result=assess(long,null,true,now);
  assert.equal(result.review,'Check quote');
  assert.equal(result.source,'Saved close');
  assert.equal(result.state,'Near trigger');
});
test('stale, undated, future, failed and closed-session quotes are reference only',()=>{
  const invalid=[{...q(99.5),fresh:false},{...q(99.5),quote_time:null},
    {...q(99.5),quote_time:now/1000-61},{...q(99.5),quote_time:now/1000+1},
    {...q(99.5),fetched_at:'bad'},{...q(99.5),fetched_at:new Date(now-61000).toISOString()}];
  for(const quote of invalid) assert.equal(assess(long,quote,true,now).review,'Check quote');
  assert.equal(assess(long,q(99.5),false,now).review,'Check quote');
  assert.equal(assess(long,q(99.5),true,now+61000).review,'Check quote');
});
test('bad or missing levels cannot become an actionable state',()=>{
  for(const row of [{...long,pivot:0},{...long,support:101},{...short,support:95},{...long,pivot:NaN}]) {
    assert.equal(assess(row,q(100),true,now).review,'Check levels');
  }
  assert.equal(assess({symbol:'OLD'},q(100),true,now).review,'No setup');
});
test('export quotes commas/newlines and neutralizes spreadsheet formulas',()=>{
  assert.equal(csvCell('Wait, then "check"\nagain'),'"Wait, then ""check""\nagain"');
  for(const text of ['=1+1',' +SUM(A1)','-1+2','@SUM(A1)','\t=cmd']) assert.ok(csvCell(text).startsWith('"\''));
});
