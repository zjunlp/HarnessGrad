"""Selected endpoint identity and real owned HTTP wire paths remain identical."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import urllib.request

import pytest

from eval.service import modelgate
from harnessgrad import native_judge_identity
from data.scaffolds.native_harbor_runtime.judge_gateway import open_gateway


@pytest.mark.parametrize('path,canonical', [('/v1','/v1'),('/v1/','/v1'),
    ('/review/v1;variant=one','/review/v1;variant=one'),
    ('/review/v1;variant=one/','/review/v1;variant=one'),
    ('/review;group=one/v1;variant=two','/review;group=one/v1;variant=two')])
def test_selected_identity_and_gateway_forward_the_complete_path(path,canonical):
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            requests.append(self.path)
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(200);self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'{}')
    upstream=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=upstream.serve_forever,daemon=True);thread.start()
    gateway=None
    try:
        raw='http://127.0.0.1:'+str(upstream.server_port)+path
        selected=native_judge_identity.selection({'HG_JUDGE_HARBOR_BASE_URL':raw,'HG_JUDGE_HARBOR_MODEL':'authored'})
        target,forward=modelgate.split(selected['judge_base_url'])
        assert target==upstream.server_address and forward==canonical
        assert selected['judge_identity']['endpoint_sha256']==hashlib.sha256(selected['judge_base_url'].encode()).hexdigest()
        gateway=open_gateway(raw,'127.0.0.1')
        request=urllib.request.Request(gateway.base_url+'/chat/completions',data=b'{}')
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=2) as response:
            assert response.status==200 and response.read()==b'{}'
        assert gateway.path==canonical and requests==[canonical+'/chat/completions']
    finally:
        if gateway:gateway.stop()
        upstream.shutdown();upstream.server_close();thread.join(2)


def test_split_keeps_existing_query_fragment_and_transport_interpretation():
    # split has historically returned a path, not query/fragment, and inferred
    # HTTPS port443 without itself implementing TLS. Preserve those contracts.
    assert modelgate.split('http://127.0.0.1/v1;x=1?ignored=yes#fragment')==(('127.0.0.1',80),'/v1;x=1')
    assert modelgate.split('https://127.0.0.1/v1;x=1')==(('127.0.0.1',443),'/v1;x=1')
    assert modelgate.split('http://127.0.0.1:9000')==(('127.0.0.1',9000),'')
    for suffix in ('?token=x','#fragment'):
        with pytest.raises(ValueError):native_judge_identity.normalize_endpoint('http://127.0.0.1/v1'+suffix)
