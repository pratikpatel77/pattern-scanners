const {readFileSync}=require('node:fs');
const {runInNewContext}=require('node:vm');
const assert=require('node:assert/strict');
const sandbox={window:{}};runInNewContext(readFileSync('kronos/static/workflow.js','utf8'),sandbox);
const review=sandbox.window.KronosWorkflow.review,now=new Date('2026-10-29T12:00:00+05:30');
const base={asof:'2026-10-28',timeframe:'D',pattern:{scanner:'darvas'},forecast:[{}],summary:{sample_count:32}};
assert.match(review(null,now).title,/Choose/);
assert.match(review({...base,asof:'2026-10-10',forecast:null},now).title,/Refresh/);
assert.match(review({...base,forecast:null},now).title,/Ready/);
assert.match(review(base,now).title,/not yet measured/);
assert.match(review({...base,summary:{sample_count:4}},now).title,/Small sample/);
for(const [mae,baseline,word] of [[10,20,'lower'],[20,10,'higher'],[10,10,'equal']]){
 const actual={...base,comparison:{available:10,required:10,complete:true,metrics:{mae,baseline_mae:baseline}}};
 assert.match(review(actual,now).title,/Historical/);assert.ok(review(actual,now).reason.includes(word));
}
assert.match(review({...base,comparison:{available:1,required:10,complete:false,metrics:{mae:1,baseline_mae:0}}},now).title,/so far/);
assert.ok(review({...base,pattern:null},now).checks.some(row=>row[1].includes('Unavailable')));
assert.ok(review({...base,metadata:{partial_week:true}},now).checks.some(row=>row[1].includes('Still forming')));
assert.ok(review(base,now).checks.some(row=>row[1].includes('Not calibrated')));
console.log('12 prediction quality checks passed');
