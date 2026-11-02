'use strict';
window.LivePrices={
  async load(symbols){
    const names=[...new Set(symbols.filter(Boolean))],result={};
    for(let i=0;i<names.length;i+=50){
      const response=await fetch('/api/quotes?'+new URLSearchParams({symbols:names.slice(i,i+50).join(',')}),{cache:'no-store'});
      const data=await response.json();if(!response.ok)throw new Error(data.error||'Price refresh failed.');
      Object.assign(result,data.quotes);
    }
    return result;
  },
  label(q){
    if(!q)return 'LTP unavailable';
    const at=q.quote_time?`${q.timestamp_kind==='quote'?'Quote':'Trade'} ${new Date(q.quote_time*1000).toLocaleString('en-IN',{timeZone:'Asia/Kolkata'})} IST`:q.reported_trade_time?`${q.reported_trade_time} IST · trade date not supplied`:'Trade time not supplied';
    return `${q.source||'API'} LTP · ${at} · fetched ${new Date(q.fetched_at).toLocaleTimeString('en-IN',{timeZone:'Asia/Kolkata'})} IST`;
  },
  watch(symbols,render,error){
    let running=false,pending=false,shown='';
    const refresh=async()=>{
      if(document.hidden||window.frameElement?.hidden)return;
      if(running){pending=true;return;}
      const names=symbols();if(!names.length)return;
      // Show 'Fetching' only when the symbol list changes; later refreshes update in place without flicker.
      running=true;const key=names.join();if(key!==shown)render({},true);
      try{render(await LivePrices.load(names),false);shown=key;}catch(e){error(e);}
      finally{running=false;if(pending){pending=false;refresh();}}
    };
    // Every 5s in market hours (FYERS batches 50 stocks per call); once a minute otherwise.
    const marketHours=()=>{const p=Object.fromEntries(new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Kolkata',weekday:'short',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date()).map(x=>[x.type,x.value]));const m=Number(p.hour)*60+Number(p.minute);return !['Sat','Sun'].includes(p.weekday)&&m>=555&&m<930;};
    const loop=()=>setTimeout(()=>{refresh().finally(loop);},marketHours()?5000:60000);
    loop();
    window.addEventListener('focus',refresh);
    document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
    window.addEventListener('message',event=>{if(event.origin===location.origin&&event.data==='refresh-prices')refresh();});
    return refresh;
  }
};
