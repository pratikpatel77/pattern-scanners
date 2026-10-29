"""Small loopback-only HTTP server. No credentials or filesystem browsing API."""
import argparse
import csv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import mimetypes
from pathlib import Path
import secrets
import socket
from urllib.parse import parse_qs, urlparse
import webbrowser
from .data import ROOT
from .service import BusyError, ResearchService
from .settings import Settings

STATIC=Path(__file__).resolve().parent/'static'


def create_server(service,port=8765):
    token=secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass

        def send(self,status,body,content_type='application/json; charset=utf-8',download=None):
            if not isinstance(body,bytes):
                body=json.dumps(body,allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store' if not self.path.startswith('/vendor/') else 'public, max-age=86400')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'self'")
            if download:
                self.send_header('Content-Disposition',f'attachment; filename="{download}"')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):
                pass

        def allowed(self,write=False):
            hosts={f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
            if self.headers.get('Host') not in hosts:
                self.send(403,{'error':'Local host only'}); return False
            origin=self.headers.get('Origin')
            if origin and origin not in {f'http://{h}' for h in hosts}:
                self.send(403,{'error':'Origin not allowed'}); return False
            if write and not secrets.compare_digest(self.headers.get('X-Kronos-Token',''),token):
                self.send(403,{'error':'Reload the dashboard before making changes.'}); return False
            return True

        def do_GET(self):
            if not self.allowed(): return
            route=urlparse(self.path)
            query=parse_qs(route.query)
            try:
                if route.path=='/api/health':
                    self.send(200,{'app':'kronos-research','version':'2.0.0'}); return
                if route.path=='/api/bootstrap':
                    self.send(200,{**service.bootstrap(),'token':token}); return
                if route.path=='/api/context':
                    self.send(200,service.market.context(query.get('symbol',[''])[0],query.get('timeframe',['D'])[0],query.get('asof',[None])[0])); return
                if route.path=='/api/runs':
                    self.send(200,service.store.list()); return
                if route.path=='/api/watch':
                    self.send(200,service.monitor.rows()); return
                if route.path.startswith('/api/runs/'):
                    key=route.path.split('/')[-1]
                    run=service.run(key)
                    if query.get('format')==['csv']:
                        out=io.StringIO(newline='')
                        writer=csv.writer(out)
                        writer.writerow(['symbol','timeframe','origin','forecast_date','median','q10','q90','actual','actual_date'])
                        result=run['result'] or {}
                        windows=result.get('windows',[result])
                        for w in windows:
                            actual=w.get('comparison',{}).get('rows',[])
                            for i,row in enumerate(w.get('forecast',[])):
                                writer.writerow([w['symbol'],w['timeframe'],w['asof'],row['date'],row['median'],row['q10'],row['q90'],actual[i]['actual'] if i<len(actual) else '',actual[i]['date'] if i<len(actual) else ''])
                        self.send(200,out.getvalue().encode('utf-8'),'text/csv; charset=utf-8',f'kronos-{key[:8]}.csv'); return
                    if query.get('format')==['json']:
                        self.send(200,run,download=f'kronos-{key[:8]}.json'); return
                    self.send(200,run); return
                if route.path=='/scanner':
                    path=service.root/'results.html'
                    if not path.exists(): raise FileNotFoundError('Run the original scanner to create its report.')
                    # Original report is served byte-for-byte; its inline JS needs its own CSP.
                    self.send_scanner(path); return
                relative=('index.html' if route.path=='/ai' or (route.path=='/' and query.get('run')) else 'workspace.html') if route.path in ('/','/ai') else route.path.lstrip('/')
                path=(STATIC/relative).resolve()
                if STATIC.resolve() not in path.parents or not path.is_file():
                    self.send(404,{'error':'Not found'}); return
                ctype=mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
                self.send(200,path.read_bytes(),ctype+'; charset=utf-8')
            except (ValueError,KeyError,FileNotFoundError) as exc:
                self.send(400,{'error':str(exc)})
            except Exception:
                self.send(500,{'error':'Unable to read local research data.'})

        def send_scanner(self,path):
            body=path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(body)))
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Cache-Control','no-store')
            self.end_headers(); self.wfile.write(body)

        def do_POST(self):
            if not self.allowed(write=True): return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=65536 or self.headers.get('Content-Type','').split(';')[0]!='application/json':
                    raise ValueError('A small JSON request is required.')
                request=json.loads(self.rfile.read(size))
                if self.path=='/api/settings':
                    settings=Settings.from_dict(request)
                    service.store.save_settings(settings)
                    self.send(200,settings.to_dict())
                elif self.path=='/api/jobs':
                    self.send(202,service.submit(request))
                elif self.path=='/api/watch':
                    self.send(200,service.monitor.configure(request))
                elif self.path.startswith('/api/cancel/'):
                    self.send(200,service.cancel(self.path.split('/')[-1]))
                else:
                    self.send(404,{'error':'Not found'})
            except BusyError as exc:
                self.send(409,{'error':str(exc)})
            except (ValueError,KeyError,TypeError) as exc:
                self.send(400,{'error':str(exc)})
            except Exception:
                self.send(500,{'error':'The request could not be completed.'})
    return ThreadingHTTPServer(('127.0.0.1',port),Handler)


def main(argv=None):
    parser=argparse.ArgumentParser(description='Kronos Research — local AI forecast workspace')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--no-open',action='store_true')
    args=parser.parse_args(argv)
    if not 1024<=args.port<=65535:
        parser.error('Choose a port from 1024 to 65535.')
    url=f'http://127.0.0.1:{args.port}'
    # Opening the launcher twice reuses the same workspace process.
    from urllib.request import urlopen
    try:
        with urlopen(url+'/api/health',timeout=1) as response:
            running=json.load(response)
        if running.get('app')=='kronos-research':
            if not args.no_open: webbrowser.open(url)
            print(f'Kronos is already running: {url}',flush=True)
            return 0
    except (OSError,ValueError):
        pass
    service=ResearchService()
    try:
        server=create_server(service,args.port)
    except OSError:
        service.close()
        print(f'Port {args.port} is in use. Start with --port 8766.',flush=True)
        return 1
    service.store.recover()
    service.monitor.start()
    print(f'Pattern Scanner + SAI Research: {url}\nKeep this window open. Press Ctrl+C to stop.',flush=True)
    if not args.no_open: webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=.5)
    except KeyboardInterrupt:
        pass
    finally:
        service.close(); server.server_close()
    return 0
