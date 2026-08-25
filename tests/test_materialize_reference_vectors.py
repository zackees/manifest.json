"""Executable vectors shared with reference/rust-materializer."""
from __future__ import annotations
import asyncio, importlib.util, json
from pathlib import Path
import httpx, pytest

ROOT=Path(__file__).parents[1]
SPEC=importlib.util.spec_from_file_location("reference", ROOT/"examples"/"materialize_httpx.py")
reference=importlib.util.module_from_spec(SPEC); assert SPEC.loader; SPEC.loader.exec_module(reference)
V={x["id"]:x for x in json.loads((ROOT/"reference"/"materializer-fixtures.json").read_text())["scenarios"]}

def client_for(vector, calls):
    bodies={x["url"]:x["body"].encode() for x in vector.get("responses", [])}
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=bodies.get(str(request.url), b""), request=request)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))

def test_shared_vectors_execute_relative_multipart_and_fatal_part(tmp_path):
    async def run():
        for ident in ("trusted-relative", "multipart-reconstruction"):
            v=V[ident]; calls=[]; c=client_for(v,calls); out=tmp_path/ident
            await reference.materialize(v["asset"],out,tmp_path/"cache",c,base_url=v.get("base_url"))
            assert calls==v["expected_requests"]
            assert out.read_bytes()==(b"abc" if ident=="trusted-relative" else b"abcdef")
            await c.aclose()
        v=V["bad-part-is-fatal"]; calls=[]; c=client_for(v,calls)
        with pytest.raises(reference.MaterializeError): await reference.materialize(v["asset"],tmp_path/"bad",tmp_path/"bad-cache",c)
        assert calls==v["expected_requests"] and not (tmp_path/"bad").exists(); await c.aclose()
    asyncio.run(run())

def test_part_cache_reused_after_full_cache_removed(tmp_path):
    async def run():
        v=V["multipart-reconstruction"]; calls=[]; c=client_for(v,calls); cache=tmp_path/"cache"
        await reference.materialize(v["asset"],tmp_path/"one",cache,c)
        (cache/"full"/v["asset"]["sha256"]).unlink(); calls.clear()
        await reference.materialize(v["asset"],tmp_path/"two",cache,c)
        assert not calls; await c.aclose()
    asyncio.run(run())

def test_oversize_aborts_at_size_plus_one_and_preserves_destination(tmp_path):
    async def run():
        v=V["oversize"]; calls=[]; c=client_for(v,calls); out=tmp_path/"out"; out.write_bytes(b"old")
        with pytest.raises(reference.MaterializeError): await reference.materialize(v["asset"],out,tmp_path/"cache",c)
        assert calls==v["expected_requests"] and out.read_bytes()==b"old"; await c.aclose()
    asyncio.run(run())

def test_redirect_limits_origin_and_downgrade_are_enforced(tmp_path):
    async def fetch(locations):
        calls=[]
        def handler(req):
            calls.append(str(req.url)); i=len(calls)-1
            return httpx.Response(302,headers={"location":locations[i]},request=req) if i<len(locations) else httpx.Response(200,content=b"abc",request=req)
        c=httpx.AsyncClient(transport=httpx.MockTransport(handler)); v=V["trusted-relative"]
        try: return await reference.materialize(v["asset"],tmp_path/str(len(locations)),tmp_path/"cache"/str(len(locations)),c,base_url=v["base_url"]),calls
        finally: await c.aclose()
    async def run():
        _,calls=await fetch([f"https://mirror.example/r{i}" for i in range(5)]); assert len(calls)==6
        with pytest.raises(reference.MaterializeError): await fetch([f"https://mirror.example/r{i}" for i in range(6)])
        for loc in ("https://elsewhere.example/x", "http://mirror.example/x"):
            with pytest.raises(reference.MaterializeError): await fetch([loc])
    asyncio.run(run())

def test_signed_absolute_urls_allowed_and_relative_escapes_are_safe():
    assert reference.resolve_url("https://mirror.example/file?X-Amz-Signature=not-a-secret",None).startswith("https://")
    for bad in ("%2e%2e/x", "%252e%252e/x", "%255cfoo", "x?secret=1", "x#fragment"):
        with pytest.raises(reference.MaterializeError) as exc: reference.resolve_url(bad,"https://mirror.example/base/")
        assert "secret" not in str(exc.value)

def test_concurrent_materializations_share_cache_without_corrupting_output(tmp_path):
    async def run():
        v=V["concurrency"]; calls=[]; c=client_for(v,calls); cache=tmp_path/"cache"; count=v["concurrency"]["workers"]
        await asyncio.gather(*(reference.materialize(v["asset"],tmp_path/f"out-{n}",cache,c) for n in range(count)))
        assert all((tmp_path/f"out-{n}").read_bytes()==v["concurrency"]["expected_bytes"].encode() for n in range(count)); await c.aclose()
    asyncio.run(run())

def test_cancellation_cleans_temporary_materialization_and_keeps_final(tmp_path):
    class Slow(httpx.AsyncByteStream):
        def __init__(self, started): self.started=started
        async def __aiter__(self):
            yield b"a"; self.started.set(); await asyncio.Event().wait()
        async def aclose(self): pass
    async def run():
        started=asyncio.Event(); out=tmp_path/"out"; out.write_bytes(b"old")
        def handler(req): return httpx.Response(200,stream=Slow(started),request=req)
        c=httpx.AsyncClient(transport=httpx.MockTransport(handler)); v=V["cancellation"]
        task=asyncio.create_task(reference.materialize(v["asset"],out,tmp_path/"cache",c))
        await started.wait(); task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert out.read_bytes()==b"old" and not list((tmp_path/"cache").glob("materialize-*"))
        await c.aclose()
    asyncio.run(run())
