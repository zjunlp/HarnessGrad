"""JobBench's explicit loopback judge, published only on the selected bridge IP."""
import ipaddress
from eval.service import modelgate


def open_gateway(base, bridge):
    from harnessgrad.native_judge_identity import normalize_endpoint
    base = normalize_endpoint(base)
    address = ipaddress.ip_address(bridge)
    if address.version != 4 or address.is_unspecified or address.is_multicast:
        raise ValueError('JobBench judge gateway requires one explicit Docker bridge IPv4 address')
    target, path = modelgate.split(base)
    gateway = modelgate.ModelGateway(bind_host=str(address), target=target, path=path)
    try:
        return gateway.start()
    except BaseException:
        gateway.stop()
        raise
