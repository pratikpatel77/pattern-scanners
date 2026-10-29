'use strict';
window.KronosCharts = (() => {
  const colors = { text:'#57534e', grid:'#e7e5e4', green:'#15803d', red:'#b91c1c', amber:'#b45309', blue:'#2563eb' };
  const config = {responsive:true,displaylogo:false,scrollZoom:true,displayModeBar:'hover',modeBarButtonsToRemove:['select2d','lasso2d','toImage'],doubleClick:'reset'};
  const moving = (rows,n) => rows.map((_,i)=>i<n-1?null:rows.slice(i-n+1,i+1).reduce((v,r)=>v+r.close,0)/n);
  const baseLayout = () => ({paper_bgcolor:'transparent',plot_bgcolor:'transparent',font:{family:'-apple-system, Segoe UI, system-ui, Roboto, sans-serif',color:colors.text,size:11},margin:{l:12,r:65,t:20,b:35},showlegend:false,dragmode:'pan',hovermode:'x',hoverlabel:{bgcolor:'#ffffff',bordercolor:'#d6d3d1',font:{color:'#1c1917',size:12}},autosize:true});

  async function price(id, data, options={}) {
    const el=document.getElementById(id), history=data?.history||[], forecast=data?.forecast||[], actual=data?.comparison?.rows||[];
    const dates=[...new Set([...history.map(r=>r.date),...forecast.map(r=>r.date),...actual.map(r=>r.date)])].sort();
    const xs=history.map(r=>r.date), traces=[];
    if(options.candles!==false){
      traces.push({type:'candlestick',name:'Observed OHLC',x:xs,open:history.map(r=>r.open),high:history.map(r=>r.high),low:history.map(r=>r.low),close:history.map(r=>r.close),increasing:{line:{color:colors.green,width:1},fillcolor:colors.green},decreasing:{line:{color:colors.red,width:1},fillcolor:colors.red},whiskerwidth:.3,hovertext:history.map(r=>`${r.date}<br>Observed market prices<br>Open ₹${r.open.toFixed(2)}<br>High ₹${r.high.toFixed(2)}<br>Low ₹${r.low.toFixed(2)}<br>Close ₹${r.close.toFixed(2)}`),hoverinfo:'text'});
    }else{
      traces.push({type:'scatter',mode:'lines',name:'Observed close',x:xs,y:history.map(r=>r.close),line:{color:'#57534e',width:1.8},hovertemplate:'%{x}<br>Observed close ₹%{y:,.2f}<extra></extra>'});
    }
    if(options.sma20) traces.push({type:'scatter',mode:'lines',name:'MA 20',x:xs,y:moving(history,20),line:{color:colors.blue,width:1},hovertemplate:'MA 20 ₹%{y:,.2f}<extra></extra>'});
    if(options.sma50) traces.push({type:'scatter',mode:'lines',name:'MA 50',x:xs,y:moving(history,50),line:{color:'#7e22ce',width:1},hovertemplate:'MA 50 ₹%{y:,.2f}<extra></extra>'});
    const shapes=[],annotations=[];
    if(forecast.length && history.length){
      const anchor=history.at(-1),fx=[anchor.date,...forecast.map(r=>r.date)];
      if(options.band!==false){
        traces.push({type:'scatter',mode:'lines',name:'AI 10th percentile',x:fx,y:[anchor.close,...forecast.map(r=>r.q10)],line:{width:0},hoverinfo:'skip'});
        traces.push({type:'scatter',mode:'lines',name:'AI 10–90% range',x:fx,y:[anchor.close,...forecast.map(r=>r.q90)],line:{width:0},fill:'tonexty',fillcolor:'rgba(180,83,9,.10)',hoverinfo:'skip'});
      }
      traces.push({type:'scatter',mode:'lines',name:'AI median (forecast)',x:fx,y:[anchor.close,...forecast.map(r=>r.median)],line:{color:colors.amber,width:2,dash:'dash'},customdata:[[anchor.close,anchor.close],...forecast.map(r=>[r.q10,r.q90])],hovertemplate:'%{x}<br>AI median ₹%{y:,.2f}<br>10–90%: ₹%{customdata[0]:,.2f} – ₹%{customdata[1]:,.2f}<extra>Forecast</extra>'});
      shapes.push({type:'line',xref:'x',yref:'paper',x0:anchor.date,x1:anchor.date,y0:0,y1:1,line:{color:'#78716c',dash:'dot',width:1}});
      annotations.push({xref:'x',yref:'paper',x:anchor.date,y:1.035,text:'FORECAST →',showarrow:false,xanchor:'left',font:{color:colors.amber,size:9}});

    }
    if(actual.length) traces.push({type:'scatter',mode:'lines+markers',name:'Actual after forecast',x:actual.map(r=>r.date),y:actual.map(r=>r.actual),line:{color:colors.green,width:2},marker:{size:4},hovertemplate:'%{x}<br>Actual close ₹%{y:,.2f}<extra>Observed later</extra>'});
    const volume=options.volume!==false;
    if(volume) traces.push({type:'bar',name:'Observed volume',x:xs,y:history.map(r=>r.volume),yaxis:'y2',marker:{color:history.map(r=>r.close>=r.open?'rgba(21,128,61,.25)':'rgba(185,28,28,.25)')},hovertemplate:'%{x}<br>Observed volume %{y:,.0f}<extra></extra>'});
    const layout={...baseLayout(),uirevision:`${data?.symbol||''}|${data?.asof||''}|${forecast.length}|${id}`,xaxis:{type:'category',categoryorder:'array',categoryarray:dates,rangeslider:{visible:false},showgrid:false,zeroline:false,tickfont:{size:9},nticks:8,showspikes:true,spikemode:'across',spikesnap:'cursor',spikecolor:'#78716c',spikethickness:.8},yaxis:{domain:volume?[.24,1]:[0,1],side:'right',showgrid:true,gridcolor:colors.grid,zeroline:false,tickformat:',.2f',fixedrange:false,showspikes:true,spikemode:'across',spikecolor:'#78716c',spikethickness:.8},yaxis2:{domain:[0,.17],side:'right',showgrid:false,zeroline:false,tickformat:'.2s',title:{text:'Vol',font:{size:9}},fixedrange:true},shapes,annotations};
    if(!history.length) layout.annotations=[{xref:'paper',yref:'paper',x:.5,y:.5,text:'Load a symbol to view market data',showarrow:false,font:{color:'#78716c',size:13}}];
    await Plotly.react(el,traces,layout,config);
    el._kronosDates=dates;
  }
  async function equity(id,rows){
    const layout={...baseLayout(),margin:{l:10,r:55,t:10,b:30},xaxis:{showgrid:false,nticks:4,tickfont:{size:9}},yaxis:{side:'right',gridcolor:colors.grid,tickformat:'.3s',zeroline:false}};
    await Plotly.react(id,[{type:'scatter',mode:'lines',x:rows.map(r=>r.date),y:rows.map(r=>r.equity),line:{color:colors.green,width:2},hovertemplate:'%{x}<br>Paper account ₹%{y:,.2f}<extra></extra>'}],layout,{...config,scrollZoom:false});
  }
  function reset(id){return Plotly.relayout(id,{'xaxis.autorange':true,'yaxis.autorange':true});}
  function range(id,days){
    const el=document.getElementById(id),dates=el._kronosDates||[];
    if(days==='all'||!dates.length)return reset(id);
    const cutoff=new Date(dates.at(-1));cutoff.setDate(cutoff.getDate()-Number(days));
    let first=dates.findIndex(d=>new Date(d)>=cutoff);if(first<0)first=0;
    return Plotly.relayout(id,{'xaxis.range':[first-.5,dates.length-.5]});
  }
  function resize(){for(const id of ['price-chart','backtest-chart','equity-chart']){const el=document.getElementById(id);if(el?.data&&el.offsetWidth)Plotly.Plots.resize(el);}}
  function save(id){return Plotly.downloadImage(id,{format:'png',filename:'sai-research',width:1500,height:760,scale:1});}
  return {price,equity,reset,range,resize,save};
})();
