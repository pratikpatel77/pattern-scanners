"""Isolated browser check: synthetic OI, real API/UI, no orders or account access."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import tempfile
import threading
from unittest.mock import patch
from playwright.sync_api import sync_playwright
from kronos.service import ResearchService
from kronos.server import create_server
from test_options_oi import raw, snapshot
from kronos.options_oi import build_context

with tempfile.TemporaryDirectory() as tmp:
    service=ResearchService(root=tmp)
    service.desk.bootstrap=lambda:dict(universe=[],ideas=[],ai={},paper=[],settings={})
    def response(url, body):
        choice=body.get('expiries')
        expiry=next(k for k,v in choice.items() if v['is_enabled']) if choice else '2026-11-23'
        return raw(expiry=expiry)
    service.sensibull._post=response
    service.sensibull.series=lambda symbol,kind:dict(rows=[])
    server=create_server(service,0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1000})
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}/options')
            page.wait_for_function("typeof window.openOI === 'function'")
            page.evaluate("S.symbol='TEST'; view('oi')")
            page.locator('#oi-body table').first.wait_for()
            assert page.locator('#oi-body table').count()==2
            assert '2026-11-23' in page.locator('#oi-body').inner_text()
            assert '2026-12-29' in page.locator('#oi-body').inner_text()
            assert 'not available' in page.locator('#oi-body').inner_text()
            assert 'today' in page.locator('#oi-summary').inner_text().lower()
            assert '+0.200' in page.locator('#oi-summary').inner_text()
            assert 'EOD / stale for live use' in page.locator('#oi-meta').inner_text()
            page.locator('[data-m="180"]').click()
            page.wait_for_function("document.querySelector('[data-m=\"180\"]').getAttribute('aria-pressed') === 'true'")
            page.screenshot(path='outputs/oi-browser-2026-11-07.png',full_page=True)
            context=build_context(snapshot(),{},{},101,'EOD','2026-11-06')
            page.evaluate("c => {document.querySelector('#oi-history').innerHTML=oiContextHtml(c);}",context)
            page.locator('#oi-history summary').click()
            assert 'next-month share' in page.locator('#oi-history').inner_text().lower()
            assert 'futures history' in page.locator('#oi-history').inner_text().lower()
            assert not errors, errors
            browser.close()
            print('Browser passed: two-expiry API/table, daily changes, incomplete windows, context explanation; no JS errors.')
    finally:
        server.shutdown();server.server_close();service.close()
