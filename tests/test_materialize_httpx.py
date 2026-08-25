from __future__ import annotations
import asyncio, hashlib, importlib.util, json
from pathlib import Path
import httpx, pytest

SPEC = importlib.util.spec_from_file_location("reference", Path(__file__).parents[1] / "examples" / "materialize_httpx.py")
reference = importlib.util.module_from_spec(SPEC); assert SPEC.loader; SPEC.loader.exec_module(reference)
FIXTURES = json.loads((Path(__file__).parents[1] / "reference" / "materializer-fixtures.json").read_text())

def asset(data: bytes, url: str) -> dict:
    return {"filename":"x", "size_bytes":len(data), "sha256":hashlib.sha256(data).hexdigest(), "urls":[url], "parts":[]}

def test_direct_multipart_cache_mirrors_and_atomicity(tmp_path: Path) -> None:
    assert next(x for x in FIXTURES["scenarios"] if x["id"] == "redirect-policy")["redirects"]["max_accepted"] == reference.MAX_REDIRECTS == 5
    data, calls = b"abcdef", []
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        if "down" in str(req.url): return httpx.Response(503, request=req)
        return httpx.Response(200, content={"one":data[:3], "two":data[3:], "direct":data}[req.url.host], request=req)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    direct = asset(data, "https://direct/")
    p1, p2 = asset(data[:3], "https://one/"), asset(data[3:], "https://two/")
    multi = {**asset(data, "https://unused/"), "urls":[], "parts":[{"number":1, **{k:v for k,v in p1.items() if k != 'filename' and k != 'parts'}},{"number":2, **{k:v for k,v in p2.items() if k != 'filename' and k != 'parts'}}]}
    out, cache = tmp_path / "out", tmp_path / "cache"
    asyncio.run(reference.materialize(direct, out, cache, client)); assert out.read_bytes() == data
    calls.clear(); asyncio.run(reference.materialize(multi, out, cache, client)); assert out.read_bytes() == data and not calls
    bad = asset(data, "https://down/"); bad["urls"].append("https://direct/")
    (out).write_bytes(b"old")
    with pytest.raises(reference.MaterializeError): asyncio.run(reference.materialize({**bad, "sha256":"0"*64}, out, tmp_path / "fresh", client))
    assert out.read_bytes() == b"old"
    asyncio.run(client.aclose())
