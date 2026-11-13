const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../kronos/static/desk-trend.js'), 'utf8');
const start = source.indexOf('async function trendTrade(');
const end = source.indexOf('// Show the idea', start);

test('manual confirmation uses the selected strategy decision', async () => {
  for (const selectedCall of ['Pass', 'Take']) {
    const prompts = [], requests = [];
    const context = vm.createContext({
      TREND: {pick: 'signal', board: {auto: true, rows: [{id: 'signal', symbol: 'TEST'}]},
        detail: {decision: {call: selectedCall === 'Pass' ? 'Take' : 'Pass'},
          plan: {ideas: [{name: 'Selected spread', max_loss: 100,
            decision: {call: selectedCall, why: 'Selected position reason'}}]}}},
      P: {api: '/api/trend1h', name: 'ADX 1H', key: 'trend1h'},
      confirm: text => { prompts.push(text); return true; },
      api: async (url, request) => { requests.push(request); },
      inr: String, toast() {}, loadPaper() {}, store() {}, trendLoad: async () => {}
    });
    vm.runInContext(source.slice(start, end), context);
    await context.trendTrade(0, {});
    assert.equal(requests.length, 1);
    assert.equal(requests[0].override, selectedCall === 'Pass');
    assert.equal(prompts[0].includes('OVERRIDE'), selectedCall === 'Pass');
  }
});
