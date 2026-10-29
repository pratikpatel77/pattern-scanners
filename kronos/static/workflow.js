'use strict';
window.KronosWorkflow = {
  review(data, now = new Date()) {
    const out=(title,reason,tone='neutral',checks=[])=>({title,reason,tone,checks});
    if(!data)return out('Choose a stock to analyze','Select a scanner setup or load a cached stock to see its predicted price path and breakout scenarios.');
    const signal=data.pattern||data.signals?.[0];
    const age=(now-new Date(data.asof+'T15:30:00+05:30'))/86400000;
    const checks=[['Price data through',data.asof],['Detected pattern',signal?'Yes':'No']];
    if(!data.forecast){
      if(age>(data.timeframe==='W'?10:1))return out('Refresh prices for a current prediction','These cached prices are old. Fetch newer data with the original scanner, or analyze this date as a historical forecast.','caution',checks);
      return out('Ready to analyze','Choose a forecast period and analyze the predicted path. Later actual prices are needed to measure prediction quality.','neutral',checks);
    }
    const count=data.summary?.sample_count||0;
    checks.push(['Sampled scenarios',String(count)],['Probability quality','Not calibrated against live outcomes']);
    if(data.partial_week||data.metadata?.partial_week)checks.push(['Weekly input','Still forming; forecast can change']);
    if(!signal)checks.push(['Breakout estimate','Unavailable without a detected pattern']);
    const c=data.comparison;
    if(c?.available){
      checks.push(['Actual observations',`${c.available} / ${c.required}`]);
      const m=c.metrics;
      if(m&&Number.isFinite(m.mae)&&Number.isFinite(m.baseline_mae)){
        const better=m.mae<m.baseline_mae,equal=m.mae===m.baseline_mae;
        return out(c.complete?'Historical prediction quality':'Prediction quality so far',`${c.complete?'Across this completed forecast':'Across the available part of this forecast'}, Kronos price error is ${equal?'equal to':better?'lower than':'higher than'} the unchanged-price baseline. ${c.complete?'Inspect more dates before drawing a conclusion.':'The full forecast period has not finished yet.'}`,better?'neutral':'caution',checks);
      }
    }
    if(age>(data.timeframe==='W'?10:1))checks.push(['Freshness','Older cached prices; use Latest prices after refreshing']);
    if(count<20)return out('Small sample — quality unverified','Too few scenarios for a stable frequency estimate. Run a new analysis with the fixed sampling defaults.','caution',checks);
    return out('Prediction quality not yet measured','No later actual prices are available for this forecast. Breakout frequencies describe model scenarios, not proven market odds. Use Backtest to compare past predictions with actual outcomes.','neutral',checks);
  }
};
