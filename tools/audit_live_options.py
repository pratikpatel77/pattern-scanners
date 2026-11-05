"""Exercise the live forecast/chain/ranking pipeline in a temporary journal only."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kronos.data import ROOT
from kronos.service import ResearchService
from kronos.options import preferences
from kronos.server import create_server


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--serve',action='store_true')
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='kronos-audit-') as directory:
        service=ResearchService(root=directory)
        try:
            result=service.options.analyse('SBIN',preferences({}),progress=lambda _,msg:print(msg,flush=True))
            f=result.get('forecast') or {}
            report={k:result.get(k) for k in ('symbol','status','reason','spot','quote_time','forecast_created','data_mode','forecast_end','expiry')}
            report['forecast']={k:f.get(k) for k in ('asof','summary','metadata')}
            report['candidate_count']=len(result.get('candidates',[]))
            report['candidates']=[{k:c.get(k) for k in ('name','status','reasons','entry_credit','max_loss','max_profit','stressed_mean_net','scenario_profit_pct','stressed_profit_pct','calculation_version')}
                                  for c in service.options.top_ideas(result.get('candidates',[]))]
            out=ROOT/'outputs'/'live-options-audit-2026-11-05.json'
            out.parent.mkdir(exist_ok=True)
            out.write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
            print(json.dumps(report,allow_nan=False),flush=True)
            if args.serve:
                server=create_server(service,8767)
                print('TEMPORARY AUDIT DESK http://127.0.0.1:8767/options',flush=True)
                try:server.serve_forever()
                except KeyboardInterrupt:pass
                finally:server.server_close()
        finally:service.close()


if __name__=='__main__':main()
